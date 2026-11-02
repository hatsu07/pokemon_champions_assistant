"""Phase 2 synthetic-only orchestration regression; no real-game claims."""
from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from champions_assist.local_strategy.models import (
    CapabilityStatus, Hypothesis, PROTOCOL_VERSION, SimulationResult,
    canonical_json, fingerprint,
)
from champions_assist.local_strategy.shadow_runner import (
    ReplayOpponentScenario, run_shadow_turn,
)
from tests.test_issue_141_one_turn import current, EARTH, CLAW, OTHER_SPECIES
from tests.test_issue_140_foundation import VERIFIED_ASSETS


def scenario(hypothesis="", choices=("move 1", "switch 2"), *,
             seed=(1, 2, 3, 4), source="FULL_REPLAY_SYNTHETIC"):
    battle = {"fixture": "full-synthetic-replay", "turn": 7,
              "rng": {"seed": list(seed)}, "p1_active": "garchomp"}
    return ReplayOpponentScenario(
        hypothesis, canonical_json(battle), choices, source)


class FakePhase1Worker:
    """Produces a realistic adapter-style echo; values are synthetic only."""

    def __init__(self, *, status=CapabilityStatus.PARTIAL,
                 mutate_input=False, stale_echo=False, fail_at=0):
        self.calls = []
        self.status = status
        self.mutate_input = mutate_input
        self.stale_echo = stale_echo
        self.fail_at = fail_at

    def simulate(self, snapshot, battle_json, own_choice, opponent_choice, *,
                 hypothesis_id, candidate_action_id, assets):
        self.calls.append((hypothesis_id, opponent_choice, candidate_action_id,
                           canonical_json(battle_json)))
        index = len(self.calls)
        if self.fail_at and index == self.fail_at:
            from champions_assist.local_strategy.simulator_adapter import WorkerUnavailable
            raise WorkerUnavailable("worker aborted")
        from champions_assist.local_strategy.simulator_adapter import _verified_roster_binding
        roster = _verified_roster_binding(snapshot, assets)
        payload = {
            "source": "FULL_REPLAY_SYNTHETIC",
            "battle": battle_json, "p1_choice": own_choice,
            "p2_choice": opponent_choice, "hypothesis_id": hypothesis_id,
            "candidate_action_id": candidate_action_id,
            "expected_p1_roster": roster,
            "expected_p1_active": snapshot.window.active_pokemon,
        }
        payload["branch_input_sha256"] = fingerprint(payload)
        branch_id = f"{index:032x}"
        request = {
            "protocol": PROTOCOL_VERSION, "op": "SIMULATE",
            "request_id": snapshot.window.request_id,
            "branch_request_id": branch_id,
            "identity": snapshot.window.identity(),
            "snapshot_sha256": snapshot.snapshot_sha256,
            "payload": payload,
        }
        # Earthquake wins the ordinal only in a controlled Showdown-like
        # synthetic fixture. No accuracy for Champions is asserted.
        target_hp = 20 if own_choice == "move 1" else 60
        ours = [{"species": row["species_id"], "hp": 100, "fainted": False}
                for row in roster]
        theirs = [{"species": name, "hp": target_hp if slot == 0 else 100,
                   "fainted": False}
                  for slot, name in enumerate(OTHER_SPECIES)]
        response = SimulationResult.from_worker(
            request["request_id"], request["identity"],
            request["snapshot_sha256"], hypothesis_id, self.status,
            {"next": {"side_hp": [ours, theirs]}}, branch_request_id=branch_id,
            branch_input_sha256=payload["branch_input_sha256"],
            candidate_action_id=candidate_action_id,
            submitted_request=request,
        )
        if self.mutate_input:
            battle_json["rng"]["seed"][0] = index + 100
        if self.stale_echo:
            return replace(response, branch_request_id="stale-branch")
        return response


