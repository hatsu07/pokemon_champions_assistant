"""Five-tab screenshot diagnostics + REAL opt-in live screen integration.

All tests here use offline mocked frames. Only the Windows user can verify
end-to-end timing and UI recognition against actual running Champions.
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from time import perf_counter

import numpy as np
from PySide6.QtWidgets import QApplication

from champions_assist.app_config import CaptureConfiguration
from champions_assist.selection_test_dialog import SelectionTestDialog, _TimingTestWorker
from champions_assist.live_timing_test import switch_timing_test, selection_test_prep_state
from tests.test_live_timing_test import selection_fixture
from tests.test_match_replay import fixture_assets


class DiagnosticsTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.root=TemporaryDirectory()
        self.assets=fixture_assets()
        self.dialog=SelectionTestDialog(
            assets=self.assets,model="mock",reasoning_effort="auto",
            app_root=Path(self.root.name))

    def tearDown(self):
        self.dialog.close()
        self.root.cleanup()

    def test_five_separate_tabs_and_no_implicit_live_capture(self):
        self.assertEqual(self.dialog.tabs.count(),5)
        self.assertEqual([self.dialog.tabs.tabText(i) for i in range(5)],[
            "選出画面テスト","ターン画面テスト",
            "交代先選択画面テスト","バトルイベント読み取りテスト",
            "1対戦統合テスト（実ゲーム画面）"])
        self.assertIsNone(self.dialog.live_panel)
        self.assertFalse(self.dialog.turn_run_button.isEnabled())
        self.assertFalse(self.dialog.switch_run_button.isEnabled())
        self.assertFalse(self.dialog.event_run_button.isEnabled())
        self.dialog.tabs.setCurrentWidget(self.dialog.integration_page)
        self.assertIn("設定",self.dialog.integration_status.text())
        self.assertIsNone(self.dialog.live_panel)

    def test_tabs_share_only_explicitly_completed_selection_and_prep(self):
        self.dialog.set_image(np.zeros((250,500,3),dtype=np.uint8))
        self.dialog.test_state=selection_test_prep_state(
            self.assets,selection_fixture())
        self.dialog.prep_attempted=True
        self.dialog.switch_image=np.zeros((250,500,3),dtype=np.uint8)
        self.dialog.event_image=np.zeros((250,500,3),dtype=np.uint8)
        self.dialog.turn_image=np.zeros((250,500,3),dtype=np.uint8)
        self.dialog._clear_worker()
        self.assertTrue(self.dialog.turn_run_button.isEnabled())
        self.assertTrue(self.dialog.switch_run_button.isEnabled())
        self.assertTrue(self.dialog.event_run_button.isEnabled())
        self.dialog.set_image(np.ones((250,500,3),dtype=np.uint8))
        self.assertIsNone(self.dialog.test_state)
        self.assertIsNone(self.dialog.switch_image)
        self.assertIsNone(self.dialog.event_image)
        self.assertIsNone(self.dialog.turn_image)
        self.assertFalse(self.dialog.switch_run_button.isEnabled())
        self.assertFalse(self.dialog.event_run_button.isEnabled())

    def test_event_observation_requires_actual_event_not_generic_screen(self):
        state=selection_test_prep_state(self.assets,selection_fixture())
        emitted=[]
        worker=_TimingTestWorker(
            mode="EVENT",image=np.zeros((300,500,3),dtype=np.uint8),
            assets=self.assets,state=state,app_root=Path(self.root.name),
            model="mock",reasoning_effort="auto",requested_at=perf_counter())
        worker.finished.connect(lambda payload:emitted.append(payload))
        revised=deepcopy(state)
        revised["phase"]="ENDED"
        revised["result"]="WIN"
        observed={"status":"MATCH_END","screen":"RESULT_SCREEN",
                  "observation":{"observed_event":"TURN_RESOLVED",
                                 "messages":["勝敗の表示"],"match_result":"WIN"}}
        with patch("champions_assist.selection_test_dialog.ChatGPTSignIn.access_token",
                   return_value="dummy"),patch(
            "champions_assist.match_replay.run_replay_batch",
            return_value=(revised,observed)):
            worker.run()
        self.assertEqual(emitted[0]["report"]["status"],"EVENT_READ_PASS")
        self.assertEqual(emitted[0]["state"]["phase"],"ENDED")

    def test_live_integrated_tab_uses_real_capture_engine_not_saved_images(self):
        from champions_assist.match_replay_dialog import MatchReplayDialog
        cfg=CaptureConfiguration(
            backend="WINDOWS_NATIVE",
            selected_window_identity={"hwnd":123,"pid":55},
            game_viewport_normalized=(0.0,0.0,1.0,1.0))
        self.dialog.master_directory=Path(self.root.name)
        self.dialog.team_path=Path(self.root.name)/"team.csv"
        self.dialog.capture_config=cfg
        self.dialog.tabs.setCurrentWidget(self.dialog.integration_page)
        panel=self.dialog.live_panel
        self.assertIsInstance(panel,MatchReplayDialog)
        self.assertIs(panel.capture_config,cfg)
        self.assertFalse(panel._auto_running)
        self.assertFalse(panel._poll_timer.isActive())
        self.assertIsNone(self.dialog.image)
        self.dialog._on_live_state(
            {"phase":"ENDED","result":"WIN"},
            {"status":"MATCH_END","screen":"RESULT_SCREEN"})
        self.assertIn("決着確認",self.dialog.integration_status.text())
        self.assertIn("WIN",self.dialog.integration_status.text())


class SwitchReplayTests(unittest.TestCase):
    def test_only_party_menu_with_legal_switch_can_pass(self):
        from champions_assist.match_replay import new_replay
        assets=fixture_assets()
        state=selection_test_prep_state(assets,selection_fixture())
        def response(screen,kind):
            def runner(**kwargs):
                result={
                    "status":"PROVISIONAL_ADVICE","screen":screen,
                    "action":{"kind":kind},
                    "recommendation":"マリルリに交代。"}
                return deepcopy(kwargs["state"]),result
            return runner
        args=dict(
            image=np.zeros((250,500,3),dtype=np.uint8),
            state=state,assets=assets,access_token="dummy",model="mock")
        _,report=switch_timing_test(
            **args,turn_runner=response("PARTY_MENU","SWITCH"))
        self.assertTrue(report["pass"])
        self.assertEqual(report["status"],"SWITCH_TIMING_PASS")
        _,report=switch_timing_test(
            **args,turn_runner=response("MOVE_MENU","MOVE"))
        self.assertFalse(report["pass"])
        self.assertEqual(report["recommendation"],"")


if __name__=="__main__":
    unittest.main()
