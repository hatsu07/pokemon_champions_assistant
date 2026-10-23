"""Offline Qt regression tests for embedded ChatGPT settings (no API use)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QMessageBox, QWidget

from champions_assist.app_config import AppSettingsStore
from champions_assist.capture_settings import CapturePreviewService
from champions_assist.desktop import ensure_application
from champions_assist.settings_dialog import SettingsDialog
from champions_assist.settings_management import DesktopSettingsService
from champions_assist.vision_benchmark_dialog import VisionBenchmarkPanel
from champions_assist.vision_preferences import VisionPreferenceStore


MASTER_ROOT = Path(__file__).resolve().parents[1] / "masters"


class InlineChatGPTSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = ensure_application()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        store = AppSettingsStore(self.root / "config" / "settings.json", defaults_root=self.root)
        self.service = DesktopSettingsService(store, master_directory=MASTER_ROOT)
        self.preview = CapturePreviewService(window_enumerator=lambda: ())

    def dialog(self):
        # Never start model refresh, login or external API during Qt UI tests.
        with patch("champions_assist.vision_benchmark_dialog.default_app_root", return_value=self.root), \
             patch.object(VisionBenchmarkPanel, "_refresh_status", return_value=False):
            return SettingsDialog(self.service, match_active=lambda: False,
                                  preview_service=self.preview)

    def test_chatgpt_controls_are_inline_without_open_dialog_button(self):
        dlg = self.dialog()
        try:
            self.assertIsInstance(dlg.chatgpt_panel, QWidget)
            self.assertEqual(dlg.tabs.tabText(dlg.tabs.indexOf(dlg.chatgpt_tab)), "ChatGPT")
            self.assertEqual(dlg.chatgpt_panel.login_button.text(), "ChatGPTでログイン")
            self.assertEqual(dlg.chatgpt_panel.refresh_button.text(), "利用可能モデルを取得")
            self.assertEqual(dlg.chatgpt_panel.send.text(), "この画像1枚を送信して実測")
            self.assertIsNotNone(dlg.chatgpt_panel.reasoning_effort)
            self.assertFalse(any(
                "ChatGPTでログイン・認識実測を開く" in item.text()
                for item in dlg.findChildren(QWidget)
                if hasattr(item, "text") and callable(item.text)
            ))
            dlg.vision_benchmark_button.click()
            self.assertIs(dlg.tabs.currentWidget(), dlg.chatgpt_tab)
        finally:
            dlg.close()

    def test_saved_reasoning_and_selected_team_are_used_with_in_dialog_roi(self):
        prefs = VisionPreferenceStore(self.root)
        prefs.save_reasoning_effort("low")
        prefs.save_model("gpt-6")
        dlg = self.dialog()
        try:
            panel = dlg.chatgpt_panel
            self.assertEqual(panel.reasoning_effort.currentData(), "low")
            panel._restore_model_options([{"slug": "gpt-6", "display_name": "GPT-6"}])
            self.assertEqual(panel.model.currentData(), "gpt-6")
            record = self.service.import_team(
                MASTER_ROOT / "Pokemon_Champions_MC_team_2026-10-03.csv", match_active=False)
            dlg._selected_team_id = record.team_id
            dlg._set_roi_from_controls((0.1, 0.15, 0.9, 0.85))
            panel._refresh_match_context()
            self.assertEqual(panel.team_path, record.csv_path)
            self.assertEqual(panel.capture_config.game_viewport_normalized, (0.1, 0.15, 0.9, 0.85))
            panel.reasoning_effort.setCurrentIndex(panel.reasoning_effort.findData("high"))
            self.assertEqual(VisionPreferenceStore(self.root).reasoning_effort(), "high")
        finally:
            dlg.close()

    def test_worker_lifetime_blocks_save_close_and_cancel(self):
        dlg = self.dialog()
        dlg.show()
        self.application.processEvents()
        try:
            # A stand-in marks a running child thread without making an API request.
            dlg.chatgpt_panel._thread = object()
            with patch.object(QMessageBox, "information") as info, \
                 patch.object(self.service, "save_capture") as save_capture:
                dlg._save()
                dlg.reject()
                dlg.accept()
                self.assertGreaterEqual(info.call_count, 3)
                save_capture.assert_not_called()
            dlg.close()
            self.assertTrue(dlg.isVisible())
            self.assertIs(dlg.tabs.currentWidget(), dlg.chatgpt_tab)
        finally:
            dlg.chatgpt_panel._thread = None
            dlg.close()


if __name__ == "__main__":
    unittest.main()
