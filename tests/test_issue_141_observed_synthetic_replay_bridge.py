"""#141(②) all-branch synthetic replay comparison (never executable).

Mocked worker tests validate orchestration and the strict non-promotion
boundary. Actual pinned Showdown worker E2E is exercised separately in
tests.test_issue_141_real_worker_python_e2e (Phase 2 workflow).
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from unittest import TestCase
from typing import Any

from champions_assist.local_strategy.models import canonical_json, fingerprint
from champions_assist.local_strategy.simulator_adapter import (
    LocalSimulatorWorker, SyntheticEngineReadoutReceipt, WorkerUnavailable,
)
from champions_assist.local_strategy.observed_synthetic_replay_bridge import (
    ExplicitSyntheticBattleBranch, compare_observed_to_synthetic_replays,
)
from tests.test_issue_140_foundation import VERIFIED_ASSETS
from tests.test_issue_141_replay_projection_audit import (
    replay, snapshot, sourced,
)
from tests.test_issue_141_engine_replay_readout_bridge import converted
from tests.test_issue_141_observed_id_bridge import with_state


class FakeProcessWorker(LocalSimulatorWorker):
    """Test stub. NOT an authenticated engine, process or game provenance."""

    def __init__(self, *, altered_case="", bad_receipt="", reused_nonce=False):
        self.calls=[]
        self.altered_case=altered_case
        self.bad_receipt=bad_receipt
        self.reused_nonce=reused_nonce

    def readout_synthetic_replay(self, snap, battle_json, *, assets,
                                 hypothesis_id="", joint_candidates=()):
        self.calls.append(hypothesis_id)
        if self.bad_receipt=="unavailable":
            raise WorkerUnavailable("synthetic stub unavailable")
        raw=canonical_json(dict(battle_json))
        h=sha256(raw.encode("utf-8")).hexdigest()
        payload={
            "source":"FULL_REPLAY_SYNTHETIC","battle_json":raw,
            "hypothesis_id":hypothesis_id,
            "expected_p1_roster":[{"test":"not-authenticated"}],
            "expected_p1_active":snap.window.active_pokemon,
        }
        payload["branch_input_sha256"]=fingerprint(payload)
        seq=0 if self.reused_nonce else len(self.calls)
        nonce=f"{seq:032x}"
        request={
            "protocol":"issue140.v1","op":"READOUT_REPLAY",
            "request_id":snap.window.request_id,
            "branch_request_id":nonce,
            "identity":snap.window.identity(),
            "snapshot_sha256":snap.snapshot_sha256,
            "payload":payload,
        }
        row=converted(replay(snap,hypothesis_id=hypothesis_id)).readout
        row["battle_sha256"]=h
        if hypothesis_id==self.altered_case:
            row["members"][4]["hp"]=3
        if self.bad_receipt=="source":
            row["source"]="REAL_CHAMPIONS_OCR"
        if self.bad_receipt=="input_hash":
            row["battle_sha256"]="0"*64
        if self.bad_receipt=="request":
            request["identity"]["state_revision"]=-1
        if self.bad_receipt=="window":
            snapshot_sha="0"*64
        else:
            snapshot_sha=snap.snapshot_sha256
        return SyntheticEngineReadoutReceipt(
            snap.window.match_id,snap.window.decision_window_id,
            snap.window.state_revision,snapshot_sha,
            hypothesis_id,nonce,payload["branch_input_sha256"],
            h,canonical_json(row),canonical_json(request),
        )


def cases():
    return (
        ExplicitSyntheticBattleBranch(
            "Joint-1",{"complete_hypothetical_battle":True,
                       "state":{"turn":4.0,"seed":[1,2,3,4]}},
            ("サーフゴー","カイリュー","ドドゲザン")),
        ExplicitSyntheticBattleBranch(
            "Joint-2",{"complete_hypothetical_battle":True,
                       "state":{"turn":4,"seed":[1,2,3,5]}},
            ("サーフゴー","カイリュー","ドドゲザン")),
    )


def run(s=None, *, worker=None, branches=None, sources=None):
    return compare_observed_to_synthetic_replays(
        s or snapshot(),VERIFIED_ASSETS,
        joint_candidates=sourced() if sources is None else sources,
        complete_branches=cases() if branches is None else branches,
        worker=worker or FakeProcessWorker())


class SyntheticWorkerBatchAuditTests(TestCase):
    def test_matching_all_worker_cases_remains_diagnostic_only(self):
        s=snapshot()
        old=s.observed_state_json
        worker=FakeProcessWorker()
        out=run(s,worker=worker)
        self.assertEqual(out.status,
                         "SYNTHETIC_REPLAYS_MATCH_OBSERVED_CONSTRAINTS_ONLY",
                         out.reason)
        self.assertEqual(out.ranking_status,"NO_RANKING")
        self.assertEqual(out.hypothesis_ids,("Joint-1","Joint-2"))
        self.assertEqual(worker.calls,["Joint-1","Joint-2"])
        self.assertEqual(len(out.battle_sha256),2)
        self.assertEqual(len(set(out.battle_sha256)),2)
        self.assertIn("p1:1.hp",out.checked_fields[0])
        self.assertIn("p2:2.hp",out.checked_fields[0])
        self.assertFalse(out.ready_for_engine)
        self.assertFalse(out.full_state_reconstructed)
        self.assertFalse(out.game_battle_authenticated)
        self.assertFalse(out.opponent_mapping_authenticated)
        self.assertFalse(out.exhaustive_opponent_policy)
        self.assertEqual(s.observed_state_json,old)

    def test_one_wrong_observed_hp_rejects_entire_batch_no_partial_prefix(self):
        worker=FakeProcessWorker(altered_case="Joint-2")
        out=run(worker=worker)
        self.assertEqual(out.status,"NO_SYNTHETIC_REPLAY_COMPARISON")
        self.assertIn("READOUT_OBSERVATION_CONFLICT",out.reason)
        self.assertEqual(worker.calls,["Joint-1","Joint-2"])
        self.assertEqual(out.hypothesis_ids,())
        self.assertEqual(out.battle_sha256,())
        self.assertEqual(out.checked_fields,())

    def test_source_name_order_missing_case_or_duplicate_rejected_preflight(self):
        for branches in (
            cases()[:1],tuple(reversed(cases())),
            (cases()[0],cases()[0]),
            (replace(cases()[0],opponent_display_by_slot=(
                "サーフゴー","サーフゴー","ドドゲザン")),cases()[1]),
        ):
            with self.subTest(branches=branches):
                worker=FakeProcessWorker()
                out=run(worker=worker,branches=branches)
                self.assertEqual(out.status,"NO_SYNTHETIC_REPLAY_COMPARISON")
                self.assertFalse(worker.calls)

    def test_receipt_nonce_identity_battle_sha_or_worker_fails_closed(self):
        for bad in ("source","input_hash","request","window","unavailable"):
            with self.subTest(bad=bad):
                out=run(worker=FakeProcessWorker(bad_receipt=bad))
                self.assertEqual(out.status,"NO_SYNTHETIC_REPLAY_COMPARISON")
                self.assertEqual(out.hypothesis_ids,())
        out=run(worker=FakeProcessWorker(reused_nonce=True))
        self.assertEqual(out.reason,"WORKER_READOUT_RECEIPT_OR_NONCE_UNBOUND")
        self.assertEqual(out.battle_sha256,())

    def test_changed_snapshot_and_source_provenance_reject_stale_observed_cases(self):
        s=snapshot()
        shifted=with_state(s,field="new_observation",value=42)
        worker=FakeProcessWorker()
        out=run(shifted,worker=worker)
        # Fake worker supplies the altered snapshot in these tests, but
        # the comparison still cannot authenticate its actual game source.
        self.assertEqual(out.status,"SYNTHETIC_REPLAYS_MATCH_OBSERVED_CONSTRAINTS_ONLY")
        self.assertNotEqual(out.snapshot_sha256,s.snapshot_sha256)
        wrong=tuple(replace(c,hypothesis_id="Old-"+c.hypothesis_id)
                    for c in cases())
        self.assertEqual(run(shifted,worker=FakeProcessWorker(),
                             branches=wrong).status,
                         "NO_SYNTHETIC_REPLAY_COMPARISON")
        self.assertEqual(run(s,sources=()).reason,
                         "EXPLICIT_COMPLETE_SYNTHETIC_BATCH_REQUIRED")

    def test_opponent_alias_claim_can_match_but_is_not_authenticated(self):
        swapped=(
            replace(cases()[0],opponent_display_by_slot=(
                "カイリュー","サーフゴー","ドドゲザン")),
            cases()[1],
        )
        worker=FakeProcessWorker()
        out=run(worker=worker,branches=swapped)
        self.assertIn("READOUT_OBSERVATION_CONFLICT",out.reason)
        self.assertEqual(out.ranking_status,"NO_RANKING")
        self.assertFalse(out.opponent_mapping_authenticated)

    def test_bad_inputs_are_rejected_before_first_worker_call(self):
        for branches in (
            (replace(cases()[0],complete_battle={"bad":{1,2}}),cases()[1]),
            (replace(cases()[0],complete_battle={"bad":float("nan")}),cases()[1]),
            (replace(cases()[0],complete_battle={"big":"a"*(4*1024*1024)}),
             cases()[1]),
        ):
            with self.subTest():
                worker=FakeProcessWorker()
                out=run(worker=worker,branches=branches)
                self.assertEqual(out.status,"NO_SYNTHETIC_REPLAY_COMPARISON")
                self.assertFalse(worker.calls)


if __name__=="__main__":
    import unittest
    unittest.main()
