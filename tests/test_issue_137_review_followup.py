"""Review #137: AI schema coverage and all source fingerprint inputs are distinct."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from champions_assist.matchup_evidence import SOURCE_FILES
from champions_assist.team_ai_matrix import publish_ai_matrix
from champions_assist.team_ai_analysis import SCHEMA as V1
from champions_assist.team_live_assets import (
    MATRIX, LOOKUP, BASE_TEAM, prepare_team_assets,
)
from champions_assist.team_management import TeamDocument
from champions_assist.team_matchup_v2 import (
    SCHEMA_V2, eligible_v2_request, validate_v2_reply,
)
from champions_assist.team_prep_readiness import team_readiness

MASTER = Path(__file__).resolve().parents[1] / "masters"


class Review137FollowupTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.masters = self.root / "masters"
        self.masters.mkdir()
        for name in (*SOURCE_FILES, MATRIX, LOOKUP, BASE_TEAM):
            shutil.copyfile(MASTER / name, self.masters / name)
        self.team = self.root / "team.csv"
        self.team.write_bytes((self.masters / BASE_TEAM).read_bytes())
        doc = TeamDocument.load(self.team)
        rows = [dict(row) for row in doc.rows]
        for row in rows:
            if row.get("record") == "MON":
                row["priority"] = "REVIEW_SCHEMA_COVERAGE_TEST"
        self.team.write_text(
            TeamDocument(doc.fieldnames, tuple(rows)).to_text(), encoding="utf-8")
        self.team_id = "d" * 32
        self.ready = prepare_team_assets(
            master_dir=self.masters, data_root=self.root,
            team_id=self.team_id, team_path=self.team)
        self.catalog = self.ready.manifest_path.with_name("matchup-evidence.json")

    def _v2_request(self):
        req = eligible_v2_request(self.catalog, max_cells=2)
        self.assertEqual(len(req["cells"]), 2)
        return req

    def _v1(self, cells):
        digest = sha256(self.catalog.read_bytes()).hexdigest()
        rows = [
            {**cell, "assessment": "確認できない型のため条件付き",
             "risks": "先制技に警戒", "unknowns": "相手実際の技構成"}
            for cell in cells
        ]
        doc = {
            "schema": V1, "catalog_sha256": digest,
            "status": "AI_PROPOSAL_UNVERIFIED",
            "live_approved": False, "processor_certified": False,
            "safe_switch_certified": False, "proposals": rows,
        }
        (self.catalog.parent / "ai-proposal-v1.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def _v2(self, req, count):
        members = {member["slot"]: member for member in req["own_members"]}
        rows = []
        for c in req["cells"][:count]:
            moves = members[c["slot"]]["moves"]
            rows.append({
                **c, "attack_move": moves[0]["name"] if moves else "UNKNOWN",
                "base_speed": "UNKNOWN", "damage": "UNKNOWN",
                "priority_risk": "POSSIBLE", "setup_risk": "UNKNOWN",
                "direct_processor": "UNKNOWN", "safe_switch": "UNKNOWN",
                "note": "未検証の対面候補", "unknowns": "相手の実際の型",
            })
        # Rebuild request with exactly those keys when only 1 v2 is saved.
        subset = dict(req, cells=req["cells"][:count])
        doc = validate_v2_reply(subset, {
            "schema": SCHEMA_V2, "catalog_sha256": req["catalog_sha256"],
            "proposals": rows,
        })
        (self.catalog.parent / "ai-proposal-v2.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def _assert_version_counts(self, v1, v2, total):
        published = publish_ai_matrix(
            matrix_path=self.ready.matrix_path,
            manifest_path=self.ready.manifest_path,
            catalog_path=self.catalog)
        self.assertEqual(published["ai_v1_cells"], v1)
        self.assertEqual(published["ai_v2_cells"], v2)
        self.assertEqual(published["ai_proposal_cells"], total)
        state = team_readiness(
            self.root, self.team_id, self.team, master_dir=self.masters)
        self.assertEqual(state["ai_v1"], v1)
        self.assertEqual(state["ai_v2"], v2)
        self.assertEqual(state["ai_total"], total)
        self.assertEqual(state["status"], "条件付き準備")
        self.assertEqual(state["confirmed_cells"], 0)

    def test_v1_only_not_counted_as_structured_v2(self):
        request = self._v2_request()
        self._v1(request["cells"])
        self._assert_version_counts(2, 0, 2)

    def test_v2_only_counted_as_structured(self):
        request = self._v2_request()
        self._v2(request, 2)
        self._assert_version_counts(0, 2, 2)

    def test_overlapping_v1_v2_prefers_v2_once(self):
        request = self._v2_request()
        self._v1(request["cells"])
        self._v2(request, 1)
        self._assert_version_counts(1, 1, 2)

    def test_legacy_combined_only_manifest_never_claims_v2_coverage(self):
        self._v1(self._v2_request()["cells"])
        publish_ai_matrix(
            matrix_path=self.ready.matrix_path,
            manifest_path=self.ready.manifest_path,
            catalog_path=self.catalog)
        manifest = json.loads(self.ready.manifest_path.read_text(encoding="utf-8"))
        manifest.pop("ai_v1_cells")
        manifest.pop("ai_v2_cells")
        self.ready.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        state = team_readiness(
            self.root, self.team_id, self.team, master_dir=self.masters)
        self.assertEqual(state["ai_total"], 2)
        self.assertIsNone(state["ai_v1"])
        self.assertIsNone(state["ai_v2"])

    def _expect_stale(self, reason: str):
        state = team_readiness(
            self.root, self.team_id, self.team, master_dir=self.masters)
        self.assertEqual(state["status"], "再準備が必要")
        self.assertTrue(state["tactical_match"])
        self.assertIn(reason, state["stale_reasons"])

    def test_changed_original_live_matrix_marks_stale(self):
        source = self.masters / MATRIX
        source.write_bytes(source.read_bytes() + b"\n# changed reviewed source\n")
        self._expect_stale("LIVE元マトリクスが更新されました")

    def test_changed_original_lookup_marks_stale(self):
        source = self.masters / LOOKUP
        source.write_bytes(source.read_bytes() + b"\n# changed lookup\n")
        self._expect_stale("元60秒ルックアップが更新されました")

    def test_changed_baseline_battle_set_marks_stale(self):
        source = self.masters / BASE_TEAM
        doc = TeamDocument.load(source)
        rows = [dict(x) for x in doc.rows]
        next(row for row in rows if row.get("record") == "MON")["item"] = "いのちのたま"
        source.write_text(
            TeamDocument(doc.fieldnames, tuple(rows)).to_text(), encoding="utf-8")
        self._expect_stale("基準構築が更新されました")

    def test_changed_human_review_marks_stale(self):
        reviews = self.root / "matchup-reviews"
        reviews.mkdir()
        (reviews / (self.team_id + ".json")).write_text(
            '{"updated":"human-reviewed"}', encoding="utf-8")
        self._expect_stale("人間レビューが更新されました")

    def test_old_manifest_without_source_provenance_cannot_claim_ready(self):
        manifest = json.loads(self.ready.manifest_path.read_text(encoding="utf-8"))
        manifest.pop("master_matrix_sha256")
        self.ready.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        self._expect_stale("LIVE元マトリクスが更新されました")
        # An explicit off-match preparation can restore the absent provenance.
        refreshed = prepare_team_assets(
            master_dir=self.masters, data_root=self.root,
            team_id=self.team_id, team_path=self.team)
        self.assertFalse(refreshed.cache_hit)
        state = team_readiness(
            self.root, self.team_id, self.team, master_dir=self.masters)
        self.assertNotEqual(state["status"], "再準備が必要")


if __name__ == "__main__":
    unittest.main()
