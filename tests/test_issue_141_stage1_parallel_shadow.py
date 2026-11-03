"""Stage-1 opt-in concurrent Shadow control-plane tests, not real gameplay."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from champions_assist.local_strategy.parallel_shadow_session import (
    ParallelShadowSession,
)
from champions_assist.local_strategy.shadow_comparison_store import (
    BoundGPTAdvice, read_shadow_comparisons,
)
from champions_assist.local_strategy.observed_full_grid_diagnostic import (
    ObservedFullGridDiagnostic,
)
from tests.test_issue_140_foundation import frozen, VERIFIED_ASSETS


class ParallelShadowSessionTests(unittest.TestCase):
    def test_default_off_does_not_spawn_or_write(self):
        snap = frozen()
        with TemporaryDirectory() as d:
            db = Path(d) / "shadow.sqlite"
            session = ParallelShadowSession(
                snap, VERIFIED_ASSETS, database_path=db,
                current_snapshot=lambda: snap, preserve="ガブリアス",
                stopper="ミミッキュ")
            self.assertEqual(session.start(), "DISABLED")
            self.assertIsNone(session.attach_gpt(
                BoundGPTAdvice(snap.snapshot_sha256, "GPT_ABSTAIN")))
            self.assertFalse(db.exists())
            session.close()

    def test_unverified_replay_abstains_and_still_records_same_window_gpt(self):
        snap = frozen()
        with TemporaryDirectory() as d:
            db = Path(d) / "shadow.sqlite"
            session = ParallelShadowSession(
                snap, VERIFIED_ASSETS, database_path=db,
                current_snapshot=lambda: snap, preserve="ガブリアス",
                stopper="ミミッキュ", enabled=True)
            try:
                self.assertEqual(session.start(), "RUNNING")
                gpt = BoundGPTAdvice(snap.snapshot_sha256, "GPT_ADVICE",
                                     snap.verified_action_ids[0],
                                     "free text never saved", 25)
                outcome = session.attach_gpt(gpt).result(timeout=10)
                self.assertEqual(outcome.status, "RECORDED", outcome.reason)
                self.assertFalse(outcome.ready_for_live)
                self.assertEqual(outcome.record.local_status, "NO_RANKING")
                self.assertTrue(outcome.record.gpt_action_verified_in_window)
                self.assertEqual(outcome.record.local_reason_code,
                                 "LOCAL_SOURCE_REFUSED")
                self.assertIsNone(session.attach_gpt(gpt))
                self.assertEqual(read_shadow_comparisons(db), (outcome.record,))
                self.assertNotIn("free text", str(read_shadow_comparisons(db)))
            finally:
                session.close()

    def test_local_worker_runs_before_gpt_and_is_nonblocking(self):
        snap = frozen()
        started, release = Event(), Event()
        def diagnostic(*args, **kwargs):
            started.set()
            self.assertTrue(release.wait(timeout=10))
            return ObservedFullGridDiagnostic(
                "SYNTHETIC_TURN_DIAGNOSTIC_ONLY",
                "UNVERIFIED_REAL_BATTLE_MAPPING_RNG_AND_OPPONENT_POLICY",
                match_id=snap.window.match_id,
                decision_window_id=snap.window.decision_window_id,
                state_revision=snap.window.state_revision,
                observed_snapshot_sha256=snap.snapshot_sha256,
                evaluated_branch_count=8)
        with TemporaryDirectory() as d:
            db = Path(d) / "shadow.sqlite"
            with patch(
                "champions_assist.local_strategy.parallel_shadow_session."
                "audit_observed_synthetic_turn", side_effect=diagnostic):
                session = ParallelShadowSession(
                    snap, VERIFIED_ASSETS, database_path=db,
                    current_snapshot=lambda: snap,
                    preserve="ガブリアス", stopper="ミミッキュ",
                    joint_candidates=(object(),),complete_branches=(object(),),
                    worker=object(),enabled=True)
                try:
                    self.assertEqual(session.start(), "RUNNING")
                    self.assertTrue(started.wait(timeout=10))
                    future = session.attach_gpt(
                        BoundGPTAdvice(snap.snapshot_sha256, "GPT_ABSTAIN"))
                    self.assertIsNotNone(future)
                    self.assertFalse(future.done(),
                                     "GPT thread must not wait on LOCAL")
                    release.set()
                    result = future.result(timeout=10)
                    self.assertEqual(result.status, "RECORDED", result.reason)
                    self.assertEqual(result.record.local_status,
                                     "MODEL_DIAGNOSTIC")
                    self.assertFalse(result.ready_for_live)
                finally:
                    release.set()
                    session.close()

    def test_stale_revision_refused_without_any_db_write(self):
        snap = frozen()
        newer = replace(snap, window=replace(snap.window, state_revision=9))
        current = [snap]
        with TemporaryDirectory() as d:
            db = Path(d) / "shadow.sqlite"
            session = ParallelShadowSession(
                snap, VERIFIED_ASSETS, database_path=db,
                current_snapshot=lambda: current[0],
                preserve="ガブリアス",stopper="ミミッキュ",enabled=True)
            try:
                self.assertEqual(session.start(), "RUNNING")
                current[0] = newer
                outcome = session.attach_gpt(BoundGPTAdvice(
                    snap.snapshot_sha256, "GPT_ABSTAIN")).result(timeout=10)
                self.assertEqual(outcome.status, "REFUSED")
                self.assertFalse(db.exists())
            finally:
                session.close()


if __name__ == "__main__":
    unittest.main()
