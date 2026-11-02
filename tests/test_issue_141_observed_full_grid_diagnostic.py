"""#141(②/③) synthetic-only whole-grid join; no real-game ranking.

Mocked worker is not a pinned engine. The integration contract must preserve
NO_RANKING even when a hypothetical model has a unique preferred move.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from champions_assist.local_strategy.models import canonical_json, fingerprint
from champions_assist.local_strategy.observed_synthetic_replay_bridge import (
    compare_observed_to_synthetic_replays,
)
from champions_assist.local_strategy.observed_full_grid_diagnostic import (
    audit_observed_synthetic_turn,
)
from champions_assist.local_strategy.simulator_adapter import (
    EngineP2CandidateSet, WorkerUnavailable,
)
from tests.test_issue_140_foundation import VERIFIED_ASSETS
from tests.test_issue_141_observed_synthetic_replay_bridge import (
    FakeProcessWorker, cases,
)
from tests.test_issue_141_replay_projection_audit import snapshot, sourced
from tests.test_issue_141_shadow_runner import FakePhase1Worker
from tests.test_issue_141_observed_id_bridge import with_state


class FullGridFakeWorker(FakeProcessWorker):
    def __init__(self, *, refuse_id="", changed_hash_id="", sim_fail_at=0,
                 **kwargs):
        super().__init__(**kwargs)
        self.enumerated = []
        self.refuse_id = refuse_id
        self.changed_hash_id = changed_hash_id
        self.simulator = FakePhase1Worker(fail_at=sim_fail_at)

    def enumerate_p2_from_replay(self, snap, battle_json, *,
                                 hypothesis_id="", assets=None):
        self.enumerated.append(hypothesis_id)
        if hypothesis_id == self.refuse_id:
            return EngineP2CandidateSet("NO_CHOICES", "UNSUPPORTED")
        return EngineP2CandidateSet(
            "ENGINE_CHECKED_SIMPLE_CHOICES","TEST_ONLY",
            ("move 1","switch 2"),
            ("0"*64 if hypothesis_id == self.changed_hash_id
             else fingerprint(dict(battle_json))),
            "a"*64,"PINNED_SHOWDOWN_SIMPLE_SINGLES_ONLY", False)

    def simulate(self, *args, **kwargs):
        return self.simulator.simulate(*args, **kwargs)


def run(*, snap=None, worker=None, records=None, branches=None,
        current_snapshot=None):
    snap = snap or snapshot()
    return audit_observed_synthetic_turn(
        snap, VERIFIED_ASSETS,
        joint_candidates=sourced() if records is None else records,
        complete_branches=cases() if branches is None else branches,
        worker=worker or FullGridFakeWorker(),
        preserve="ガブリアス",stopper="ミミッキュ",
        current_snapshot=current_snapshot)


class ObservedFullGridDiagnosticTests(unittest.TestCase):
    def test_full_synthetic_grid_never_promotes_to_real_game_ranking(self):
        snap=snapshot()
        old=snap.observed_state_json
        worker=FullGridFakeWorker()
        out=run(snap=snap,worker=worker,current_snapshot=lambda:snap)
        self.assertEqual(out.status,"SYNTHETIC_TURN_DIAGNOSTIC_ONLY",out.reason)
        self.assertEqual(out.ranking_status,"NO_RANKING")
        self.assertEqual(out.hypothesis_ids,("Joint-1","Joint-2"))
        self.assertEqual(out.engine_choice_counts,(2,2))
        self.assertEqual(out.evaluated_branch_count,4)
        self.assertEqual(out.hypothetical_model_status,"MODEL_PROVISIONAL")
        self.assertTrue(out.hypothetical_nondominated_action_ids)
        self.assertFalse(out.ready_for_engine)
        self.assertFalse(out.game_battle_authenticated)
        self.assertFalse(out.opponent_mapping_authenticated)
        self.assertFalse(out.exhaustive_opponent_policy)
        self.assertFalse(out.full_state_reconstructed)
        self.assertEqual(worker.calls,["Joint-1","Joint-2"])
        self.assertEqual(worker.enumerated,["Joint-1","Joint-2"])
        self.assertEqual(len(worker.simulator.calls),4)
        self.assertEqual(snap.observed_state_json,old)
        self.assertEqual(snap.hypotheses,())

    def test_one_bad_readout_aborts_before_p2_or_simulation(self):
        worker=FullGridFakeWorker(altered_case="Joint-2")
        out=run(worker=worker)
        self.assertEqual(out.status,"NO_OBSERVED_TURN_DIAGNOSTIC")
        self.assertIn("OBSERVED_SYNTHETIC_READOUT_REFUSED",out.reason)
        self.assertEqual(worker.enumerated,[])
        self.assertEqual(worker.simulator.calls,[])
        self.assertEqual(out.hypothetical_nondominated_action_ids,())

    def test_missing_reordered_duplicate_cases_rejected_as_whole(self):
        for source_batch, branch_batch in (
            (sourced(),cases()[:1]),
            (sourced(),tuple(reversed(cases()))),
            (sourced(),(cases()[0],cases()[0])),
            ((),cases()),
        ):
            with self.subTest(source_batch=source_batch,
                              branch_batch=branch_batch):
                worker=FullGridFakeWorker()
                out=run(worker=worker,records=source_batch,branches=branch_batch)
                self.assertEqual(out.status,"NO_OBSERVED_TURN_DIAGNOSTIC")
                self.assertEqual(worker.simulator.calls,[])
                self.assertEqual(out.hypothesis_ids,())

    def test_refused_or_sha_mismatched_p2_discards_entire_grid(self):
        for kwargs in ({"refuse_id":"Joint-2"},
                       {"changed_hash_id":"Joint-2"}):
            with self.subTest(kwargs=kwargs):
                worker=FullGridFakeWorker(**kwargs)
                out=run(worker=worker)
                self.assertEqual(out.status,"NO_OBSERVED_TURN_DIAGNOSTIC")
                self.assertEqual(worker.enumerated,["Joint-1","Joint-2"])
                self.assertEqual(worker.simulator.calls,[])
                self.assertEqual(out.evaluated_branch_count,0)

    def test_stale_window_refuses_before_readout_and_during_grid(self):
        snap=snapshot()
        old=with_state(snap,field="changed_ocr_fact",value="new")
        worker=FullGridFakeWorker()
        out=run(snap=snap,worker=worker,current_snapshot=lambda:old)
        self.assertEqual(out.reason,"OBSERVATION_REVISION_CHANGED")
        self.assertEqual(worker.calls,[])

        state={"reads":0}
        def advancing():
            state["reads"]+=1
            return old if state["reads"]>=4 else snap
        worker=FullGridFakeWorker()
        out=run(snap=snap,worker=worker,current_snapshot=advancing)
        self.assertEqual(out.status,"NO_OBSERVED_TURN_DIAGNOSTIC")
        self.assertEqual(worker.simulator.calls,[])
        self.assertEqual(out.hypothesis_ids,())

    def test_one_worker_branch_failure_discards_every_preview(self):
        worker=FullGridFakeWorker(sim_fail_at=3)
        out=run(worker=worker)
        self.assertEqual(out.status,"NO_OBSERVED_TURN_DIAGNOSTIC")
        self.assertEqual(out.evaluated_branch_count,0)
        self.assertEqual(out.hypothetical_nondominated_action_ids,())
        self.assertEqual(out.ranking_status,"NO_RANKING")

    def test_worker_and_caller_mutate_hp_moves_rng_after_readout_but_grid_is_frozen(self):
        # Regression for review P2: A was OCR-readout validated; B must never
        # silently replace A for ENUMERATE_P2 or SIMULATE.
        branches=cases()
        source_text={c.hypothesis_id:canonical_json(dict(c.complete_battle))
                     for c in branches}
        source_digest=tuple(sha256(t.encode("utf-8")).hexdigest()
                            for t in source_text.values())

        class MutatingWorker(FullGridFakeWorker):
            def readout_synthetic_replay(self, snap, battle_json, **kwargs):
                receipt=super().readout_synthetic_replay(
                    snap,battle_json,**kwargs)
                # The worker changes its passed readout object AFTER issuing
                # the receipt; also emulate external caller changing original
                # nested data while the diagnostic is in progress.
                battle_json["state"]["seed"][0]=9999
                battle_json["state"]["hp"]=[0,999]
                battle_json["state"]["moves"]=["changed"]
                for case in branches:
                    case.complete_battle["state"]["seed"][0]=8080
                    case.complete_battle["state"]["hp"]=[0,0]
                    case.complete_battle["state"]["moves"]=["different"]
                return receipt

            def enumerate_p2_from_replay(self, snap, battle_json, **kwargs):
                result=super().enumerate_p2_from_replay(
                    snap,battle_json,**kwargs)
                # Enumeration may mutate a fresh copy; SIMULATE must still
                # receive the immutable original, not the mutated p2 input.
                battle_json["state"]["seed"][0]=7070
                battle_json["state"]["hp"]=[1,1]
                battle_json["state"]["moves"]=["mutated-enumerator"]
                return result

        worker=MutatingWorker()
        out=run(worker=worker,branches=branches)
        self.assertEqual(out.status,"SYNTHETIC_TURN_DIAGNOSTIC_ONLY",out.reason)
        self.assertEqual(out.synthetic_battle_sha256,source_digest)
        self.assertEqual(out.ranking_status,"NO_RANKING")
        self.assertEqual(len(worker.simulator.calls),4)
        for hyp, p2, p1, battle_json in worker.simulator.calls:
            self.assertEqual(battle_json,source_text[hyp])
        # The originally supplied mutable mapping really did change, showing
        # that success came from a detached freeze rather than lucky timing.
        self.assertNotEqual(
            canonical_json(dict(branches[0].complete_battle)),
            source_text["Joint-1"])

    def test_forged_readout_sha_never_authorizes_enumeration(self):
        real_compare=compare_observed_to_synthetic_replays

        def mismatched(*args,**kwargs):
            good=real_compare(*args,**kwargs)
            self.assertEqual(
                good.status,"SYNTHETIC_REPLAYS_MATCH_OBSERVED_CONSTRAINTS_ONLY")
            return replace(good,battle_sha256=("0"*64,good.battle_sha256[1]))
        worker=FullGridFakeWorker()
        with patch(
            "champions_assist.local_strategy.observed_full_grid_diagnostic."
            "compare_observed_to_synthetic_replays",side_effect=mismatched):
            out=run(worker=worker)
        self.assertEqual(out.status,"NO_OBSERVED_TURN_DIAGNOSTIC")
        self.assertEqual(worker.enumerated,[])
        self.assertEqual(worker.simulator.calls,[])
        self.assertEqual(out.hypothesis_ids,())

    def test_nonfinite_and_oversized_battle_fail_before_worker(self):
        for invalid in ({"state":{"seed":[float("nan")]}},
                        {"state":{"data":"x"*(4*1024*1024)}}):
            with self.subTest(kind=list(invalid["state"])):
                branches=(replace(cases()[0],complete_battle=invalid),
                          cases()[1])
                worker=FullGridFakeWorker()
                out=run(branches=branches,worker=worker)
                self.assertEqual(out.status,"NO_OBSERVED_TURN_DIAGNOSTIC")
                self.assertEqual(worker.calls,[])
                self.assertEqual(worker.enumerated,[])

    def test_no_budget_and_unverified_input_are_fail_closed(self):
        worker=FullGridFakeWorker()
        snap=replace(snapshot(),hypotheses=())
        out=audit_observed_synthetic_turn(
            snap,VERIFIED_ASSETS,joint_candidates=sourced(),
            complete_branches=cases(),worker=worker,
            preserve="",stopper="ミミッキュ")
        self.assertEqual(out.reason,"INVALID_CURRENT_OBSERVED_GRID_REQUEST")
        self.assertFalse(worker.calls)


if __name__=="__main__":
    unittest.main()
