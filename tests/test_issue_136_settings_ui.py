"""Issue #136 settings UI exposes stored per-team readiness and workflow stages."""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from champions_assist.app_config import AppSettingsStore
from champions_assist.settings_management import DesktopSettingsService
from champions_assist.settings_dialog import SettingsDialog

MASTER = Path(__file__).resolve().parents[1] / "masters"


class Issue136SettingsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.service = DesktopSettingsService(
            AppSettingsStore(self.root / "settings.json", defaults_root=self.root),
            master_directory=MASTER)
        self.a = self.service.import_team(
            MASTER / "Pokemon_Champions_MC_team_2026-10-03.csv",
            display_name="構築A", match_active=False)
        self.b = self.service.duplicate_team(self.a.team_id, match_active=False)
        self.service.select_team(self.a.team_id, match_active=False)
        self.dialog = SettingsDialog(self.service, match_active=lambda: False)
        self.addCleanup(self.dialog.close)

    def test_all_registered_teams_show_prep_status_without_triggering_ai(self):
        self.assertEqual(self.dialog.team_list.columnCount(), 6)
        self.assertEqual(self.dialog.team_list.rowCount(), 2)
        self.assertEqual(self.dialog.team_list.horizontalHeaderItem(5).text(), "PREP準備")
        self.assertIn("PREPデータを一括準備・再開",
                      self.dialog.matchup_ai_button.text())
        self.assertEqual(self.dialog.prep_stage_table.rowCount(), 8)
        states = [self.dialog.team_list.item(row, 5).text()
                  for row in range(2)]
        self.assertTrue(all(state for state in states))
        self.assertIsNone(self.dialog._matchup_ai_worker)

    def test_progress_updates_selected_team_but_not_other_team(self):
        self.dialog._active_prep_team_id = self.a.team_id
        event = {
            "team_id": self.a.team_id,
            "stage": "analysis", "stage_status": "実行中",
            "stages": {
                "team": {"status": "完了", "detail": "six"},
                "analysis": {"status": "実行中", "detail": "12 cells"},
            },
            "counters": {"generated": 12, "attempts": 1},
        }
        self.dialog._matchup_ai_progress(event)
        self.assertIn("12", self.dialog.matchup_review_status.text())
        self.assertEqual(self.dialog.prep_stage_table.item(0, 1).text(), "完了")
        self.assertEqual(self.dialog.prep_stage_table.item(4, 1).text(), "実行中")
        self.assertEqual(self.dialog.prep_progress_bar.value(), 1)
        self.dialog._matchup_ai_progress({
            **event, "team_id": "f" * 32,
            "stages": {"analysis": {"status": "完了"}}})
        self.assertEqual(self.dialog.prep_stage_table.item(4, 1).text(), "実行中")


if __name__ == "__main__":
    unittest.main()
