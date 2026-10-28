"""Issue #102/B synthetic postmatch E2E and production MATCH_END wiring tests."""
from __future__ import annotations

from contextlib import closing

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from champions_assist.desktop import DesktopMainWindow
from champions_assist.matchup_learning_store import MatchupLearningStore
from champions_assist.postmatch_learning import REVIEW_SCHEMA
from champions_assist.postmatch_review_e2e_test import (
    SOURCE, run_postmatch_review_e2e,
)
from champions_assist.postmatch_review_test_page import PostmatchReviewTestPage


class IsolatedReviewE2ETest(TestCase):
    def test_offline_full_pipeline_readback_and_stages_without_credentials(self):
        stages = []
        with patch("champions_assist.siwc_auth.ChatGPTSignIn.access_token",
                   side_effect=AssertionError("No API authentication in offline mode")):
            result = run_postmatch_review_e2e(
                model="", use_api=False, on_stage=stages.append)
        self.assertEqual(result["status"], "PASS", result)
        self.assertTrue(result["record_saved"])
        self.assertTrue(result["review_saved"])
        self.assertEqual(result["review_status"], "STORED_UNVERIFIED_REVIEW")
        self.assertEqual(stages, [
            "RECORD_END", "REVIEW_REQUEST", "READBACK", "COMPLETE",
        ])
        self.assertEqual(result["source"], SOURCE)
        self.assertEqual(result["game_capture"], "NOT_USED")
        self.assertFalse(result["production_db_modified"])

    def test_api_mode_requires_auth_without_network_or_storage(self):
        with patch("champions_assist.postmatch_learning.request_review",
                   side_effect=AssertionError("Should not call GPT")):
            result = run_postmatch_review_e2e(
                model="gpt-5", use_api=True, access_token=None)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["stage"], "AUTH_REQUIRED")
        self.assertFalse(result["record_saved"])

    def test_offline_does_not_change_production_sqlite(self):
        with TemporaryDirectory() as temp:
            production = MatchupLearningStore(temp)
            self.assertIsNone(production.pending_match("not-a-match"))
            before = production.db_path.read_bytes()
            result = run_postmatch_review_e2e(model="unused", use_api=False)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(production.db_path.read_bytes(), before)


class ReviewE2EQtTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_explicit_offline_click_only_and_thread_cleanup(self):
        with TemporaryDirectory() as temp:
            page = PostmatchReviewTestPage(
                app_root=Path(temp), model="", effort="auto")
            self.addCleanup(page.close)
            self.assertFalse(page.has_active_worker)
            self.assertIn("未開始", page.stage_label.text())
            self.assertFalse(list(Path(temp).glob("*.sqlite")))
            page.start(False)
            deadline = __import__("time").monotonic() + 10
            while page.has_active_worker and __import__("time").monotonic() < deadline:
                QCoreApplication.processEvents()
                __import__("time").sleep(0.01)
            QCoreApplication.processEvents()
            self.assertFalse(page.has_active_worker)
            self.assertIn("PASS", page.stage_label.text())
            self.assertIn("SQLite保存: True", page.report_view.toPlainText())
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_api_not_invoked_without_explicit_click_and_missing_model(self):
        with TemporaryDirectory() as temp:
            page = PostmatchReviewTestPage(
                app_root=Path(temp), model="", effort="auto")
            with patch("champions_assist.postmatch_review_test_page.run_postmatch_review_e2e",
                       side_effect=AssertionError("Unwanted API")):
                page.start(True)
            self.assertFalse(page.has_active_worker)
            self.assertIn("モデル未設定", page.stage_label.text())
            page.close()



