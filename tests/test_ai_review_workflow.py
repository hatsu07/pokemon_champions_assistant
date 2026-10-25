"""Regression for human-reviewed ChatGPT candidates and bounded LIVE notes.

These tests use synthetic evidence files: passing them does not certify a
Pokémon Champions game observation, damage engine, or safe hard switch.
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from champions_assist.app_config import ConfigurationError
from champions_assist.team_ai_analysis import SCHEMA as AI_SCHEMA, build_request, validate_reply, save_proposals
from champions_assist.ai_review_workflow import available_ai_reviews, make_human_review_packet
from champions_assist.matchup_evidence import import_reviews
from champions_assist.team_live_assets import prepare_team_assets, UNKNOWN_CELL
from champions_assist.team_management import TeamDocument

MASTER=Path(__file__).resolve().parents[1]/"masters"
BASE=MASTER/"Pokemon_Champions_MC_team_2026-10-03.csv"

class HumanReviewIntoLiveTest(unittest.TestCase):
    def setUp(self):
        t=TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.team_id="b"*32
        self.team=self.root/"team.csv"
        self.team.write_bytes(BASE.read_bytes())
        document=TeamDocument.load(self.team)
        rows=[dict(row) for row in document.rows]
        first=next(i for i,x in enumerate(rows) if x.get("record")=="MON")
        rows[first]["item"]="いのちのたま"
        self.team.write_text(TeamDocument(document.fieldnames,tuple(rows)).to_text(),encoding="utf-8")
        self.before=self.build()
        self.catalog=self.read_catalog(self.before)
        target=self.before.manifest_path.with_name("matchup-evidence.json")
        request=build_request(target,max_cells=2)
        raw=dict(schema=AI_SCHEMA,catalog_sha256=request["catalog_sha256"],
            proposals=[dict(opponent=c["opponent"],slot=c["slot"],
                            member_signature=c["member_signature"],
                            assessment="相手の型が未判明のため確定できない。",
                            risks="先制技に注意。",unknowns="相手の技とS")
                       for c in request["cells"]])
        proposal=validate_reply(request,raw)
        save_proposals(self.before.manifest_path.with_name("ai-proposal-test.json"),target,proposal)
        self.options=available_ai_reviews(data_root=self.root,team_id=self.team_id,catalog=self.catalog)
        self.assertEqual(len(self.options),2)
        self.proof1=self.root/"synthetic-proof-1.png"
        self.proof2=self.root/"synthetic-proof-2.png"
        self.proof1.write_bytes(b"synthetic input one")
        self.proof2.write_bytes(b"synthetic input two")

    def build(self):
        return prepare_team_assets(master_dir=MASTER,data_root=self.root,
                                   team_id=self.team_id,team_path=self.team)
    @staticmethod
    def read_catalog(ready):
        return json.loads(ready.manifest_path.with_name("matchup-evidence.json").read_text(encoding="utf-8"))
    def make(self,idx=0,proofs=None):
        return make_human_review_packet(
            data_root=self.root,team_id=self.team_id,
            catalog=self.read_catalog(self.build()),option=self.options[idx],
            evidence_files=proofs if proofs is not None else [self.proof1],
            rationale="実機観測を目視照合して条件付きで承認。",observed_moves=["確認した技"])
    def apply(self,packet):
        path=self.root/"review-input.json"
        path.write_text(json.dumps(packet,ensure_ascii=False),encoding="utf-8")
        import_reviews(data_root=self.root,team_id=self.team_id,
                       packet_path=path,candidate_catalog=self.read_catalog(self.build()))
        return self.build()

    def test_one_review_updates_matrix_lookup_warnings_not_tactical_cell(self):
        record=self.options[0]
        after=self.apply(self.make())
        matrix={r.split("|",1)[0]:r.split("|") for r in
                after.matrix_path.read_text(encoding="utf-8").splitlines() if r and not r.startswith("#")}
        lookups={r.split("|",1)[0]:r.split("|") for r in
                 after.lookup_path.read_text(encoding="utf-8").splitlines() if r and not r.startswith("#")}
        self.assertEqual(matrix[record["opponent"]][3+record["slot"]],UNKNOWN_CELL)
        self.assertIn("人間確認(",matrix[record["opponent"]][10])
        self.assertIn("処理受け未認定",matrix[record["opponent"]][10])
        self.assertEqual(matrix[record["opponent"]][10],lookups[record["opponent"]][10])
        self.assertEqual(len(matrix[record["opponent"]]),14)
        self.assertEqual(len(lookups[record["opponent"]]),12)
        result=self.read_catalog(after)
        self.assertEqual(result["counts"]["CONDITIONAL"],self.catalog["counts"]["CONDITIONAL"]+1)
        self.assertFalse(result["quality_gate"]["live_ready"])
        manifest=json.loads(after.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["live_observation_markers"],1)
        self.assertEqual(manifest["live_grade_certified_from_reviews"],0)
        self.assertTrue(self.build().cache_hit)

    def test_two_independent_proofs_confirm_observation_not_safety(self):
        after=self.apply(self.make(proofs=[self.proof1,self.proof2]))
        first=self.read_catalog(after)["cells"][0]
        self.assertEqual(first["evaluation"],"CONFIRMED")
        self.assertEqual(first["direct_processor"],"UNKNOWN")
        self.assertEqual(first["safe_hard_switch"],"UNKNOWN")
        self.assertFalse(self.read_catalog(after)["quality_gate"]["live_ready"])

    def test_another_review_preserves_existing_while_skipping_reviewed_candidates(self):
        self.apply(self.make())
        options=available_ai_reviews(data_root=self.root,team_id=self.team_id,
                                     catalog=self.read_catalog(self.build()))
        self.assertEqual(len(options),1)
        self.apply(self.make(idx=1))
        result=self.read_catalog(self.build())
        self.assertEqual(result["counts"]["CONDITIONAL"],self.catalog["counts"]["CONDITIONAL"]+2)
        self.assertEqual(len(available_ai_reviews(data_root=self.root,team_id=self.team_id,
                                                  catalog=result)),0)

    def test_requires_actual_non_ai_source_and_rejects_duplicates(self):
        with self.assertRaises(ConfigurationError):
            self.make(proofs=[self.proof1,self.proof1])
        with self.assertRaises(ConfigurationError):
            self.make(proofs=[self.before.manifest_path.with_name("ai-proposal-test.json")])
        wrong=dict(self.options[0],opponent="違う相手")
        with self.assertRaises(ConfigurationError):
            make_human_review_packet(data_root=self.root,team_id=self.team_id,
                catalog=self.catalog,option=wrong,evidence_files=[self.proof1],
                rationale="対面根拠が不一致です。")
        self.assertFalse((self.root/"matchup-reviews"/(self.team_id+".json")).exists())

    def test_changed_set_fails_closed_and_legacy_master_unmodified(self):
        original_matrix=(MASTER/"Pokemon_Champions_MC_live_selection_matrix.txt").read_bytes()
        doc=TeamDocument.load(self.team)
        rows=[dict(r) for r in doc.rows]
        pos=next(i for i,r in enumerate(rows) if r.get("record")=="MON")
        rows[pos]["move1"]="ボルトチェンジ"
        self.team.write_text(TeamDocument(doc.fieldnames,tuple(rows)).to_text(),encoding="utf-8")
        current=self.read_catalog(self.build())
        self.assertEqual(available_ai_reviews(data_root=self.root,team_id=self.team_id,
                                              catalog=current),[])
        self.assertEqual((MASTER/"Pokemon_Champions_MC_live_selection_matrix.txt").read_bytes(),
                         original_matrix)

if __name__=="__main__":
    unittest.main()
