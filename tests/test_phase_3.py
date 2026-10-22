from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from champions_assist.fsm import MatchController, ObservationEvent, VisualUI, WindowKind
from champions_assist.model_adapter import MockResponsesTransport, ModelAdapter, ProviderResult, TransportRequest
from champions_assist.session_payloads import build_session_payload, current_session_identity, request_session_model_job
from champions_assist.settings import ModelSettings
from champions_assist.simulate import run_simulated_normal_trace
from champions_assist.store import BattleStore
from champions_assist.structured_schema import responses_schema_for_phase
from champions_assist.wire import AllowedActions


OPPONENTS = ("Opp1", "Opp2", "Opp3", "Opp4", "Opp5", "Opp6")
TEAM = ("Own1", "Own2", "Own3", "Own4", "Own5", "Own6")


def write_masters(root: Path, marker: str = "v1") -> None:
    (root / "Pokemon_Champions_MC_live_decision_gate.txt").write_text(f"LIVE GATE {marker}\nNO_CROSSGAME=1\n", encoding="utf-8")
    rows = "\n".join(f"{name}|matrix-{marker}-{name}" for name in OPPONENTS)
    lookup = "\n".join(f"{name}|lookup-{marker}-{name}" for name in OPPONENTS)
    (root / "Pokemon_Champions_MC_live_selection_matrix.txt").write_text(rows + "\n", encoding="utf-8")
    (root / "Pokemon_Champions_MC_60sec_lookup_master.txt").write_text(lookup + "\n", encoding="utf-8")
    header = "record,name,item,ability,nature,ap_h_a_b_c_d_s,move1,move2,move3,move4,role,form_change,runtime_note\n"
    rows = "\n".join(
        f"MON,{name},item,ability,nature,evs,{name}-move1,{name}-move2,{name}-move3,{name}-move4,role,,note"
        for name in TEAM
    )
    (root / "Pokemon_Champions_MC_team_2026-10-03.csv").write_text(header + rows + "\n", encoding="utf-8")


def response_base(payload: dict) -> dict:
    return {
        "protocol_version": "2.3.0",
        "request_id": payload["request_id"],
        "match_id": payload["match_id"],
        "decision_window_id": payload["decision_window_id"],
        "tactical_state_version": payload["tactical_state_version"],
        "allowed_action_digest": payload["allowed_action_digest"],
        "decision_status": "RECOMMENDED",
        "reason_code": "TEST_FIXTURE",
        "warning": None,
    }


def select_response(payload: dict) -> dict:
    selected = payload["allowed_actions"]["team_candidates"][:3]
    return {**response_base(payload), "selected_three": selected, "lead": selected[0]}


def prep_response(payload: dict, *, complete: bool = True) -> dict:
    selected = payload["context"]["selected_three"]
    lead = payload["context"]["lead"]
    opponents = payload["context"]["opponent_preview_six"]
    lead_code = "ACT=TEST|KO=?|DPS=TEST|PRI=?|S=?|OUR=?|OPP=?|SAFE=?|PROC=TEST|LOCK=?|MEGA=?"
    bench_code = "S=?|OUR=?|OPP=?|PRI=?|SAFE=?|PROC=TEST"
    matchups = [
        {"our_pokemon": own, "opponent_pokemon": opponent, "eval_code": lead_code if own == lead else bench_code}
        for own in selected
        for opponent in opponents
    ]
    if not complete:
        matchups.pop()
    return {
        **response_base(payload),
        "prep_status": "COMPLETE",
        "matchups_18": matchups,
        "preserve": selected[0],
        "stopper": selected[1],
        "closer": selected[2],
        "unfinished_reason": None,
    }


def battle_response(payload: dict, *, action_kind: str, move: str | None = None, target: str | None = None, keep_back: str | None = None) -> dict:
    return {
        **response_base(payload),
        "action_kind": action_kind,
        "move": move,
        "mega": False,
        "switch_target": target,
        "planned_pivot_target": None,
        "keep_back": keep_back,
    }


class ExternalFixtureTransport(MockResponsesTransport):
    """A test double marked external; it never imports or contacts the SDK."""

    name = "external_fixture"
    external = True


