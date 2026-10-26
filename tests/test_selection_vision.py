"""No-network acceptance tests for image-to-three selection safeguards."""
from __future__ import annotations
import unittest
from types import MappingProxyType
from champions_assist.preload import FrozenMatchAssets
from champions_assist.selection_vision import (
    SelectionPlanner, verify_selection_observation, SelectionBlocked,
    selection_summary_ja, unmatched_opponent_slots,
    roster_constrained_retry_guidance, retry_unresolved_opponent_six,
)

OWN=("マスカーニャ","アシレーヌ","ガブリアス","ルカリオ → メガルカリオZ","サーフゴー","ミミッキュ")
OPP=("ピカチュウ","ドドゲザン","ゲンガー","マリルリ","ギャラドス","カイリュー")
def assets():
    rows={name:name+"|matrix" for name in OPP}
    return FrozenMatchAssets(
        contents=MappingProxyType({"Pokemon_Champions_MC_live_decision_gate.txt":"CHAMPIONS_GATE",
            "Pokemon_Champions_MC_live_selection_matrix.txt":"# OWN={M:マスカーニャ,P:アシレーヌ,C:ガブリアス,L:メガルカリオZ,G:サーフゴー,K:ミミッキュ}\n"}),
        sha256=MappingProxyType({}),
        matrix_index=MappingProxyType(rows),
        lookup_index=MappingProxyType({}),
        own_team=tuple({"name":name} for name in OWN),
        frozen_at="fixture",
    )
def observation():
    return {"visual_ui":"TEAM_SELECTION","our_six":[name.split("→",1)[0].strip() for name in OWN],"opponent_six":list(OPP),"uncertain_slots":[]}

