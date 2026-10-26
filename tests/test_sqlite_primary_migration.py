"""Offline migration, form isolation, SQLite-first and bounded OAuth tests."""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
import csv
import json
import unittest

from champions_assist.app_config import ConfigurationError
from champions_assist.champions_form_index import (
    build_form_index, get_exact_form, form_key)
from champions_assist.matchup_evidence import build_candidate_catalog
from champions_assist.sqlite_primary import ChampionsPrimaryData
from champions_assist.team_ai_analysis import save_proposals
from champions_assist.team_matchup_v2 import (
    SCHEMA_V2,eligible_v2_request,validate_v2_reply)
from champions_assist.team_ai_matrix import proposal_notes
from champions_assist.team_auto_analysis import run_automatic_analysis

ROOT=Path(__file__).resolve().parents[1]
MASTER=ROOT/"masters"


def fake_reply(req):
    own={o["slot"]:o for o in req["own_members"]}
    opp={o["name"]:o for o in req["opponents"]}
    rows=[]
    for c in req["cells"]:
        member=own[c["slot"]]
        os=member["traits"].get("base_speed")
        ps=opp[c["opponent"]]["traits"].get("base_speed")
        speed=("BASE_FASTER" if os>ps else "BASE_SLOWER" if os<ps else "BASE_TIE") if (
            type(os) is int and type(ps) is int) else "UNKNOWN"
        rows.append({
            **c,"attack_move":member["moves"][0]["name"] if member["moves"] else "UNKNOWN",
            "base_speed":speed,"damage":"UNKNOWN",
            "priority_risk":"UNKNOWN","setup_risk":"UNKNOWN",
            "direct_processor":"UNKNOWN","safe_switch":"UNKNOWN",
            "note":"未検証候補","unknowns":"相手の型・実測未取得",
        })
    return validate_v2_reply(req,{
        "schema":SCHEMA_V2,"catalog_sha256":req["catalog_sha256"],
        "proposals":rows})


class SqlitePrimaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.team="f"*32
        self.assets=self.root/"live-team-assets"/self.team/("1"*64)
        self.assets.mkdir(parents=True)
        self.catalog=self.assets/"matchup-evidence.json"
        own={"types":["じめん"],"base_speed":100,"stats":{},
             "species_source":"masters"}
        foe={"types":["みず"],"base_speed":90,"stats":{},
             "species_source":"masters"}
        def cells():
            return [{
                "opponent":"ロトム（ウォッシュロトム）","slot":i,
                "own":f"own-{i}","member_signature":f"member-{i}",
                "evaluation":"UNKNOWN","missing_source":[],
                "own_species":own,"opponent_species":foe,
                "registered_moves":[{"name":"じしん"}],
                "registered_build":{"item":"-"},
                "opponent_priority_candidates":[],
                "opponent_potential_high_power_moves":[],
            } for i in range(6)]
        self.evidence={
            "schema":"champions-matchup-evidence-v1",
            "form_lookup_revision":"exact-form-v2",
            "generated_at":"2026-10-26T00:00:00+00:00",
            "regulation":"M-C","member_signatures":[f"member-{i}" for i in range(6)],
            "master_sha256":{"pokemon":"sha","moves":"sha","learnset":"sha"},
            "cells":cells(),
        }
        self.catalog.write_bytes(json.dumps(self.evidence,
            ensure_ascii=False,sort_keys=True).encode())
        self.primary=ChampionsPrimaryData(self.root)

    def test_exact_form_indexes_preserve_normal_and_regional(self):
        with (MASTER/"Pokemon_Champions_MC_usable_pokemon.csv").open(
                encoding="utf-8-sig",newline="") as f:
            species=build_form_index(list(csv.DictReader(f)))
        with (MASTER/"Pokemon_Champions_MC_pokemon_learnsets_linked.csv").open(
                encoding="utf-8-sig",newline="") as f:
            learn=build_form_index(list(csv.DictReader(f)))
        for label in ("ライチュウ（アローラのすがた）",
                      "ロトム（ウォッシュロトム）",
                      "ギルガルド（ブレードフォルム）",
                      "イルカマン（マイティフォルム）"):
            a=get_exact_form(label,species)
            b=get_exact_form(label,learn)
            self.assertIsNotNone(a,label)
            self.assertIsNotNone(b,label)
            self.assertEqual(a["ポケモン名"],b["ポケモン名"])
            self.assertEqual(a["フォーム/姿"],b["フォーム/姿"])
        self.assertIsNone(get_exact_form("ロトム（未実装のすがた）",species))
        self.assertIsNone(get_exact_form("ロトム",species))

    def test_all_358_live_opponents_have_exact_fixed_master_forms(self):
        matrix=(MASTER/"Pokemon_Champions_MC_live_selection_matrix.txt").read_text(
            encoding="utf-8-sig")
        with (MASTER/"Pokemon_Champions_MC_usable_pokemon.csv").open(
                encoding="utf-8-sig",newline="") as stream:
            species=build_form_index(list(csv.DictReader(stream)))
        with (MASTER/"Pokemon_Champions_MC_pokemon_learnsets_linked.csv").open(
                encoding="utf-8-sig",newline="") as stream:
            learn=build_form_index(list(csv.DictReader(stream)))
        opponents=[line.split("|")[0] for line in matrix.splitlines()
                   if line and not line.startswith("#")]
        self.assertEqual(len(opponents),358)
        unresolved=[name for name in opponents
                    if get_exact_form(name,species) is None
                    or get_exact_form(name,learn) is None]
        self.assertEqual(unresolved,[])

    def test_sqlite_first_resumes_without_json_mirror(self):
        req=eligible_v2_request(self.catalog,max_cells=3)
        proposal=fake_reply(req)
        mirror=self.assets/"ai-proposal-original.json"
        save_proposals(mirror,self.catalog,proposal)
        self.assertTrue(mirror.is_file())
        data=mirror.read_bytes()
        mirror.unlink()
        next_request=eligible_v2_request(self.catalog,max_cells=6)
        self.assertEqual(len(next_request["cells"]),3)
        self.assertEqual({c["slot"] for c in next_request["cells"]},{3,4,5})
        notes,n=proposal_notes(self.catalog)
        self.assertEqual(n,3)
        self.assertIn("ロトム（ウォッシュロトム）",notes)
        # Recover without a token or a request to the model.
        self.assertEqual(data,json.dumps(proposal,ensure_ascii=False,indent=2).encode())

    def test_migration_audit_and_sources_unchanged(self):
        teams=self.root/"teams"
        teams.mkdir()
        csv_path=teams/("a"*32+".csv")
        csv_path.write_bytes(b"pokemon,move\nA,B\n")
        index=teams/"index.json"
        index.write_text("[]",encoding="utf-8")
        old_proposal=self.assets/"ai-proposal-v28.json"
        req=eligible_v2_request(self.catalog,max_cells=6)
        old_proposal.write_text(json.dumps(fake_reply(req),ensure_ascii=False),
                                encoding="utf-8")
        snapshots={p:p.read_bytes() for p in (self.catalog,csv_path,index,old_proposal)}
        report=self.primary.migrate()
        self.assertEqual(report["status"],"COMPLETE")
        self.assertEqual(report["stored_batches"],1)
        self.assertEqual(self.primary.learning.coverage(self.catalog)["structured_v2"],6)
        self.assertTrue(Path(report["audit_path"]).is_file())
        for file,raw in snapshots.items():
            self.assertEqual(file.read_bytes(),raw)
        self.assertEqual(self.primary.verified_export(
            "team_csv",csv_path.stem),b"pokemon,move\nA,B\n")
        second=self.primary.migrate()
        self.assertEqual(second["imported_cells"],0)
        self.assertEqual(second["status"],"COMPLETE")

    def test_migrate_later_v28_batches_idempotently(self):
        req=eligible_v2_request(self.catalog,max_cells=3)
        first=self.assets/"ai-proposal-first.json"
        first.write_text(json.dumps(fake_reply(req),ensure_ascii=False),encoding="utf-8")
        self.primary.migrate()
        self.assertEqual(self.primary.learning.coverage(self.catalog)["structured_v2"],3)
        # v28 may continue after the initial migration. This must not be lost.
        more=eligible_v2_request(self.catalog,max_cells=3)
        second=self.assets/"ai-proposal-second.json"
        second.write_text(json.dumps(fake_reply(more),ensure_ascii=False),encoding="utf-8")
        out=self.primary.migrate()
        self.assertEqual(out["imported_cells"],3)
        self.assertEqual(self.primary.learning.coverage(self.catalog)["structured_v2"],6)

    def test_form_evidence_revision_excludes_old_name_only_reuse(self):
        req=eligible_v2_request(self.catalog,max_cells=6)
        old=json.loads(self.catalog.read_text(encoding="utf-8"))
        old.pop("form_lookup_revision")
        old["generated_at"]="old"
        old_dir=self.root/"live-team-assets"/self.team/("2"*64)
        old_dir.mkdir()
        old_path=old_dir/"matchup-evidence.json"
        old_path.write_text(json.dumps(old,ensure_ascii=False,sort_keys=True),encoding="utf-8")
        previous=eligible_v2_request(old_path,max_cells=6)
        save_proposals(old_dir/"ai-proposal-legacy.json",old_path,fake_reply(previous))
        self.assertNotEqual(
            self.primary.learning.coverage(self.catalog)["ai_evaluated"],6)
        self.assertEqual(self.primary.hydrate_unchanged(self.catalog)["reused_from_sqlite"],0)
        self.assertEqual(len(eligible_v2_request(self.catalog,max_cells=6)["cells"]),6)

    def test_sqlite_materialized_live_inputs_and_legacy_matrix_match(self):
        from champions_assist.team_live_assets import prepare_team_assets
        original=MASTER/"Pokemon_Champions_MC_team_2026-10-03.csv"
        self.primary.archive_all_sources(master_dir=MASTER)
        materialized_master,materialized_team,env=self.primary.materialize_for_live(
            team_id=self.team,master_dir=MASTER,
            source_path=original,environment_snapshot=None)
        self.assertIsNone(env)
        self.assertEqual(materialized_team.read_bytes(),original.read_bytes())
        for name in (
            "Pokemon_Champions_MC_move_master.csv",
            "Pokemon_Champions_MC_usable_pokemon.csv",
            "Pokemon_Champions_MC_pokemon_learnsets_linked.csv",
            "Pokemon_Champions_MC_live_selection_matrix.txt",
            "Pokemon_Champions_MC_60sec_lookup_master.txt",
        ):
            self.assertEqual((materialized_master/name).read_bytes(),
                             (MASTER/name).read_bytes())
        old=prepare_team_assets(master_dir=MASTER,
            data_root=self.root/"legacy-generation",
            team_id="2"*32,team_path=original)
        new=prepare_team_assets(master_dir=materialized_master,
            data_root=self.root/"sqlite-generation",
            team_id="2"*32,team_path=materialized_team)
        self.assertEqual(new.matrix_path.read_bytes(),old.matrix_path.read_bytes())
        self.assertEqual(new.lookup_path.read_bytes(),old.lookup_path.read_bytes())

    def test_401_retry_exact_once_without_resetting_saved_cells(self):
        requests=[]
        calls=[]
        class NotAuthorized(Exception):
            status_code=401
        def provider(force):
            calls.append(force)
            return "fresh" if force else "expired"
        def ai(req,*,access_token,**unused):
            requests.append((req,access_token))
            if access_token=="expired":
                raise NotAuthorized()
            return fake_reply(req)
        report=run_automatic_analysis(
            self.catalog,model="mock",access_token="expired",structured=True,
            max_batches=2,token_provider=provider,analyze=ai)
        self.assertEqual(report["count"],6)
        self.assertEqual(report["attempts"],2)
        self.assertEqual(calls,[False,True])
        self.assertEqual(requests[0][0],requests[1][0])
        self.assertEqual(self.primary.learning.coverage(self.catalog)["structured_v2"],6)


if __name__=="__main__":
    unittest.main()
