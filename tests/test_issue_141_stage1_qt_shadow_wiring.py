"""#141 Stage1 real Qt hook contract, with synthetic GUI states only."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch
import unittest

import numpy as np

from champions_assist.desktop import ensure_application
from champions_assist.match_replay_dialog import MatchReplayDialog
from champions_assist.preload import MasterPreloader
from champions_assist.local_strategy.shadow_comparison_store import (
    read_shadow_comparisons,
)

MASTER = Path(__file__).resolve().parents[1] / "masters"


class QtShadowWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ensure_application()

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        with patch.object(MatchReplayDialog,
                          "_load_selected_screen_classifier",
                          return_value=(None,"MODEL_UNAVAILABLE")):
            self.panel = MatchReplayDialog(
                model="gpt-test", master_directory=str(MASTER),
                team_path=str(MASTER / "Pokemon_Champions_MC_team_2026-10-03.csv"),
                store_root=self.root, embedded=True)
        self.panel.assets = MasterPreloader(MASTER).load_for_new_match()
        self.panel.state = {
            "phase":"BATTLE", "our_active":"マスカーニャ",
            "opponent_active":"ガブリアス",
            "selected_three":["マスカーニャ","アシレーヌ","ガブリアス"],
            "lead":"マスカーニャ","_live_state_revision":3}
        self.panel.match_id="capture-testqt"
        self.panel._turn_epoch=1
        self.panel._turn_screen_seen_at=monotonic()
        self.panel._auto_running=True
        self.panel._cancel_event.clear()
        self.panel._status_timer.stop()

    def tearDown(self):
        self.panel._stop_auto()
        self.panel._thread=None
        self.panel._status_timer.stop()
        self.panel.close()

    def _dispatch(self):
        self.panel._pending_batch=[np.zeros((80,120,3),dtype=np.uint8)]
        self.panel._pending_screen_label="TURN_ACTION"
        with patch("champions_assist.match_replay_dialog.QThread"), patch(
                "champions_assist.match_replay_dialog._ReplayWorker"), patch.object(
                self.panel,"_freeze_local_events_for_action",return_value=[]):
            self.panel._send_pending()

    def test_shadow_off_by_default_no_source_or_database_access(self):
        self.assertFalse(self.panel.shadow_capture_check.isChecked())
        self.assertFalse((self.root/"phase2-shadow").exists())
        self._dispatch()
        self.assertIsNone(self.panel._shadow_dispatcher.session)
        self.assertFalse((self.root/"phase2-shadow").exists())

    def test_actual_gpt_dispatch_is_opt_in_and_does_not_replace_action(self):
        self.panel.shadow_capture_check.setChecked(True)
        self.assertTrue((self.root/"phase2-shadow").is_dir())
        self._dispatch()
        self.assertIsNotNone(self.panel._shadow_dispatcher.session)
        self.assertEqual(self.panel._shadow_dispatcher.revision,3)
        self.assertEqual(self.panel._shadow_dispatcher.turn_epoch,1)
        self.assertEqual(
            self.panel._shadow_dispatcher.request_id,
            self.panel._active_request_id)
        self.assertTrue(any(item["event"]=="LOCAL_SHADOW_DISPATCH"
                            for item in self.panel._timing_trace))
        self.panel._completed({"state":dict(self.panel.state),"report":{
            "status":"ADVICE","screen":"MOVE_MENU",
            "recommendation":"トリックフラワー。"}})
        future=self.panel._shadow_dispatcher.last_future
        self.assertIsNotNone(future)
        self.assertEqual(future.result(timeout=10).status,"RECORDED")
        rows=read_shadow_comparisons(
            self.root/"phase2-shadow"/"comparison-v2.sqlite")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].gpt_status,"GPT_UNVERIFIED")
        self.assertEqual(rows[0].local_status,"NO_RANKING")
        self.assertEqual(self.panel._last_action_text,"トリックフラワー。")
        self.assertNotIn("トリックフラワー",str(rows))
        self.panel.shadow_capture_check.setChecked(False)
        self.assertIsNone(self.panel._shadow_dispatcher.session)

    def test_ocr_revision_invalidates_running_shadow_before_gpt(self):
        self.panel.shadow_capture_check.setChecked(True)
        self._dispatch()
        self.assertIsNotNone(self.panel._shadow_dispatcher.session)
        self.panel.state["_live_state_revision"]=4
        self.panel._refresh_live_status()
        self.assertIsNone(self.panel._shadow_dispatcher.session)
        self.assertFalse((self.root/"phase2-shadow"/"comparison-v2.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
