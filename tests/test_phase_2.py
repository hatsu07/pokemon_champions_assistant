from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from champions_assist.fsm import (
    MatchController,
    MatchPhase,
    MockDecision,
    ObservationEvent,
    SessionInactiveError,
    VisualUI,
    WindowKind,
    WindowStatus,
)
from champions_assist.mock_provider import ScriptedMockDecisionProvider
from champions_assist.preload import MasterPreloader, PreloadError
from champions_assist.prep import PrepResult, mocked_complete_prep
from champions_assist.store import BattleStore
from champions_assist.wire import AllowedActions


OPPONENTS = ("Opp1", "Opp2", "Opp3", "Opp4", "Opp5", "Opp6")
TEAM = ("Own1", "Own2", "Own3", "Own4", "Own5", "Own6")


def write_masters(root: Path, marker: str = "v1") -> None:
    (root / "Pokemon_Champions_MC_live_decision_gate.txt").write_text(f"gate {marker}\n", encoding="utf-8")
    rows = "\n".join(f"{name}|matrix-{marker}-{name}" for name in OPPONENTS)
    lookup = "\n".join(f"{name}|lookup-{marker}-{name}" for name in OPPONENTS)
    (root / "Pokemon_Champions_MC_live_selection_matrix.txt").write_text(f"# matrix\n{rows}\n", encoding="utf-8")
    (root / "Pokemon_Champions_MC_60sec_lookup_master.txt").write_text(f"# lookup\n{lookup}\n", encoding="utf-8")
    team_rows = "\n".join(f"MON,{name}" for name in TEAM)
    (root / "Pokemon_Champions_MC_team_2026-10-03.csv").write_text(f"record,name\nTEAM,meta\n{team_rows}\n", encoding="utf-8")


