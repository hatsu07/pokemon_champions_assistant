"""No-network acceptance tests for image-to-three selection safeguards."""
from __future__ import annotations
import unittest
from types import MappingProxyType
from champions_assist.preload import FrozenMatchAssets
from champions_assist.selection_vision import SelectionPlanner, verify_selection_observation, SelectionBlocked

OWN=("マスカーニャ","アシレーヌ","ガブリアス","ルカリオ → メガルカリオZ","サーフゴー","ミミッキュ")
OPP=("ピカチュウ","ドドゲザン","ゲンガー","マリルリ","ギャラドス","カイリュー")
def assets():
    rows={name:name+"|matrix" for name in OPP}
    return FrozenMatchAssets(
        contents=MappingProxyType({"Pokemon_Champions_MC_live_decision_gate.txt":"CHAMPIONS_GATE"}),
        sha256=MappingProxyType({}),
        matrix_index=MappingProxyType(rows),
        lookup_index=MappingProxyType({}),
        own_team=tuple({"name":name} for name in OWN),
        frozen_at="fixture",
    )
def observation():
    return {"visual_ui":"TEAM_SELECTION","our_six":[name.split("→",1)[0].strip() for name in OWN],"opponent_six":list(OPP),"uncertain_slots":[]}

class SelectionVisionTest(unittest.TestCase):
    def test_six_matrix_rows_and_exact_three_with_lead(self):
        obs=observation()
        self.assertEqual(verify_selection_observation(obs,assets()),OPP)
        called=[]
        def chooser(context,deadline):
            called.append((context,deadline))
            return {"selected_three":list(OWN[:3]),"lead":OWN[0]}
        result=SelectionPlanner(clock=lambda:10.5).choose(obs,assets(),chooser,started_at=10.0)
        self.assertEqual(result.status,"OK")
        self.assertEqual(result.selected_three,OWN[:3])
        self.assertEqual(result.lead,OWN[0])
        self.assertEqual(len(called[0][0]["matrix_rows_six"]),6)

    def test_form_alias_is_normalized_to_registered_name(self):
        obs=observation()
        def chooser(context,seconds):
            return {"selected_three":["ルカリオ","マスカーニャ","ガブリアス"],"lead":"ルカリオ"}
        result=SelectionPlanner(clock=lambda:10.0).choose(obs,assets(),chooser,started_at=0.0)
        self.assertEqual(result.status,"OK")
        self.assertEqual(result.selected_three[0],"ルカリオ → メガルカリオZ")
        self.assertEqual(result.lead,"ルカリオ → メガルカリオZ")

    def test_mismatch_blocks_before_model(self):
        obs=observation()
        obs["our_six"][0]="ピカチュウ"
        calls=[]
        result=SelectionPlanner(clock=lambda:10.0).choose(obs,assets(),lambda *args:calls.append(args),started_at=10.0)
        self.assertEqual(result.status,"BLOCKED")
        self.assertEqual(result.reason,"REGISTERED_TEAM_MISMATCH")
        self.assertEqual(result.as_dict()["observed_own_six"][0],"ピカチュウ")
        self.assertEqual(result.as_dict()["observed_opponent_six"],list(OPP))
        self.assertEqual(result.as_dict()["only_in_image"],["ピカチュウ"])
        self.assertEqual(result.as_dict()["only_in_registered_team"],["マスカーニャ"])
        self.assertEqual(result.opponent_six,())  # Unverified, never promoted to trusted.
        self.assertEqual(calls,[])

    def test_ocr_typo_is_explicit_in_diagnostics_not_silently_corrected(self):
        obs=observation()
        obs["our_six"][2]="ガブリヤス"
        result=SelectionPlanner(clock=lambda:12.0).choose(obs,assets(),lambda *_:None,started_at=12.0)
        self.assertEqual(result.reason,"REGISTERED_TEAM_MISMATCH")
        self.assertIn("ガブリヤス",result.as_dict()["only_in_image"])
        self.assertIn("ガブリアス",result.as_dict()["only_in_registered_team"])
        self.assertEqual(result.selected_three,())

    def test_unreadable_opponent_blocks_without_guess(self):
        obs=observation()
        obs["opponent_six"]=obs["opponent_six"][:5]
        result=SelectionPlanner(clock=lambda:10.0).choose(obs,assets(),lambda *_:None,started_at=10.0)
        self.assertEqual(result.reason,"OPPONENT_SIX_INCOMPLETE")

    def test_deadline_and_invalid_recommendation_block(self):
        obs=observation()
        bad=SelectionPlanner(clock=lambda:45.0).choose(obs,assets(),lambda *_:None,started_at=0.0)
        self.assertEqual(bad.status,"DEADLINE_EXCEEDED")
        invalid=SelectionPlanner(clock=lambda:10.0).choose(obs,assets(),
            lambda *_:{"selected_three":[OWN[0],OWN[0],OWN[1]],"lead":OWN[0]},started_at=0.0)
        self.assertEqual(invalid.status,"BLOCKED")
        self.assertEqual(invalid.reason,"INVALID_SELECTION")
