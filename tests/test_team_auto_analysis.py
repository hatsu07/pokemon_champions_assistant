"""Bounded automatic AI batches and Champions-only LIVE warning projection."""
import json
import tempfile
import unittest
from pathlib import Path

from champions_assist.app_config import ConfigurationError
from champions_assist.team_auto_analysis import run_automatic_analysis
from champions_assist.team_ai_analysis import SCHEMA


class AutomaticMatchupTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.path=self.root/"matchup-evidence.json"
        cells=[]
        for slot in range(6):
            for opponent in ("ガブリアス","マリルリ","ピカチュウ"):
                cells.append(dict(opponent=opponent,slot=slot,own="test",
                    member_signature=str(slot),evaluation="UNKNOWN",missing_source=[],
                    own_species={},opponent_species={},registered_moves=[],
                    registered_build={},opponent_priority_candidates=[],
                    opponent_potential_high_power_moves=[]))
        self.path.write_text(json.dumps(dict(schema="champions-matchup-evidence-v1",
            regulation="M-C",member_signatures=[str(i) for i in range(6)],
            cells=cells)),encoding="utf-8")
        self.calls=[]

    def analyzer(self,request,**_kwargs):
        self.calls.append(len(request["cells"]))
        return dict(schema=SCHEMA,catalog_sha256=request["catalog_sha256"],
            regulation=request["regulation"],member_signatures=request["member_signatures"],
            status="AI_PROPOSAL_UNVERIFIED",live_approved=False,
            processor_certified=False,safe_switch_certified=False,
            proposals=[dict(opponent=c["opponent"],slot=c["slot"],
                member_signature=c["member_signature"],assessment="追加検証が必要",
                risks="相手の型は不明",unknowns="ダメージ/S") for c in request["cells"]])

    def call(self,**options):
        return run_automatic_analysis(self.path,model="mock",access_token="mock",
                                      analyze=self.analyzer,**options)

    def test_public_usage_priority_changes_order_not_certification(self):
        from champions_assist.team_ai_analysis import build_request
        first = build_request(self.path,max_cells=1,priority_opponents=["ピカチュウ"])
        self.assertEqual(first["cells"][0]["opponent"],"ピカチュウ")
        result=self.call(max_batches=1,priority_opponents=["ピカチュウ"])
        self.assertEqual(result["count"],12)
        saved=list(self.root.glob("ai-proposal-*.json"))
        rows=json.loads(saved[0].read_text(encoding="utf-8"))["proposals"]
        self.assertTrue(all(r["opponent"]=="ピカチュウ" for r in rows[:6]))
        self.assertFalse(json.loads(saved[0].read_text(encoding="utf-8"))["live_approved"])

    def test_one_action_continues_multiple_batches(self):
        events=[]
        result=self.call(progress=events.append)
        self.assertEqual(result,dict(status="COMPLETE",count=18,batches=2))
        self.assertEqual(self.calls,[12,6])
        self.assertEqual(len(events),2)
        self.assertEqual(len(list(self.root.glob("ai-proposal-*.json"))),2)
        self.assertEqual(self.call()["count"],0)

    def test_capped_and_resumable(self):
        result=self.call(max_batches=1)
        self.assertEqual(result["status"],"BATCH_LIMIT")
        self.assertEqual(result["count"],12)
        self.assertEqual(self.call()["count"],6)

    def test_interrupt_saves_previous_only(self):
        events=[]
        result=self.call(interrupted=lambda:len(events)>=1,progress=events.append)
        self.assertEqual(result["status"],"INTERRUPTED")
        self.assertEqual(result["count"],12)
        self.assertEqual(len(list(self.root.glob("ai-proposal-*.json"))),1)

    def test_model_failure_keeps_previous_and_does_not_certify(self):
        def failing(request,**kwargs):
            if self.calls:
                raise RuntimeError("MODEL_ERROR")
            return self.analyzer(request,**kwargs)
        with self.assertRaises(RuntimeError):
            run_automatic_analysis(self.path,model="mock",access_token="mock",analyze=failing)
        files=list(self.root.glob("ai-proposal-*.json"))
        self.assertEqual(len(files),1)
        self.assertFalse(json.loads(files[0].read_text(encoding="utf-8"))["live_approved"])
        self.assertEqual(self.call()["count"],6)

    def test_reject_fake_approval_and_malformed_matchup_response(self):
        def fake(request,**kwargs):
            result=self.analyzer(request,**kwargs)
            result["live_approved"]=True
            return result
        with self.assertRaises(ConfigurationError):
            run_automatic_analysis(self.path,model="mock",access_token="mock",analyze=fake)
        self.assertFalse(list(self.root.glob("ai-proposal-*.json")))
        def wrong_keys(request,**kwargs):
            result=self.analyzer(request,**kwargs)
            result["proposals"][0]["opponent"]="別の相手"
            return result
        with self.assertRaises(ConfigurationError):
            run_automatic_analysis(self.path,model="mock",access_token="mock",analyze=wrong_keys)
        self.assertFalse(list(self.root.glob("ai-proposal-*.json")))

    def test_no_model_call_when_complete_or_invalid_limit(self):
        with self.assertRaises(ConfigurationError):self.call(max_batches=181)
        with self.assertRaises(ConfigurationError):self.call(max_batches=True)
        self.call()
        n=len(self.calls)
        self.call()
        self.assertEqual(len(self.calls),n)


