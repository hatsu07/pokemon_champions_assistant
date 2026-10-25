"""Evidence-gated team generator and offline Singles battle-data refresh tests."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from champions_assist.app_config import ConfigurationError
from champions_assist.environment_refresh import SinglesEnvironmentStore, SOURCE_URL
from champions_assist.matrix_compatibility import matrix_matches_registered_team
from champions_assist.preload import MasterPreloader
from champions_assist.team_live_assets import (
    MATRIX, LOOKUP, UNKNOWN_CELL, prepare_team_assets,
)
from champions_assist.team_management import TeamDocument

MASTER=Path(__file__).resolve().parents[1]/"masters"
ORIGINAL=MASTER/"Pokemon_Champions_MC_team_2026-10-03.csv"


class TeamLiveAssetsTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.team=self.root/"team.csv"
        self.team.write_bytes(ORIGINAL.read_bytes())
        self.id="a"*32

    def build(self):
        return prepare_team_assets(master_dir=MASTER,data_root=self.root,
                                   team_id=self.id,team_path=self.team)

    def test_exact_team_preserves_all_reviewed_rows_and_is_cached(self):
        ready=self.build()
        self.assertEqual(ready.status,"REVIEWED_REUSE")
        self.assertEqual(ready.reviewed_members,6)
        self.assertGreater(ready.opponent_rows,300)
        self.assertFalse(ready.cache_hit)
        assets=MasterPreloader(MASTER,team_source=self.team,
                               matrix_source=ready.matrix_path,
                               lookup_source=ready.lookup_path).load_for_new_match()
        self.assertTrue(matrix_matches_registered_team(assets))
        # Preserve source line endings exactly on Windows and Unix.
        self.assertEqual(assets.contents[MATRIX].encode("utf-8"),
                         (MASTER/MATRIX).read_bytes())
        self.assertEqual(set(assets.matrix_index),set(assets.lookup_index))
        second=self.build()
        self.assertTrue(second.cache_hit)
        self.assertEqual(ready.manifest_path,second.manifest_path)
        self.assertEqual(len(assets.sha256),4)

    def test_changed_move_never_inherits_reviewed_rating_or_processor(self):
        document=TeamDocument.load(self.team)
        rows=[dict(x) for x in document.rows]
        first=next(i for i,x in enumerate(rows) if x.get("record")=="MON")
        rows[first]["move1"]="ボルトチェンジ"
        self.team.write_text(TeamDocument(document.fieldnames,tuple(rows)).to_text(),encoding="utf-8")
        result=self.build()
        self.assertEqual(result.status,"PROVISIONAL_UNVERIFIED")
        self.assertEqual(result.reviewed_members,5)
        assets=MasterPreloader(MASTER,team_source=self.team,
                               matrix_source=result.matrix_path,
                               lookup_source=result.lookup_path).load_for_new_match()
        self.assertFalse(matrix_matches_registered_team(assets))
        row=next(iter(assets.matrix_index.values())).split("|")
        self.assertEqual(row[3],UNKNOWN_CELL)
        self.assertTrue(row[12].startswith("??/"))
        self.assertEqual(len(row),14)
        self.assertEqual(len(assets.lookup_index),result.opponent_rows)
        self.assertEqual(json.loads(result.manifest_path.read_text())["unverified_cells"],
                         result.opponent_rows)

    def test_reordering_team_changes_column_order_not_evidence(self):
        document=TeamDocument.load(self.team)
        rows=[dict(x) for x in document.rows]
        a=next(i for i,x in enumerate(rows) if x.get("record")=="MON")
        rows[a],rows[a+1]=rows[a+1],rows[a]
        self.team.write_text(TeamDocument(document.fieldnames,tuple(rows)).to_text(),encoding="utf-8")
        result=self.build()
        self.assertEqual(result.status,"REVIEWED_REUSE")
        assets=MasterPreloader(MASTER,team_source=self.team,
                               matrix_source=result.matrix_path,
                               lookup_source=result.lookup_path).load_for_new_match()
        self.assertTrue(matrix_matches_registered_team(assets))
        original=next(iter(MasterPreloader(MASTER).load_for_new_match().matrix_index.values())).split("|")
        derived=next(iter(assets.matrix_index.values())).split("|")
        self.assertEqual(derived[3],original[4])
        self.assertEqual(derived[4],original[3])

    def test_changed_item_and_stats_invalidate_column(self):
        document=TeamDocument.load(self.team)
        rows=[dict(x) for x in document.rows]
        target=next(i for i,x in enumerate(rows) if x.get("record")=="MON")
        rows[target]["item"]="いのちのたま"
        rows[target]["ap_h_a_b_c_d_s"]="0-32-0-0-0-32"
        self.team.write_text(TeamDocument(document.fieldnames,tuple(rows)).to_text(),encoding="utf-8")
        self.assertEqual(self.build().reviewed_members,5)

    def test_bad_id_or_incomplete_reference_refused_without_mutating_sources(self):
        with self.assertRaises(ConfigurationError):
            prepare_team_assets(master_dir=MASTER,data_root=self.root,
                                team_id="../escape",team_path=self.team)
        self.assertFalse((self.root/"live-team-assets").exists())


class EnvironmentEvidenceTests(unittest.TestCase):
    def setUp(self):
        t=TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.store=SinglesEnvironmentStore(self.root)
        self.snapshot={"format":"Singles","generatedAt":"2026-10-25T01:00:00Z",
                       "pokemon":[{"name":"Garchomp","position":1,
                                   "moves":[["Earthquake",65.2]],
                                   "items":[["Choice Scarf",20.0]],
                                   "abilities":[["Rough Skin",90.0]]}]}

    def test_refresh_imports_only_champions_singles_and_records_provenance(self):
        body=json.dumps(self.snapshot).encode()
        receipt=self.store.refresh_public_singles(fetch=lambda:body)
        self.assertEqual(receipt["source_url"],SOURCE_URL)
        self.assertEqual(receipt["source_kind"],"PUBLIC_CHAMPIONS_STATS")
        self.assertEqual(receipt["format"],"Singles")
        self.assertEqual(receipt["regulation"],"UNDECLARED_BY_SOURCE")
        self.assertEqual(self.store.summary()["number_of_pokemon"],1)
        self.assertEqual(json.loads(self.store.snapshot.read_text())["pokemon"][0]["moves"],
                         [["Earthquake",65.2]])

    def test_public_trends_update_lookup_warning_not_matchup_grade(self):
        environment=SinglesEnvironmentStore(self.root)
        environment.import_snapshot(
            {"format":"Singles","generatedAt":"2026-10-25T01:00:00Z",
             "pokemon":[{"name":"Garchomp","position":1,
                         "moves":[["Earthquake",65.2]]}]},
            source_url=SOURCE_URL,source_kind="PUBLIC_CHAMPIONS_STATS")
        prepared=prepare_team_assets(
            master_dir=MASTER,data_root=self.root,
            team_id="f"*32,team_path=ORIGINAL,
            environment_snapshot=environment.snapshot)
        self.assertEqual(prepared.status,"REVIEWED_REUSE")
        lines=prepared.matrix_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("Earthquake@65.2%" in line for line in lines))
        self.assertTrue(any("Garchomp" not in line for line in lines))
        stats=json.loads(prepared.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(stats["environment_source_kind"],"PUBLIC_CHAMPIONS_STATS")

    def test_newer_game_rankings_are_not_overwritten_by_old_public_data(self):
        mine={"format":"Singles","generatedAt":"2026-10-25T02:00:00Z",
              "pokemon":[{"name":"Garchomp","position":1}]}
        self.store.import_snapshot(mine,source_url="user-ranked-list",
                                   source_kind="USER_INGAME_CONFIRMED")
        old=self.store.snapshot.read_bytes()
        with self.assertRaises(ConfigurationError):
            self.store.refresh_public_singles(
                fetch=lambda:json.dumps(self.snapshot).encode("utf-8"))
        self.assertEqual(old,self.store.snapshot.read_bytes())

    def test_failed_refresh_keeps_last_good(self):
        self.store.refresh_public_singles(fetch=lambda:json.dumps(self.snapshot).encode())
        old=self.store.snapshot.read_bytes()
        for broken in [b"not-json",b'{"format":"Doubles","pokemon":[]}',b"x"*(8*1024*1024+1)]:
            with self.assertRaises((ValueError,ConfigurationError)):
                self.store.refresh_public_singles(fetch=lambda:broken)
            self.assertEqual(old,self.store.snapshot.read_bytes())

    def test_user_snapshot_remains_distinct_from_public_and_tracks_date(self):
        f=self.root/"user-evidence.json"
        f.write_text(json.dumps(self.snapshot),encoding="utf-8")
        receipt=self.store.import_user_json(f)
        self.assertEqual(receipt["source_kind"],"USER_INGAME_CONFIRMED")
        self.assertTrue(receipt["source_url"].endswith(f.name))

    def test_completed_match_stored_once_and_no_predicted_result(self):
        pending={"phase":"BATTLE","result":"UNKNOWN"}
        self.assertFalse(self.store.record_finished_match(match_id="match-1",state=pending))
        complete={"phase":"ENDED","result":"WIN",
                  "selected_three":["マスカーニャ","アシレーヌ","ガブリアス"],
                  "opponent_six":["ガブリアス"],"lead":"マスカーニャ"}
        self.assertTrue(self.store.record_finished_match(match_id="match-1",state=complete))
        self.assertFalse(self.store.record_finished_match(match_id="match-1",state=complete))
        lines=self.store.local_battles.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines),1)
        self.assertEqual(json.loads(lines[0])["source"],
                         "LOCAL_VISUAL_AI_RESULT_UNVERIFIED_BY_HUMAN")


if __name__=="__main__":
    unittest.main()
