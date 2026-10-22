"""No-network tests for the opt-in automatic in-memory battle capture pipeline."""
from __future__ import annotations
import unittest
from unittest.mock import patch
import numpy as np

from champions_assist.app_config import CaptureConfiguration
from champions_assist.capture_settings import CapturePreviewResult
from champions_assist.auto_match_capture import (
    FrameChangeGate, AutoCaptureUnavailable, capture_for_chatgpt,
)

CONFIG=CaptureConfiguration(
    backend="WINDOWS_NATIVE", selected_window_identity={"hwnd":55},
    game_viewport_normalized=(0.25,0.25,0.75,0.75),
)
class FakePreview:
    def __init__(self, image):
        self.image=image
        self.times=0
    def preview_once(self, configuration):
        self.times+=1
        return CapturePreviewResult("CAPTURED_READ_ONLY_UNVERIFIED_CONTENT",
                                    "mock", image=self.image)

class AutoCaptureTests(unittest.TestCase):
    def test_only_selected_roi_reaches_ai_without_file_io(self):
        image=np.zeros((40,60,3),dtype=np.uint8)
        image[10:30,15:45]=[45,120,185]
        preview=FakePreview(image)
        captured=capture_for_chatgpt(CONFIG,preview=preview)
        self.assertEqual(captured.shape,(20,30,3))
        self.assertTrue(np.all(captured == [45,120,185]))
        self.assertEqual(preview.times,1)
        self.assertFalse(np.shares_memory(image,captured))

    def test_refuses_unscoped_window_or_missing_roi(self):
        image=np.zeros((40,60,3),dtype=np.uint8)
        preview=FakePreview(image)
        no_window=CaptureConfiguration(backend="WINDOWS_NATIVE",
                     game_viewport_normalized=(0,0,1,1))
        no_roi=CaptureConfiguration(backend="WINDOWS_NATIVE",
                     selected_window_identity={"hwnd":55})
        for config in (no_window,no_roi):
            with self.assertRaises(AutoCaptureUnavailable):
                capture_for_chatgpt(config,preview=preview)
        self.assertEqual(preview.times,0)

    def test_change_gate_skips_duplicate_frames_and_timer_only_change(self):
        now=[0.0]
        gate=FrameChangeGate(clock=lambda:now[0],minimum_interval=2)
        frame=np.zeros((624,1047,3),dtype=np.uint8)
        self.assertTrue(gate.accept(frame))
        now[0]=3.0
        self.assertFalse(gate.accept(frame.copy()))
        minor=frame.copy()
        minor[0:12,0:12]=255
        now[0]=5.0
        self.assertFalse(gate.accept(minor))
        changed=np.full_like(frame,210)
        now[0]=7.0
        self.assertTrue(gate.accept(changed))
        gate.reset()
        self.assertTrue(gate.accept(frame))

    def test_reports_specific_capture_failure_kind(self):
        class MissingWindow:
            def preview_once(self, configuration):
                return CapturePreviewResult(
                    "WINDOW_IDENTITY_UNAVAILABLE", "window is gone")
        with self.assertRaisesRegex(AutoCaptureUnavailable, "WINDOW_IDENTITY_UNAVAILABLE"):
            capture_for_chatgpt(CONFIG, preview=MissingWindow())

    def test_auto_capture_pauses_and_recovers_without_turning_off(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QMessageBox
        from champions_assist.match_replay_dialog import MatchReplayDialog
        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)
        dialog = MatchReplayDialog(
            model="test", master_directory="unused", team_path="unused",
            capture_config=CONFIG,
        )
        try:
            dialog.state = {"phase": "MATCHMAKING", "matrix_compatible": False}
            dialog.assets = object()
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                 patch.object(dialog, "_capture_next"):
                dialog._start_auto()
            dialog._poll_timer.stop()  # Drive captures deterministically, not by a GUI timer.
            self.assertTrue(dialog._auto_running)
            self.assertIn("自動送信中", dialog.status.text())

            with patch("champions_assist.match_replay_dialog.capture_for_chatgpt",
                       side_effect=AutoCaptureUnavailable("WINDOW_MINIMIZED")):
                for _ in range(3):
                    dialog._capture_next()
            self.assertTrue(dialog._auto_running)
            self.assertTrue(dialog._capture_paused)
            self.assertEqual(dialog._poll_timer.interval(), 2000)
            self.assertIn("画面取得待機中", dialog.status.text())
            self.assertEqual(dialog.entries[-1]["status"], "CAPTURE_PAUSED")
            self.assertEqual(dialog.entries[-1]["reason"], "WINDOW_MINIMIZED")

            frame = np.zeros((60,100,3), dtype=np.uint8)
            with patch("champions_assist.match_replay_dialog.capture_for_chatgpt",
                       return_value=frame):
                dialog._capture_next()
            self.assertTrue(dialog._auto_running)
            self.assertFalse(dialog._capture_paused)
            self.assertEqual(dialog._poll_timer.interval(), 300)
            self.assertIn("自動送信中", dialog.status.text())
            self.assertEqual(dialog.entries[-1]["status"], "CAPTURE_RESUMED")
            dialog._stop_auto()
            self.assertFalse(dialog._auto_running)
            self.assertIn("自動送信停止", dialog.status.text())
            self.assertEqual(dialog._capture_error_count, 0)
        finally:
            dialog._stop_auto()
            dialog.close()

    def test_worker_automatically_sends_frame_to_ai_without_saved_image(self):
        from champions_assist.match_replay_dialog import _ReplayWorker
        pixels=np.full((32,56,3),200,dtype=np.uint8)
        gate=FrameChangeGate(clock=lambda:6.0)
        state={"phase":"MATCHMAKING"}
        calls=[]
        worker=_ReplayWorker(
            app_root="unused",selected_model="test",assets=object(),state=state,
            capture_config=CONFIG,capture_gate=gate,
            capture_function=lambda config:pixels)
        worker.finished.connect(lambda payload:calls.append(payload))
        with patch("champions_assist.match_replay_dialog.ChatGPTSignIn") as auth, \
             patch("champions_assist.match_replay_dialog.run_replay_image") as replay:
            auth.return_value.access_token.return_value="fixture"
            replay.return_value=({"phase":"SELECTION"},{"status":"OBSERVED"})
            worker.run()
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0]["state"]["phase"],"SELECTION")
            self.assertTrue(np.array_equal(replay.call_args.args[0],pixels))
            replay.assert_called_once()
            worker.run()
            self.assertEqual(calls[-1]["report"]["status"],"UNCHANGED_FRAME")
            replay.assert_called_once()

if __name__=="__main__":
    unittest.main()