class Phase3TestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "masters"
        self.root.mkdir()
        write_masters(self.root)
        self.store = BattleStore(Path(self.temp.name) / "battle.sqlite3")
        self.controller = MatchController(self.root, store=self.store)
        self._match_number = 0

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def new_selection_session(self):
        self._match_number += 1
        session = self.controller.start_match(f"match-3-{self._match_number}", now_ms=0)
        session.receive_opponent_preview(OPPONENTS, now_ms=1)
        return session

    def prep_session(self):
        session = self.new_selection_session()
        session.record_actual_selection(TEAM[:3], TEAM[0], now_ms=2)
        return session

    def battle_session(self, kind: WindowKind = WindowKind.NORMAL_COMMAND, *, choice_lock: str | None = None):
        session = self.prep_session()
        session.observe_event("lead", ObservationEvent.OPPONENT_LEAD_VISIBLE, now_ms=3)
        if kind is WindowKind.NORMAL_COMMAND:
            actions = AllowedActions(
                completeness="COMPLETE",
                moves=("move-a", "move-b"),
                pivot_moves=("move-b",),
                switch_targets=("Own2", "Own3"),
                choice_lock=choice_lock,
            )
            session.update_observed_state("active", {"our_active": "Own1"})
            window = session.present_ui(VisualUI.COMMAND_MENU, actions, now_ms=4)
        else:
            event = {
                WindowKind.PIVOT_TARGET: ObservationEvent.OUR_PIVOT_SUCCEEDED,
                WindowKind.FAINT_REPLACEMENT: ObservationEvent.OUR_FAINTED,
                WindowKind.EFFECT_REPLACEMENT: ObservationEvent.EFFECT_SWITCH_CHOICE_AVAILABLE,
            }[kind]
            session.observe_event(f"cause-{kind.value}", event, now_ms=4)
            window = session.present_ui(
                VisualUI.PARTY_MENU,
                AllowedActions(completeness="COMPLETE", switch_targets=("Own2", "Own3")),
                now_ms=5,
                actual_ui_remaining_ms=30_000,
            )
        assert window is not None
        return session

    @staticmethod
    def adapter(store: BattleStore, transport: MockResponsesTransport, *, settings: ModelSettings | None = None, now=None) -> ModelAdapter:
        return ModelAdapter(store=store, transport=transport, settings=settings or ModelSettings(), now_ms=now or (lambda: 0))

    def test_select_response_is_validated_applied_and_prompt_uses_frozen_gate(self) -> None:
        session = self.new_selection_session()
        write_masters(self.root, marker="changed-after-preload")
        transport = MockResponsesTransport([lambda request: select_response(request.payload)])
        outcome = request_session_model_job(
            session,
            self.adapter(self.store, transport),
            phase="SELECT",
            request_id="select-1",
            now_ms=2,
        )
        self.assertEqual(outcome.status.value, "DISPLAYABLE")
        self.assertEqual(session.selection_recommendation.selected_three, TEAM[:3])
        self.assertIn("LIVE GATE v1", transport.requests[0].prompt.system)
        self.assertNotIn("changed-after-preload", transport.requests[0].prompt.system)
        self.assertNotIn("test-key", transport.requests[0].prompt.system)
        self.assertEqual(transport.requests[0].text_format["type"], "json_schema")

    def test_prep_complete_and_incomplete_are_distinguished(self) -> None:
        session = self.prep_session()
        payload = build_session_payload(session, phase="PREP", request_id="prep-ok", now_ms=3)
        transport = MockResponsesTransport([prep_response(payload)])
        ok = self.adapter(self.store, transport).decide(
            payload, session.assets, current_supplier=lambda: current_session_identity(session, phase="PREP")
        )
        self.assertEqual(ok.status.value, "DISPLAYABLE")
        bad_payload = deepcopy(payload)
        bad_payload["request_id"] = "prep-incomplete"
        bad_payload["decision_window_id"] = f"{session.match_id}:prep-incomplete"
        current = {
            "match_id": bad_payload["match_id"],
            "decision_window_id": bad_payload["decision_window_id"],
            "tactical_state_version": bad_payload["tactical_state_version"],
            "allowed_action_digest": bad_payload["allowed_action_digest"],
            "window_open": True,
        }
        bad = self.adapter(self.store, MockResponsesTransport([prep_response(bad_payload, complete=False)])).decide(
            bad_payload, session.assets, current_supplier=lambda: current
        )
        self.assertEqual(bad.status.value, "REJECTED")
        self.assertIn("PREP_MATCHUPS_18_INCOMPLETE", bad.reason)

    def test_all_battle_window_kinds_accept_only_their_legal_shape(self) -> None:
        for kind in (WindowKind.NORMAL_COMMAND, WindowKind.PIVOT_TARGET, WindowKind.FAINT_REPLACEMENT, WindowKind.EFFECT_REPLACEMENT):
            with self.subTest(kind=kind):
                session = self.battle_session(kind)
                payload = build_session_payload(session, phase=kind.value, request_id=f"{kind.value}-ok", now_ms=6)
                if kind is WindowKind.NORMAL_COMMAND:
                    response = battle_response(payload, action_kind="MOVE", move="move-a")
                else:
                    response = battle_response(
                        payload,
                        action_kind="SWITCH",
                        target="Own2",
                        keep_back="Own3" if kind is WindowKind.FAINT_REPLACEMENT else None,
                    )
                outcome = self.adapter(self.store, MockResponsesTransport([response])).decide(
                    payload, session.assets, current_supplier=lambda: current_session_identity(session, phase=kind.value)
                )
                self.assertEqual(outcome.status.value, "DISPLAYABLE")

    def test_illegal_move_choice_lock_and_switch_target_are_rejected(self) -> None:
        session = self.battle_session()
        payload = build_session_payload(session, phase="NORMAL_COMMAND", request_id="illegal-move", now_ms=6)
        illegal = battle_response(payload, action_kind="MOVE", move="not-listed")
        outcome = self.adapter(self.store, MockResponsesTransport([illegal])).decide(
            payload, session.assets, current_supplier=lambda: current_session_identity(session, phase="NORMAL_COMMAND")
        )
        self.assertEqual(outcome.reason, "ILLEGAL_MOVE")
        locked = self.battle_session(choice_lock="move-a")
        locked_payload = build_session_payload(locked, phase="NORMAL_COMMAND", request_id="lock", now_ms=6)
        lock_violation = battle_response(locked_payload, action_kind="MOVE", move="move-b")
        locked_outcome = self.adapter(self.store, MockResponsesTransport([lock_violation])).decide(
            locked_payload, locked.assets, current_supplier=lambda: current_session_identity(locked, phase="NORMAL_COMMAND")
        )
        self.assertEqual(locked_outcome.reason, "CHOICE_LOCK_VIOLATION")
        pivot = self.battle_session(WindowKind.PIVOT_TARGET)
        pivot_payload = build_session_payload(pivot, phase="PIVOT_TARGET", request_id="bad-target", now_ms=6)
        wrong_target = battle_response(pivot_payload, action_kind="SWITCH", target="not-listed")
        pivot_outcome = self.adapter(self.store, MockResponsesTransport([wrong_target])).decide(
            pivot_payload, pivot.assets, current_supplier=lambda: current_session_identity(pivot, phase="PIVOT_TARGET")
        )
        self.assertEqual(pivot_outcome.reason, "ILLEGAL_SWITCH")
        mega_session = self.battle_session()
        mega_payload = build_session_payload(mega_session, phase="NORMAL_COMMAND", request_id="illegal-mega", now_ms=6)
        mega = battle_response(mega_payload, action_kind="MOVE", move="move-a")
        mega["mega"] = True
        mega_outcome = self.adapter(self.store, MockResponsesTransport([mega])).decide(
            mega_payload, mega_session.assets, current_supplier=lambda: current_session_identity(mega_session, phase="NORMAL_COMMAND")
        )
        self.assertEqual(mega_outcome.reason, "ILLEGAL_MEGA")

    def test_stale_and_late_responses_are_not_displayed(self) -> None:
        session = self.battle_session()
        payload = build_session_payload(session, phase="NORMAL_COMMAND", request_id="stale", now_ms=6)
        current = current_session_identity(session, phase="NORMAL_COMMAND")

        def mutate_after_send(request: TransportRequest) -> dict:
            current["tactical_state_version"] += 1
            return battle_response(request.payload, action_kind="MOVE", move="move-a")

        stale = self.adapter(self.store, MockResponsesTransport([mutate_after_send])).decide(
            payload, session.assets, current_supplier=lambda: current
        )
        self.assertEqual(stale.status.value, "STALE")
        session = self.battle_session()
        late_payload = build_session_payload(session, phase="NORMAL_COMMAND", request_id="late", now_ms=6)
        clock = [0]

        def delayed(request: TransportRequest) -> dict:
            clock[0] = late_payload["deadline_ms_remaining"]
            return battle_response(request.payload, action_kind="MOVE", move="move-a")

        late = self.adapter(self.store, MockResponsesTransport([delayed]), now=lambda: clock[0]).decide(
            late_payload, session.assets, current_supplier=lambda: current_session_identity(session, phase="NORMAL_COMMAND")
        )
        self.assertEqual(late.status.value, "TIMEOUT")

    def test_transport_failure_duplicate_and_budget_do_not_send_extra_calls(self) -> None:
        session = self.new_selection_session()
        payload = build_session_payload(session, phase="SELECT", request_id="failure", now_ms=2)
        failed_transport = MockResponsesTransport([RuntimeError("synthetic")])
        failed = self.adapter(self.store, failed_transport).decide(
            payload, session.assets, current_supplier=lambda: current_session_identity(session, phase="SELECT")
        )
        self.assertEqual(failed.status.value, "UNAVAILABLE")
        duplicate_session = self.controller.start_match("duplicate-match", now_ms=0)
        duplicate_session.receive_opponent_preview(OPPONENTS, now_ms=1)
        duplicate_payload = build_session_payload(duplicate_session, phase="SELECT", request_id="same", now_ms=2)
        transport = MockResponsesTransport([select_response(duplicate_payload)])
        adapter = self.adapter(self.store, transport)
        first = adapter.decide(duplicate_payload, duplicate_session.assets, current_supplier=lambda: current_session_identity(duplicate_session, phase="SELECT"))
        second = adapter.decide(duplicate_payload, duplicate_session.assets, current_supplier=lambda: current_session_identity(duplicate_session, phase="SELECT"))
        self.assertEqual(first.status.value, "DISPLAYABLE")
        self.assertEqual(second.status.value, "DUPLICATE_SUPPRESSED")
        self.assertEqual(len(transport.requests), 1)
        budget_session = self.controller.start_match("budget-match", now_ms=0)
        budget_session.receive_opponent_preview(OPPONENTS, now_ms=1)
        budget_payload = build_session_payload(budget_session, phase="SELECT", request_id="budget-1", now_ms=2)
        budget_transport = ExternalFixtureTransport(
            [ProviderResult(response=select_response(budget_payload), input_tokens=11, output_tokens=7, total_tokens=18)]
        )
        settings = ModelSettings(
            api_enabled=True,
            api_key="TEST_ONLY_NOT_A_REAL_KEY",
            spend_cap_usd=0.01,
            estimated_request_usd=0.01,
            max_requests_per_match=2,
            max_requests_per_day=2,
        )
        budget_adapter = self.adapter(self.store, budget_transport, settings=settings)
        allowed = budget_adapter.decide(
            budget_payload, budget_session.assets, current_supplier=lambda: current_session_identity(budget_session, phase="SELECT")
        )
        next_payload = deepcopy(budget_payload)
        next_payload["request_id"] = "budget-2"
        next_payload["decision_window_id"] = "budget-match:select-2"
        next_current = {
            "match_id": next_payload["match_id"],
            "decision_window_id": next_payload["decision_window_id"],
            "tactical_state_version": next_payload["tactical_state_version"],
            "allowed_action_digest": next_payload["allowed_action_digest"],
            "window_open": True,
        }
        stopped = budget_adapter.decide(next_payload, budget_session.assets, current_supplier=lambda: next_current)
        self.assertEqual(allowed.status.value, "DISPLAYABLE")
        self.assertEqual(stopped.status.value, "BUDGET_EXCEEDED")
        self.assertEqual(len(budget_transport.requests), 1)
        totals = self.store.api_budget_totals("budget-match", day_utc=datetime.now(timezone.utc).date().isoformat())
        self.assertEqual(totals["match_calls"], 1)
        usage = self.store._connection.execute(
            "SELECT input_tokens, output_tokens, total_tokens FROM api_usage WHERE match_id = 'budget-match'"
        ).fetchone()
        self.assertEqual(tuple(usage), (11, 7, 18))

    def test_external_transport_is_disabled_by_default_without_contacting_it(self) -> None:
        session = self.new_selection_session()
        payload = build_session_payload(session, phase="SELECT", request_id="external-disabled", now_ms=2)
        transport = ExternalFixtureTransport([select_response(payload)])
        outcome = self.adapter(self.store, transport).decide(
            payload, session.assets, current_supplier=lambda: current_session_identity(session, phase="SELECT")
        )
        self.assertEqual(outcome.reason, "EXTERNAL_API_DISABLED")
        self.assertEqual(transport.requests, [])

    def test_structured_schema_is_separate_and_offline_end_to_end_never_uses_network(self) -> None:
        response_schema = responses_schema_for_phase("NORMAL_COMMAND")
        self.assertEqual(response_schema["additionalProperties"], False)
        self.assertIn("anyOf", response_schema["properties"]["move"])
        result = run_simulated_normal_trace(self.root)
        self.assertEqual(result["mock_model_jobs"], 4)
        self.assertEqual(result["network"], "NOT_USED")
        self.assertEqual(result["phase"], "S08_NEXT_MATCH_READY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