class MasterEvidenceProjectionTests(unittest.TestCase):
    def test_auto_priority_learnset_warning_never_promotes_tactical_grade(self):
        from champions_assist.team_live_assets import prepare_team_assets,UNKNOWN_CELL
        from champions_assist.team_management import TeamDocument
        root=Path(__file__).resolve().parents[1]
        masters=root/"masters"
        with tempfile.TemporaryDirectory() as temporary:
            tmp=Path(temporary)
            team=tmp/"team.csv"
            team.write_bytes((masters/"Pokemon_Champions_MC_team_2026-10-03.csv").read_bytes())
            document=TeamDocument.load(team)
            rows=[dict(r) for r in document.rows]
            first=next(i for i,r in enumerate(rows) if r.get("record")=="MON")
            rows[first]["item"]="いのちのたま"
            team.write_text(TeamDocument(document.fieldnames,tuple(rows)).to_text(),encoding="utf-8")
            assets=prepare_team_assets(master_dir=masters,data_root=tmp,
                                       team_id="e"*32,team_path=team)
            manifest=json.loads(assets.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(assets.status,"PROVISIONAL_UNVERIFIED")
            self.assertEqual(manifest["auto_master_priority_status"],
                             "LEARNSET_ONLY_NOT_OPPONENT_SET")
            catalog=json.loads(assets.manifest_path.with_name("matchup-evidence.json").read_text(encoding="utf-8"))
            target=next(c for c in catalog["cells"]
                        if c["slot"]==0 and c["opponent_priority_candidates"]
                        and not any(x in c["missing_source"] for x in
                                    ("OPPONENT_SPECIES_MASTER_NOT_FOUND",
                                     "OPPONENT_LEARNSET_NOT_FOUND")))
            matrix={r.split("|",1)[0]:r.split("|") for r in
                    assets.matrix_path.read_text(encoding="utf-8").splitlines()
                    if r and not r.startswith("#")}
            lookup={r.split("|",1)[0]:r.split("|") for r in
                    assets.lookup_path.read_text(encoding="utf-8").splitlines()
                    if r and not r.startswith("#")}
            cols=matrix[target["opponent"]]
            self.assertIn("Champions習得可能な優先度+技(採用未確定):",cols[10])
            self.assertEqual(cols[10],lookup[target["opponent"]][10])
            self.assertEqual(cols[3],UNKNOWN_CELL)
            self.assertEqual(manifest["live_grade_certified_from_reviews"],0)
            self.assertFalse(manifest["quality_gate"]["live_ready"])


if __name__=="__main__":
    unittest.main()