class ProductionReviewVisibleQtTest(TestCase):
    """Exercise the actual desktop widgets with a temporary storage root."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from champions_assist.app_config import AppSettingsStore
        from champions_assist.settings_management import DesktopSettingsService
        from champions_assist.desktop import DesktopController
        from champions_assist.store import BattleStore
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        master = Path(__file__).resolve().parents[1] / "masters"
        settings = AppSettingsStore(root / "settings.json", defaults_root=root)
        service = DesktopSettingsService(settings, master_directory=master)
        self.battle_store = BattleStore(root / "matches.sqlite3")
        self.window = DesktopMainWindow(
            DesktopController(store=self.battle_store, settings_service=service),
            settings_service=service)

    def tearDown(self):
        self.window.close()
        self.battle_store.close()

    def test_end_event_to_visible_missing_model_and_saved_sqlite_body(self):
        match_id = "synthetic-visible-match"
        self.window._match_panel.match_id = match_id
        self.window._match_panel.state = {"phase": "ENDED", "result": "WIN"}
        state = {"phase": "ENDED", "result": "WIN",
                 "history": [], "opponent_six": [], "selected_three": []}
        with patch.object(self.window.settings_service.environment,
                          "record_finished_match") as saved, patch(
            "champions_assist.vision_preferences.VisionPreferenceStore"
        ) as prefs:
            prefs.return_value.selected_model.return_value = ""
            self.window._on_unified_state(
                state, {"status": "MATCH_END", "result_source": "SYNTHETIC_TEST"})
            saved.assert_called_once()
        self.assertIn("未実行", self.window.postmatch_status_label.text())
        self.assertIn("モデル未設定", self.window.postmatch_status_label.text())
        self.assertIn(match_id, self.window.postmatch_match_label.text())
        self.assertIn("matchup-learning.sqlite3",
                      self.window.postmatch_storage_label.text())
        self.assertTrue(self.window.postmatch_retry_button.isEnabled())
        store = self.window._postmatch_store()
        self.assertIsNotNone(store.pending_match(match_id))
        review = {"schema": REVIEW_SCHEMA, "match_id": match_id,
                  "findings": [], "next_match_notes": "SQLiteから読み出した合成レビュー"}
        store.save_postmatch_review(match_id=match_id, review=review)
        self.window._show_saved_postmatch_review(match_id)
        self.assertIn("完了・SQLite保存済み", self.window.postmatch_status_label.text())
        self.assertIn("SQLiteから読み出した合成レビュー",
                      self.window.postmatch_detail.toPlainText())
        self.assertFalse(self.window.postmatch_retry_button.isEnabled())
        self.assertEqual(self.window.tabs.tabText(self.window.tabs.count()-1),
                         "対戦後レビュー")

    def test_production_review_worker_failure_visible_and_pending_retry(self):
        match_id = "synthetic-failed-review"
        store = self.window._postmatch_store()
        self.assertTrue(store.record_finished(
            match_id=match_id, team_id=None,
            state={"phase": "ENDED", "result": "LOSS"},
            source="SYNTHETIC_TEST"))
        self.window._postmatch_review_done({
            "match_id": match_id, "status": "FAILED_REVIEW_RETAINED"})
        self.assertIn("失敗", self.window.postmatch_status_label.text())
        self.assertIn("FAILED_REVIEW_RETAINED",
                      self.window.postmatch_detail.toPlainText())
        self.assertIsNotNone(store.pending_match(match_id))
        self.assertTrue(self.window.postmatch_retry_button.isEnabled())

class ProductionMatchEndWiringTest(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = MatchupLearningStore(self.temp.name)
        self.match_id = "synthetic-production-wiring"
        self.panel = SimpleNamespace(
            match_id=self.match_id, assets=None, _auto_running=False,
            _capture_paused=False, _proposed_selection=None,
            model="test", entries=[], _ai_last_status="NOT_STARTED",
            deadline_banner=SimpleNamespace(text=lambda: "—"),
            _last_selection_text="", live_assets_status="",
            state={"phase": "ENDED"},
        )
        self.environment = SimpleNamespace(record_finished_match=Mock())
        self.window = SimpleNamespace(
            _match_panel=self.panel,
            header_labels={k: Mock() for k in (
                "phase", "capture", "preload", "prep", "selected", "model",
                "report", "records", "ai", "clock")},
            recommendation_label=Mock(), state_tree=Mock(),
            _unified_match_id=None, _unified_last_report_key=None,
            _unified_team_id=None, _unified_team_sha="",
            _postmatch_worker=None, _postmatch_match_id=None,
            controller=SimpleNamespace(store=Mock()),
            settings_service=SimpleNamespace(
                environment=self.environment,
                settings=SimpleNamespace(
                    storage=SimpleNamespace(data_root=self.temp.name))),
            _refresh_history=Mock(), _start_postmatch_review=Mock(),
            _show_saved_postmatch_review=Mock(),
            _set_postmatch_status=Mock(),
        )

    def test_real_unified_state_handler_records_and_requests_review(self):
        state = {"phase": "ENDED", "result": "WIN",
                 "selected_three": [], "opponent_six": [], "history": []}
        report = {"status": "MATCH_END", "result_source": "SYNTHETIC_TEST"}
        DesktopMainWindow._on_unified_state(self.window, state, report)
        self.environment.record_finished_match.assert_called_once()
        self.window._start_postmatch_review.assert_called_once_with(self.match_id)
        self.assertIsNotNone(self.store.pending_match(self.match_id))
        with closing(sqlite3.connect(self.store.db_path)) as db:
            saved = db.execute(
                "SELECT result,source FROM match_history WHERE match_id=?",
                (self.match_id,)).fetchone()
            count = db.execute(
                "SELECT COUNT(*) FROM match_history WHERE match_id=?",
                (self.match_id,)).fetchone()[0]
        self.assertEqual(saved, ("WIN", "SYNTHETIC_TEST"))
        self.assertEqual(count, 1)
        # A repeated completion must not insert a second match/review job.
        self.window._postmatch_worker=object()
        DesktopMainWindow._on_unified_state(self.window, state, report)
        self.window._start_postmatch_review.assert_called_once()
        with closing(sqlite3.connect(self.store.db_path)) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM match_history WHERE match_id=?",
                (self.match_id,)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_review_saved_body_is_read_from_real_sqlite(self):
        state = {"phase": "ENDED", "result": "WIN"}
        self.assertTrue(self.store.record_finished(
            match_id=self.match_id, team_id=None, state=state,
            source="SYNTHETIC_TEST"))
        review = {"schema": REVIEW_SCHEMA, "match_id": self.match_id,
                  "findings": [], "next_match_notes": "未検証の合成要約"}
        self.assertTrue(self.store.save_postmatch_review(
            match_id=self.match_id, review=review))
        self.window._postmatch_store=lambda: self.store
        self.window.postmatch_detail=Mock()
        self.window.postmatch_retry_button=Mock()
        DesktopMainWindow._show_saved_postmatch_review(self.window, self.match_id)
        self.window.postmatch_detail.setPlainText.assert_called_once()
        displayed = self.window.postmatch_detail.setPlainText.call_args.args[0]
        self.assertIn("未検証の合成要約", displayed)
        self.assertIn(REVIEW_SCHEMA, displayed)

    def test_missing_model_is_visible_and_keeps_pending_review(self):
        self.assertTrue(self.store.record_finished(
            match_id=self.match_id, team_id=None,
            state={"phase": "ENDED", "result": "LOSS"},
            source="SYNTHETIC_TEST"))
        self.window._postmatch_store=lambda: self.store
        with patch("champions_assist.vision_preferences.VisionPreferenceStore") as prefs:
            prefs.return_value.selected_model.return_value=""
            DesktopMainWindow._start_postmatch_review(self.window, self.match_id)
        self.assertIsNone(self.window._postmatch_worker)
        self.assertIsNotNone(self.store.pending_match(self.match_id))
        self.assertIn("モデル未設定",
                      self.window._set_postmatch_status.call_args.args[0])
