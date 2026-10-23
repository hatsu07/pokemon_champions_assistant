"""GUI regression: one observed match drives main advice/state and SQLite history."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QInputDialog

from champions_assist.app_config import CaptureConfiguration
from champions_assist.windows_capture import WindowCandidate

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from champions_assist.app_config import AppSettingsStore
from champions_assist.desktop import DesktopMainWindow, DesktopController, ensure_application
from champions_assist.settings_management import DesktopSettingsService
from champions_assist.store import BattleStore

MASTER_DIR = Path(__file__).resolve().parents[1] / "masters"


class UnifiedMainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ensure_application()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        settings = AppSettingsStore(self.root / "settings.json", defaults_root=self.root)
        self.service = DesktopSettingsService(settings, master_directory=MASTER_DIR)
        self.store = BattleStore(self.root / "matches.sqlite3")
        self.controller = DesktopController(store=self.store, settings_service=self.service)
        self.window = DesktopMainWindow(self.controller, settings_service=self.service)

    def tearDown(self):
        self.window.close()
        self.store.close()

    def test_main_is_live_support_not_launcher_or_detached_legacy_fsm(self):
        window = self.window
        panel = window._match_panel
        self.assertTrue(window._unified)
        self.assertIsNone(window.controller.session)
        self.assertIs(window.tabs.widget(0), window._match_scroll)
        self.assertIs(window._match_scroll.widget(), panel)
        self.assertTrue(panel.embedded)
        self.assertEqual(window.tabs.tabText(0), "対戦支援")
        self.assertEqual(panel.start_button.text(), "対戦開始（4ファイル事前読込）")
        self.assertIsNone(window._auto_match_dialog)
        window._open_auto_match_support()
        self.assertIs(window.tabs.currentWidget(), window._match_scroll)
        self.assertIsNone(window._auto_match_dialog)
        self.assertEqual(window.header_labels["phase"].text(), "S00_WAIT_START")

    def test_one_preload_one_sqlite_match_and_live_report_updates_main(self):
        member = self.service.import_team(
            MASTER_DIR / "Pokemon_Champions_MC_team_2026-10-03.csv",
            match_active=False)
        self.service.select_team(member.team_id, match_active=False)
        with patch("champions_assist.vision_preferences.VisionPreferenceStore") as prefs:
            prefs.return_value.selected_model.return_value = "gpt-5.6-terra"
            prefs.return_value.reasoning_effort.return_value = "low"
            panel = self.window._match_panel
            panel._new_match()

        self.assertIsNotNone(panel.assets)
        self.assertIsNotNone(panel.state)
        self.assertEqual(self.window.header_labels["preload"].text(), "4/4 読込済み")
        self.assertEqual(self.window.header_labels["phase"].text(), "MATCHMAKING")
        self.assertEqual(panel.model, "gpt-5.6-terra")
        self.assertEqual(panel.reasoning_effort, "low")
        self.assertIsNone(self.controller.session)  # No second observer FSM.
        matches = self.store.list_matches()
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["match_id"], panel.match_id)
        self.assertEqual(len(panel.assets.sha256), 4)
        self.assertEqual(self.store.events(panel.match_id)[0]["event_type"], "NOTE")

        panel.state["phase"] = "SELECTION"
        panel.state["opponent_six"] = [
            "マスカーニャ", "アシレーヌ", "ガブリアス", "ルカリオ",
            "サーフゴー", "ミミッキュ"]
        panel._render({
            "status": "TEST_ONLY",
            "recommendation": "選出：ピカチュウ / ドドゲザン / ガブリアス　初手：ピカチュウ"})
        self.assertIn("選出：ピカチュウ", self.window.recommendation_label.text())
        self.assertIn("機能試験", self.window.recommendation_label.text())
        self.assertEqual(self.window.header_labels["phase"].text(), "SELECTION")
        self.assertEqual(len(self.store.list_matches()), 1)
        self.assertEqual(len(self.store.events(panel.match_id)), 2)
        self.assertEqual(self.window.history_table.rowCount(), 1)
        # Rendering the exact same report is not another recorded game event.
        panel._render({
            "status": "TEST_ONLY",
            "recommendation": "選出：ピカチュウ / ドドゲザン / ガブリアス　初手：ピカチュウ"})
        self.assertEqual(len(self.store.events(panel.match_id)), 2)

    def test_capture_stop_and_unconfirmed_recommendations_are_visible(self):
        panel = self.window._match_panel
        panel.state = {"phase": "SELECTION", "matrix_compatible": False,
                       "prep_status": "NOT_STARTED"}
        panel._auto_running = True
        panel._render({"status": "AUTO_STARTED"})
        self.assertEqual(self.window.header_labels["capture"].text(), "監視中")
        panel._render({"status": "BLOCKED", "reason": "UNCERTAIN_TEAM_SLOT"})
        self.assertIn("UNCERTAIN_TEAM_SLOT", self.window.recommendation_label.text())
        panel._stop_auto()
        self.assertEqual(self.window.header_labels["capture"].text(), "停止")

    def test_live_preview_uses_current_capture_without_extra_capture_or_api(self):
        panel=self.window._match_panel
        panel.state={"phase": "MATCHMAKING", "matrix_compatible": False}
        panel._auto_running=True
        panel._render({"status": "AUTO_STARTED"})
        image=np.full((80, 140, 3), 180, dtype=np.uint8)
        with patch("champions_assist.match_replay_dialog.capture_for_chatgpt", return_value=image) as capture:
            panel._capture_next()
        capture.assert_called_once()
        self.assertIsNotNone(panel.capture_preview.pixmap())
        self.assertFalse(panel.capture_preview.pixmap().isNull())
        self.assertIn("140×80 px",panel.capture_preview_info.text())
        self.assertEqual(self.window.header_labels["capture"].text(),"監視中")
        self.assertEqual(panel.entries,[])  # Rendering never sends/records an AI decision.
        self.assertIsNone(panel._thread)
        last_pixmap=panel.capture_preview.pixmap().cacheKey()

        with patch("champions_assist.match_replay_dialog.capture_for_chatgpt",
                   side_effect=__import__("champions_assist.auto_match_capture",fromlist=["AutoCaptureUnavailable"]).AutoCaptureUnavailable("WINDOW_IDENTITY_UNAVAILABLE")):
            for _ in range(3):
                panel._capture_next()
        self.assertTrue(panel._capture_paused)
        self.assertEqual(panel.capture_preview.pixmap().cacheKey(),last_pixmap)
        self.assertIn("WINDOW_IDENTITY_UNAVAILABLE",panel.capture_preview_info.text())
        self.assertIn("最後に取得した画像",panel.capture_preview_info.text())
        self.assertEqual(self.window.header_labels["capture"].text(),"再取得待機")
        panel._stop_auto()
        cleared=panel.capture_preview.pixmap()
        self.assertTrue(cleared is None or cleared.isNull())
        self.assertIn("監視停止中",panel.capture_preview.text())

    def test_explicit_mirror_reconnect_persists_selected_window_and_keeps_preload(self):
        panel=self.window._match_panel
        old=CaptureConfiguration(
            backend="WINDOWS_NATIVE",
            selected_window_identity={"hwnd": 55, "pid": 123, "title": "旧ウィンドウ"},
            game_viewport_normalized=(0.0,0.0,1.0,1.0))
        panel.capture_config=old
        panel.state={"phase":"MATCHMAKING","matrix_compatible":False}
        initial_assets=object()
        panel.assets=initial_assets
        candidate=WindowCandidate(
            hwnd=82,pid=888,process_name="PhoneLink.exe",
            title="スマートフォンの画面",client_bbox=(0,0,250,170),
            dpi=96,minimized=False)
        with patch("champions_assist.match_replay_dialog.CapturePreviewService") as service, \
             patch.object(QInputDialog,"getItem",return_value=(candidate.display_name(),True)), \
             patch("champions_assist.match_replay_dialog.capture_for_chatgpt",
                   return_value=np.full((170,250,3),55,dtype=np.uint8)) as cap:
            service.return_value.list_windows.return_value=(candidate,)
            panel._reconnect_capture_window()
            cap.assert_called_once()
        self.assertEqual(panel.capture_config.selected_window_identity,candidate.identity())
        self.assertEqual(self.service.settings.capture.selected_window_identity,candidate.identity())
        self.assertIs(panel.assets,initial_assets)
        self.assertFalse(panel._auto_running)
        self.assertEqual(panel.entries[-1]["status"],"CAPTURE_TARGET_RECONNECTED")
        self.assertIsNotNone(panel.capture_preview.pixmap())
        self.assertIn("再接続完了",self.window.recommendation_label.text())
        self.assertEqual(panel._turn_buffer.templates,{})

    def test_legacy_controller_refresh_never_clobbers_live_recommendation(self):
        panel = self.window._match_panel
        panel.state = {"phase": "BATTLE", "matrix_compatible": False}
        panel._render({"status": "TEST_ONLY", "recommendation": "10まんボルト。"})
        self.controller.refresh()
        self.assertIn("10まんボルト", self.window.recommendation_label.text())


if __name__ == "__main__":
    unittest.main()
