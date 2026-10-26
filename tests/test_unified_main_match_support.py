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

    def test_local_clock_export_has_unverified_bounded_timing_evidence(self):
        import json
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE","matrix_compatible":False}
        panel._trace_event("SCREEN_CLASSIFIER_CANDIDATE",
                           screen_label="TURN_ACTION",confidence="UNVERIFIED")
        panel._trace_event("AI_RESULT_DISPLAYED",status="ADVICE",
                           recommendation_present=True)
        path=self.root/"private-timing-export.json"
        with patch("champions_assist.match_replay_dialog.QFileDialog.getSaveFileName",
                   return_value=(str(path),"JSON (*.json)")):
            panel._export()
        observed=json.loads(path.read_text(encoding="utf-8"))
        timing=observed["timing_observations"]
        self.assertFalse(timing["automatically_certified"])
        self.assertEqual(timing["source"],"UNVERIFIED_LOCAL_GUI_MONOTONIC_CLOCK")
        self.assertEqual(len(timing["events"]),2)
        self.assertLessEqual(timing["events"][0]["at_ms"],timing["events"][1]["at_ms"])
        self.assertEqual(timing["events"][0]["screen_label"],"TURN_ACTION")
        self.assertIn("USER_VIDEO_REVIEW_REQUIRED",timing["warning"])

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
        self.assertIsNotNone(panel._field_profile)
        self.assertEqual(panel._field_profile.reference_resolution, (960, 572))
        self.assertFalse(panel._field_profile.validated_on_game_capture)
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

    def test_live_turn_unclassified_frames_do_not_discard_inflight_advice(self):
        from champions_assist.match_replay_dialog import (
            _should_replace_inflight, _supersedes_result)
        self.assertFalse(_should_replace_inflight("TURN_ACTION",None))
        self.assertFalse(_should_replace_inflight("TURN_ACTION","TURN_ACTION"))
        self.assertTrue(_should_replace_inflight(None,"TURN_ACTION"))
        self.assertTrue(_should_replace_inflight("TURN_ACTION","BATTLE_EVENT"))
        move={"status":"PROVISIONAL_ADVICE","screen":"MOVE_MENU",
              "recommendation":"じしん。"}
        self.assertFalse(_supersedes_result(move,None))
        self.assertFalse(_supersedes_result(move,"TURN_ACTION"))
        self.assertTrue(_supersedes_result(move,"BATTLE_EVENT"))

        panel=self.window._match_panel
        panel.state={
            "phase":"BATTLE","selected_three":["ピカチュウ","ドドゲザン","ガブリアス"],
            "lead":"ピカチュウ","prep_status":"CACHE_STRUCTURE_COMPLETE",
            "matrix_provisional":True,"matrix_compatible":False}
        panel._auto_running=True
        panel._thread=object()  # a running worker; no Qt thread launched
        panel._active_job="batch"
        panel._inflight_screen_label="TURN_ACTION"
        image=np.zeros((80,140,3),dtype=np.uint8)
        try:
            panel._queue_batch([image],screen_label=None)
            self.assertIsNone(panel._pending_batch)
            panel._queue_batch([image],screen_label="TURN_ACTION")
            self.assertIsNone(panel._pending_batch)
            payload={"state":dict(panel.state),
                     "report":{"status":"PROVISIONAL_ADVICE",
                               "screen":"MOVE_MENU",
                               "recommendation":"じしん。",
                               "action":{"kind":"MOVE","move":"じしん"}}}
            panel._completed(payload)
            self.assertEqual(panel.entries[-1]["status"],"PROVISIONAL_ADVICE")
            self.assertIn("じしん",panel.action_banner.text())
            self.assertIn("じしん",self.window.recommendation_label.text())
        finally:
            panel._thread=None
            panel._active_job=None
            panel._auto_running=False

    def test_busy_worker_does_not_consume_fallback_scene_transition(self):
        panel=self.window._match_panel
        panel.state={"phase":"PREP","matrix_compatible":False,
                     "matrix_provisional":True}
        panel._auto_running=True
        panel._thread=object()  # worker without launching network
        panel._active_job="batch"
        panel._screen_model=None
        image=np.zeros((100,160,3),dtype=np.uint8)
        try:
            with (patch("champions_assist.match_replay_dialog.capture_for_chatgpt",
                        return_value=image),
                  patch.object(panel._turn_buffer,"observe",return_value=None),
                  patch.object(panel._automatic_probe,"observe") as probe,
                  patch.object(panel,"_queue_batch") as queued):
                panel._capture_next()
                probe.assert_not_called()
                queued.assert_not_called()
        finally:
            panel._thread=None
            panel._active_job=None
            panel._auto_running=False

    def test_reset_during_live_worker_cancels_and_completes_automatically(self):
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE","matrix_compatible":False}
        panel._thread=object()  # Simulate an active owned worker; no API
        panel._active_job="batch"
        panel._auto_running=True
        panel._cancel_event.clear()
        self.assertTrue(panel.reset_button.isEnabled())
        panel._reset()
        self.assertEqual(panel._shutdown_intent,"reset")
        self.assertTrue(panel._cancel_event.is_set())
        self.assertIsNotNone(panel.state)
        # Late GPT output from a cancelled operation must be ignored.
        panel._completed({"state":{"phase":"ENDED"},
                          "report":{"status":"ADVICE",
                                    "recommendation":"じしん。"}})
        self.assertEqual(panel.state["phase"],"BATTLE")
        panel._cleanup()
        self.assertIsNone(panel._shutdown_intent)
        self.assertIsNone(panel.state)
        self.assertIsNone(panel._thread)
        self.assertTrue(panel.reset_button.isEnabled())

    def test_main_close_during_live_worker_finishes_on_cleanup(self):
        from PySide6.QtCore import QCoreApplication
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE","matrix_compatible":False}
        panel._thread=object()
        panel._active_job="batch"
        panel._auto_running=True
        self.window.show()
        self.window.close()
        self.assertTrue(self.window._close_pending)
        self.assertEqual(panel._shutdown_intent,"close")
        self.assertTrue(panel._cancel_event.is_set())
        self.assertTrue(self.window.isVisible())
        panel._cleanup()
        QCoreApplication.processEvents()
        QCoreApplication.processEvents()
        self.assertTrue(panel._shutdown_ready)
        self.assertFalse(self.window.isVisible())

    def test_prominent_selection_and_explicit_battle_work_clock(self):
        panel=self.window._match_panel
        panel.state={"phase":"SELECTION","matrix_compatible":False,
                     "matrix_provisional":True,"selected_three":[],
                     "prep_status":"NOT_STARTED"}
        report={
            "status":"PROVISIONAL_ADVICE","screen":"TEAM_SELECTION",
            "selected_three":["ピカチュウ","ドドゲザン","ガブリアス"],
            "lead":"ピカチュウ",
            "recommendation":"選出：ピカチュウ / ドドゲザン / ガブリアス　初手：ピカチュウ"}
        panel._proposed_selection=(tuple(report["selected_three"]),report["lead"])
        panel._render(report)
        self.assertIn("初手：ピカチュウ",panel.selection_banner.text())
        self.assertIn("提案：",self.window.header_labels["selected"].text())
        self.assertIn("ピカチュウ",self.window.recommendation_label.text())
        panel._render({"status":"PREP_PROVISIONAL_RUNNING"})
        self.assertIn("ピカチュウ",self.window.recommendation_label.text())
        panel.state["phase"]="BATTLE"
        panel._render({"status":"OBSERVED","screen":"COMMAND_MENU"})
        self.assertIn("たたかう",panel.action_banner.text())
        self.assertIn("技一覧",self.window.recommendation_label.text())
        self.assertIn("対戦中",panel.phase_banner.text())
        self.assertIn("時間：",panel.deadline_banner.text())

    def test_selected_team_automatically_prepares_matching_live_sources(self):
        from champions_assist.matrix_compatibility import matrix_matches_registered_team
        team=self.service.import_team(
            MASTER_DIR/"Pokemon_Champions_MC_team_2026-10-03.csv",
            match_active=False)
        self.service.select_team(team.team_id,match_active=False)
        prepared=self.service.prepare_team_live_assets(team.team_id)
        self.assertEqual(prepared.status,"REVIEWED_REUSE")
        self.assertTrue(prepared.cache_hit)
        with patch("champions_assist.vision_preferences.VisionPreferenceStore") as prefs:
            prefs.return_value.selected_model.return_value="gpt-5.6-terra"
            prefs.return_value.reasoning_effort.return_value="low"
            panel=self.window._match_panel
            panel._new_match()
        self.assertEqual(panel.matrix_source,str(prepared.matrix_path))
        self.assertEqual(panel.lookup_source,str(prepared.lookup_path))
        self.assertEqual(panel.live_assets_status,"REVIEWED_REUSE")
        self.assertTrue(matrix_matches_registered_team(panel.assets))
        self.assertEqual(len(panel.assets.sha256),4)
        self.assertEqual(self.window.header_labels["preload"].text(),"4/4 読込済み")

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
        with patch("champions_assist.match_replay_dialog.capture_for_chatgpt", return_value=image) as capture, \
             patch.object(panel,"_queue_batch") as queued:
            panel._capture_next()
        queued.assert_called_once()  # The first live frame now initiates an AI candidate.
        capture.assert_called_once()
        self.assertIsNotNone(panel.capture_preview.pixmap())
        self.assertFalse(panel.capture_preview.pixmap().isNull())
        self.assertIn("140×80 px",panel.capture_preview_info.text())
        self.assertEqual(self.window.header_labels["capture"].text(),"監視中")
        self.assertEqual(panel.entries,[])  # The mocked queue has no network side effects.
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

    def test_no_model_still_probes_without_manual_registration_and_no_duplicate(self):
        panel=self.window._match_panel
        panel.state={"phase":"MATCHMAKING","matrix_compatible":False}
        panel.assets=object()
        panel.model="test-model"
        panel.capture_config=CaptureConfiguration(
            backend="WINDOWS_NATIVE",selected_window_identity={"hwnd":7},
            game_viewport_normalized=(0,0,1,1))
        image=np.zeros((120,200,3),dtype=np.uint8)
        image[60:,100:]=200
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.Yes), \
             patch.object(QMessageBox,"warning",side_effect=AssertionError("unexpected warning dialog")), \
             patch("champions_assist.match_replay_dialog.capture_for_chatgpt",
                   return_value=image) as cap, \
             patch.object(panel,"_queue_batch") as queue:
            panel._start_auto()
            panel._poll_timer.stop()
            self.assertIn("標準自動判定",panel.ai_status_label.text())
            self.assertIn("登録不要",panel.menu_detection_label.text())
            self.assertIn("モデル未配置",panel.local_classifier_label.text())
            self.assertIsNotNone(panel.capture_preview.pixmap())
            queue.assert_called_once()  # Initial frame is inspected immediately.
            panel._calibrate("TEAM_SELECTION")
            self.assertIn("選出画面",panel.menu_detection_label.text())
            queue.assert_called_once()  # Manual action cannot duplicate a just-sent frame.
            for _ in range(3):
                panel._capture_next()
            queue.assert_called_once()
            self.assertEqual(cap.call_count,5)
        panel._stop_auto()

    def test_turn_and_switch_calibration_use_five_class_labels(self):
        panel=self.window._match_panel
        from champions_assist.app_config import CaptureConfiguration
        panel.capture_config=CaptureConfiguration(
            backend="WINDOWS_NATIVE", selected_window_identity={"hwnd":2},
            game_viewport_normalized=(0,0,1,1))
        panel.state={"phase":"BATTLE","matrix_compatible":True}
        image=np.full((120,200,3),60,dtype=np.uint8)
        with patch("champions_assist.match_replay_dialog.capture_for_chatgpt",
                   return_value=image), patch.object(panel,"_queue_batch") as send:
            panel._calibrate("TURN_ACTION")
            panel._calibrate("SWITCH_SELECTION")
            self.assertIn("TURN_ACTION",panel._turn_buffer.templates)
            self.assertIn("SWITCH_SELECTION",panel._turn_buffer.templates)
            self.assertIn("技選択画面",panel.menu_detection_label.text())
            self.assertIn("交代画面",panel.menu_detection_label.text())
            self.assertTrue(panel._eligible_menu("TURN_ACTION"))
            self.assertTrue(panel._eligible_menu("SWITCH_SELECTION"))
            panel._auto_running=True
            panel._calibrate("TURN_ACTION")
            send.assert_called_once()
            self.assertEqual(send.call_args.kwargs["screen_label"],"TURN_ACTION")
        panel._stop_auto()

    def test_selection_confirmation_automatically_starts_prep_once(self):
        from champions_assist.match_replay import new_replay
        from champions_assist.preload import MasterPreloader
        panel=self.window._match_panel
        member=self.service.import_team(
            MASTER_DIR / "Pokemon_Champions_MC_team_2026-10-03.csv",
            match_active=False)
        self.service.select_team(member.team_id,match_active=False)
        assets=MasterPreloader(MASTER_DIR,team_source=self.service.teams.path_for_next_match(member.team_id)).load_for_new_match()
        self.assertTrue(assets.matrix_index)
        panel.assets=assets
        panel.state=new_replay(assets)
        panel.state["phase"]="SELECTION"
        panel.state["opponent_six"]=list(assets.matrix_index)[:6]
        panel.actual_three.setText("マスカーニャ / アシレーヌ / ガブリアス")
        panel.actual_lead.setText("マスカーニャ")
        with patch.object(panel,"_start_prep_auto") as launch:
            panel._confirm_selection()
            launch.assert_called_once()
        self.assertEqual(panel.state["phase"],"PREP")
        self.assertEqual(panel.state["prep_status"],"PENDING")

    def test_90s_preview_allows_55s_selection_then_32s_prep(self):
        from champions_assist.match_replay_dialog import prep_budget_before_battle
        self.assertEqual(prep_budget_before_battle(100.0,now=100.0),40)
        self.assertEqual(prep_budget_before_battle(100.0,now=155.0),32)
        self.assertEqual(prep_budget_before_battle(100.0,now=160.0),27)
        self.assertEqual(prep_budget_before_battle(100.0,now=188.0),0)
        self.assertEqual(prep_budget_before_battle(None,now=900.0),40)

    def test_early_prep_launches_while_actual_selection_is_not_yet_confirmed(self):
        panel=self.window._match_panel
        panel.state={"phase":"SELECTION","matrix_provisional":True}
        panel._proposed_selection=(("ピカチュウ","ドドゲザン","ガブリアス"),"ピカチュウ")
        panel._proposed_prep_key=None
        panel._prep_thread=None
        panel._thread=None
        panel._pending_batch=None
        with patch.object(panel,"_start_prep_auto") as start:
            panel._cleanup()
            start.assert_called_once_with(provisional=True)
        self.assertEqual(panel.state["phase"],"SELECTION")

    def test_proposed_prep_is_not_promoted_before_actual_confirmation(self):
        panel=self.window._match_panel
        panel.state={"phase":"SELECTION","matrix_provisional":True}
        panel._proposed_prep_key=(("ピカチュウ","ドドゲザン","ガブリアス"),"ピカチュウ")
        panel._proposed_prep_payload=None
        panel._prep_cancel_event.clear()
        fake={"state":{"phase":"PREP","prep_status":"STRUCTURE_COMPLETE",
                       "selected_three":["ピカチュウ","ドドゲザン","ガブリアス"],
                       "lead":"ピカチュウ"},
              "report":{"status":"PREP_PROVISIONAL","matchups_evaluated":18}}
        panel._on_prep_result(fake)
        self.assertIs(panel._proposed_prep_payload,fake)
        self.assertEqual(panel.state["phase"],"SELECTION")
        self.assertEqual(panel.entries[-1]["status"],"PROVISIONAL_PREP_STORED")

    def test_actual_selection_can_consume_matching_early_prep(self):
        from champions_assist.match_replay import new_replay, confirm_actual_selection
        from champions_assist.preload import MasterPreloader
        panel=self.window._match_panel
        assets=MasterPreloader(
            MASTER_DIR,
            team_source=MASTER_DIR / "Pokemon_Champions_MC_team_2026-10-03.csv",
        ).load_for_new_match()
        panel.assets=assets
        panel.state=new_replay(assets)
        panel.state["phase"]="SELECTION"
        panel.state["opponent_six"]=list(assets.matrix_index)[:6]
        own=["マスカーニャ","アシレーヌ","ガブリアス"]
        preview=confirm_actual_selection(panel.state,assets,own,own[0])
        preview["prep_status"]="STRUCTURE_COMPLETE"
        panel._proposed_prep_key=(tuple(own),own[0])
        panel._proposed_prep_payload={
            "state":preview,
            "report":{"status":"PREP_STRUCTURE_OK","matchups_evaluated":18}}
        panel.actual_three.setText(" / ".join(own))
        panel.actual_lead.setText(own[0])
        with patch.object(panel,"_start_prep_auto") as launch:
            panel._confirm_selection()
            launch.assert_not_called()
        self.assertEqual(panel.state["phase"],"PREP")
        self.assertEqual(panel.state["prep_status"],"STRUCTURE_COMPLETE")

    def test_prep_interruption_prioritizes_battle_batch_without_waiting(self):
        panel=self.window._match_panel
        panel.state={"phase":"PREP","prep_status":"RUNNING"}
        panel._auto_running=True
        panel._prep_thread=object()
        panel._prep_cancel_event.clear()
        panel._prep_timer.start(40000)
        image=np.zeros((100,160,3),dtype=np.uint8)
        try:
            with patch.object(panel,"_send_pending") as queued:
                panel._queue_batch([image],screen_label="TURN_ACTION")
                queued.assert_called_once()
                self.assertTrue(panel._prep_cancel_event.is_set())
                self.assertEqual(panel.state["prep_status"],"INTERRUPTED_BY_NEW_BATTLE_SCREEN")
            old={"state":{"phase":"PREP","prep_status":"STRUCTURE_COMPLETE"},
                 "report":{"status":"PREP_STRUCTURE_OK"}}
            panel._on_prep_result(old)
            self.assertEqual(panel.state["prep_status"],"INTERRUPTED_BY_NEW_BATTLE_SCREEN")
        finally:
            panel._prep_thread=None
            panel._prep_timer.stop()
            panel._pending_batch=None
            panel._stop_auto()

    def test_unclassified_fallback_waits_for_verified_battle_before_prep_interrupt(self):
        panel=self.window._match_panel
        panel.state={"phase":"PREP","prep_status":"RUNNING"}
        panel._auto_running=True
        panel._prep_thread=object()
        panel._prep_cancel_event.clear()
        image=np.zeros((100,160,3),dtype=np.uint8)
        try:
            with patch.object(panel,"_send_pending") as queued:
                panel._queue_batch([image],screen_label=None)
                queued.assert_called_once()
            self.assertFalse(panel._prep_cancel_event.is_set())
            self.assertEqual(panel.state["prep_status"],"RUNNING")
            # An actual image observation (not the fallback scene delta)
            # has now confirmed an opponent's turn.
            panel._pending_batch=None
            panel._active_job="batch"
            panel._completed({
                "state":{"phase":"BATTLE","prep_status":"INTERRUPTED_BY_BATTLE_SCREEN"},
                "report":{"status":"OBSERVED","recommendation":""}})
            self.assertTrue(panel._prep_cancel_event.is_set())
            self.assertEqual(panel.state["phase"],"BATTLE")
        finally:
            panel._prep_thread=None
            panel._pending_batch=None
            panel._stop_auto()

    def test_prep_timeout_discards_late_result(self):
        panel=self.window._match_panel
        panel.state={"phase":"PREP","prep_status":"RUNNING"}
        panel._prep_thread=object()
        panel._prep_cancel_event.clear()
        try:
            panel._prep_deadline()
            self.assertEqual(panel.state["prep_status"],"DEADLINE_EXCEEDED")
            panel._on_prep_result({
                "state":{"phase":"PREP","prep_status":"STRUCTURE_COMPLETE"},
                "report":{"status":"PREP_STRUCTURE_OK"}})
            self.assertEqual(panel.state["prep_status"],"DEADLINE_EXCEEDED")
        finally:
            panel._prep_thread=None

    def test_local_classifier_triggers_on_two_stable_selection_frames(self):
        from champions_assist.screen_classifier import ScreenPrediction
        panel=self.window._match_panel
        panel.state={"phase":"MATCHMAKING","matrix_compatible":False}
        panel._auto_running=True
        frame=np.zeros((100,160,3),dtype=np.uint8)
        results=iter(["MATCHMAKING","TEAM_SELECTION","TEAM_SELECTION"])
        class LocalModel:
            def predict(self,image):
                return ScreenPrediction(next(results),0.99,0.95,3.5)
        panel._screen_model=LocalModel()
        panel._screen_model_status="MODEL_READY_CPU"
        with patch("champions_assist.match_replay_dialog.capture_for_chatgpt",return_value=frame), \
             patch.object(panel,"_queue_batch") as queue:
            panel._capture_next()
            panel._capture_next()
            queue.assert_not_called()
            panel._capture_next()
            queue.assert_called_once()
        self.assertIn("TEAM_SELECTION",panel.local_classifier_label.text())
        self.assertIn("ONNX_CPU",panel.local_classifier_label.text())
        panel._stop_auto()

    def test_live_training_capture_is_off_by_default_and_uses_existing_frame(self):
        from champions_assist.background_training_capture import CaptureStats
        panel=self.window._match_panel
        self.assertFalse(panel.training_capture_check.isChecked())
        self.assertIsNone(panel._background_capture)
        panel.state={"phase":"MATCHMAKING","matrix_compatible":False}
        panel._auto_running=True
        frame=np.full((110,180,3),35,dtype=np.uint8)
        with patch("champions_assist.match_replay_dialog.BackgroundTrainingCapture") as factory, \
             patch("champions_assist.match_replay_dialog.capture_for_chatgpt",
                   return_value=frame) as capture, \
             patch.object(panel,"_queue_batch") as queue:
            worker=factory.return_value
            worker.stats.return_value=CaptureStats(0,0,0,None,True)
            panel._capture_next()
            capture.assert_called_once()
            factory.assert_not_called()
            panel.training_capture_check.setChecked(True)
            factory.assert_called_once()
            worker.start.assert_called_once()
            self.assertTrue(panel._storage_status_timer.isActive())
            panel._capture_next()
            self.assertEqual(capture.call_count,2)
            worker.offer.assert_called_once_with(frame)
            # The AI decision route is unchanged, not a new API request.
            queue.assert_called_once()
            panel.training_capture_check.setChecked(False)
            worker.stop.assert_called_once_with(wait=False)
            self.assertFalse(panel._storage_status_timer.isActive())
            self.assertIsNone(panel._background_capture)
        panel._stop_auto()

    def test_live_training_capture_failure_only_changes_storage_status(self):
        from champions_assist.background_training_capture import CaptureStats
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE","matrix_compatible":False}
        panel._auto_running=True
        with patch("champions_assist.match_replay_dialog.BackgroundTrainingCapture") as factory:
            worker=factory.return_value
            worker.stats.return_value=CaptureStats(4,1,0,None,True)
            panel.training_capture_check.setChecked(True)
            worker.stats.return_value=CaptureStats(
                4,1,0,"STAGING_QUOTA_REACHED",False)
            panel._refresh_training_capture_status()
            self.assertIn("STAGING_QUOTA_REACHED",panel.training_capture_status.text())
            worker.stop.assert_called_once_with(wait=False)
            self.assertTrue(panel._auto_running)
            self.assertIsNone(panel._background_capture)
        panel._stop_auto()

    def test_prep_auth_error_does_not_leave_running_forever(self):
        panel=self.window._match_panel
        panel.state={"phase":"PREP","prep_status":"RUNNING"}
        panel._prep_cancel_event.clear()
        panel._on_prep_result({
            "state":{"phase":"PREP","prep_status":"RUNNING"},
            "report":{"status":"ERROR","reason":"SignInError"}})
        self.assertEqual(panel.state["prep_status"],"ERROR")
        self.assertEqual(panel.entries[-1]["status"],"PREP_ERROR")

    def test_superseded_api_response_does_not_revert_battle_state(self):
        panel=self.window._match_panel
        panel._auto_running=True
        panel._active_job="batch"
        panel.state={"phase":"BATTLE","our_active":"アシレーヌ"}
        panel._pending_batch=[np.zeros((16,20,3),dtype=np.uint8)]
        panel._pending_screen_label="SWITCH_SELECTION"
        panel._completed({
            "state":{"phase":"BATTLE","our_active":"マスカーニャ"},
            "report":{"status":"ADVICE","screen":"MOVE_MENU",
                      "recommendation":"はたきおとす。"}})
        self.assertEqual(panel.entries[-1]["status"],"SUPERSEDED")
        self.assertEqual(panel.state["our_active"],"アシレーヌ")
        self.assertEqual(panel.entries[-1]["recommendation"],"")
        panel._pending_batch=None
        panel._stop_auto()

    def test_selection_known_preview_duplicate_is_not_queued_again(self):
        panel=self.window._match_panel
        panel._auto_running=True
        panel.state={"phase":"MATCHMAKING","matrix_provisional":True}
        panel._thread=object()  # an AI selection request is still running
        panel._active_job="batch"
        panel._inflight_screen_label="TEAM_SELECTION"
        panel._pending_batch=None
        frame=np.zeros((80,100,3),dtype=np.uint8)
        try:
            with patch.object(panel,"_send_pending") as enqueue:
                panel._queue_batch([frame],screen_label="TEAM_SELECTION")
                panel._queue_batch([frame],screen_label=None)
                enqueue.assert_not_called()
            self.assertIsNone(panel._pending_batch)
        finally:
            panel._thread=None
            panel._pending_batch=None
            panel._stop_auto()

    def test_new_definite_selection_can_supersede_old_unknown_observation(self):
        panel=self.window._match_panel
        panel._auto_running=True
        panel.state={"phase":"MATCHMAKING","matrix_provisional":True}
        panel._thread=object()
        panel._active_job="batch"
        panel._inflight_screen_label=None
        panel._pending_batch=None
        try:
            with patch.object(panel,"_send_pending") as enqueue:
                panel._queue_batch(
                    [np.zeros((80,100,3),dtype=np.uint8)],
                    screen_label="TEAM_SELECTION")
                enqueue.assert_called_once()
            self.assertIsNotNone(panel._pending_batch)
            self.assertEqual(panel._pending_screen_label,"TEAM_SELECTION")
        finally:
            panel._thread=None
            panel._pending_batch=None
            panel._stop_auto()

    def test_completed_legal_selection_survives_duplicate_pending_animation(self):
        panel=self.window._match_panel
        panel._auto_running=True
        panel._active_job="batch"
        panel._inflight_screen_label="TEAM_SELECTION"
        panel.state={"phase":"MATCHMAKING","matrix_provisional":True}
        panel._pending_batch=[np.zeros((100,160,3),dtype=np.uint8)]
        panel._pending_screen_label=None  # unclassified animation/recheck
        three=["ピカチュウ","ドドゲザン","ガブリアス"]
        panel._completed({
            "state":{"phase":"SELECTION","matrix_provisional":True,
                     "selected_three":three,"lead":three[0]},
            "report":{"status":"PROVISIONAL_ADVICE","screen":"TEAM_SELECTION","selected_three":three,
                      "lead":three[0],"recommendation":"選出：ピカチュウ",
                      "live_reasoning_effort":"low"}})
        try:
            self.assertEqual(panel.entries[-1]["status"],"PROVISIONAL_ADVICE")
            self.assertEqual(panel.state["selected_three"],three)
            self.assertIsNone(panel._pending_batch)
            self.assertEqual(panel.actual_three.text()," / ".join(three))
            self.assertIn("low",panel.ai_status_label.text())
        finally:
            panel._pending_batch=None
            panel._stop_auto()

    def test_unconfirmed_preview_does_not_mislabel_matchmaking_as_current_screen(self):
        panel=self.window._match_panel
        panel.state={"phase":"MATCHMAKING","matrix_provisional":True}
        panel._auto_running=True
        unknown={"status":"AI_REQUEST_QUEUED","screen_label_hint":None}
        panel._render(unknown)
        self.assertEqual(panel.state["phase"],"MATCHMAKING")
        self.assertIn("画面判定中",self.window.header_labels["phase"].text())
        self.assertIn("未確定",self.window.header_labels["phase"].text())
        labelled={"status":"AI_REQUEST_QUEUED","screen_label_hint":"TEAM_SELECTION"}
        panel._render(labelled)
        self.assertIn("SELECTION候補",self.window.header_labels["phase"].text())
        self.assertEqual(panel.state["phase"],"MATCHMAKING")
        self.assertFalse(panel.state.get("opponent_six"))
        self.assertFalse(panel.state.get("selected_three"))

    def test_readtimeout_marks_stale_matchmaking_without_forging_selection(self):
        panel=self.window._match_panel
        panel.state={"phase":"MATCHMAKING","matrix_provisional":True}
        panel._active_job="batch"
        panel._auto_running=True
        payload={"state":{"phase":"MATCHMAKING","matrix_provisional":True},
                 "report":{"status":"ERROR","reason":"ReadTimeout",
                           "elapsed_ms":18002}}
        panel._completed(payload)
        self.assertEqual(panel.state["phase"],"MATCHMAKING")
        self.assertTrue(panel.entries[-1]["phase_recognition_pending"])
        self.assertIn("未更新",self.window.header_labels["phase"].text())
        self.assertIn("AI通信タイムアウト",self.window.recommendation_label.text())
        self.assertTrue(panel._ai_last_status.startswith("AI画像認識タイムアウト"))

    def test_ai_dispatch_attempt_and_response_are_visible_in_main_header(self):
        panel=self.window._match_panel
        panel.state={"phase":"MATCHMAKING","matrix_compatible":False}
        panel.assets=object()
        panel._auto_running=True
        panel._pending_batch=[np.zeros((80,100,3),dtype=np.uint8)]
        with patch("champions_assist.match_replay_dialog.QThread") as mock_thread, \
             patch("champions_assist.match_replay_dialog._ReplayWorker") as mock_worker:
            panel._send_pending()
            self.assertEqual(panel.entries[-1]["status"],"AI_REQUEST_QUEUED")
            self.assertEqual(panel.entries[-1]["image_count"],1)
            self.assertIn("処理を起動中",panel.ai_status_label.text())
            self.assertEqual(self.window.header_labels["ai"].text(),panel.ai_status_label.text())
            mock_thread.return_value.start.assert_called_once()
            mock_worker.return_value.progress.connect.assert_called_once()
        panel._on_ai_progress("AI画像送信処理中（結果待ち）")
        self.assertIn("画像送信処理中",self.window.header_labels["ai"].text())
        panel._completed({"state":{"phase":"SELECTION","matrix_compatible":False},
                          "report":{"status":"TEST_ONLY","recommendation":"選出：ピカチュウ"}})
        self.assertIn("応答受信",self.window.header_labels["ai"].text())
        self.assertEqual(panel.entries[-1]["status"],"TEST_ONLY")
        panel._thread=None
        panel._worker=None
        panel._stop_auto()

    def test_legacy_controller_refresh_never_clobbers_live_recommendation(self):
        panel = self.window._match_panel
        panel.state = {"phase": "BATTLE", "matrix_compatible": False}
        panel._render({"status": "TEST_ONLY", "recommendation": "10まんボルト。"})
        self.controller.refresh()
        self.assertIn("10まんボルト", self.window.recommendation_label.text())


if __name__ == "__main__":
    unittest.main()
