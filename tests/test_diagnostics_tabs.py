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
from PySide6.QtWidgets import QApplication, QLabel

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

    def test_local_ocr_diagnostics_are_separate_from_api_event_test(self):
        # No selection, PREP or ChatGPT access is necessary to inspect a
        # user-selected screenshot's exact ROI and OCR result.
        self.assertFalse(self.dialog.event_run_button.isEnabled())
        with patch.object(self.dialog,"_pick_diagnostic_image",
                          return_value=np.zeros((572,960,3),dtype=np.uint8)):
            self.dialog.choose_event_image()
        self.assertTrue(self.dialog.event_ocr_button.isEnabled())
        self.assertFalse(self.dialog.event_run_button.isEnabled())
        result={"status":"OCR_DIAGNOSTIC_COMPLETE","rows":[{
            "name":"battle_message","kind":"MESSAGE",
            "crop":np.zeros((40,150,3),dtype=np.uint8),
            "text":"ガブリアスは たおれた",
            "confidence":0.91,"parsed":"ガブリアスは たおれた",
            "elapsed_ms":20,"error":""}]}
        self.dialog._local_ocr_result(result)
        self.assertIn("1領域",self.dialog.event_ocr_status.text())
        self.assertEqual(self.dialog.event_ocr_rows.count(),1)
        container=self.dialog.event_ocr_rows.itemAt(0).widget()
        detail=next(label.text() for label in container.findChildren(QLabel)
                    if "認識:" in label.text())
        self.assertIn("\n認識:",detail)
        self.assertNotIn("\\n",detail)

    def test_local_ocr_worker_forwards_gpu_mode_and_index_without_ai(self):
        from champions_assist.calibrated_screen_fields import ScreenFieldProfile
        from champions_assist.selection_test_dialog import _LocalOcrWorker
        image=np.full((572,960,3),255,dtype=np.uint8)
        profile=ScreenFieldProfile.from_user_samples_960x572()
        worker=_LocalOcrWorker(image,profile,device="directml",gpu_id=3)
        results=[]
        worker.finished.connect(results.append)
        sample={"status":"OCR_DIAGNOSTIC_COMPLETE","rows":[],
                "engine":"pp-ocrv6-small-onnx","provider":"DmlExecutionProvider"}
        with patch("champions_assist.ocr_diagnostics.inspect_battle_event_image",
                   return_value=sample) as inspect:
            worker.run()
        inspect.assert_called_once_with(image,profile,device="directml",gpu_id=3)
        self.assertEqual(results,[sample])

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

    def test_live_integration_close_after_worker_without_retry_dialog(self):
        from PySide6.QtCore import QCoreApplication
        cfg=CaptureConfiguration(
            backend="WINDOWS_NATIVE",
            selected_window_identity={"hwnd":123,"pid":55},
            game_viewport_normalized=(0.0,0.0,1.0,1.0))
        self.dialog.master_directory=Path(self.root.name)
        self.dialog.team_path=Path(self.root.name)/"team.csv"
        self.dialog.capture_config=cfg
        self.dialog.tabs.setCurrentWidget(self.dialog.integration_page)
        live=self.dialog.live_panel
        live.state={"phase":"BATTLE","matrix_compatible":False}
        live._auto_running=True
        live._thread=object()  # Simulated active worker, no network
        live._active_job="batch"
        self.dialog.show()
        self.dialog.close()
        self.assertTrue(self.dialog._close_after_workers)
        self.assertTrue(self.dialog.isVisible())
        self.assertEqual(live._shutdown_intent,"close")
        self.assertTrue(live._cancel_event.is_set())
        live._cleanup()
        QCoreApplication.processEvents()
        QCoreApplication.processEvents()
        self.assertFalse(self.dialog.isVisible())
        self.assertTrue(live._shutdown_ready)

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
