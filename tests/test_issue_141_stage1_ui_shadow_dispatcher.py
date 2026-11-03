"""#141 Stage 1: opt-in actual GPT event -> read-only Shadow SQLite pairing.

These tests use deterministic synthetic GUI dispatches. They do not certify a
Champions game, supported battle reconstruction, or correct tactical choice.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
import unittest

from champions_assist.local_strategy.ui_shadow_dispatcher import UiShadowDispatcher
from champions_assist.local_strategy.shadow_comparison_store import (
    read_shadow_comparisons,
)
from tests.test_issue_141_selection import make_assets


def dispatch(session, **kwargs):
    base = {
        "enabled": True, "match_id": "capture-1111", "request_id": "req-1",
        "turn_epoch": 1, "state_revision": 4, "screen_label": "TURN_ACTION",
        "observed_state": {
            "our_active": "A", "opponent_active": "O1",
            "selected_three": ["A", "B", "C"],
        }, "deadline_monotonic": monotonic() + 30,
    }
    base.update(kwargs)
    return session.start(make_assets(), **base)


class UiShadowDispatcherTests(unittest.TestCase):
    def test_default_disabled_and_opt_in_storage_boundary(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "private" / "shadow.sqlite"
            bridge = UiShadowDispatcher(database)
            self.assertEqual(dispatch(bridge, enabled=False), "DISABLED")
            self.assertFalse(database.parent.exists())
            self.assertEqual(dispatch(bridge), "REFUSED:STORAGE_NOT_PREPARED")
            self.assertEqual(bridge.prepare_storage(), "READY")
            self.assertTrue(database.parent.is_dir())
            self.assertFalse(database.exists())
            bridge.invalidate()

    def test_real_gpt_dispatch_paired_with_nonactionable_local_abstention(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "private" / "shadow.sqlite"
            bridge = UiShadowDispatcher(database)
            self.assertEqual(bridge.prepare_storage(), "READY")
            self.assertEqual(dispatch(bridge), "RUNNING_UNVERIFIED")
            self.assertEqual(bridge.attach_gpt(
                request_id="req-1", turn_epoch=1, state_revision=4,
                status="ADVICE", advice_present=True, elapsed_ms=550,
                fresh=True,reported_screen="MOVE_MENU"), "JOIN_SUBMITTED_NONACTIONABLE")
            receipt = bridge.last_future.result(timeout=10)
            self.assertEqual(receipt.status,"RECORDED",receipt.reason)
            self.assertFalse(receipt.ready_for_live)
            record = read_shadow_comparisons(database)[0]
            self.assertEqual(record.gpt_status,"GPT_UNVERIFIED")
            self.assertEqual(record.local_status,"NO_RANKING")
            self.assertEqual(record.same_action_comparison,"LOCAL_ABSTAINED")
            self.assertFalse(record.gpt_action_verified_in_window)
            self.assertEqual(record.gpt_action_sha256,"")
            self.assertEqual(record.local_reason_code,"LOCAL_SOURCE_REFUSED")
            self.assertTrue(record.input_deadline_met)
            self.assertIsNone(record.__dict__.get("raw_ocr"))
            self.assertEqual(bridge.attach_gpt(
                request_id="req-1",turn_epoch=1,state_revision=4,
                status="ADVICE",advice_present=True,elapsed_ms=550,
                fresh=True,reported_screen="MOVE_MENU"), "DUPLICATE_OR_CLOSED")
            bridge.invalidate()

    def test_revision_advance_and_new_window_refuse_old_gpt(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "private" / "shadow.sqlite"
            bridge = UiShadowDispatcher(database)
            bridge.prepare_storage()
            self.assertEqual(dispatch(bridge), "RUNNING_UNVERIFIED")
            self.assertTrue(bridge.invalidate_if_changed(
                turn_epoch=1, state_revision=5, phase="BATTLE"))
            self.assertEqual(bridge.attach_gpt(
                request_id="req-1",turn_epoch=1,state_revision=4,
                status="ADVICE",advice_present=True,elapsed_ms=500,
                fresh=True,reported_screen="MOVE_MENU"), "NOT_RUNNING")
            self.assertFalse(database.exists())
            self.assertEqual(dispatch(bridge,request_id="req-2",
                                      state_revision=5), "RUNNING_UNVERIFIED")
            self.assertEqual(bridge.attach_gpt(
                request_id="req-1",turn_epoch=1,state_revision=5,
                status="ADVICE",advice_present=True,elapsed_ms=500,
                fresh=True,reported_screen="MOVE_MENU"), "STALE_REFUSED")
            self.assertFalse(database.exists())

    def test_non_action_screen_and_cancelled_gpt_do_not_write(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "private" / "shadow.sqlite"
            bridge = UiShadowDispatcher(database)
            bridge.prepare_storage()
            self.assertEqual(dispatch(bridge,screen_label="TEAM_SELECTION"),
                             "REFUSED:MALFORMED_DISPATCH_WINDOW")
            self.assertEqual(dispatch(bridge), "RUNNING_UNVERIFIED")
            self.assertEqual(bridge.attach_gpt(
                request_id="req-1",turn_epoch=1,state_revision=4,
                status="CANCELLED",advice_present=False,elapsed_ms=100,
                fresh=True,reported_screen="MOVE_MENU"), "GPT_RESPONSE_NOT_VERIFIED")
            self.assertFalse(database.exists())

    def test_invalidated_result_is_not_silent_success(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "private" / "shadow.sqlite"
            bridge = UiShadowDispatcher(database)
            bridge.prepare_storage()
            self.assertEqual(dispatch(bridge), "RUNNING_UNVERIFIED")
            self.assertEqual(bridge.attach_gpt(
                request_id="req-1",turn_epoch=1,state_revision=4,
                status="ADVICE",advice_present=True,elapsed_ms=100,
                fresh=False,reported_screen="MOVE_MENU"), "STALE_REFUSED")
            self.assertFalse(database.exists())


if __name__ == "__main__":
    unittest.main()