class SelectionVisionTest(unittest.TestCase):
    def test_exact_user_english_screenshot_matches_japanese_registered_team(self):
        # Reproduce the actual observed 2026-10-22 six-on-six diagnostic.
        english_own=("Pikachu","Kingambit","Garchomp","Azumarill","Gyarados","Gengar")
        own=("ピカチュウ","ドドゲザン","ガブリアス","マリルリ","ギャラドス","ゲンガー")
        english_opp=("Meowscarada","Primarina","Garchomp","Lucario","Gholdengo","Mimikyu")
        opponent=("マスカーニャ","アシレーヌ","ガブリアス","ルカリオ","サーフゴー","ミミッキュ")
        fixture=FrozenMatchAssets(
            contents=MappingProxyType({"Pokemon_Champions_MC_live_decision_gate.txt":"GATE",
                "Pokemon_Champions_MC_live_selection_matrix.txt":"# OWN={P:ピカチュウ,K:ドドゲザン,C:ガブリアス,A:マリルリ,G:ギャラドス,E:ゲンガー}\n"}),
            sha256=MappingProxyType({}),
            matrix_index=MappingProxyType({name: name+"|matrix" for name in opponent}),
            lookup_index=MappingProxyType({}),
            own_team=tuple({"name":name,"role":""} for name in own),
            frozen_at="fixture",
        )
        observed={"visual_ui":"TEAM_SELECTION","our_six":list(english_own),
                  "opponent_six":list(english_opp),"uncertain_slots":[]}
        self.assertEqual(verify_selection_observation(observed,fixture),opponent)
        calls=[]
        def chooser(context,seconds):
            calls.append(context)
            self.assertEqual(context["opponent_six"],list(opponent))
            self.assertEqual(len(context["matrix_rows_six"]),6)
            # Tactical model may also answer with English names.
            return {"selected_three":["Pikachu","Kingambit","Garchomp"],"lead":"Pikachu"}
        result=SelectionPlanner(clock=lambda:15.0).choose(observed,fixture,chooser,started_at=5.0)
        self.assertEqual(result.status,"OK")
        self.assertEqual(result.selected_three,own[:3])
        self.assertEqual(result.lead,"ピカチュウ")
        self.assertEqual(result.opponent_six,opponent)
        self.assertEqual(result.as_dict()["observed_own_six"],[])
        self.assertEqual(result.as_dict()["observed_opponent_six"],list(opponent))
        self.assertEqual(result.as_dict()["only_in_image"],[])
        self.assertEqual(result.as_dict()["only_in_registered_team"],[])
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]["own_roster_source"],"PRELOADED_USER_SELECTED_TEAM_NOT_VISION")

    def test_out_of_roster_vision_hallucination_is_not_missing_ai_evidence(self):
        # Captured on 2026-10-26: five correct English names, slot 3 was
        # hallucinated as Gouging Fire instead of a valid Champions entry.
        valid=(
            "ロトム（ウォッシュロトム）","ハッサム","クエスパトラ",
            "キラフロル","ウルガモス","カバルドン",
        )
        local=assets()
        frozen=FrozenMatchAssets(
            contents=local.contents,sha256=local.sha256,
            matrix_index=MappingProxyType({p:p+"|matrix" for p in valid}),
            lookup_index=local.lookup_index,own_team=local.own_team,
            frozen_at=local.frozen_at,
        )
        first={
            "visual_ui":"TEAM_SELECTION","our_six":[],
            "opponent_six":["Rotom-Wash","Scizor","Gouging Fire",
                            "Glimmora","Volcarona","Hippowdon"],
            "uncertain_slots":[],
        }
        self.assertEqual(unmatched_opponent_slots(first,frozen),[
            {"slot":3,"label":"Gouging Fire"}])
        with self.assertRaisesRegex(SelectionBlocked,"OPPONENT_MATRIX_MISSING"):
            verify_selection_observation(first,frozen)
        guidance=roster_constrained_retry_guidance(
            reason="OPPONENT_MATRIX_MISSING",previous=first,assets=frozen)
        self.assertIn("3:Gouging Fire",guidance)
        self.assertIn("クエスパトラ",guidance)
        self.assertNotIn("MATRIX_CANONICAL_NAMES: Gouging Fire",guidance)
        calls=[]
        def model_retry(reason,previous):
            calls.append((reason,tuple(previous["opponent_six"])))
            return {**previous,"opponent_six":[
                "Rotom-Wash","Scizor","Espathra","Glimmora",
                "Volcarona","Hippowdon"]}
        second,attempts=retry_unresolved_opponent_six(
            first,frozen,model_retry,remaining_seconds=lambda:40.0)
        self.assertEqual(attempts,2)
        self.assertEqual(len(calls),1)
        self.assertEqual(verify_selection_observation(second,frozen),valid)
        # No silent code-side substitution on a second wrong observation.
        second_bad,again=retry_unresolved_opponent_six(
            first,frozen,lambda reason,previous:dict(previous),
            remaining_seconds=lambda:40.0)
        self.assertEqual(again,2)
        with self.assertRaises(SelectionBlocked):
            verify_selection_observation(second_bad,frozen)

    def test_retry_never_runs_after_time_budget_exhausted(self):
        valid=("ロトム（ウォッシュロトム）","ハッサム","クエスパトラ",
               "キラフロル","ウルガモス","カバルドン")
        local=assets()
        frozen=FrozenMatchAssets(
            contents=local.contents,sha256=local.sha256,
            matrix_index=MappingProxyType({p:p+"|matrix" for p in valid}),
            lookup_index=local.lookup_index,own_team=local.own_team,
            frozen_at=local.frozen_at,
        )
        first={"visual_ui":"TEAM_SELECTION","opponent_six":[
            "Rotom-Wash","Scizor","Gouging Fire","Glimmora",
            "Volcarona","Hippowdon"],"uncertain_slots":[]}
        called=[]
        data,count=retry_unresolved_opponent_six(
            first,frozen,lambda *args:called.append(args),
            remaining_seconds=lambda:9.0,reserve_seconds=21.0)
        self.assertEqual(count,1)
        self.assertFalse(called)
        self.assertEqual(data["opponent_six"][2],"Gouging Fire")

    def test_mismatched_matrix_blocks_ordinary_selection_but_allows_labeled_function_test(self):
        original=assets()
        bad=FrozenMatchAssets(
            contents=MappingProxyType({
                "Pokemon_Champions_MC_live_decision_gate.txt":"GATE",
                "Pokemon_Champions_MC_live_selection_matrix.txt":"# OWN={A:ピカチュウ,B:ドドゲザン,C:ガブリアス,D:マリルリ,E:ギャラドス,F:ゲンガー}\n",
            }),
            sha256=original.sha256,matrix_index=original.matrix_index,
            lookup_index=original.lookup_index,own_team=original.own_team,
            frozen_at=original.frozen_at,
        )
        called=[]
        blocked=SelectionPlanner(clock=lambda:2).choose(
            observation(),bad,lambda *args: called.append(args),started_at=0)
        self.assertEqual(blocked.status,"BLOCKED")
        self.assertEqual(blocked.reason,"OWN_MATRIX_MISMATCH")
        self.assertEqual(called,[])
        def model(ctx,seconds):
            self.assertEqual(ctx["matrix_rows_six"],[])
            self.assertEqual(ctx["frozen_gate"],"")
            return {"selected_three":list(OWN[:3]),"lead":OWN[0]}
        test=SelectionPlanner(clock=lambda:3).choose(
            observation(),bad,model,started_at=0,allow_test_without_matrix=True)
        self.assertEqual(test.status,"TEST_ONLY")
        self.assertIn("機能試験",selection_summary_ja(test.as_dict()))

    def test_japanese_player_facing_summary(self):
        self.assertEqual(
            selection_summary_ja({
                "status":"OK",
                "selected_three":["Pikachu","Kingambit","Garchomp"],
                "lead":"Pikachu",
            }),
            "選出：ピカチュウ / ドドゲザン / ガブリアス　初手：ピカチュウ",
        )
        self.assertEqual(
            selection_summary_ja({"status":"BLOCKED","reason":"REGISTERED_TEAM_INVALID"}),
            "選出できません：事前ロードした登録構築6匹に重複・不足があります。",
        )

    def test_ambiguous_species_form_is_not_guessed(self):
        base=assets()
        rows=dict(base.matrix_index)
        rows["ライチュウ（通常のすがた）"]="normal|matrix"
        rows["ライチュウ（アローラのすがた）"]="alolan|matrix"
        two_forms=FrozenMatchAssets(
            contents=base.contents,sha256=base.sha256,
            matrix_index=MappingProxyType(rows),
            lookup_index=base.lookup_index,own_team=base.own_team,
            frozen_at=base.frozen_at,
        )
        obs=observation()
        obs["opponent_six"][0]="Raichu"
        result=SelectionPlanner(clock=lambda:8.0).choose(
            obs,two_forms,lambda *_:None,started_at=0.0)
        self.assertEqual(result.reason,"OPPONENT_FORM_AMBIGUOUS")
        self.assertEqual(result.selected_three,())
        # A visually identified specific form can be matched exactly.
        obs["opponent_six"][0]="Alolan Raichu"
        self.assertEqual(verify_selection_observation(obs,two_forms)[0],
                         "ライチュウ（アローラのすがた）")

    def test_hallucinated_own_species_is_ignored_and_registry_wins(self):
        obs=observation()
        obs["our_six"][0]="UnrecognizedCreature"
        called=[]
        result=SelectionPlanner(clock=lambda:5.0).choose(
            obs,assets(),lambda context,_:(
                called.append(context) or
                {"selected_three":list(OWN[:3]),"lead":OWN[0]}),started_at=0.0)
        self.assertEqual(result.status,"OK")
        self.assertEqual(result.selected_three,OWN[:3])
        self.assertEqual(result.as_dict()["observed_own_six"],[])
        self.assertEqual(result.as_dict()["only_in_image"],[])
        self.assertEqual(called[0]["own_team"][0]["name"],OWN[0])

    def test_alias_duplicate_in_opponent_six_blocks(self):
        obs=observation()
        obs["opponent_six"][0]="Pikachu"
        obs["opponent_six"][1]="ピカチュウ"
        result=SelectionPlanner(clock=lambda:5.0).choose(obs,assets(),lambda *_:None,started_at=0.0)
        self.assertEqual(result.reason,"OPPONENT_SIX_DUPLICATES")

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

    def test_blank_role_is_presented_for_contextual_ai_inference(self):
        frozen=assets()
        called=[]
        def chooser(context,seconds):
            called.append(context)
            return {"selected_three":["マスカーニャ","アシレーヌ","ガブリアス"],"lead":"マスカーニャ"}
        result=SelectionPlanner(clock=lambda:4.0).choose(
            observation(),frozen,chooser,started_at=0.0)
        self.assertEqual(result.status,"OK")
        self.assertTrue(any("infer" in word.lower() for word in called[0]["instructions"].split()))
        self.assertTrue(all(not row.get("role") for row in called[0]["own_team"]))

    def test_registered_own_six_not_blocked_by_mismatch_or_ocr_typo(self):
        for hallucination in ("ピカチュウ","ガブリヤス","Iron Valiant"):
            obs=observation()
            obs["our_six"][0]=hallucination
            calls=[]
            result=SelectionPlanner(clock=lambda:10.0).choose(
                obs,assets(),
                lambda ctx, remaining:(
                    calls.append(ctx) or {"selected_three":list(OWN[:3]),"lead":OWN[0]}),
                started_at=5.0)
            self.assertEqual(result.status,"OK")
            self.assertEqual(len(calls),1)
            self.assertEqual(result.as_dict()["only_in_image"],[])
            self.assertEqual(result.as_dict()["only_in_registered_team"],[])

    def test_invalid_frozen_team_still_blocks_before_tactics(self):
        original=assets()
        from dataclasses import replace
        invalid=replace(original,own_team=tuple(original.own_team[:5]))
        called=[]
        result=SelectionPlanner(clock=lambda:5).choose(
            observation(),invalid,lambda *args:called.append(args),started_at=0)
        self.assertEqual(result.reason,"REGISTERED_TEAM_INVALID")
        self.assertEqual(called,[])

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
