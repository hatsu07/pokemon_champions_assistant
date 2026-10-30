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

    def test_execution_complete_does_not_certify_prep_quality(self):
        journal = PrepWorkflowProgress(self.root, self.aid, self.a)
        journal.start(evidence_version="masters-1")
        for key, _ in STAGES:
            journal.stage(key, "実行中")
            journal.stage(key, "一部完了" if key == "prep" else "完了")
        journal.evidence(
            evaluated=100, proposals=15, total=120,
            source_gaps=3, quality_ready=False)
        journal.finish("完了")
        saved = json.loads(journal.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["run_status"], "完了")
        self.assertEqual(saved["stages"]["prep"]["status"], "一部完了")
        self.assertEqual(saved["evidence_status"], "根拠不足")
        self.assertFalse(saved["quality_ready"])

    def test_resume_keeps_successful_stage_history_and_cumulative_counts(self):
        first = PrepWorkflowProgress(self.root, self.aid, self.a)
        first.start(evidence_version="master-A")
        first.fingerprint("fingerprint-A")
        first.stage("team", "実行中")
        first.stage("team", "完了", "verified")
        first.stage("analysis", "実行中", "12 cells", generated=12, attempts=1)
        first.finish("停止")
        later = PrepWorkflowProgress(self.root, self.aid, self.a)
        state = later.start(evidence_version="master-A")
        self.assertEqual(state["attempt"], 2)
        self.assertEqual(len(state["history"]), 1)
        self.assertEqual(state["history"][0]["stages"]["team"]["status"], "完了")
        self.assertEqual(state["stages"]["team"]["status"], "完了")
        self.assertEqual(state["counters"]["generated_total"], 12)
        later.fingerprint("fingerprint-A")
        later.stage("analysis", "実行中", "next six", generated=6, attempts=1)
        later.stage("analysis", "完了", "total eighteen")
        later.finish("完了")
        final = json.loads(later.path.read_text(encoding="utf-8"))
        self.assertEqual(final["counters"]["generated_total"], 18)
        self.assertEqual(final["run_counters"]["generated"], 6)
        self.assertEqual(final["history"][0]["run_status"], "停止")
        snapshot = team_readiness(self.root, self.aid, self.a)
        self.assertEqual(snapshot["run_status"], "完了")
        self.assertEqual(snapshot["history_count"], 1)

    def test_changed_evidence_version_does_not_reuse_previous_stage_success(self):
        first = PrepWorkflowProgress(self.root, self.aid, self.a)
        first.start(evidence_version="old-masters")
        first.stage("team", "完了", "previous success")
        first.finish("完了")
        next_run = PrepWorkflowProgress(self.root, self.aid, self.a)
        state = next_run.start(evidence_version="new-masters")
        self.assertEqual(state["stages"]["team"]["status"], "未実行")
        self.assertEqual(state["attempt"], 1)
        self.assertFalse(state["history"])

    def test_csv_metadata_change_preserves_tactical_match_but_marks_stale(self):
        prepared = prepare_team_assets(
            master_dir=MASTER, data_root=self.root,
            team_id=self.aid, team_path=self.a)
        journal = PrepWorkflowProgress(self.root, self.aid, self.a)
        journal.start(evidence_version="current")
        journal.finish("完了")
        document = TeamDocument.load(self.a)
        rows = [dict(row) for row in document.rows]
        team_row = next(row for row in rows if row.get("record") == "TEAM")
        team_row["name"] = "改名した登録構築"
        self.a.write_text(
            TeamDocument(document.fieldnames, tuple(rows)).to_text(),
            encoding="utf-8")
        result = team_readiness(self.root, self.aid, self.a, master_dir=MASTER)
        self.assertEqual(result["status"], "再準備が必要")
        self.assertFalse(result["csv_match"])
        self.assertTrue(result["tactical_match"])
        self.assertTrue(result["team_match"])
        self.assertIn("構築CSVが更新されました", result["stale_reasons"])
        self.assertTrue(prepared.manifest_path.is_file())

    def test_environment_snapshot_change_invalidates_old_manifest(self):
        snapshot = self.root / "environment.json"
        snapshot.write_text(
            json.dumps({"format": "Singles", "pokemon": []}),
            encoding="utf-8")
        prepare_team_assets(
            master_dir=MASTER, data_root=self.root, team_id=self.aid,
            team_path=self.a, environment_snapshot=snapshot)
        good = team_readiness(
            self.root, self.aid, self.a, master_dir=MASTER,
            environment_snapshot=snapshot)
        self.assertTrue(good["environment_match"])
        snapshot.write_text(json.dumps({
            "format": "Singles", "pokemon": [],
            "generatedAt": "2026-10-30T16:50:00+09:00",
        }), encoding="utf-8")
        stale = team_readiness(
            self.root, self.aid, self.a, master_dir=MASTER,
            environment_snapshot=snapshot)
        self.assertFalse(stale["environment_match"])
        self.assertEqual(stale["status"], "再準備が必要")
        self.assertIn("Singles環境スナップショットが更新されました",
                      stale["stale_reasons"])



if __name__ == "__main__":
    unittest.main()