class Phase2TestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "masters"
        self.root.mkdir()
        write_masters(self.root)
        self.store = BattleStore(Path(self.temp.name) / "battle.sqlite3")
        self.controller = MatchController(self.root, store=self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def start_battle(self, match_id: str = "match-1"):
        session = self.controller.start_match(match_id, now_ms=0)
        session.receive_opponent_preview(OPPONENTS, now_ms=1)
        session.record_selection_recommendation(TEAM[:3], TEAM[0])
        session.record_actual_selection(TEAM[:3], TEAM[0], now_ms=2)
        session.observe_event("lead-1", ObservationEvent.OPPONENT_LEAD_VISIBLE, now_ms=3)
        return session

    @staticmethod
    def normal_actions() -> AllowedActions:
        return AllowedActions(
            completeness="COMPLETE",
            moves=("move-a", "pivot-move"),
            pivot_moves=("pivot-move",),
            switch_targets=("Own2", "Own3"),
        )

    @staticmethod
    def party_actions() -> AllowedActions:
        return AllowedActions(completeness="COMPLETE", switch_targets=("Own2", "Own3"))

    def test_four_file_preload_is_immutable_and_persisted(self) -> None:
        session = self.controller.start_match("match-1", now_ms=0)
        initial_hash = session.assets.gate_sha256
        initial_rows = session.assets.matrix_rows_for(OPPONENTS)
        write_masters(self.root, marker="v2")
        self.assertEqual(session.assets.gate_sha256, initial_hash)
        self.assertEqual(session.assets.matrix_rows_for(OPPONENTS), initial_rows)
        self.assertEqual(
            self.store._connection.execute("SELECT COUNT(*) FROM match_assets WHERE match_id = 'match-1'").fetchone()[0],
            4,
        )
        new_session = self.controller.start_match("match-2", now_ms=1)
        self.assertNotEqual(new_session.assets.gate_sha256, initial_hash)

    def test_incomplete_preload_is_never_accepted(self) -> None:
        (self.root / "Pokemon_Champions_MC_60sec_lookup_master.txt").unlink()
        with self.assertRaises(PreloadError) as raised:
            MasterPreloader(self.root).load_for_new_match()
        self.assertIn("Pokemon_Champions_MC_60sec_lookup_master.txt", raised.exception.failures)

    def test_preview_uses_one_cached_batch_and_actual_selection_overrides_model(self) -> None:
        session = self.controller.start_match("match-1", now_ms=0)
        rows = session.receive_opponent_preview(OPPONENTS, now_ms=1, actual_ui_remaining_ms=30_000)
        self.assertEqual(len(rows), 6)
        self.assertEqual(session.selection_deadline.budget_ms, 30_000)
        session.record_selection_recommendation(("Own1", "Own2", "Own3"), "Own1")
        prep = session.record_actual_selection(("Own2", "Own3", "Own4"), "Own2", now_ms=2, actual_ui_remaining_ms=50_000)
        self.assertEqual(prep.selected_three, ("Own2", "Own3", "Own4"))
        self.assertEqual(prep.lead, "Own2")
        self.assertEqual(len(prep.expected_pairs), 18)
        self.assertEqual(session.prep_deadline.budget_ms, 40_000)

    def test_deadline_management_expires_model_work_without_tactical_fallback(self) -> None:
        session = self.controller.start_match("match-1", now_ms=0)
        session.receive_opponent_preview(OPPONENTS, now_ms=1, actual_ui_remaining_ms=100)
        self.assertEqual(session.enforce_deadlines(now_ms=101), ("SELECTION",))
        with self.assertRaises(RuntimeError):
            session.record_selection_recommendation(TEAM[:3], TEAM[0])
        # Observed human selection remains loggable even after the model deadline.
        session.record_actual_selection(TEAM[:3], TEAM[0], now_ms=102, actual_ui_remaining_ms=100)
        self.assertEqual(session.enforce_deadlines(now_ms=202), ("PREP",))
        self.assertFalse(session.prep_validation.complete)

    def test_prep_requires_all_eighteen_and_win_conditions(self) -> None:
        session = self.controller.start_match("match-1", now_ms=0)
        session.receive_opponent_preview(OPPONENTS, now_ms=1)
        prep_input = session.record_actual_selection(TEAM[:3], TEAM[0], now_ms=2)
        result = mocked_complete_prep(prep_input)
        validation = session.record_prep_result(result, now_ms=3)
        self.assertTrue(validation.complete)
        incomplete = PrepResult("COMPLETE", result.matchups_18[:-1], "P", "S", "C")
        self.assertFalse(session.record_prep_result(incomplete, now_ms=4).complete)

    def test_opponent_lead_stops_unfinished_prep_and_enters_s05(self) -> None:
        session = self.controller.start_match("match-1", now_ms=0)
        session.receive_opponent_preview(OPPONENTS, now_ms=1)
        session.record_actual_selection(TEAM[:3], TEAM[0], now_ms=2)
        session.observe_event("lead", ObservationEvent.OPPONENT_LEAD_VISIBLE, now_ms=3)
        self.assertEqual(session.phase, MatchPhase.S05_IN_BATTLE)
        self.assertFalse(session.prep_validation.complete)
        self.assertEqual(session.prep_validation.reason, "PREP_INTERRUPTED_BY_OPPONENT_LEAD")

    def test_normal_party_back_reuses_one_window_and_request(self) -> None:
        session = self.start_battle()
        actions = self.normal_actions()
        normal = session.present_ui(VisualUI.COMMAND_MENU, actions, now_ms=10, actual_ui_remaining_ms=30_000)
        provider = ScriptedMockDecisionProvider([MockDecision("RECOMMENDED", "MOVE", move="move-a")])
        session.request_mock_decision(provider, now_ms=11)
        via_party = session.present_ui(VisualUI.PARTY_MENU, self.party_actions(), now_ms=12)
        back = session.present_ui(VisualUI.COMMAND_MENU, actions, now_ms=13)
        session.request_mock_decision(provider, now_ms=14)
        self.assertIs(via_party, normal)
        self.assertIs(back, normal)
        self.assertEqual(normal.decision_window_id, "match-1:dw:001")
        self.assertEqual(normal.deadline.budget_ms, 30_000)
        self.assertEqual(len(provider.requests), 1)

    def test_valid_pivot_plan_reuses_only_planned_target(self) -> None:
        session = self.start_battle()
        normal = session.present_ui(VisualUI.COMMAND_MENU, self.normal_actions(), now_ms=10)
        provider = ScriptedMockDecisionProvider(
            [MockDecision("RECOMMENDED", "MOVE", move="pivot-move", planned_pivot_target="Own2")]
        )
        session.request_mock_decision(provider, now_ms=11)
        session.record_actual_action("actual-pivot", {"move": "pivot-move"})
        session.observe_event("pivot-success", ObservationEvent.OUR_PIVOT_SUCCEEDED, now_ms=12)
        pivot = session.present_ui(VisualUI.PARTY_MENU, self.party_actions(), now_ms=13, actual_ui_remaining_ms=30_000)
        self.assertNotEqual(pivot.decision_window_id, normal.decision_window_id)
        self.assertEqual(pivot.kind, WindowKind.PIVOT_TARGET)
        self.assertEqual(pivot.status, WindowStatus.REUSED_PLAN)
        self.assertEqual(pivot.recommendation["switch_target"], "Own2")
        session.request_mock_decision(provider, now_ms=14)
        self.assertEqual(len(provider.requests), 1)

    def test_pivot_plan_is_not_reused_after_observed_state_change(self) -> None:
        session = self.start_battle()
        session.present_ui(VisualUI.COMMAND_MENU, self.normal_actions(), now_ms=10)
        provider = ScriptedMockDecisionProvider(
            [
                MockDecision("RECOMMENDED", "MOVE", move="pivot-move", planned_pivot_target="Own2"),
                MockDecision("RECOMMENDED", "SWITCH", switch_target="Own3"),
            ]
        )
        session.request_mock_decision(provider, now_ms=11)
        session.record_actual_action("actual-pivot", {"move": "pivot-move"})
        session.observe_event("pivot-success", ObservationEvent.OUR_PIVOT_SUCCEEDED, now_ms=12)
        session.update_observed_state("opponent-switch", {"opponent_active": "changed"})
        pivot = session.present_ui(VisualUI.PARTY_MENU, self.party_actions(), now_ms=13, actual_ui_remaining_ms=30_000)
        self.assertEqual(pivot.status, WindowStatus.OPEN)
        self.assertTrue(pivot.requires_model)
        session.request_mock_decision(provider, now_ms=14)
        self.assertEqual(pivot.recommendation["switch_target"], "Own3")
        self.assertEqual(len(provider.requests), 2)

    def test_random_forced_switch_never_opens_party_decision(self) -> None:
        session = self.start_battle()
        session.observe_event("roar", ObservationEvent.RANDOM_FORCED_SWITCH, now_ms=10)
        self.assertIsNone(session.present_ui(VisualUI.PARTY_MENU, self.party_actions(), now_ms=11))
        self.assertEqual(session.decision_seq, 0)
        command = session.present_ui(VisualUI.COMMAND_MENU, self.normal_actions(), now_ms=12)
        provider = ScriptedMockDecisionProvider([MockDecision("RECOMMENDED", "MOVE", move="move-a")])
        session.request_mock_decision(provider, now_ms=13)
        self.assertEqual(command.kind, WindowKind.NORMAL_COMMAND)
        self.assertEqual(len(provider.requests), 1)

    def test_faint_replacement_and_subsequent_command_are_separate_windows(self) -> None:
        session = self.start_battle()
        session.observe_event("faint", ObservationEvent.OUR_FAINTED, now_ms=10)
        faint = session.present_ui(VisualUI.PARTY_MENU, self.party_actions(), now_ms=11, actual_ui_remaining_ms=30_000)
        provider = ScriptedMockDecisionProvider(
            [
                MockDecision("RECOMMENDED", "SWITCH", switch_target="Own2"),
                MockDecision("RECOMMENDED", "MOVE", move="move-a"),
            ]
        )
        session.request_mock_decision(provider, now_ms=12)
        session.record_actual_action("send-out", {"switch_target": "Own2"})
        command = session.present_ui(VisualUI.COMMAND_MENU, self.normal_actions(), now_ms=13)
        session.request_mock_decision(provider, now_ms=14)
        self.assertEqual(faint.kind, WindowKind.FAINT_REPLACEMENT)
        self.assertEqual(command.kind, WindowKind.NORMAL_COMMAND)
        self.assertNotEqual(faint.decision_window_id, command.decision_window_id)
        self.assertEqual(len(provider.requests), 2)

    def test_effect_replacement_requires_an_observed_choice_ui(self) -> None:
        session = self.start_battle()
        session.observe_event("auto-item", ObservationEvent.EFFECT_AUTO_SWITCH, now_ms=10)
        self.assertIsNone(session.present_ui(VisualUI.PARTY_MENU, self.party_actions(), now_ms=11))
        session.observe_event("choice-item", ObservationEvent.EFFECT_SWITCH_CHOICE_AVAILABLE, now_ms=12)
        effect = session.present_ui(VisualUI.PARTY_MENU, self.party_actions(), now_ms=13, actual_ui_remaining_ms=30_000)
        self.assertEqual(effect.kind, WindowKind.EFFECT_REPLACEMENT)

    def test_unknown_non_normal_input_timer_blocks_a_new_model_request(self) -> None:
        session = self.start_battle()
        session.observe_event("faint", ObservationEvent.OUR_FAINTED, now_ms=10)
        faint = session.present_ui(VisualUI.PARTY_MENU, self.party_actions(), now_ms=11)
        self.assertEqual(faint.kind, WindowKind.FAINT_REPLACEMENT)
        self.assertEqual(faint.status, WindowStatus.BLOCKED)
        self.assertFalse(faint.requires_model)

    def test_turn_observation_decision_and_ui_navigation_are_separate(self) -> None:
        session = self.start_battle()
        session.set_game_turn("turn-7")
        session.update_observed_state("hp-change", {"our_hp": {"value": 80, "certainty": "CONFIRMED"}})
        window = session.present_ui(VisualUI.COMMAND_MENU, self.normal_actions(), now_ms=10)
        session.present_ui(VisualUI.MOVE_MENU, self.normal_actions(), now_ms=11)
        session.present_ui(VisualUI.PARTY_MENU, self.party_actions(), now_ms=12)
        self.assertEqual(session.game_turn_seq, "turn-7")
        self.assertEqual(session.observation_seq, 1)
        self.assertEqual(session.decision_seq, 1)
        self.assertEqual(window.game_turn_seq, "turn-7")
        self.assertEqual(self.store.replay(session.match_id)["fields"]["our_hp"]["value"]["value"], 80)

    def test_only_confirmed_turn_end_changes_phase_one_duration_counter(self) -> None:
        session = self.start_battle()
        self.store.append_event(
            session.match_id,
            "wall-start",
            "DURATION_STARTED",
            {"duration_id": "wall", "field": "screen", "remaining_candidates": [4, 7]},
        )
        session.observe_event("faint", ObservationEvent.OUR_FAINTED, now_ms=10)
        session.present_ui(VisualUI.PARTY_MENU, self.party_actions(), now_ms=11)
        self.assertEqual(self.store.replay(session.match_id)["durations"]["wall"]["remaining_candidates"], [4, 7])
        self.assertTrue(session.confirm_turn_end("turn-end", "turn-1"))
        self.assertFalse(session.confirm_turn_end("turn-end-repeat", "turn-1"))
        self.assertEqual(self.store.replay(session.match_id)["durations"]["wall"]["remaining_candidates"], [3, 6])

    def test_rematch_invalidates_old_windows_plans_and_deadlines(self) -> None:
        old = self.start_battle("match-old")
        old.present_ui(VisualUI.COMMAND_MENU, self.normal_actions(), now_ms=10)
        new = self.controller.start_match("match-new", now_ms=20)
        self.assertEqual(new.phase, MatchPhase.S02_MATCHMAKING)
        self.assertIsNone(new.current_window)
        self.assertIsNone(new.last_pivot_plan)
        self.assertIsNone(new.selection_deadline)
        with self.assertRaises(SessionInactiveError):
            old.present_ui(VisualUI.COMMAND_MENU, self.normal_actions(), now_ms=21)


if __name__ == "__main__":
    unittest.main(verbosity=2)