class Phase2ShadowRunnerTests(unittest.TestCase):
    def run_plan(self, snapshot=None, scenarios=None, worker=None, **kwargs):
        s = snapshot or current()
        return run_shadow_turn(
            s, VERIFIED_ASSETS, worker or FakePhase1Worker(),
            scenarios if scenarios is not None else (scenario(),),
            preserve="ガブリアス", stopper="ミミッキュ",
            **kwargs,
        )

    def test_exact_cross_product_of_two_legal_moves_and_two_p2_commands(self):
        s = current()
        worker = FakePhase1Worker()
        result = self.run_plan(snapshot=s, worker=worker)
        self.assertEqual(result.status, "MODEL_PROVISIONAL")
        self.assertEqual(result.best_action_id, EARTH)
        self.assertEqual(result.evaluated_branch_count, 4)
        self.assertEqual(len(worker.calls), 4)
        self.assertEqual({row[1] for row in worker.calls},
                         {"move 1", "switch 2"})
        self.assertEqual({row[2] for row in worker.calls}, {EARTH, CLAW})
        self.assertEqual(len({row[3] for row in worker.calls}), 1)

    def test_each_joint_hypothesis_gets_its_own_complete_consistent_grid(self):
        s = current()
        hypotheses = (
            Hypothesis.from_assumptions("H1", {"item": "unknown"}, ("frame-1",)),
            Hypothesis.from_assumptions("H2", {"item": "other"}, ("frame-1",)),
        )
        s = replace(s, hypotheses=hypotheses)
        worker = FakePhase1Worker()
        result = self.run_plan(s, (scenario("H1", seed=(1,2,3,4)),
                                   scenario("H2", seed=(5,6,7,8))), worker)
        self.assertEqual(result.best_action_id, EARTH)
        self.assertEqual(result.evaluated_branch_count, 8)
        for h in ("H1", "H2"):
            lines = [row for row in worker.calls if row[0] == h]
            self.assertEqual(len(lines), 4)
            self.assertEqual(len({row[3] for row in lines}), 1)

    def test_missing_unknown_or_duplicate_hypotheses_never_use_worker(self):
        s = replace(current(), hypotheses=(
            Hypothesis.from_assumptions("H1", {"item": "unknown"}),))
        for scenarios in (
            (scenario(),),
            (scenario("H1"), scenario("H1")),
            (scenario("H2"),),
            (),
        ):
            w = FakePhase1Worker()
            with self.subTest(scenarios=scenarios):
                self.assertEqual(self.run_plan(s, scenarios, w).reason,
                                 "JOINT_HYPOTHESIS_SET_INCOMPLETE")
                self.assertFalse(w.calls)

    def test_missing_source_unconfirmed_p2_and_branch_limit_fail_closed(self):
        for value in (
            (scenario(source="OCR_PARTIAL"),),
            (scenario(choices=()),),
            (scenario(choices=("move 1", "move 1")),),
            (scenario(choices=("move 1\nmove 2",)),),
            (ReplayOpponentScenario("", "{not a replay", ("move 1",)),),
        ):
            worker = FakePhase1Worker()
            self.assertEqual(self.run_plan(scenarios=value,worker=worker).status,
                             "NO_RANKING")
            self.assertFalse(worker.calls)
        too_many = tuple(f"move {i}" for i in range(1,26))
        worker = FakePhase1Worker()
        self.assertEqual(self.run_plan(
            scenarios=(scenario(choices=too_many),), worker=worker).reason,
            "UNVERIFIED_REPLAY_OR_P2_CHOICE_SET")
        self.assertFalse(worker.calls)

    def test_missing_or_unsupported_worker_branch_discards_all(self):
        for w in (FakePhase1Worker(fail_at=3),
                  FakePhase1Worker(status=CapabilityStatus.UNSUPPORTED),
                  FakePhase1Worker(stale_echo=True)):
            with self.subTest(worker=w.__dict__):
                result=self.run_plan(worker=w)
                self.assertEqual(result.status, "NO_RANKING")
                self.assertEqual(result.best_action_id, "")

    def test_malformed_worker_returns_never_raise_or_publish_ranking(self):
        class MalformedWorker:
            def __init__(self, value):
                self.value = value
                self.calls = 0

            def simulate(self, *args, **kwargs):
                self.calls += 1
                return self.value

        # None/dict/partial attribute object and structurally incomplete
        # SimulationResult all previously leaked AttributeError or TypeError.
        legitimate = FakePhase1Worker().simulate(
            current(), {"state": "synthetic"}, "move 1", "move 1",
            hypothesis_id="", candidate_action_id=EARTH,
            assets=VERIFIED_ASSETS)
        for malformed in (
            None,
            {},
            {"capability_status": "PARTIAL"},
            SimpleNamespace(capability_status=CapabilityStatus.PARTIAL),
            replace(legitimate, submitted_request_json=""),
            replace(legitimate, identity_json=None),
            replace(legitimate, branch_input_sha256="g"*64),
            replace(legitimate, branch_request_id=""),
        ):
            with self.subTest(malformed=type(malformed).__name__):
                worker = MalformedWorker(malformed)
                outcome = self.run_plan(worker=worker)
                self.assertEqual(outcome.status, "NO_RANKING")
                self.assertEqual(outcome.reason, "WORKER_BRANCH_UNAVAILABLE")
                self.assertEqual(outcome.best_action_id, "")
                self.assertEqual(worker.calls, 1)

    def test_worker_runtime_error_and_snapshot_provider_exception_fail_closed(self):
        class CrashingWorker:
            def simulate(self, *args, **kwargs):
                raise RuntimeError("synthetic external worker failure")

        outcome = self.run_plan(worker=CrashingWorker())
        self.assertEqual(outcome.reason, "WORKER_BRANCH_UNAVAILABLE")
        self.assertEqual(outcome.status, "NO_RANKING")

        for interrupt_at in (1, 5):
            called = 0
            def current_snapshot():
                nonlocal called
                called += 1
                if called == interrupt_at:
                    raise RuntimeError("state store switched sessions")
                return current_state

            current_state = current()
            worker = FakePhase1Worker()
            with self.subTest(interrupt_at=interrupt_at):
                outcome = self.run_plan(
                    snapshot=current_state, worker=worker,
                    current_snapshot=current_snapshot)
                self.assertEqual(outcome.status, "NO_RANKING")
                self.assertEqual(outcome.reason, "CURRENT_SNAPSHOT_UNAVAILABLE")
                self.assertEqual(outcome.best_action_id, "")
                self.assertEqual(len(worker.calls), interrupt_at-1)

    def test_downstream_unexpected_branch_payload_parser_error_stays_isolated(self):
        with patch(
            "champions_assist.local_strategy.shadow_runner.compare_shadow_turn",
            side_effect=AttributeError("malformed optional result field"),
        ):
            result = self.run_plan(worker=FakePhase1Worker())
        self.assertEqual(result.status, "NO_RANKING")
        self.assertEqual(result.reason, "COMPARISON_UNAVAILABLE")
        self.assertEqual(result.best_action_id, "")

    def test_worker_mutating_passed_battle_copy_does_not_change_next_branch(self):
        worker = FakePhase1Worker(mutate_input=True)
        result = self.run_plan(worker=worker)
        self.assertEqual(result.status, "MODEL_PROVISIONAL")
        self.assertEqual(len(worker.calls), 4)
        self.assertEqual(len({row[3] for row in worker.calls}), 1)

    def test_deadline_and_midrun_revision_are_not_silently_adopted(self):
        s = current()
        worker = FakePhase1Worker()
        result = self.run_plan(snapshot=s,worker=worker,
                               reserve_seconds=10,clock=lambda: s.window.deadline_monotonic-1)
        self.assertEqual(result.reason, "INPUT_DEADLINE_PASSED")
        self.assertFalse(worker.calls)
        changed=replace(s,observed_state_json=s.observed_state_json+" ")
        supplier_calls=0
        def read_current():
            nonlocal supplier_calls
            supplier_calls += 1
            return changed if supplier_calls >= 3 else s
        worker=FakePhase1Worker()
        result=self.run_plan(snapshot=s,worker=worker,
                             current_snapshot=read_current)
        self.assertEqual(result.reason, "CURRENT_OBSERVATION_CHANGED")
        self.assertEqual(len(worker.calls),2)

    def test_no_legal_actions_never_explores_synthetic_state(self):
        s = current()
        s=replace(s,constraints_confirmed=False)
        w=FakePhase1Worker()
        result=self.run_plan(snapshot=s,worker=w)
        self.assertEqual(result.reason, "NO_COMPLETE_CURRENT_LEGAL_ACTION_SET")
        self.assertFalse(w.calls)


if __name__ == "__main__":
    unittest.main()
