"""SQLite migration and next-match learning: offline synthetic tests.

No real captures, API calls, private user files or automatic game controls.
"""
from __future__ import annotations
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from champions_assist.app_config import ConfigurationError
from champions_assist.matchup_learning_store import MatchupLearningStore
from champions_assist.team_ai_analysis import save_proposals, validate_reply, SCHEMA as V1
from champions_assist.team_matchup_v2 import eligible_v2_request, validate_v2_reply, SCHEMA_V2
from champions_assist.postmatch_learning import request_review, REVIEW_SCHEMA
from champions_assist.postmatch_matrix_notes import publish_postmatch_notes
from champions_assist.team_annotation_ai import (
    validate_annotation_draft,SCHEMA as ANNOT_SCHEMA)


class LearningSQLiteTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.asset=self.root/"live-team-assets"/("a"*32)/("b"*32)
        self.asset.mkdir(parents=True)
        self.catalog=self.asset/"matchup-evidence.json"
        members=[{"opponent":"カバルドン","slot":i,"member_signature":f"team-{i}",
                  "own":f"ours-{i}","evaluation":"UNKNOWN","missing_source":[],
                  "own_species":{"base_speed":100},"opponent_species":{"base_speed":40},
                  "registered_moves":[{"name":"じしん"}],
                  "registered_build":{"item":"-"},
                  "opponent_priority_candidates":[],
                  "opponent_potential_high_power_moves":[]} for i in range(6)]
        self.catalog.write_text(json.dumps({
            "schema":"champions-matchup-evidence-v1",
            "regulation":"M-C","member_signatures":[f"team-{i}" for i in range(6)],
            "master_sha256":{"move":"testhash"},"cells":members
        },ensure_ascii=False),encoding="utf-8")
        self.store=MatchupLearningStore(self.root)

    def proposal(self, *, max_cells=3):
        req=eligible_v2_request(self.catalog,max_cells=max_cells)
        rows=[{**c,"attack_move":"じしん","base_speed":"BASE_FASTER",
               "damage":"UNKNOWN","priority_risk":"UNKNOWN",
               "setup_risk":"UNKNOWN","direct_processor":"UNKNOWN",
               "safe_switch":"UNKNOWN","note":"仮説","unknowns":"相手の型"} for c in req["cells"]]
        return validate_v2_reply(req,{
            "schema":SCHEMA_V2,"catalog_sha256":req["catalog_sha256"],
            "proposals":rows})

    def test_v28_batches_import_without_mutation_and_resume_with_new_files(self):
        first=self.asset/"ai-proposal-first.json"
        save_proposals(first,self.catalog,self.proposal(max_cells=3))
        original=first.read_bytes()
        before_cat=self.catalog.read_bytes()
        result=self.store.import_legacy_assets()
        self.assertEqual(result["imported_cells"],3)
        self.assertTrue(result["legacy_files_preserved"])
        self.assertEqual(self.store.coverage(self.catalog)["structured_v2"],3)
        self.assertEqual(first.read_bytes(),original)
        self.assertEqual(self.catalog.read_bytes(),before_cat)
        self.assertEqual(self.store.import_legacy_assets()["imported_cells"],0)
        # v28 app continues completing batches after initial migration.
        second=self.asset/"ai-proposal-second.json"
        save_proposals(second,self.catalog,self.proposal(max_cells=3))
        result=self.store.import_legacy_assets()
        self.assertEqual(result["imported_cells"],3)
        self.assertEqual(self.store.coverage(self.catalog)["ai_evaluated"],6)
        self.assertEqual(result["stored_batches"],2)
        self.assertTrue(second.is_file())

    def test_v1_old_prose_still_imports_but_does_not_pretend_structured(self):
        from champions_assist.team_ai_analysis import build_request
        req=build_request(self.catalog,max_cells=4)
        value=validate_reply(req,{
            "schema":V1,"catalog_sha256":req["catalog_sha256"],
            "proposals":[{**{k:c[k] for k in
                           ("opponent","slot","member_signature")},
                          "assessment":"まだ仮説","risks":"安全受け未確認",
                          "unknowns":"被ダメージ"} for c in req["cells"]]})
        save_proposals(self.asset/"ai-proposal-legacy.json",self.catalog,value)
        self.assertEqual(self.store.import_legacy_assets()["imported_cells"],4)
        self.assertEqual(self.store.coverage(self.catalog)["structured_v2"],0)
        self.assertEqual(self.store.coverage(self.catalog)["ai_evaluated"],4)

    def test_corrupt_or_stale_batches_not_recorded(self):
        proposal=self.proposal()
        proposal["live_approved"]=True
        bad=self.asset/"ai-proposal-bad.json"
        bad.write_text(json.dumps(proposal,ensure_ascii=False),encoding="utf-8")
        self.assertEqual(self.store.import_legacy_assets()["imported_cells"],0)
        self.assertEqual(self.store.coverage(self.catalog)["ai_evaluated"],0)
        bad.write_text("{",encoding="utf-8")
        self.assertEqual(self.store.import_legacy_assets()["imported_cells"],0)
        self.assertTrue(bad.is_file())

    def test_append_only_match_and_next_match_hypothesis_overlay(self):
        state={"phase":"ENDED","result":"LOSS",
               "opponent_six":["カバルドン"],"selected_three":["ガブリアス"],
               "lead":"ガブリアス",
               "history":[{"ui":"RESULT_SCREEN","result":"MATCH_END"}],
               "battle_evidence":{"hp_by_pokemon":{"own":{"ガブリアス":"50%"},
                   "opponent":{"カバルドン":"65%"}}},
               "observed_hp":{"own":"50%","opponent":"65%"}}
        self.assertTrue(self.store.record_finished(
            match_id="m1",team_id="a"*32,state=state))
        self.assertFalse(self.store.record_finished(
            match_id="m1",team_id="a"*32,state=state))
        self.assertIsNotNone(self.store.pending_match("m1"))
        review={"schema":REVIEW_SCHEMA,"match_id":"m1",
                "findings":[{"opponent":"カバルドン","our_pokemon":"ガブリアス",
                             "observation":"HPを視認候補として記録",
                             "issue":"受け出しの安全性不明",
                             "suggestion":"最大打点を確認",
                             "certainty":"HYPOTHESIS"}],
                "next_match_notes":"交代前に想定最大打点を確認"}
        self.assertTrue(self.store.save_postmatch_review(match_id="m1",review=review))
        self.assertIsNone(self.store.pending_match("m1"))
        self.assertEqual(len(self.store.recent_review_notes("a"*32)),1)
        cols=["カバルドン","type","90"]+["?,?,?,?,U,0"]*6+["-","-","-","??/??/??/??/??/??","-"]
        self.assertEqual(len(cols),14)
        matrix=self.asset/"matrix.txt"
        text="# TEAM_SCOPED_MATRIX v1; test\n"+"|".join(cols)+"\n"
        matrix.write_text(text,encoding="utf-8")
        manifest=self.asset/"manifest.json"
        manifest.write_text(json.dumps({"status":"PROVISIONAL_UNVERIFIED",
            "matrix_sha256":sha256(text.encode()).hexdigest()}),encoding="utf-8")
        out=publish_postmatch_notes(
            data_root=self.root,team_id="a"*32,
            manifest_path=manifest,matrix_path=matrix)
        self.assertEqual(out["postmatch_hint_rows"],1)
        new=matrix.read_text(encoding="utf-8")
        self.assertIn("POSTMATCH_UNVERIFIED:",new)
        self.assertIn("?,?,?,?,U,0",new)
        self.assertEqual(len(new.splitlines()[-1].split("|")),14)
        self.assertEqual(publish_postmatch_notes(
            data_root=self.root,team_id="a"*32,
            manifest_path=manifest,matrix_path=matrix),out)
        self.assertEqual(matrix.read_text(encoding="utf-8"),new)

    def test_postmatch_chatgpt_mock_no_unobserved_species(self):
        state={"phase":"ENDED","result":"WIN","opponent_six":["カバルドン"],
               "selected_three":["ガブリアス"],"history":[],
               "battle_evidence":{}}
        self.assertTrue(self.store.record_finished(
            match_id="m2",team_id="a"*32,state=state))
        reply={"schema":REVIEW_SCHEMA,"match_id":"m2","findings":[
            {"opponent":"カバルドン","our_pokemon":"ガブリアス",
             "observation":"不明","issue":"Sの確認が必要",
             "suggestion":"情報不足を維持","certainty":"HYPOTHESIS"}],
            "next_match_notes":"次戦ではSを確認"}
        events=[SimpleNamespace(type="response.output_text.delta",
                                delta=json.dumps(reply,ensure_ascii=False)),
                SimpleNamespace(type="response.completed")]
        class Stream:
            def __enter__(self):return self
            def __exit__(self,*x):pass
            def __iter__(self):return iter(events)
        client=lambda **_kw:SimpleNamespace(
            responses=SimpleNamespace(create=lambda **_kw:Stream()))
        result=request_review(data_root=self.root,match_id="m2",model="mock",
                              access_token="fake",client_factory=client)
        self.assertEqual(result["status"],"STORED_UNVERIFIED_REVIEW")
        self.assertIsNone(self.store.pending_match("m2"))
        self.assertEqual(request_review(
            data_root=self.root,match_id="m2",model="mock",
            access_token="fake",client_factory=client)["status"],"NO_PENDING_MATCH")

    def test_optional_annotation_never_invents_new_form(self):
        members=[{"name":"ルカリオ → メガルカリオZ"}]+[
            {"name":"ポケモン"+str(i)} for i in range(5)]
        def draft():
            return {"schema":ANNOT_SCHEMA,"suggestions":[{
                "slot":i,"role":"PIVOT","form_change":(
                    "ルカリオ=>メガルカリオZ" if i==0 else ""),
                "runtime_note":"技固定に注意"} for i in range(6)]}
        self.assertEqual(len(validate_annotation_draft(members,draft())),6)
        fake=draft()
        fake["suggestions"][2]["form_change"]="メガXY"
        with self.assertRaises(ConfigurationError):
            validate_annotation_draft(members,fake)


if __name__=="__main__":
    unittest.main()
