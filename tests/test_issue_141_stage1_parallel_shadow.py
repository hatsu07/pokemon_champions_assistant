"""Stage-1 opt-in concurrent Shadow control-plane tests, not real gameplay."""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic
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
from champions_assist.local_strategy.shadow_comparison_store import (
    append_shadow_comparison as real_append,
    finalize_shadow_comparison as real_finalize,
    pending_shadow_lease_count,
)
from champions_assist.local_strategy.ui_shadow_dispatcher import UiShadowDispatcher
from tests.test_issue_141_selection import make_assets


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

    def _test_cancel_during_write(self, *, block_after_commit: bool,
                                  use_revision_change: bool) -> None:
        """Event-barrier regression for P2-2; UI never waits for sqlite IO."""
        entered, resume = Event(), Event()
        with TemporaryDirectory() as directory:
            db = Path(directory) / "private" / "shadow.sqlite"
            bridge = UiShadowDispatcher(db)
            self.assertEqual(bridge.prepare_storage(), "READY")
            result = bridge.start(
                make_assets(), enabled=True, match_id="capture-race",
                request_id="req-1", turn_epoch=1, state_revision=4,
                screen_label="TURN_ACTION",
                observed_state={
                    "our_active":"A", "opponent_active":"O1",
                    "selected_three":["A","B","C"]},
                deadline_monotonic=monotonic()+60)
            self.assertEqual(result,"RUNNING_UNVERIFIED")
            session = bridge.session

            def suspended_append(path, record, **kwargs):
                if block_after_commit:
                    result = real_append(path, record, **kwargs)
                entered.set()
                if not resume.wait(timeout=10):
                    raise AssertionError("REVIEW_RACE_BARRIER_TIMEOUT")
                if not block_after_commit:
                    return real_append(path, record, **kwargs)
                return result

            with patch(
                "champions_assist.local_strategy.parallel_shadow_session."
                "append_shadow_comparison",
                side_effect=suspended_append):
                self.assertEqual(bridge.attach_gpt(
                    request_id="req-1", turn_epoch=1, state_revision=4,
                    status="ADVICE", advice_present=True, elapsed_ms=25,
                    fresh=True, reported_screen="MOVE_MENU"),
                    "JOIN_SUBMITTED_NONACTIONABLE")
                done = bridge.last_future
                try:
                    self.assertTrue(entered.wait(timeout=10))
                    since = monotonic()
                    if use_revision_change:
                        self.assertTrue(bridge.invalidate_if_changed(
                            turn_epoch=1, state_revision=5, phase="BATTLE"))
                    else:
                        bridge.invalidate()  # Actual UI opt-out/stop
                    self.assertLess(monotonic()-since, 1.0,
                                    "Qt invalidation must not wait for SQLite")
                    self.assertIsNotNone(session.cleanup_future)
                finally:
                    resume.set()
                result = done.result(timeout=10)
                self.assertEqual(result.status, "REFUSED", result.reason)
                # _finish can proactively revoke first; then the
                # serialized cleanup sees NO_ROW. Both are safe outcomes.
                self.assertIn(session.cleanup_future.result(timeout=10),
                              ("REVOKED", "NO_ROW"))
                self.assertEqual(read_shadow_comparisons(db), (),
                                 "Old-window success must not survive OFF")
                bridge.invalidate()

    def test_review_p2_2_off_during_precommit_write(self):
        self._test_cancel_during_write(
            block_after_commit=False, use_revision_change=False)

    def test_review_p2_2_revision_change_after_sqlite_commit(self):
        self._test_cancel_during_write(
            block_after_commit=True, use_revision_change=True)


    def test_review_p2_2_cancel_inside_sqlite_transaction_rolls_back(self):
        """Cancel after INSERT and before COMMIT; no transient audit row."""
        entered, resume = Event(), Event()
        snap = frozen()
        with TemporaryDirectory() as directory:
            db = Path(directory) / "shadow.sqlite"
            session = ParallelShadowSession(
                snap, VERIFIED_ASSETS, database_path=db,
                current_snapshot=lambda: snap, preserve="ガブリアス",
                stopper="ミミッキュ", enabled=True)
            self.assertEqual(session.start(), "RUNNING")
            def inside_transaction(path, record, **kwargs):
                original = kwargs["active_at_commit"]
                n = [0]
                def pausing_fence():
                    n[0] += 1
                    if n[0] == 2:
                        entered.set()
                        if not resume.wait(timeout=10):
                            raise AssertionError("COMMIT_FENCE_TIMEOUT")
                    return original()
                return real_append(path, record,
                                   active_at_commit=pausing_fence)
            with patch(
                "champions_assist.local_strategy.parallel_shadow_session."
                "append_shadow_comparison", side_effect=inside_transaction):
                future = session.attach_gpt(
                    BoundGPTAdvice(snap.snapshot_sha256, "GPT_ABSTAIN"))
                try:
                    self.assertTrue(entered.wait(timeout=10))
                    started = monotonic()
                    session.close()
                    self.assertLess(monotonic() - started, 1.0)
                finally:
                    resume.set()
                result = future.result(timeout=10)
                self.assertEqual(result.status, "REFUSED")
                self.assertIn(session.cleanup_future.result(timeout=10),
                              ("NO_ROW","REVOKED"))
                self.assertEqual(read_shadow_comparisons(db), ())


    def test_review_p2_3_locked_database_cleanup_fails_but_audit_rejects_row(self):
        """Real SQLite write-lock > timeout leaves raw data, NEVER finalized.

        An in-flight GPT+LOCAL row can commit BEFORE user OFF, while _finish
        is blocked waiting to return. Both cleanup attempts then encounter
        SQLite OperationalError('database is locked'). Once released, raw
        rows must remain explicitly untrusted in the postmatch report.
        """
        from scripts.report_phase2_shadow_software import (
            stage1_software_shadow_report,
        )
        from tests.test_issue_141_gui_timing_trace_audit import scene
        entered, resume = Event(), Event()
        with TemporaryDirectory() as directory:
            db = Path(directory) / "private" / "shadow.sqlite"
            bridge = UiShadowDispatcher(db)
            self.assertEqual(bridge.prepare_storage(), "READY")
            self.assertEqual(bridge.start(
                make_assets(), enabled=True, match_id="capture-race",
                request_id="req-1", turn_epoch=1, state_revision=4,
                screen_label="TURN_ACTION",
                observed_state={
                    "our_active":"A", "opponent_active":"O1",
                    "selected_three":["A","B","C"]},
                deadline_monotonic=monotonic()+60),
                "RUNNING_UNVERIFIED")
            session = bridge.session

            def committed_then_suspended(path, record, **kwargs):
                status = real_append(path, record, **kwargs)
                entered.set()
                if not resume.wait(timeout=15):
                    raise AssertionError("COMMITTED_CANCEL_BARRIER_TIMEOUT")
                return status

            with patch(
                "champions_assist.local_strategy.parallel_shadow_session."
                "append_shadow_comparison",
                side_effect=committed_then_suspended):
                self.assertEqual(bridge.attach_gpt(
                    request_id="req-1", turn_epoch=1, state_revision=4,
                    status="ADVICE", advice_present=True, elapsed_ms=30,
                    fresh=True, reported_screen="MOVE_MENU"),
                    "JOIN_SUBMITTED_NONACTIONABLE")
                done = bridge.last_future
                self.assertTrue(entered.wait(timeout=10))
                # Own a genuine write transaction while invalidation and
                # both cleanup attempts run in the independent worker.
                with closing(sqlite3.connect(db, timeout=0.2)) as locked, locked:
                    locked.execute("BEGIN IMMEDIATE")
                    try:
                        since = monotonic()
                        self.assertTrue(bridge.invalidate_if_changed(
                            turn_epoch=1, state_revision=5, phase="BATTLE"))
                        self.assertLess(monotonic()-since, 1.0)
                        resume.set()
                        result = done.result(timeout=15)
                        self.assertEqual(result.status, "REFUSED")
                        self.assertEqual(
                            session.cleanup_future.result(timeout=15),
                            "REVOKE_STORAGE_FAILED")
                    finally:
                        locked.rollback()
                        resume.set()
                raw = read_shadow_comparisons(db)
                self.assertEqual(len(raw), 1,
                                 "Mock must genuinely reproduce stale data")
                self.assertEqual(
                    read_shadow_comparisons(db, finalized_only=True), ())
                self.assertEqual(pending_shadow_lease_count(db), 1)
                gui = scene(turns=1)
                for event in gui["timing_observations"]["events"]:
                    event["match_id"]="capture-race"
                    event["state_revision"]=4
                report = stage1_software_shadow_report(
                    raw, gui, acceptance_database_path=db)
                self.assertEqual(report["sqlite_window_count"], 1)
                self.assertEqual(report["sqlite_finalized_window_count"], 0)
                self.assertEqual(
                    report["sqlite_unfinalized_or_revoked_count"], 1)
                self.assertEqual(report["qt_gpt_pair_matched"], 0)
                self.assertEqual(report["status"], "SOFTWARE_EVIDENCE_PARTIAL")
                self.assertFalse(report["phase2_accepted"])
                bridge.invalidate()


    def _test_review_p2_4_cancel_during_finalization(
        self, pause_at: str, revision_change: bool,
    ) -> None:
        """Event interleaving before/during/after finalization COMMIT.

        UI OFF/new revision must remain nonblocking. A valid raw/finalized
        SQLite row cannot become accepted software evidence after OFF.
        """
        started, resume = Event(), Event()
        with TemporaryDirectory() as directory:
            db = Path(directory) / "private" / "shadow.sqlite"
            bridge = UiShadowDispatcher(db)
            self.assertEqual(bridge.prepare_storage(), "READY")
            self.assertEqual(bridge.start(
                make_assets(), enabled=True, match_id="capture-finalizerace",
                request_id="req-1", turn_epoch=1, state_revision=4,
                screen_label="TURN_ACTION", observed_state={
                    "our_active":"A", "opponent_active":"O1",
                    "selected_three":["A","B","C"]},
                deadline_monotonic=monotonic()+60),
                "RUNNING_UNVERIFIED")
            session = bridge.session

            def blocked_finalize(database, record, **kwargs):
                if pause_at == "before":
                    started.set()
                    if not resume.wait(timeout=12):
                        raise AssertionError("FINALIZE_PREPARE_TIMEOUT")
                if pause_at == "inside":
                    original_fence = kwargs["active_at_commit"]
                    calls = [0]
                    def paused_fence():
                        calls[0] += 1
                        if calls[0] == 2:
                            started.set()
                            if not resume.wait(timeout=12):
                                raise AssertionError("FINALIZE_TRANSACTION_TIMEOUT")
                        return original_fence()
                    kwargs["active_at_commit"] = paused_fence
                value = real_finalize(database, record, **kwargs)
                if pause_at == "after":
                    started.set()
                    if not resume.wait(timeout=12):
                        raise AssertionError("FINALIZE_AFTER_COMMIT_TIMEOUT")
                return value

            with patch(
                "champions_assist.local_strategy.parallel_shadow_session."
                "finalize_shadow_comparison", side_effect=blocked_finalize):
                self.assertEqual(bridge.attach_gpt(
                    request_id="req-1",turn_epoch=1,state_revision=4,
                    status="ADVICE",advice_present=True,elapsed_ms=25,
                    fresh=True,reported_screen="MOVE_MENU"),
                    "JOIN_SUBMITTED_NONACTIONABLE")
                future = bridge.last_future
                try:
                    self.assertTrue(started.wait(timeout=10))
                    self.assertEqual(
                        pending_shadow_lease_count(db), 1,
                        "Pending audit barrier must predate finalization")
                    t0 = monotonic()
                    if revision_change:
                        self.assertTrue(bridge.invalidate_if_changed(
                            turn_epoch=1,state_revision=5,phase="BATTLE"))
                    else:
                        bridge.invalidate()
                    self.assertLess(monotonic() - t0, 1.0)
                    self.assertIsNotNone(
                        session.cleanup_future,
                        "Finalization in progress is not persisted")
                finally:
                    resume.set()
                result = future.result(timeout=12)
                self.assertEqual(result.status, "REFUSED", result.reason)
                self.assertIn(session.cleanup_future.result(timeout=12),
                              ("REVOKED","NO_ROW"))
                self.assertEqual(read_shadow_comparisons(db), ())
                self.assertEqual(
                    read_shadow_comparisons(db,finalized_only=True), ())
                self.assertEqual(pending_shadow_lease_count(db), 0)

    def test_review_p2_4_cancel_before_finalize_starts(self):
        self._test_review_p2_4_cancel_during_finalization(
            pause_at="before",revision_change=False)

    def test_review_p2_4_cancel_inside_finalize_transaction(self):
        self._test_review_p2_4_cancel_during_finalization(
            pause_at="inside",revision_change=True)

    def test_review_p2_4_cancel_after_finalize_commit(self):
        self._test_review_p2_4_cancel_during_finalization(
            pause_at="after",revision_change=True)

    def test_review_p2_2_off_after_completed_write_preserves_history(self):
        """A commit accepted while enabled must not be erased by later OFF."""
        snap = frozen()
        with TemporaryDirectory() as d:
            db = Path(d) / "shadow.sqlite"
            session = ParallelShadowSession(
                snap, VERIFIED_ASSETS, database_path=db,
                current_snapshot=lambda: snap,
                preserve="ガブリアス", stopper="ミミッキュ", enabled=True)
            self.assertEqual(session.start(), "RUNNING")
            result = session.attach_gpt(BoundGPTAdvice(
                snap.snapshot_sha256, "GPT_ABSTAIN")).result(timeout=10)
            self.assertEqual(result.status, "RECORDED")
            session.close()
            self.assertIsNone(session.cleanup_future)
            self.assertEqual(len(read_shadow_comparisons(db)), 1)
            self.assertEqual(
                len(read_shadow_comparisons(db, finalized_only=True)), 1)
            self.assertEqual(pending_shadow_lease_count(db), 0)


if __name__ == "__main__":
    unittest.main()
