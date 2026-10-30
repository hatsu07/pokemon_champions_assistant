"""Review #137 fourth round: source-master cache invalidation and current-cache selection."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from champions_assist.matchup_evidence import SOURCE_FILES
from champions_assist.team_live_assets import (
    MATRIX, LOOKUP, BASE_TEAM, prepare_team_assets,
)
from champions_assist.team_prep_readiness import team_readiness
from champions_assist.team_matchup_reuse import reuse_unchanged_matchups

MASTER = Path(__file__).resolve().parents[1] / "masters"


class Review137CacheFreshnessTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.master = self.root / "masters"
        self.master.mkdir()
        for filename in (*SOURCE_FILES, MATRIX, LOOKUP, BASE_TEAM):
            shutil.copyfile(MASTER / filename, self.master / filename)
        self.team = self.root / "team.csv"
        self.team.write_bytes((self.master / BASE_TEAM).read_bytes())
        self.team_id = "e" * 32

    def prepare(self, *, snapshot=None):
        return prepare_team_assets(
            master_dir=self.master, data_root=self.root,
            team_id=self.team_id, team_path=self.team,
            environment_snapshot=snapshot)

    def readiness(self, *, snapshot=None):
        return team_readiness(
            self.root, self.team_id, self.team, master_dir=self.master,
            environment_snapshot=snapshot)

    def test_any_single_champions_master_change_invalidates_old_cached_catalog(self):
        original = self.prepare()
        old_catalog = original.manifest_path.with_name("matchup-evidence.json")
        old_catalog_data = json.loads(old_catalog.read_text(encoding="utf-8"))
        baseline_master_sha = dict(old_catalog_data["master_sha256"])
        old_manifest = original.manifest_path.read_bytes()
        self.assertTrue(self.prepare().cache_hit)

        for filename in SOURCE_FILES:
            with self.subTest(changed_master=filename):
                path = self.master / filename
                previous = path.read_bytes()
                path.write_bytes(previous + b"\n")
                stale = self.readiness()
                self.assertEqual(stale["status"], "再準備が必要")
                self.assertIn("Champions固定マスターが更新されました",
                              stale["stale_reasons"])
                self.assertEqual(stale["fingerprint"], original.manifest_path.parent.name)

                refreshed = self.prepare()
                self.assertFalse(refreshed.cache_hit)
                self.assertNotEqual(refreshed.manifest_path, original.manifest_path)
                current_catalog = json.loads(
                    refreshed.manifest_path.with_name(
                        "matchup-evidence.json").read_text(encoding="utf-8"))
                expected = sha256(path.read_bytes()).hexdigest()
                self.assertEqual(current_catalog["master_sha256"][filename], expected)
                self.assertNotEqual(expected, baseline_master_sha[filename])
                self.assertEqual(
                    json.loads(refreshed.manifest_path.read_text(encoding="utf-8"))
                    ["champions_master_sha256"], current_catalog["master_sha256"])
                self.assertEqual(self.readiness()["fingerprint"],
                                 refreshed.manifest_path.parent.name)
                self.assertNotEqual(self.readiness()["status"], "再準備が必要")
                # A new Champions source revision cannot silently reuse an
                # old v2 proposal (requires full catalog evidence equality).
                self.assertEqual(reuse_unchanged_matchups(
                    refreshed.manifest_path.with_name("matchup-evidence.json"),
                    self.root / "live-team-assets")["reused_cells"], 0)
                path.write_bytes(previous)
                self.assertTrue(self.prepare().cache_hit)
                self.assertEqual(self.readiness()["fingerprint"],
                                 original.manifest_path.parent.name)
        self.assertEqual(original.manifest_path.read_bytes(), old_manifest)
        self.assertEqual(json.loads(old_catalog.read_text(encoding="utf-8"))
                         ["master_sha256"], baseline_master_sha)

    def test_environment_A_B_A_picks_current_not_newer_manifest(self):
        snapshot = self.root / "singles.json"
        a = {"format": "Singles", "generatedAt": "2026-10-25",
             "pokemon": [{"name": "ガブリアス", "position": 1}]}
        b = {"format": "Singles", "generatedAt": "2026-10-30",
             "pokemon": [{"name": "カイリュー", "position": 1}]}
        snapshot.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
        first = self.prepare(snapshot=snapshot)
        snapshot.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
        second = self.prepare(snapshot=snapshot)
        self.assertNotEqual(first.manifest_path, second.manifest_path)
        self.assertEqual(self.readiness(snapshot=snapshot)["fingerprint"],
                         second.manifest_path.parent.name)
        snapshot.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.readiness(snapshot=snapshot)["fingerprint"],
                         first.manifest_path.parent.name)
        self.assertNotEqual(self.readiness(snapshot=snapshot)["status"],
                            "再準備が必要")
        self.assertTrue(self.prepare(snapshot=snapshot).cache_hit)
        self.assertEqual(self.readiness(snapshot=snapshot)["fingerprint"],
                         first.manifest_path.parent.name)

    def test_review_A_B_A_picks_current_not_newer_manifest(self):
        reviews = self.root / "matchup-reviews"
        reviews.mkdir()
        review = reviews / (self.team_id + ".json")
        a = '{"schema":"placeholder-A"}'
        b = '{"schema":"placeholder-B"}'
        # Even non-approved review packets affect the fingerprint; their
        # tactical contents cannot be trusted as processor certification.
        review.write_text(a, encoding="utf-8")
        first = self.prepare()
        review.write_text(b, encoding="utf-8")
        second = self.prepare()
        self.assertNotEqual(first.manifest_path, second.manifest_path)
        review.write_text(a, encoding="utf-8")
        state = self.readiness()
        self.assertEqual(state["fingerprint"], first.manifest_path.parent.name)
        self.assertNotEqual(state["status"], "再準備が必要")
        self.assertTrue(self.prepare().cache_hit)
        self.assertEqual(self.readiness()["fingerprint"],
                         first.manifest_path.parent.name)


if __name__ == "__main__":
    unittest.main()
