"""Issue #136: off-match PREP progress and per-team readiness regression."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from champions_assist.team_live_assets import prepare_team_assets
from champions_assist.team_management import TeamDocument
from champions_assist.team_prep_readiness import (
    PrepWorkflowProgress, STAGES, team_readiness, progress_path,
)

MASTER = Path(__file__).resolve().parents[1] / "masters"
BASE = MASTER / "Pokemon_Champions_MC_team_2026-10-03.csv"


class PrepReadinessTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.a = self.root / "a.csv"
        self.b = self.root / "b.csv"
        self.a.write_bytes(BASE.read_bytes())
        self.b.write_bytes(BASE.read_bytes())
        document = TeamDocument.load(self.b)
        rows = [dict(row) for row in document.rows]
        first = next(row for row in rows if row.get("record") == "MON")
        first["item"] = "いのちのたま"
        self.b.write_text(
            TeamDocument(document.fieldnames, tuple(rows)).to_text(),
            encoding="utf-8")
        self.aid = "a" * 32
        self.bid = "b" * 32

    def test_all_eight_stages_are_persisted_with_exact_team_identity(self):
        progress = PrepWorkflowProgress(self.root, self.aid, self.a)
        state = progress.start()
        self.assertEqual(set(state["stages"]), {key for key, _ in STAGES})
        self.assertEqual(state["run_status"], "実行中")
        progress.stage("team", "実行中", "read")
        progress.stage("team", "完了", "six members")
        progress.stage("analysis", "実行中", "batch", generated=12, attempts=1)
        progress.finish("停止")
        saved = json.loads(progress_path(self.root, self.aid).read_text(encoding="utf-8"))
        self.assertEqual(saved["stages"]["team"]["status"], "完了")
        self.assertEqual(saved["stages"]["analysis"]["status"], "停止")
        self.assertEqual(saved["stages"]["cache"]["status"], "未実行")
        self.assertEqual(saved["counters"]["generated"], 12)
        self.assertEqual(saved["run_status"], "停止")

    def test_two_builds_keep_independent_readiness_without_regeneration(self):
        a = prepare_team_assets(
            master_dir=MASTER, data_root=self.root,
            team_id=self.aid, team_path=self.a)
        b = prepare_team_assets(
            master_dir=MASTER, data_root=self.root,
            team_id=self.bid, team_path=self.b)
        self.assertEqual(a.status, "REVIEWED_REUSE")
        self.assertEqual(b.status, "PROVISIONAL_UNVERIFIED")
        before = sorted(str(p) for p in self.root.rglob("*"))
        aa = team_readiness(self.root, self.aid, self.a, master_dir=MASTER)
        bb = team_readiness(self.root, self.bid, self.b, master_dir=MASTER)
        after = sorted(str(p) for p in self.root.rglob("*"))
        self.assertEqual(before, after, "Read-only UI must not create files")
        self.assertTrue(aa["team_match"])
        self.assertTrue(bb["team_match"])
        self.assertTrue(aa["matrix"] and aa["lookup"])
        self.assertTrue(bb["matrix"] and bb["lookup"])
        self.assertEqual(aa["total"], a.opponent_rows * 6)
        self.assertEqual(bb["total"], b.opponent_rows * 6)
        self.assertEqual(aa["legacy_cells"], aa["total"])
        self.assertEqual(bb["unknown"], b.opponent_rows)
        self.assertIsNone(bb["prep_lead"])
        self.assertIsNone(bb["prep_bench"])
        self.assertFalse(bb["quality_ready"])
        self.assertNotEqual(aa["fingerprint"], bb["fingerprint"])

    def test_changed_team_marks_old_assets_stale_and_retains_old_files(self):
        prepared = prepare_team_assets(
            master_dir=MASTER, data_root=self.root,
            team_id=self.bid, team_path=self.b)
        journal = PrepWorkflowProgress(self.root, self.bid, self.b)
        journal.start()
        journal.stage("team", "完了")
        journal.finish("一部完了")
        before = prepared.manifest_path.read_bytes()
        doc = TeamDocument.load(self.b)
        rows = [dict(row) for row in doc.rows]
        next(row for row in rows if row.get("record") == "MON")["move1"] = "でんこうせっか"
        self.b.write_text(TeamDocument(doc.fieldnames, tuple(rows)).to_text(),
                          encoding="utf-8")
        result = team_readiness(self.root, self.bid, self.b, master_dir=MASTER)
        self.assertEqual(result["status"], "再準備が必要")
        self.assertFalse(result["team_match"])
        self.assertEqual(before, prepared.manifest_path.read_bytes())

    def test_corrupted_generated_matrix_is_not_considered_ready(self):
        prepared = prepare_team_assets(
            master_dir=MASTER, data_root=self.root,
            team_id=self.aid, team_path=self.a)
        prepared.matrix_path.write_text("BROKEN", encoding="utf-8")
        result = team_readiness(self.root, self.aid, self.a, master_dir=MASTER)
        self.assertEqual(result["status"], "生成物不整合")
        self.assertFalse(result["team_match"])
        self.assertFalse(result["matrix"])

    def test_invalid_stage_does_not_create_false_completion(self):
        progress = PrepWorkflowProgress(self.root, self.aid, self.a)
        progress.start()
        with self.assertRaises(ValueError):
            progress.stage("cache", "CERTIFIED", "unsupported")
        with self.assertRaises(ValueError):
            progress.stage("unknown", "完了")
        self.assertEqual(
            json.loads(progress.path.read_text(encoding="utf-8"))
            ["stages"]["cache"]["status"], "未実行")


if __name__ == "__main__":
    unittest.main()
