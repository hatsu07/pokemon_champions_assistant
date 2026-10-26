"""No-network acceptance tests for image-to-three selection safeguards."""
from __future__ import annotations
import unittest
from types import MappingProxyType
from champions_assist.preload import FrozenMatchAssets
from champions_assist.selection_vision import (
    SelectionPlanner, verify_selection_observation, SelectionBlocked,
    selection_summary_ja, unmatched_opponent_slots,
    roster_constrained_retry_guidance, retry_unresolved_opponent_six,
    partial_opponent_preview,
    selection_retry_image_content, opponent_type_mismatch_slots,
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

def typed_assets():
    """Type labels transcribed from the pinned Champions matrix test rows."""
    source = assets()
    types = {
        "ピカチュウ": "でんき",
        "ドドゲザン": "あく/はがね",
        "ゲンガー": "ゴースト/どく",
        "マリルリ": "みず/フェアリー",
        "ギャラドス": "みず/ひこう",
        "カイリュー": "ドラゴン/ひこう",
    }
    return FrozenMatchAssets(
        contents=source.contents, sha256=source.sha256,
        matrix_index=MappingProxyType(
            {name: name + "|" + types[name] + "|0" for name in OPP}),
        lookup_index=source.lookup_index,
        own_team=source.own_team, frozen_at=source.frozen_at,
    )


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

    def test_independent_visible_type_icons_agree_with_champions_matrix(self):
        frozen=typed_assets()
        observed=observation()
        observed["opponent_type_icons"]=[
            ["でんき"],["あく","はがね"],["ゴースト","どく"],
            ["みず","フェアリー"],["みず","ひこう"],["ドラゴン","ひこう"],
        ]
        self.assertEqual(verify_selection_observation(observed,frozen),OPP)
        self.assertEqual(opponent_type_mismatch_slots(observed,frozen),[])
        # Partial icon readings are not fabricated into a second type.
        observed["opponent_type_icons"][3]=["water"]
        self.assertEqual(verify_selection_observation(observed,frozen),OPP)
        observed["opponent_type_icons"][3]=[]
        self.assertEqual(verify_selection_observation(observed,frozen),OPP)

    def test_type_conflict_is_diagnostic_and_does_not_trigger_retry(self):
        frozen=typed_assets()
        observed=observation()
        observed["opponent_type_icons"]=[
            ["でんき"],["あく","はがね"],["エスパー"],
            ["みず","フェアリー"],["みず","ひこう"],["ドラゴン","ひこう"],
        ]
        mismatch=opponent_type_mismatch_slots(observed,frozen)
        self.assertEqual([item["slot"] for item in mismatch],[3])
        self.assertEqual(mismatch[0]["matrix_types"],["ゴースト","どく"])
        self.assertEqual(verify_selection_observation(observed,frozen),OPP)
        self.assertEqual(unmatched_opponent_slots(observed,frozen),[])
        retry=[]
        original,attempts=retry_unresolved_opponent_six(
            observed,frozen,lambda *args:retry.append(args),
            remaining_seconds=lambda:40.0)
        self.assertEqual(attempts,1)
        self.assertEqual(retry,[])
        self.assertEqual(original["opponent_six"],observed["opponent_six"])

    def test_type_conflict_still_keeps_all_species_in_partial_helper(self):
        frozen=typed_assets()
        observed=observation()
        observed["opponent_type_icons"]=[
            [],[],["エスパー"],[],[],[],
        ]
        self.assertIsNone(partial_opponent_preview(observed,frozen))
        self.assertEqual(verify_selection_observation(observed,frozen),OPP)

    def test_observed_rotom_wash_form_and_espathra_full_six(self):
        from champions_assist.terminology import japanese_name
        self.assertEqual(japanese_name("pokemon","Rotom (Wash Form)"),
                         "ロトム（ウォッシュロトム）")
        valid=(
            "ロトム（ウォッシュロトム）","ハッサム","クエスパトラ",
            "キラフロル","ウルガモス","カバルドン")
        local=assets()
        frozen=FrozenMatchAssets(
            contents=local.contents,sha256=local.sha256,
            matrix_index=MappingProxyType({
                name:f"{name}|みず/でんき|90" for name in valid}),
            lookup_index=local.lookup_index,own_team=local.own_team,
            frozen_at=local.frozen_at)
        observed={
            "visual_ui":"TEAM_SELECTION","our_six":[],
            "opponent_six":["Rotom (Wash Form)","Scizor","Espathra",
                            "Glimmora","Volcarona","Hippowdon"],
            "uncertain_slots":[],
            "opponent_type_icons":[["でんき","みず"],["むし","はがね"],
                                   ["フェアリー"],["いわ","どく"],
                                   ["むし","ほのお"],["じめん"]],
        }
        self.assertEqual(verify_selection_observation(observed,frozen),valid)
        self.assertEqual(unmatched_opponent_slots(observed,frozen),[])
        self.assertIsNone(partial_opponent_preview(observed,frozen))
        self.assertEqual([x["slot"] for x in
                          opponent_type_mismatch_slots(observed,frozen)],
                         [2,3,4,5,6])

    def test_invalid_icon_only_evidence_does_not_veto_valid_names(self):
        frozen=typed_assets()
        for invalid in ([[],[],[],[],[]], [[],[],[],[],[],[],[]],
                        ["でんき",[],[],[],[],[]], [[],[],[],[],[],[1]]):
            observed=observation()
            observed["opponent_type_icons"]=invalid
            with self.subTest(invalid=invalid):
                self.assertEqual(verify_selection_observation(observed,frozen),OPP)
                retry_calls=[]
                _,attempts=retry_unresolved_opponent_six(
                    observed,frozen,lambda *args:retry_calls.append(args),
                    remaining_seconds=lambda:40.0)
                self.assertEqual(attempts,1)
                self.assertEqual(retry_calls,[])

    def test_type_evidence_absence_and_unverified_mega_preview(self):
        frozen=typed_assets()
        # Existing tests and manually entered observations without type-icon
        # fields remain possible; they are not claimed to be type-verified.
        self.assertEqual(verify_selection_observation(observation(),frozen),OPP)
        matrix=dict(frozen.matrix_index)
        matrix["メガリザードンX"]="メガリザードンX|ほのお/ドラゴン|100"
        mega=FrozenMatchAssets(
            contents=frozen.contents,sha256=frozen.sha256,
            matrix_index=MappingProxyType(matrix),lookup_index=frozen.lookup_index,
            own_team=frozen.own_team,frozen_at=frozen.frozen_at)
        obs=observation()
        obs["opponent_six"][0]="メガリザードンX"
        obs["opponent_type_icons"]=[["ほのお","ひこう"],[],[],[],[],[]]
        self.assertEqual(opponent_type_mismatch_slots(obs,mega),[])
        self.assertEqual(verify_selection_observation(obs,mega)[0],"メガリザードンX")

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

    def test_after_second_visual_error_still_offers_partial_selection(self):
        frozen=assets()
        obs=observation()
        obs["opponent_six"][2]="Gouging Fire"
        calls=[]
        def choose(context,remaining):
            calls.append(context)
            return {"selected_three":list(OWN[:3]),"lead":OWN[0]}
        planner=SelectionPlanner(clock=lambda:8.0)
        # Default entrypoint stays strict so untrusted image results are
        # never silently turned into incomplete selections.
        strict=planner.choose(obs,frozen,choose,started_at=0)
        self.assertEqual(strict.status,"BLOCKED")
        self.assertEqual(calls,[])
        # The live replay opts in only after an attempted revised visual read.
        advisory=planner.choose(obs,frozen,choose,started_at=0,allow_partial=True)
        self.assertEqual(advisory.status,"PARTIAL_ADVICE")
        self.assertEqual(advisory.selected_three,OWN[:3])
        self.assertEqual(advisory.lead,OWN[0])
        self.assertEqual(advisory.opponent_six[2],"未判明相手3")
        self.assertEqual(len(calls),1)
        self.assertEqual(len(calls[0]["matrix_rows_six"]),5)
        self.assertEqual(calls[0]["unknown_opponent_slots"],["未判明相手3"])
        self.assertIn("PREP未完成",selection_summary_ja(advisory.as_dict()))

    def test_retry_changes_image_evidence_not_same_right_crop(self):
        import numpy as np
        from unittest.mock import patch
        img=np.zeros((600,1000,3),dtype=np.uint8)
        # The 3rd opponent row has a distinctive pattern in the right slots.
        img[210:275,780:980,:]=[80,155,245]
        sent=[]
        def uri(x):
            sent.append(tuple(x.shape))
            return "data:image/jpeg;base64,TEST"
        with patch("champions_assist.vision_api_benchmark.jpeg_data_uri",side_effect=uri):
            result=selection_retry_image_content(
                img,introduction="Re-read enlarged slot",focus_slot=3)
        self.assertEqual(sum(i["type"]=="input_image" for i in result),2)
        self.assertEqual(sent[0],img.shape)
        self.assertNotEqual(sent[1],img.shape)
        self.assertLess(sent[1][1],img.shape[1])
        self.assertGreater(sent[1][0],100)

    def test_optional_second_request_timeout_preserves_first_five_names(self):
        frozen=assets()
        obs=observation()
        obs["opponent_six"][2]="Gouging Fire"
        def timeout(*args):
            raise TimeoutError("a second-model read timeout")
        second,attempts=retry_unresolved_opponent_six(
            obs,frozen,timeout,remaining_seconds=lambda:40.0)
        self.assertEqual(attempts,2)
        self.assertEqual(second["_retry_error_type"],"TimeoutError")
        self.assertEqual(second["opponent_six"][:2],list(OPP[:2]))
        planner=SelectionPlanner(clock=lambda:8.0)
        result=planner.choose(second,frozen,
            lambda context,_:{"selected_three":list(OWN[:3]),"lead":OWN[0]},
            started_at=0,allow_partial=True)
        self.assertEqual(result.status,"PARTIAL_ADVICE")
        self.assertEqual(result.opponent_six[2],"未判明相手3")

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
