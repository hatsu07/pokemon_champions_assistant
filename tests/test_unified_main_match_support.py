"""GUI regression: one observed match drives main advice/state and SQLite history."""
from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QInputDialog, QMessageBox

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

    def test_matchmaking_does_not_poll_static_image_every_twelve_seconds(self):
        from champions_assist.turn_capture import AutomaticVisualProbe
        panel=self.window._match_panel
        image=np.zeros((80,160,3),dtype=np.uint8)
        # The first frame is still inspected for an already visible preview,
        # but a static matchmaking screen must not burn repeated API calls.
        self.assertTrue(panel._automatic_probe.observe(image,"MATCHMAKING"))
        self.assertFalse(panel._automatic_probe.observe(image,"MATCHMAKING"))
        self.assertEqual(panel._automatic_probe.matchmaking_recheck,float("inf"))

    def test_selection_display_precedes_prep_work(self):
        from copy import deepcopy
        from tests.test_prep_cache import loaded_assets, chosen_state
        from tests.test_match_replay import OWN
        temp,assets=loaded_assets()
        self.addCleanup(temp.cleanup)
        panel=self.window._match_panel
        preview=chosen_state(assets)
        preview.update(phase="SELECTION",selected_three=[],lead=None,
                       prep=None,prep_status="NOT_STARTED")
        panel.assets=assets
        panel.state=deepcopy(preview)
        panel._auto_running=True
        panel._active_job="batch"
        panel._cancel_event.clear()
        panel._screen_model=object()
        panel._last_local_class="TEAM_SELECTION"
        order=[]
        original_render=panel._render
        original_prefill=panel._prefill_cached_prep
        def render(report):
            if report.get("status")=="PROVISIONAL_ADVICE":
                order.append("SELECTION_DISPLAYED")
            return original_render(report)
        def prefill(*,roles=None):
            order.append("PREP_STARTED")
            return original_prefill(roles=roles)
        report={"status":"PROVISIONAL_ADVICE","screen":"TEAM_SELECTION",
                "selected_three":list(OWN[:3]),"lead":OWN[0],
                "recommendation":"選出： "+" / ".join(OWN[:3])}
        with patch.object(panel,"_render",side_effect=render),patch.object(
                panel,"_prefill_cached_prep",side_effect=prefill):
            panel._completed({"state":deepcopy(preview),"report":report})
        self.assertEqual(order[:2],["SELECTION_DISPLAYED","PREP_STARTED"])
        self.assertEqual(panel.state["phase"],"PREP")
        # The cached PREP rerender must not erase the unconfirmed status.
        self.assertIn("初手（提案）：",panel.selection_banner.text())
        self.assertIn("実選出未確認",panel.selection_banner.text())

    def test_selection_reply_without_new_request_is_preserved_as_unconfirmed(self):
        from copy import deepcopy
        from tests.test_prep_cache import loaded_assets, chosen_state
        from tests.test_match_replay import OWN
        temp,assets=loaded_assets()
        self.addCleanup(temp.cleanup)
        panel=self.window._match_panel
        preview=chosen_state(assets)
        preview.update(phase="MATCHMAKING",selected_three=[],lead=None)
        panel.state=deepcopy(preview)
        panel.assets=assets
        panel._auto_running=True
        panel._active_job="batch"
        panel._cancel_event.clear()
        panel._screen_model=object()
        panel._last_local_class="OTHER"
        report={"status":"PROVISIONAL_ADVICE","screen":"TEAM_SELECTION",
                "selected_three":list(OWN[:3]),"lead":OWN[0],
                "recommendation":"選出： "+" / ".join(OWN[:3])}
        result_state=deepcopy(preview)
        result_state["phase"]="SELECTION"  # Real reducer completed vision.
        with patch.object(panel,"_render") as render:
            panel._completed({"state":result_state,"report":report})
        displayed=render.call_args.args[0]
        self.assertEqual(displayed["status"],"PROVISIONAL_ADVICE")
        self.assertTrue(displayed["selection_screen_unconfirmed"])
        self.assertIn("画面の継続未確認",displayed["recommendation"])
        # The response is retained for review, not treated as a playable
        # selection or used to start PREP on a departed screen.
        self.assertEqual(panel.state["phase"],"SELECTION")
        self.assertIsNone(panel._proposed_selection)
        self.assertTrue(any(x["event"]=="SELECTION_RESULT_SCREEN_CHANGED"
                            for x in panel._timing_trace))

    def test_newer_action_request_supersedes_unanswered_selection(self):
        from copy import deepcopy
        from tests.test_prep_cache import loaded_assets, chosen_state
        from tests.test_match_replay import OWN
        temp,assets=loaded_assets()
        self.addCleanup(temp.cleanup)
        panel=self.window._match_panel
        preview=chosen_state(assets)
        preview.update(phase="SELECTION",selected_three=[],lead=None,
                       prep=None,prep_status="NOT_STARTED")
        panel.assets=assets
        panel.state=deepcopy(preview)
        panel._auto_running=True
        panel._active_job="batch"
        panel._cancel_event.clear()
        panel._pending_batch=[np.zeros((80,160,3),dtype=np.uint8)]
        panel._pending_screen_label="TURN_ACTION"
        report={"status":"PROVISIONAL_ADVICE","screen":"TEAM_SELECTION",
                "selected_three":list(OWN[:3]),"lead":OWN[0]}
        panel._completed({"state":deepcopy(preview),"report":report})
        self.assertEqual(panel.state["phase"],"SELECTION")
        self.assertIsNone(panel._proposed_selection)
        self.assertIsNotNone(panel._pending_batch)
        self.assertEqual(panel._pending_screen_label,"TURN_ACTION")
        self.assertTrue(any(row.get("event")=="STALE_SELECTION_ADVICE_DROPPED"
                            for row in panel._timing_trace))

    def test_turn_clock_starts_from_local_screen_not_model_completion(self):
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE"}
        panel._auto_running=True
        with patch.object(panel,"_send_pending"):
            panel._queue_batch([np.zeros((80,160,3),dtype=np.uint8)],
                               screen_label="TURN_ACTION")
            started=panel._turn_screen_seen_at
            panel._queue_batch([np.zeros((80,160,3),dtype=np.uint8)],
                               screen_label="TURN_ACTION")
        self.assertIsNotNone(started)
        self.assertEqual(panel._turn_screen_seen_at,started)
        self.assertTrue(any(row.get("event")=="TURN_SCREEN_OBSERVED"
                            for row in panel._timing_trace))

    def test_new_turn_requires_event_then_immediately_restarts_local_clock(self):
        from champions_assist.screen_classifier import ScreenPrediction
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE"}
        panel._auto_running=True
        panel._turn_screen_seen_at=None
        panel._turn_action_sent=False
        panel._turn_event_after_action=False
        frame=np.zeros((80,160,3),dtype=np.uint8)
        # First command starts immediately, not on the second classifier frame.
        def prediction(label):
            return ScreenPrediction(label,0.99,0.95,2.0)
        sequence=iter([prediction(x) for x in (
            "TURN_ACTION","TURN_ACTION","OTHER","BATTLE_EVENT",
            "OTHER","BATTLE_EVENT","TURN_ACTION")])
        class FakeModel:
            def predict(self,image):
                return next(sequence)
        panel._screen_model=FakeModel()
        with patch("champions_assist.match_replay_dialog.capture_for_chatgpt",
                   return_value=frame), \
             patch.object(panel,"_send_pending"):
            panel._capture_next()
            first=panel._turn_screen_seen_at
            self.assertIsNotNone(first)
            panel._turn_action_sent=True
            panel._capture_next()
            self.assertEqual(panel._turn_screen_seen_at,first)
            panel._capture_next()  # OTHER is normal animation, not a new turn
            self.assertEqual(panel._turn_screen_seen_at,first)
            panel._capture_next()  # First event releases turn after sent action
            self.assertTrue(panel._turn_event_after_action)
            self.assertIsNone(panel._turn_screen_seen_at)
            panel._capture_next()
            panel._capture_next()
            self.assertIsNone(panel._turn_screen_seen_at)
            panel._capture_next()  # Next first TURN_ACTION frame is a new turn
            self.assertIsNotNone(panel._turn_screen_seen_at)
            self.assertEqual(panel._turn_epoch,2)
            self.assertEqual(panel._pending_screen_label,"TURN_ACTION")
        panel._stop_auto()

    def test_later_turn_same_menu_is_queued_behind_old_inflight(self):
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE"}
        panel._auto_running=True
        panel._thread=object()
        panel._active_job="batch"
        panel._inflight_screen_label="TURN_ACTION"
        panel._active_turn_epoch=1
        # Next turn has been resolved, but the first menu detection has NOT
        # yet advanced the epoch. _queue_batch is responsible for doing so.
        panel._turn_epoch=1
        panel._turn_screen_seen_at=None
        panel._turn_action_sent=True
        panel._turn_event_after_action=True
        frame=np.zeros((80,160,3),dtype=np.uint8)
        try:
            with patch.object(panel,"_send_pending") as send:
                panel._queue_batch([frame],screen_label="TURN_ACTION")
                send.assert_called_once()
            self.assertEqual(panel._pending_screen_label,"TURN_ACTION")
            self.assertEqual(panel._turn_epoch,2)
            self.assertTrue(any(x["event"]=="NEW_TURN_QUEUED_BEHIND_INFLIGHT"
                                and x["turn_epoch"]==2
                                for x in panel._timing_trace))
        finally:
            panel._thread=None
            panel._active_job=None
            panel._inflight_screen_label=None
            panel._pending_batch=None
            panel._stop_auto()

    def test_same_turn_direct_queue_is_suppressed(self):
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE"}
        panel._auto_running=True
        frame=np.zeros((80,160,3),dtype=np.uint8)
        with patch.object(panel,"_send_pending") as send:
            panel._queue_batch([frame],screen_label="TURN_ACTION")
            first=panel._turn_screen_seen_at
            self.assertIsNotNone(first)
            panel._turn_action_sent=True
            panel._queue_batch([frame],screen_label="TURN_ACTION")
            self.assertEqual(panel._turn_screen_seen_at,first)
            self.assertEqual(send.call_count,1)
            self.assertTrue(any(x["event"]=="SAME_TURN_AI_REQUEST_SUPPRESSED"
                                for x in panel._timing_trace))

    def test_forced_switch_after_move_request_is_new_decision_and_clears_move(self):
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE","selected_three":[
            "ガブリアス","ギャラドス","ピカチュウ"]}
        panel._auto_running=True
        panel._last_action_request_screen="TURN_ACTION"
        panel._turn_action_sent=True
        panel._turn_event_after_action=False
        panel._last_action_text="じしん。"
        frame=np.zeros((100,160,3),dtype=np.uint8)
        with patch.object(panel,"_send_pending") as send:
            panel._queue_batch([frame],screen_label="SWITCH_SELECTION")
            send.assert_called_once()
        self.assertEqual(panel._last_action_text,"")
        self.assertEqual(panel._pending_screen_label,"SWITCH_SELECTION")
        self.assertNotIn("じしん",panel.action_banner.text())
        self.assertTrue(any(r.get("event")=="SWITCH_NEW_DECISION_WINDOW"
                            for r in panel._timing_trace))

    def test_soft_32s_turn_target_does_not_skip_new_ai_dispatch(self):
        from time import monotonic
        from tests.test_match_replay import fixture_assets
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE","_live_state_revision":0}
        panel.assets=fixture_assets()
        panel._auto_running=True
        panel._last_action_text="じしん。"
        frame=np.zeros((80,160,3),dtype=np.uint8)
        with patch.object(panel,"_send_pending"):
            panel._queue_batch([frame],screen_label="TURN_ACTION")
        panel._turn_screen_seen_at=monotonic()-33.0
        with patch("champions_assist.match_replay_dialog.QThread"),patch(
                "champions_assist.match_replay_dialog._ReplayWorker"):
            panel._send_pending()
        self.assertIsNotNone(panel._active_request_id)
        self.assertFalse(any(x["event"]=="TURN_BUDGET_EXHAUSTED"
                             for x in panel._timing_trace))
        panel._thread=None

    def test_30_second_target_allows_22_second_first_turn_advice(self):
        from time import monotonic
        from champions_assist.match_replay_dialog import (
            _TARGET_AI_TURN_SECONDS,_CONSERVATIVE_AI_TURN_SECONDS)
        self.assertEqual(_TARGET_AI_TURN_SECONDS,30.0)
        self.assertEqual(_CONSERVATIVE_AI_TURN_SECONDS,32.0)
        panel=self.window._match_panel
        panel.state={"phase":"PREP"}
        panel._auto_running=True
        panel._active_job="batch"
        panel._active_turn_epoch=1
        panel._turn_epoch=1
        panel._turn_screen_seen_at=monotonic()-22.8
        panel._turn_action_sent=True
        panel._turn_event_after_action=False
        with patch.object(panel,"_render") as render:
            panel._completed({"state":{"phase":"BATTLE"},
                              "report":{"status":"PROVISIONAL_ADVICE",
                                        "recommendation":"じしん。",
                                        "screen":"COMMAND_MENU"}})
        self.assertNotEqual(render.call_args.args[0]["status"],
                            "DEADLINE_EXCEEDED")
        self.assertEqual(render.call_args.args[0]["recommendation"],"じしん。")

    def test_same_turn_valid_late_advice_is_presented_with_warning(self):
        from time import monotonic
        panel=self.window._match_panel
        panel.state={"phase":"PREP"}
        panel._auto_running=True
        panel._active_job="batch"
        panel._active_turn_epoch=12
        panel._turn_epoch=12
        panel._turn_screen_seen_at=monotonic()-34.0
        panel._turn_action_sent=True
        panel._turn_event_after_action=False
        with patch.object(panel,"_render") as render:
            panel._completed({"state":{"phase":"BATTLE"},
                              "report":{"status":"PROVISIONAL_ADVICE",
                                        "recommendation":"じしん。",
                                        "screen":"COMMAND_MENU"}})
        result=render.call_args.args[0]
        self.assertEqual(result["status"],"PROVISIONAL_ADVICE")
        self.assertEqual(result["recommendation"],"じしん。")
        self.assertTrue(result["late_advice_presented"])
        self.assertIn("32秒超過",result["warning"])
        self.assertTrue(any(x["event"]=="LATE_TURN_ADVICE_PRESENTED"
                            for x in panel._timing_trace))

    def test_event_observed_before_ai_finishes_discards_stale_first_turn(self):
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE"}
        panel._auto_running=True
        panel._active_job="batch"
        panel._turn_epoch=1
        panel._active_turn_epoch=1
        panel._turn_action_sent=True
        panel._turn_screen_seen_at=None
        panel._turn_event_after_action=True
        panel._last_action_text="じしん。"
        with patch.object(panel,"_render") as render:
            panel._completed({"state":{"phase":"BATTLE"},
                             "report":{"status":"PROVISIONAL_ADVICE",
                                       "screen":"COMMAND_MENU",
                                       "recommendation":"じしん。"}})
        self.assertEqual(render.call_args.args[0]["status"],"SUPERSEDED")
        self.assertEqual(render.call_args.args[0]["recommendation"],"")
        self.assertTrue(any(x["event"]=="STALE_TURN_ADVICE_DROPPED"
                            for x in panel._timing_trace))

    def test_live_partial_selection_starts_known_only_prep_after_result(self):
        """Live UI, not just offline selection test, must start partial PREP."""
        from copy import deepcopy
        from tests.test_prep_cache import loaded_assets, chosen_state
        from tests.test_match_replay import OWN
        temp,assets=loaded_assets()
        self.addCleanup(temp.cleanup)
        selected_state=chosen_state(assets,partial=True)
        preview=deepcopy(selected_state)
        preview["phase"]="SELECTION"
        preview["selected_three"]=[]
        preview["lead"]=None
        preview["prep_status"]="VISUAL_PARTIAL_5_OF_6"
        preview["prep"]=None
        panel=self.window._match_panel
        panel.assets=assets
        panel.state=preview
        panel._active_job="batch"
        panel._auto_running=True
        panel._cancel_event.clear()
        incoming={
            "status":"PARTIAL_ADVICE","screen":"TEAM_SELECTION",
            "selected_three":list(OWN[:3]),"lead":OWN[0],
            "recommendation":"選出："+ " / ".join(OWN[:3])+" 初手："+OWN[0],
        }
        panel._completed({"state":deepcopy(preview),"report":incoming})
        self.assertEqual(panel.entries[-1]["status"],"PARTIAL_ADVICE")
        self.assertEqual(panel._proposed_selection,(OWN[:3],OWN[0]))
        self.assertEqual(panel.state["phase"],"PREP")
        self.assertTrue(panel.state["selection_assumed"])
        self.assertEqual(panel.state["prep_status"],"CACHE_INCOMPLETE")
        self.assertEqual(len(panel.state["prep"]["matchups_known"]),15)
        self.assertIsNone(panel.state["prep"]["preserve"])
        self.assertIn("選出（提案）：",self.window.recommendation_label.text())
        self.assertIn("初手（提案）：",panel.selection_banner.text())
        self.assertIn("実選出未確認",panel.selection_banner.text())
        self.assertTrue(panel.confirm_button.isHidden())
        with patch.object(panel,"_start_prep_auto") as prep:
            panel._cleanup()
        prep.assert_called_once()

    def test_full_selection_auto_adopt_prefills_eighteen_and_starts_prep(self):
        from copy import deepcopy
        from tests.test_prep_cache import loaded_assets, chosen_state
        from tests.test_match_replay import OWN, OPP
        temp, assets=loaded_assets()
        self.addCleanup(temp.cleanup)
        prepared=chosen_state(assets)
        preview=deepcopy(prepared)
        preview["phase"]="SELECTION"
        preview["selected_three"]=[]
        preview["lead"]=None
        preview["prep"]=None
        preview["prep_status"]="NOT_STARTED"
        panel=self.window._match_panel
        panel.assets=assets
        panel.state=preview
        panel._active_job="batch"
        panel._auto_running=True
        panel._cancel_event.clear()
        frame=np.zeros((80,160,3),dtype=np.uint8)
        panel._active_request_frame=frame
        incoming={
            "status":"PROVISIONAL_ADVICE","screen":"TEAM_SELECTION",
            "selected_three":list(OWN[:3]),"lead":OWN[0],
            "recommendation":"選出："+" / ".join(OWN[:3])+" 初手："+OWN[0],
        }
        panel._completed({"state":deepcopy(preview),"report":incoming})
        self.assertEqual(panel.state["phase"],"PREP")
        self.assertTrue(panel.state["selection_assumed"])
        self.assertEqual(panel.state["selected_three"],list(OWN[:3]))
        self.assertEqual(panel.state["prep_status"],"CACHE_INCOMPLETE")
        self.assertEqual(panel.state["prep"]["cached_matchups"],18)
        self.assertIn("初手（提案）：",panel.selection_banner.text())
        self.assertEqual(len(panel.state["prep"]["matchups_18"]),18)
        self.assertEqual(panel._automatic_probe._phase,"PREP")
        self.assertTrue(panel.confirm_button.isHidden())
        with patch.object(panel,"_start_prep_auto") as start:
            panel._cleanup()
            start.assert_called_once()
        self.assertEqual(panel._proposed_selection,(OWN[:3],OWN[0]))
        self.assertTrue(any(e.get("event")=="AUTO_SELECTION_ASSUMED"
                            for e in getattr(panel,"_timing_trace",[]))
                        or panel.state["selection_assumed"])

    def test_confirmed_auto_move_notice_retires_same_turn_ai_without_guessing_move(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from champions_assist.calibrated_screen_fields import ScreenFieldProfile
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE","_live_state_revision":0}
        panel._field_profile=replace(ScreenFieldProfile.from_user_samples_960x572(),
                                     validated_on_game_capture=True)
        panel._turn_epoch=4
        panel._active_turn_epoch=4
        panel._active_job="batch"
        panel._active_request_id="req-notice"
        old_worker=object()
        panel._thread=object()
        panel._worker=old_worker
        panel._last_action_text="じしん。"
        notice="サーバーに入力が届かなかったため\n技が自動で選ばれました"
        try:
            panel._observe_auto_move_notice(
                SimpleNamespace(roi="system_message",text=notice,confidence=.96),4)
            self.assertIn("req-notice",panel._retired_workers)
            self.assertIsNone(panel._active_request_id)
            self.assertTrue(panel._turn_action_sent)
            self.assertEqual(panel._last_action_text,"")
            self.assertIn(4,panel._auto_move_epochs)
            self.assertTrue(any(e["event"]=="AUTO_MOVE_SELECTED_INPUT_NOT_DELIVERED"
                                for e in panel._timing_trace))
        finally:
            panel._retired_workers.clear()
            panel._thread=None
            panel._worker=None
            panel._auto_move_epochs.clear()

    def test_low_confidence_auto_move_notice_does_not_retire_ai(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from champions_assist.calibrated_screen_fields import ScreenFieldProfile
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE"}
        panel._field_profile=replace(ScreenFieldProfile.from_user_samples_960x572(),
                                     validated_on_game_capture=True)
        panel._turn_epoch=3
        panel._active_turn_epoch=3
        panel._active_job="batch"
        panel._active_request_id="req-still-valid"
        panel._thread=object()
        panel._worker=object()
        try:
            panel._observe_auto_move_notice(SimpleNamespace(
                roi="system_message",text="サーバーに入力が届かなかったため\n技が自動で選ばれました",
                confidence=.72),3)
            self.assertEqual(panel._active_request_id,"req-still-valid")
            self.assertFalse(panel._auto_move_epochs)
        finally:
            panel._thread=None
            panel._worker=None
            panel._active_request_id=None

    def test_selection_roles_complete_full_prep_without_second_api_call(self):
        from copy import deepcopy
        from tests.test_prep_cache import loaded_assets, chosen_state
        from tests.test_match_replay import OWN, OPP, frame
        from champions_assist.match_replay import advance_observation
        temp,assets=loaded_assets()
        self.addCleanup(temp.cleanup)
        preview=chosen_state(assets)
        preview["phase"]="SELECTION"
        preview["selected_three"]=[]
        preview["lead"]=None
        preview["prep"]=None
        panel=self.window._match_panel
        panel.assets=assets
        panel.state=deepcopy(preview)
        panel._active_job="batch"
        panel._auto_running=True
        panel._cancel_event.clear()
        payload={
            "state":deepcopy(preview),
            "report":{
                "status":"PROVISIONAL_ADVICE","screen":"TEAM_SELECTION",
                "selected_three":list(OWN[:3]),"lead":OWN[0],
                "preserve":OWN[0],"stopper":OWN[1],"closer":OWN[2],
                "recommendation":"選出： "+" / ".join(OWN[:3])},
        }
        panel._completed(payload)
        self.assertEqual(panel.state["phase"],"PREP")
        self.assertEqual(panel.state["prep_status"],"CACHE_STRUCTURE_COMPLETE")
        self.assertEqual(len(panel.state["prep"]["matchups_18"]),18)
        self.assertEqual(panel.state["prep"]["win_roles_status"],"SELECTION_REUSED")
        self.assertEqual((panel.state["prep"]["preserve"],
                          panel.state["prep"]["stopper"],
                          panel.state["prep"]["closer"]),OWN[:3])
        with patch.object(panel,"_start_prep_auto") as model_call:
            panel._cleanup()
        model_call.assert_not_called()
        # The first visible turn is handled from that same tentative state:
        # no manual confirmation and no PREP API are required.
        updated,out=advance_observation(
            panel.state,assets,
            frame("MOVE_MENU",our_active=OWN[0],
                  opponent_active=OPP[0],
                  visible_moves=["じしん","まもる"]),
            lambda *args:{"action_kind":"MOVE","move":"じしん",
                          "switch_target":"","warning":""},
            elapsed_seconds=lambda:1.0)
        self.assertEqual(updated["phase"],"BATTLE")
        self.assertIn(out["status"],("ADVICE","PROVISIONAL_ADVICE","TEST_ONLY"))
        self.assertEqual(out["recommendation"],"じしん。")
        self.assertEqual(updated["observed_lead"],OWN[0])

    def test_selection_invalid_win_roles_never_forge_completed_prep(self):
        from copy import deepcopy
        from tests.test_prep_cache import loaded_assets, chosen_state
        from tests.test_match_replay import OWN
        temp,assets=loaded_assets()
        self.addCleanup(temp.cleanup)
        preview=chosen_state(assets)
        preview.update(phase="SELECTION",selected_three=[],lead=None,prep=None)
        panel=self.window._match_panel
        panel.assets=assets
        panel.state=deepcopy(preview)
        panel._active_job="batch"
        panel._auto_running=True
        panel._cancel_event.clear()
        report={"status":"PROVISIONAL_ADVICE","screen":"TEAM_SELECTION",
                "selected_three":list(OWN[:3]),"lead":OWN[0],
                "preserve":OWN[0],"stopper":"未登録", "closer":OWN[2]}
        panel._completed({"state":deepcopy(preview),"report":report})
        # Missing/invalid win roles MUST NOT complete a cached PREP.
        self.assertEqual(panel.state["prep_status"],"CACHE_INCOMPLETE")
        self.assertIsNone(panel.state["prep"]["preserve"])
        with patch.object(panel,"_start_prep_auto") as rescue:
            panel._cleanup()
        rescue.assert_called_once()

    def test_cached_partial_prep_is_preserved_on_actual_selection_confirmation(self):
        """Match actual selection with cached 5/6 PREP; do not regenerate."""
        from copy import deepcopy
        from tests.test_prep_cache import loaded_assets, chosen_state, roles
        from tests.test_match_replay import OWN
        from champions_assist.match_replay import run_replay_prep
        temp,assets=loaded_assets()
        self.addCleanup(temp.cleanup)
        prepared=chosen_state(assets,partial=True)
        with patch("champions_assist.match_replay._stream_json",return_value=roles()):
            cached,report=run_replay_prep(
                state=prepared,assets=assets,access_token="mock",model="mock",
                client_factory=lambda **kwargs:object())
        self.assertEqual(report["status"],"PREP_CACHE_KNOWN_ONLY")
        self.assertEqual(len(cached["prep"]["matchups_known"]),15)
        self.assertEqual(len(cached["prep"]["unknown_pairs"]),3)
        panel=self.window._match_panel
        panel.assets=assets
        panel.state=deepcopy(prepared)
        panel.state["phase"]="SELECTION"
        panel.state["selected_three"]=[]
        panel.state["lead"]=None
        panel.state["prep"]=None
        panel._proposed_selection=(OWN[:3],OWN[0])
        panel._proposed_prep_key=(OWN[:3],OWN[0])
        panel._on_prep_result({"state":cached,"report":report})
        self.assertEqual(panel.entries[-1]["status"],"PROVISIONAL_PREP_STORED")
        panel.actual_three.setText(" / ".join(OWN[:3]))
        panel.actual_lead.setText(OWN[0])
        with patch.object(panel,"_start_prep_auto") as unnecessary:
            panel._confirm_selection()
        unnecessary.assert_not_called()
        self.assertEqual(panel.state["prep_status"],"CACHE_KNOWN_ONLY")
        self.assertEqual(len(panel.state["prep"]["matchups_known"]),15)
        self.assertEqual(panel.entries[-1]["status"],"CONFIRMED")

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

    def test_busy_worker_defers_fallback_scene_transition_without_ai_queue(self):
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
                  patch.object(panel._automatic_probe,"observe",return_value=True) as probe,
                  patch.object(panel,"_queue_batch") as queued):
                panel._capture_next()
                probe.assert_called_once()
                queued.assert_not_called()
                # A new frame during an in-flight request must survive until
                # _cleanup dispatches it, instead of being consumed and lost.
                self.assertIsNotNone(panel._deferred_fallback_batch)
                self.assertTrue(np.array_equal(
                    panel._deferred_fallback_batch[-1],image))
                self.assertTrue(any(
                    row.get("event")=="FALLBACK_TRANSITION_DEFERRED"
                    for row in panel._timing_trace))
                with patch.object(panel,"_queue_batch") as dispatch:
                    panel._thread=None
                    panel._cleanup()
                    dispatch.assert_called_once()
                    self.assertIsNone(panel._deferred_fallback_batch)
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
        # Explicit Close hides the window while a blocking HTTP worker
        # finishes cooperatively; no unsafe QThread termination.
        self.assertFalse(self.window.isVisible())
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
        self.assertIn("初手（提案）：ピカチュウ",panel.selection_banner.text())
        self.assertIn("提案：",self.window.header_labels["selected"].text())
        self.assertIn("ピカチュウ",self.window.recommendation_label.text())
        panel._render({"status":"PREP_PROVISIONAL_RUNNING"})
        self.assertIn("ピカチュウ",self.window.recommendation_label.text())
        panel.state["phase"]="BATTLE"
        panel._render({"status":"PROVISIONAL_ADVICE",
                       "screen":"COMMAND_MENU",
                       "recommendation":"じしん。",
                       "action":{"kind":"MOVE","move":"じしん"}})
        self.assertIn("じしん",panel.action_banner.text())
        self.assertIn("じしん",self.window.recommendation_label.text())
        self.assertNotIn("技一覧",panel.action_banner.text())
        self.assertIn("対戦中",panel.phase_banner.text())
        self.assertIn("時間：",panel.deadline_banner.text())

    def test_command_menu_direct_switch_is_primary_action(self):
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE","selected_three":["ピカチュウ","ギャラドス","ガブリアス"],
                     "lead":"ピカチュウ","matrix_provisional":True,
                     "prep_status":"CACHE_STRUCTURE_COMPLETE"}
        panel._render({"status":"PROVISIONAL_ADVICE","screen":"COMMAND_MENU",
                       "recommendation":"ガブリアスに交代。",
                       "action":{"kind":"SWITCH","target":"ガブリアス"}})
        self.assertIn("ガブリアスに交代",panel.action_banner.text())
        self.assertNotIn("たたかう",panel.action_banner.text())

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

    def test_model_free_start_warns_and_limits_identical_images(self):
        from types import SimpleNamespace
        panel=self.window._match_panel
        panel.state={"phase":"MATCHMAKING","matrix_compatible":False}
        panel.assets=SimpleNamespace(sha256={str(i):str(i) for i in range(4)})
        panel.model="test-model"
        panel._screen_model=None
        panel._screen_model_status="MODEL_NOT_BUNDLED"
        panel.capture_config=CaptureConfiguration(
            backend="WINDOWS_NATIVE",selected_window_identity={"hwnd":7},
            game_viewport_normalized=(0,0,1,1))
        image=np.zeros((120,200,3),dtype=np.uint8)
        image[60:,100:]=200
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.Yes), \
             patch.object(QMessageBox,"warning",
                          return_value=QMessageBox.StandardButton.Yes) as warn, \
             patch("champions_assist.match_replay_dialog.capture_for_chatgpt",
                   return_value=image) as cap, \
             patch.object(panel,"_queue_batch") as queue:
            panel._start_auto()
            panel._poll_timer.stop()
            self.assertEqual(warn.call_count,1)
            self.assertIn("モデルなし",panel.ai_status_label.text())
            self.assertIsNotNone(panel.capture_preview.pixmap())
            queue.assert_called_once()
            for _ in range(3):
                panel._capture_next()
            queue.assert_called_once()
        panel._stop_auto()

    def test_missing_preload_warns_and_does_not_start_ai(self):
        panel=self.window._match_panel
        panel.state=None
        panel.assets=None
        with patch("champions_assist.match_replay_dialog.QMessageBox.warning") as warn, \
             patch.object(panel,"_capture_next") as capture:
            panel._start_auto()
        warn.assert_called_once()
        self.assertIn("4ファイル",warn.call_args.args[1])
        capture.assert_not_called()
        self.assertFalse(panel._auto_running)

    def test_missing_classifier_decline_keeps_auto_stopped(self):
        from types import SimpleNamespace
        panel=self.window._match_panel
        panel.state={"phase":"MATCHMAKING"}
        panel.assets=SimpleNamespace(sha256={str(i):str(i) for i in range(4)})
        panel.model="test-model"
        panel._screen_model=None
        panel._screen_model_status="MODEL_NOT_BUNDLED"
        panel.capture_config=CaptureConfiguration(
            backend="WINDOWS_NATIVE",selected_window_identity={"hwnd":7},
            game_viewport_normalized=(0,0,1,1))
        with patch("champions_assist.match_replay_dialog.QMessageBox.warning",
                   return_value=QMessageBox.StandardButton.No) as warn, \
             patch.object(panel,"_capture_next") as capture:
            panel._start_auto()
        warn.assert_called_once()
        capture.assert_not_called()
        self.assertFalse(panel._auto_running)

    def test_local_ocr_snapshot_is_frozen_before_urgent_action_without_wait(self):
        from types import SimpleNamespace
        from champions_assist.local_event_worker import LocalFieldRead
        panel=self.window._match_panel
        panel._local_battle_tracker.clear()
        record=LocalFieldRead(
            field="screen_text.battle_message",value="まひになった",
            frame_id="event-1",timestamp=1.0,confidence=0.80,
            roi="battle_message",source="ROI_OCR_UNVERIFIED",
            text="まひになった")
        stopped=[]
        reader=SimpleNamespace(
            drain=lambda:(SimpleNamespace(error=None,observations=(record,)),),
            stop=lambda:stopped.append(True),
            reason="READY",diagnostic_counts=lambda:{
                "ocr_jobs_enqueued":1,"ocr_jobs_completed":1,
                "ocr_queue_pending":0,"ocr_results_pending":0},
            overflow=0,_queue=SimpleNamespace(qsize=lambda:0))
        panel._local_event_reader=reader
        result=panel._freeze_local_events_for_action("TURN_ACTION")
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]["raw_text"],"まひになった")
        self.assertEqual(result[0]["certainty"],"CANDIDATE")
        summary=[x for x in panel._timing_trace
                 if x["event"]=="LOCAL_OCR_TURN_SUMMARY"][-1]
        self.assertEqual(summary["ocr_jobs_enqueued"],1)
        self.assertEqual(summary["ocr_candidates_sent"],1)
        self.assertEqual(summary["ocr_status"],"READY")
        # The asynchronous reader now continues through the command
        # screen so late HP/name results are not discarded.
        self.assertEqual(stopped,[])
        self.assertIs(panel._local_event_reader,reader)
        self.assertEqual(panel._drain_event_candidates()[0]["raw_text"],
                         "まひになった")

    def test_battle_event_frames_are_buffered_until_next_action(self):
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE"}
        first=np.zeros((100,160,3),dtype=np.uint8)
        second=np.full_like(first,180)
        command=np.full_like(first,255)
        panel._event_frames.clear()
        with patch.object(panel,"_queue_batch") as send:
            panel._remember_event_frame(first)
            panel._remember_event_frame(first.copy())
            panel._remember_event_frame(second)
            send.assert_not_called()
        self.assertEqual(len(panel._event_frames),2)
        frames=panel._frames_for_action(command)
        self.assertEqual(len(frames),3)
        self.assertTrue(np.array_equal(frames[0],first))
        self.assertTrue(np.array_equal(frames[1],second))
        self.assertTrue(np.array_equal(frames[2],command))
        self.assertEqual(panel._event_frames,[])
        self.assertTrue(any(row.get("event")=="BATTLE_EVENT_FRAMES_INCLUDED"
                            and row.get("image_count")==3
                            for row in panel._timing_trace))

    def test_text_first_omits_recognized_event_but_keeps_current_menu(self):
        from dataclasses import replace
        from champions_assist.calibrated_screen_fields import ScreenFieldProfile
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE"}
        panel._event_frames.clear()
        panel._event_frame_ids.clear()
        panel._field_profile=replace(
            ScreenFieldProfile.from_user_samples_960x572(),
            validated_on_game_capture=True)
        panel._last_local_ocr_status="READY"
        panel._last_local_ocr_stats={
            "ocr_jobs_enqueued":1,"ocr_jobs_completed":1,
            "ocr_queue_pending":0,"overflow":0,
            "ocr_results_dropped":0,"ocr_errors":0}
        frame=np.full((80,160,3),150,dtype=np.uint8)
        panel._last_capture_seq=31
        panel._remember_event_frame(frame)
        text=[{"frame_id":"capture-31","roi":"battle_message",
               "raw_text":"相手のこうげきが上がった","confidence":0.98,
               "certainty":"CANDIDATE","game_validated":False}]
        command=np.zeros_like(frame)
        result=panel._frames_for_action(command,text)
        self.assertEqual(len(result),1)
        self.assertTrue(np.array_equal(result[0],command))
        self.assertTrue(any(row["event"]=="EVENT_IMAGE_TEXT_FIRST_DECISION"
                            and row.get("image_count")==1
                            for row in panel._timing_trace))

    def test_event_image_kept_for_nonvalidated_ocr_even_with_high_confidence(self):
        from champions_assist.calibrated_screen_fields import ScreenFieldProfile
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE"}
        panel._event_frames.clear()
        panel._event_frame_ids.clear()
        panel._field_profile=ScreenFieldProfile.from_user_samples_960x572()
        panel._last_local_ocr_status="READY"
        panel._last_local_ocr_stats={"ocr_jobs_enqueued":1,
            "ocr_jobs_completed":1,"ocr_queue_pending":0}
        frame=np.full((80,160,3),150,dtype=np.uint8)
        panel._last_capture_seq=55
        panel._remember_event_frame(frame)
        obs=[{"frame_id":"capture-55","roi":"battle_message",
              "raw_text":"ピカチュウはまひした","confidence":0.99,
              "certainty":"CANDIDATE","game_validated":False}]
        self.assertEqual(len(panel._frames_for_action(np.zeros_like(frame),obs)),2)

    def test_loaded_classifier_never_sends_non_action_or_unknown(self):
        from champions_assist.screen_classifier import ScreenPrediction
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE"}
        panel._auto_running=True
        frames=iter(["BATTLE_EVENT","BATTLE_EVENT","OTHER","OTHER",
                     "UNKNOWN","UNKNOWN","UNKNOWN","TURN_ACTION","TURN_ACTION"])
        class FakeModel:
            def predict(self,image):
                return ScreenPrediction(next(frames),0.99,0.95,2.0)
        panel._screen_model=FakeModel()
        frame=np.zeros((80,160,3),dtype=np.uint8)
        with patch("champions_assist.match_replay_dialog.capture_for_chatgpt",
                   return_value=frame), \
             patch.object(panel,"_queue_batch") as send:
            for _ in range(7):
                panel._capture_next()
            send.assert_not_called()
            panel._capture_next()
            send.assert_called_once()
            self.assertEqual(send.call_args.kwargs["screen_label"],"TURN_ACTION")
            first_clock=panel._turn_screen_seen_at
            self.assertIsNotNone(first_clock)
            panel._capture_next()
            send.assert_called_once()
            self.assertEqual(panel._turn_screen_seen_at,first_clock)
        panel._stop_auto()

    def test_model_free_fallback_does_not_spam_scene_changes(self):
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE"}
        panel._auto_running=True
        panel._screen_model=None
        frame=np.zeros((100,160,3),dtype=np.uint8)
        with patch("champions_assist.match_replay_dialog.capture_for_chatgpt",
                   return_value=frame), \
             patch.object(panel._automatic_probe,"observe",return_value=True), \
             patch.object(panel,"_queue_batch") as send:
            for _ in range(4):
                panel._capture_next()
            send.assert_called_once()
        panel._stop_auto()

    def test_prep_banner_shows_running_complete_and_incomplete(self):
        panel=self.window._match_panel
        panel.state={"phase":"PREP","selected_three":["A","B","C"],
                     "selection_assumed":True,
                     "prep_status":"CACHE_STRUCTURE_COMPLETE",
                     "visual_unknown_opponents":[],
                     "prep":{"cached_matchups":18,
                             "preserve":"A","stopper":"B","closer":"C"}}
        panel._refresh_live_status()
        self.assertIn("完了",panel.prep_banner.text())
        self.assertIn("18/18",panel.prep_banner.text())
        self.assertIn("3/3",panel.prep_banner.text())
        self.assertIn("未確認",panel.prep_banner.text())
        panel.state["prep_status"]="CACHE_INCOMPLETE"
        panel._refresh_live_status()
        self.assertIn("未完成",panel.prep_banner.text())
        panel._prep_thread=object()
        try:
            panel._prep_cancel_event.clear()
            panel._refresh_live_status()
            self.assertIn("処理中",panel.prep_banner.text())
        finally:
            panel._prep_thread=None

    def test_model_picker_selects_onnx_file_and_restores_path(self):
        panel=self.window._match_panel
        panel.store_root=self.root/"picker-local-settings"
        model=object()
        model_path=self.root/"five-class-model"/"champions_screen_classifier.onnx"
        model_path.parent.mkdir()
        model_path.write_bytes(b"fixture")
        with patch("champions_assist.match_replay_dialog.QFileDialog.getOpenFileName",
                   return_value=(str(model_path),"ONNXモデル (*.onnx)")), \
             patch("champions_assist.match_replay_dialog.load_bundled_classifier",
                   return_value=(model,"MODEL_READY_CPU")) as loader:
            panel._choose_screen_classifier()
            loaded,status=panel._load_selected_screen_classifier()
        self.assertIs(loaded,model)
        self.assertEqual(status,"MODEL_READY_CPU")
        self.assertIs(panel._screen_model,model)
        self.assertIn("6分類ONNX利用中",panel.local_classifier_label.text())
        stored=json.loads(
            (panel.store_root/"screen_classifier_choice.json").read_text())
        self.assertEqual(stored["model_path"],str(model_path.resolve()))
        self.assertIs(stored["trial_opt_in"],False)
        self.assertEqual(loader.call_count,2)
        self.assertEqual(loader.call_args.kwargs["model_path"],model_path.resolve())

    def test_trial_picker_warning_and_restart_warning(self):
        from types import SimpleNamespace
        panel=self.window._match_panel
        panel.store_root=self.root/"trial-local-settings"
        model=SimpleNamespace(experimental=True)
        model_path=self.root/"trial.onnx"
        model_path.write_bytes(b"fixture")
        panel._screen_model=None
        with patch("champions_assist.match_replay_dialog.QFileDialog.getOpenFileName",
                   return_value=(str(model_path),"ONNX (*.onnx)")), \
             patch("champions_assist.match_replay_dialog.load_bundled_classifier",
                   return_value=(model,"MODEL_TRIAL_CPU")), \
             patch("champions_assist.match_replay_dialog.QMessageBox.warning",
                   return_value=QMessageBox.StandardButton.No):
            panel._choose_screen_classifier()
        self.assertIsNone(panel._screen_model)
        self.assertFalse((panel.store_root/"screen_classifier_choice.json").exists())

        with patch("champions_assist.match_replay_dialog.QFileDialog.getOpenFileName",
                   return_value=(str(model_path),"ONNX (*.onnx)")), \
             patch("champions_assist.match_replay_dialog.load_bundled_classifier",
                   return_value=(model,"MODEL_TRIAL_CPU")), \
             patch("champions_assist.match_replay_dialog.QMessageBox.warning",
                   return_value=QMessageBox.StandardButton.Yes):
            panel._choose_screen_classifier()
        self.assertIs(panel._screen_model,model)
        self.assertIn("未検証・試験モード",panel.local_classifier_label.text())
        stored=json.loads(
            (panel.store_root/"screen_classifier_choice.json").read_text())
        self.assertTrue(stored["trial_opt_in"])

        panel.state={"phase":"MATCHMAKING"}
        panel.assets=SimpleNamespace(sha256={str(i):str(i) for i in range(4)})
        panel.model="test-model"
        panel.capture_config=CaptureConfiguration(
            backend="WINDOWS_NATIVE",selected_window_identity={"hwnd":7},
            game_viewport_normalized=(0,0,1,1))
        with patch("champions_assist.match_replay_dialog.QMessageBox.warning",
                   return_value=QMessageBox.StandardButton.No) as warn, \
             patch.object(panel,"_capture_next") as capture:
            panel._start_auto()
        warn.assert_called_once()
        self.assertFalse(panel._auto_running)
        capture.assert_not_called()

    def test_legacy_model_directory_setting_remains_readable(self):
        panel=self.window._match_panel
        panel.store_root=self.root/"legacy-local-settings"
        panel.store_root.mkdir(parents=True)
        old=self.root/"old-model"
        old.mkdir()
        (panel.store_root/"screen_classifier_choice.json").write_text(
            json.dumps({"directory":str(old)}))
        model=object()
        with patch("champions_assist.match_replay_dialog.load_bundled_classifier",
                   return_value=(model,"MODEL_READY_CPU")) as loader:
            loaded,status=panel._load_selected_screen_classifier()
        self.assertIs(loaded,model)
        self.assertEqual(status,"MODEL_READY_CPU")
        self.assertEqual(loader.call_args.kwargs["model_path"],
                         old/"champions_screen_classifier.onnx")

    def test_manual_controls_are_hidden_and_model_picker_exists(self):
        panel=self.window._match_panel
        self.assertTrue(panel.manual_batch_button.isHidden())
        self.assertTrue(panel.prep_button.isHidden())
        self.assertTrue(panel.confirm_win_button.isHidden())
        self.assertTrue(panel.confirm_loss_button.isHidden())
        self.assertEqual(panel.classifier_choose_button.objectName(),
                         "chooseScreenClassifier")
        self.assertEqual(panel.prep_banner.objectName(),"live_prep_progress")

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
            self.assertIn("自動検出",panel.menu_detection_label.text())
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
        self.assertEqual(prep_budget_before_battle(100.0,now=100.0),90)
        self.assertEqual(prep_budget_before_battle(100.0,now=155.0),35)
        self.assertEqual(prep_budget_before_battle(100.0,now=160.0),30)
        self.assertEqual(prep_budget_before_battle(100.0,now=188.0),2)
        self.assertEqual(prep_budget_before_battle(100.0,now=195.0),0)
        self.assertEqual(prep_budget_before_battle(None,now=900.0),90)

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
            start.assert_not_called()
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
                self.assertFalse(panel._prep_cancel_event.is_set())
                self.assertEqual(panel.state["prep_status"],"RUNNING")
            # Only the actual next AI dispatch, not detecting/queuing a
            # screen, supersedes the PREP request.
            panel._interrupt_prep()
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

    def test_prep_hard_deadline_discards_late_roles_without_discarding_cache(self):
        panel=self.window._match_panel
        panel.state={"phase":"PREP","prep_status":"RUNNING",
                     "prep":{"cached_matchups":18}}
        panel._prep_thread=object()
        panel._prep_budget_seconds=40.0
        panel._prep_cancel_event.clear()
        try:
            panel._prep_deadline()
            self.assertTrue(panel._prep_cancel_event.is_set())
            self.assertEqual(panel.state["prep_status"],"INCOMPLETE_DEADLINE")
            self.assertEqual(panel.state["prep"]["cached_matchups"],18)
            panel._on_prep_result({
                "state":{"phase":"PREP","prep_status":"STRUCTURE_COMPLETE"},
                "report":{"status":"PREP_STRUCTURE_OK"}})
            self.assertEqual(panel.state["prep_status"],"INCOMPLETE_DEADLINE")
            self.assertTrue(any(event["event"]=="PREP_HARD_DEADLINE_INCOMPLETE"
                                for event in panel._timing_trace))
        finally:
            panel._prep_thread=None

    def test_prep_without_win_roles_starts_role_completion_only(self):
        from tests.test_prep_cache import loaded_assets, chosen_state
        temp,assets=loaded_assets()
        self.addCleanup(temp.cleanup)
        panel=self.window._match_panel
        panel.assets=assets
        panel.state=chosen_state(assets)
        with patch("champions_assist.match_replay_dialog._ReplayWorker") as worker,patch(
                "champions_assist.match_replay_dialog.QThread") as thread:
            panel._start_prep_auto()
            worker.assert_called_once()
            thread.return_value.start.assert_called_once()
            self.assertEqual(panel.state["prep_status"],"RUNNING")
            self.assertEqual(panel.state["prep"]["cached_matchups"],18)
            self.assertEqual(panel.state["prep"]["win_roles_status"],"REQUIRED_NOT_EVALUATED")
            panel._prep_timer.stop()
            panel._prep_thread=None
            panel._prep_worker=None

    def test_following_normal_switch_reuses_published_target(self):
        panel=self.window._match_panel
        panel._auto_running=True
        panel.state={"phase":"BATTLE","awaiting_replacement":False}
        panel._turn_epoch=7
        panel._turn_action_sent=True
        panel._turn_event_after_action=False
        panel._last_advised_switch={"target":"ガブリアス","turn_epoch":7}
        with patch.object(panel,"_send_pending") as send:
            panel._queue_batch([np.zeros((12,20,3),dtype=np.uint8)],
                               screen_label="SWITCH_SELECTION")
        send.assert_not_called()
        self.assertIn("ガブリアスに交代",panel.action_banner.text())
        self.assertTrue(any(row["event"]=="PROPOSED_NORMAL_SWITCH_MENU_REUSED"
                            for row in panel._timing_trace))
        panel._auto_running=False

    def test_fainted_switch_is_new_request_even_after_old_switch_proposal(self):
        panel=self.window._match_panel
        panel._auto_running=True
        panel.state={"phase":"BATTLE","awaiting_replacement":True}
        panel._turn_epoch=7
        panel._turn_action_sent=True
        panel._turn_event_after_action=False
        panel._last_action_request_screen="TURN_ACTION"
        panel._last_advised_switch={"target":"ガブリアス","turn_epoch":7}
        with patch.object(panel,"_send_pending") as send:
            panel._queue_batch([np.zeros((12,20,3),dtype=np.uint8)],
                               screen_label="SWITCH_SELECTION")
            send.assert_called_once()
        self.assertEqual(panel._pending_screen_label,"SWITCH_SELECTION")
        panel._pending_batch=None
        panel._auto_running=False

    def test_manual_match_result_triggers_ended_state_without_images(self):
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE","result":"UNKNOWN",
                     "matrix_compatible":False}
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.Yes):
            panel._finish_match_manually("WIN")
        self.assertEqual(panel.state["phase"],"ENDED")
        self.assertEqual(panel.state["result"],"WIN")
        self.assertEqual(panel.entries[-1]["status"],"MATCH_END")
        self.assertEqual(panel.entries[-1]["images_sent"],0)

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

    def test_experimental_result_screen_starts_same_gpt_ocr_gate(self):
        from types import SimpleNamespace
        from champions_assist.screen_classifier import ScreenPrediction
        panel=self.window._match_panel
        panel.state={"phase":"BATTLE","matrix_compatible":False}
        panel._auto_running=True
        panel._cancel_event.clear()
        frame=np.full((120,200,3),60,dtype=np.uint8)
        panel._screen_model=SimpleNamespace(
            experimental=True,provider="CPUExecutionProvider",
            predict=lambda image:ScreenPrediction("RESULT_SCREEN",.99,.98,2.0))
        with patch("champions_assist.match_replay_dialog.capture_for_chatgpt",
                   return_value=frame), \
             patch.object(panel._turn_buffer,"observe",return_value=None), \
             patch.object(panel,"_note_stable_result_screen") as start_gpt:
            panel._capture_next()
            start_gpt.assert_not_called()
            panel._capture_next()
            start_gpt.assert_called_once()
        self.assertTrue(any(x["event"]=="RESULT_SCREEN_OCR_GPT_GATE"
                            and x["experimental"] for x in panel._timing_trace))
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

    def test_result_ocr_requires_two_confident_distinct_frames(self):
        from types import SimpleNamespace
        from champions_assist.local_event_worker import LocalFieldRead
        panel=self.window._match_panel
        panel._result_ocr_candidate=None
        panel._local_battle_tracker.clear()
        def record(frame,at,confidence=.96):
            return LocalFieldRead(
                field="screen_text.battle_message",value="勝利しました",
                frame_id=frame,timestamp=at,confidence=confidence,
                roi="battle_message",source="fixture",text="勝利しました")
        results=iter((
            (record("r1",1.0),),
            (record("r2",2.0,confidence=.45),),
            (record("r3",3.0),),
        ))
        panel._local_event_reader=SimpleNamespace(
            drain=lambda:(SimpleNamespace(error=None,observations=next(results,())),),
            stop=lambda:None)
        with patch.object(panel,"_request_result_from_ocr") as verify:
            panel._drain_event_candidates(include_snapshot=False)
            verify.assert_not_called()
            panel._drain_event_candidates(include_snapshot=False)
            verify.assert_not_called()
            panel._drain_event_candidates(include_snapshot=False)
            verify.assert_called_once_with(["勝利しました"])
        panel._local_event_reader=None

    def test_superseded_ocr_revision_requeues_without_new_classifier_frame(self):
        panel=self.window._match_panel
        panel._auto_running=True
        panel._active_job="batch"
        panel._active_state_revision=1
        panel.state={"phase":"BATTLE","our_active":"マスカーニャ",
                     "opponent_active":"ガブリアス","_live_state_revision":2}
        panel._pending_batch=None
        panel._pending_screen_label=None
        panel._inflight_screen_label="TURN_ACTION"
        panel._active_request_frame=np.zeros((16,20,3),dtype=np.uint8)
        panel._turn_event_after_action=False
        panel._completed({
            "state":{"phase":"BATTLE","our_active":"マスカーニャ",
                     "_live_state_revision":1},
            "report":{"status":"ADVICE","screen":"MOVE_MENU",
                      "recommendation":"トリックフラワー。"}})
        self.assertEqual(panel.entries[-1]["status"],"SUPERSEDED")
        self.assertEqual(panel.entries[-1]["recommendation"],"")
        self.assertEqual(panel._pending_screen_label,"TURN_ACTION")
        self.assertEqual(panel.state["_live_state_revision"],2)
        with patch.object(panel,"_send_pending") as resend:
            panel._cleanup()
            resend.assert_called_once()
        panel._pending_batch=None
        panel._auto_running=False

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
