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
                # With no current matching candidate, the newest stale
                # manifest is allowed for diagnostics; the status must stay stale.

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

    def _set_regulations(self, values):
        from champions_assist.team_management import TeamDocument
        doc = TeamDocument.load(self.team)
        rows = [dict(row) for row in doc.rows]
        members = [row for row in rows if row.get("record") == "MON"]
        self.assertEqual(len(members), 6)
        for member, value in zip(members, values):
            member["reg"] = value
        self.team.write_text(
            TeamDocument(doc.fieldnames, tuple(rows)).to_text(),
            encoding="utf-8")

    def test_regulation_A_B_A_selects_current_assets_without_journal(self):
        # The previous review bug required *no* prep-progress.json:
        # otherwise the CSV SHA check masked the missing regulation check.
        self._set_regulations(["REG_A"] * 6)
        first = self.prepare()
        self.assertFalse((self.root / "live-team-assets" /
                          self.team_id / "prep-progress.json").exists())
        self.assertEqual(self.readiness()["fingerprint"],
                         first.manifest_path.parent.name)

        self._set_regulations(["REG_B"] * 6)
        stale = self.readiness()
        self.assertEqual(stale["status"], "再準備が必要")
        self.assertFalse(stale["team_match"])
        self.assertIn("レギュレーションが変更されました",
                      stale["stale_reasons"])
        second = self.prepare()
        self.assertNotEqual(second.manifest_path, first.manifest_path)
        self.assertEqual(self.readiness()["fingerprint"],
                         second.manifest_path.parent.name)
        self.assertNotEqual(self.readiness()["status"], "再準備が必要")

        self._set_regulations(["REG_A"] * 6)
        recovered = self.readiness()
        self.assertEqual(recovered["fingerprint"],
                         first.manifest_path.parent.name)
        self.assertTrue(recovered["team_match"])
        self.assertNotEqual(recovered["status"], "再準備が必要")
        self.assertTrue(self.prepare().cache_hit)
        self.assertEqual(self.readiness()["fingerprint"],
                         first.manifest_path.parent.name)

    def test_other_member_regulation_change_does_not_reuse_old_scope(self):
        # The evidence catalog records the first MON's regulation, but the
        # source fingerprint covers all six MON records. The summary must
        # not accept a different regulation in slot 2 as the same scope.
        self._set_regulations(["REG_A"] * 6)
        old = self.prepare()
        self._set_regulations(["REG_A", "REG_B", "REG_A",
                               "REG_A", "REG_A", "REG_A"])
        stale = self.readiness()
        self.assertEqual(stale["status"], "再準備が必要")
        self.assertFalse(stale["team_match"])
        self.assertIn("レギュレーションが変更されました",
                      stale["stale_reasons"])
        fresh = self.prepare()
        self.assertNotEqual(old.manifest_path, fresh.manifest_path)
        self.assertEqual(self.readiness()["fingerprint"],
                         fresh.manifest_path.parent.name)

    def test_missing_regulation_scope_in_old_manifest_is_not_ready(self):
        self._set_regulations(["REG_A"] * 6)
        original = self.prepare()
        manifest = json.loads(original.manifest_path.read_text(encoding="utf-8"))
        manifest.pop("team_regulations")
        original.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        status = self.readiness()
        self.assertEqual(status["status"], "再準備が必要")
        self.assertFalse(status["team_match"])
        self.assertIn("レギュレーションが変更されました",
                      status["stale_reasons"])
        repaired = self.prepare()
        self.assertFalse(repaired.cache_hit)
        self.assertTrue(self.readiness()["team_match"])

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
