"""No-network acceptance tests for all phases of a saved-screenshot replay."""
from __future__ import annotations

from types import MappingProxyType, SimpleNamespace
import unittest
import json

from champions_assist.match_replay import (
    new_replay, advance_observation, confirm_actual_selection,
    assume_advised_selection, validate_battle_choice, run_replay_prep,
)
from champions_assist.preload import FrozenMatchAssets

OWN=("ピカチュウ","ドドゲザン","ガブリアス","マリルリ","ギャラドス","ゲンガー")
OPP=("マスカーニャ","アシレーヌ","ガブリアス","ルカリオ","サーフゴー","ミミッキュ")
MASTER_TEAM=("マスカーニャ","アシレーヌ","ガブリアス","メガルカリオZ","サーフゴー","ミミッキュ")


def fixture_assets():
    own=[{"name":name,"move1":"10まんボルト" if name=="ピカチュウ" else "じしん",
          "move2":"まもる","move3":"でんこうせっか","move4":"かみくだく",
          "item":"テスト用","ability":"テスト用","role":""} for name in OWN]
    header="# OWN={M:マスカーニャ,P:アシレーヌ,C:ガブリアス,L:メガルカリオZ,G:サーフゴー,K:ミミッキュ}"
    return FrozenMatchAssets(
        contents=MappingProxyType({
            "Pokemon_Champions_MC_live_decision_gate.txt":"OLD TEAM GATE",
            "Pokemon_Champions_MC_live_selection_matrix.txt":header+"\n",
        }),
        sha256=MappingProxyType({}),
        matrix_index=MappingProxyType({name:name+"|matrix" for name in OPP}),
        lookup_index=MappingProxyType({}),
        own_team=tuple(own),
        frozen_at="fixture",
    )


def frame(ui, **kw):
    obs=dict(
        visual_ui=ui,our_six=[],opponent_six=[],our_active="",opponent_active="",
        visible_moves=[],visible_party=[],our_hp="",opponent_hp="",messages=[],
        observed_event="NONE",match_result="UNKNOWN",uncertain_slots=[],
    )
    obs.update(kw)
    return obs


def choose(ctx, limit, mode):
    if mode=="SELECTION":
        return {"selected_three":["Pikachu","Azumarill","Gyarados"],"lead":"Pikachu"}
    if mode=="ACTION":
        if ctx["screen"]=="PARTY_MENU":
            return {"action_kind":"SWITCH","move":"","switch_target":"Gyarados","warning":""}
        return {"action_kind":"MOVE","move":"Thunderbolt","switch_target":"","warning":""}
    raise AssertionError(mode)


class FullReplayTest(unittest.TestCase):
    def setUp(self):
        self.assets=fixture_assets()
        self.state=new_replay(self.assets)

    def advance(self, observation, callback=choose):
        self.state,result=advance_observation(
            self.state,self.assets,observation,callback,elapsed_seconds=lambda:5.0)
        return result

    def test_full_match_sequence_from_matchmaking_to_result(self):
        self.assertFalse(self.state["matrix_compatible"])
        out=self.advance(frame("MATCHMAKING",messages=["マッチング中"]))
        self.assertEqual(out["status"],"OBSERVED")
        out=self.advance(frame(
            "TEAM_SELECTION",
            our_six=["Pikachu","Kingambit","Garchomp","Azumarill","Gyarados","Gengar"],
            opponent_six=["Meowscarada","Primarina","Garchomp","Lucario","Gholdengo","Mimikyu"],
        ))
        self.assertEqual(out["status"],"TEST_ONLY")
        self.assertEqual(out["selected_three"],["ピカチュウ","マリルリ","ギャラドス"])
        self.assertEqual(self.state["opponent_six"],list(OPP))
        self.assertEqual(self.state["phase"],"SELECTION")
        self.state=confirm_actual_selection(self.state,self.assets,
                                            out["selected_three"],out["lead"])
        self.assertEqual(self.state["phase"],"PREP")
        self.state,prep=run_replay_prep(state=self.state,assets=self.assets,
                                         access_token="test",model="test")
        self.assertEqual(prep["status"],"PREP_UNAVAILABLE")
        self.assertEqual(self.state["prep_status"],"UNAVAILABLE_MATRIX_MISMATCH")
        out=self.advance(frame(
            "COMMAND_MENU",our_active="Pikachu",opponent_active="Meowscarada"))
        self.assertEqual(self.state["phase"],"BATTLE")
        self.assertEqual(out["status"],"TEST_ONLY")
        self.assertEqual(out["recommendation"],"10まんボルト。")
        out=self.advance(frame(
            "MOVE_MENU",our_active="Pikachu",opponent_active="Meowscarada",
            visible_moves=["Thunderbolt","Protect","Quick Attack"],our_hp="112/112"))
        self.assertEqual(out["status"],"TEST_ONLY")
        self.assertEqual(out["recommendation"],"10まんボルト。")
        out=self.advance(frame(
            "ACTION_RESOLUTION",our_active="Pikachu",opponent_active="Meowscarada",
            observed_event="OUR_FAINTED",messages=["ピカチュウがたおれた！"],our_hp="0/112"))
        self.assertEqual(out["status"],"OBSERVED")
        self.assertEqual(self.state["last_event"],"OUR_FAINTED")
        out=self.advance(frame(
            "PARTY_MENU",our_active="Pikachu",visible_party=["Azumarill","Gyarados"]))
        self.assertEqual(out["status"],"TEST_ONLY")
        self.assertEqual(out["recommendation"],"ギャラドスに交代。")
        self.assertEqual(self.state["last_event"],"OUR_FAINTED")
        out=self.advance(frame(
            "RESULT_SCREEN",match_result="WIN",messages=["勝利しました"]))
        self.assertEqual(out["status"],"MATCH_END")
        self.assertEqual(out["recommendation"],"勝利。")
        self.assertEqual(self.state["phase"],"ENDED")
        self.assertGreaterEqual(len(self.state["history"]),7)
        self.assertTrue(any("たおれた" in " ".join(x["messages"])
                            for x in self.state["image_observations_unverified"]))
        out=self.advance(frame("MOVE_MENU"))
        self.assertEqual(out["status"],"BLOCKED")
        self.assertEqual(out["reason"],"MATCH_ALREADY_ENDED")

    def test_selection_planner_returns_roles_from_the_same_tactical_call(self):
        def with_roles(_ctx,_remaining,_mode):
            return {
                "selected_three":["Pikachu","Azumarill","Gyarados"],
                "lead":"Pikachu",
                "preserve":"Pikachu",
                "stopper":"Azumarill",
                "closer":"Gyarados",
            }
        out=self.advance(frame(
            "TEAM_SELECTION",opponent_six=[
                "Meowscarada","Primarina","Garchomp",
                "Lucario","Gholdengo","Mimikyu"],
        ),callback=with_roles)
        self.assertEqual(out["status"],"TEST_ONLY")
        self.assertEqual(out["preserve"],"ピカチュウ")
        self.assertEqual(out["stopper"],"マリルリ")
        self.assertEqual(out["closer"],"ギャラドス")
        self.assertEqual(self.state["phase"],"SELECTION")  # Pure reducer waits for live adapter

    def test_invalid_role_is_not_accepted_as_a_win_condition(self):
        def bad_role(_ctx,_remaining,_mode):
            return {
                "selected_three":["Pikachu","Azumarill","Gyarados"],
                "lead":"Pikachu",
                "preserve":"Pikachu",
                "stopper":"NotOnOurTeam",
                "closer":"Gyarados",
            }
        out=self.advance(frame("TEAM_SELECTION",opponent_six=list(OPP)),
                         callback=bad_role)
        self.assertEqual(out["status"],"TEST_ONLY")
        self.assertIsNone(out["stopper"])

    def test_live_auto_accept_selection_and_observed_different_lead(self):
        selected=list(OWN[:3])
        state=new_replay(self.assets)
        state["phase"]="SELECTION"
        state["opponent_six"]=list(OPP)
        state=assume_advised_selection(state,self.assets,selected,selected[0])
        self.assertEqual(state["phase"],"PREP")
        self.assertTrue(state["selection_assumed"])
        state["prep"]={"source":"FROZEN_CACHE","cached_matchups":18}
        updated,report=advance_observation(
            state,self.assets,
            frame("COMMAND_MENU",our_active="Gyarados",
                  opponent_active="Meowscarada"),
            choose,elapsed_seconds=lambda:1.0)
        self.assertEqual(report["status"],"TEST_ONLY")
        self.assertEqual(report["recommendation"],"じしん。")
        self.assertEqual(updated["phase"],"BATTLE")
        self.assertEqual(updated["observed_lead"],"ギャラドス")
        self.assertEqual(updated["lead"],"ギャラドス")
        self.assertEqual(len(updated["selected_three"]),3)
        self.assertIn("ギャラドス",updated["selected_three"])
        self.assertEqual(updated["prep_status"],"LEAD_DIFFERS_FROM_PROPOSAL")
        self.assertIsNone(updated["prep"])
        self.assertTrue(updated["selection_assumed"])

    def test_live_auto_accept_keeps_cache_when_lead_observed_matches(self):
        selected=list(OWN[:3])
        state=new_replay(self.assets)
        state["phase"]="SELECTION"
        state["opponent_six"]=list(OPP)
        state=assume_advised_selection(state,self.assets,selected,selected[0])
        state["prep"]={"source":"FROZEN_CACHE","cached_matchups":18}
        updated,report=advance_observation(
            state,self.assets,
            frame("COMMAND_MENU",our_active="Pikachu",
                  opponent_active="Meowscarada"),
            choose,elapsed_seconds=lambda:1.0)
        self.assertEqual(report["status"],"OBSERVED")
        self.assertEqual(updated["observed_lead"],"ピカチュウ")
        self.assertEqual(updated["selected_three"],selected)
        self.assertIsNotNone(updated["prep"])

    def test_command_menu_recommends_four_registered_move_without_visible_moves(self):
        state=new_replay(self.assets)
        state.update(phase="BATTLE",selected_three=list(OWN[:3]),
                     lead=OWN[0],opponent_six=list(OPP))
        calls=[]
        def tactical(context, remaining, kind):
            self.assertEqual(kind,"ACTION")
            self.assertEqual(context["screen"],"COMMAND_MENU")
            self.assertEqual(context["registered_move_options"],
                             ["10まんボルト","まもる","でんこうせっか","かみくだく"])
            self.assertEqual(context["selected_switch_options"],list(OWN[1:3]))
            calls.append(context)
            return {"action_kind":"MOVE","move":"Thunderbolt",
                    "switch_target":"","warning":""}
        next_state,out=advance_observation(
            state,self.assets,frame("COMMAND_MENU",
                our_active="Pikachu",opponent_active="Meowscarada",
                visible_moves=[],visible_party=[]),
            tactical,elapsed_seconds=lambda:2.0)
        self.assertEqual(len(calls),1)
        self.assertEqual(next_state["phase"],"BATTLE")
        self.assertEqual(out["status"],"TEST_ONLY")
        self.assertEqual(out["screen"],"COMMAND_MENU")
        self.assertEqual(out["recommendation"],"10まんボルト。")
        self.assertEqual(out["action_basis"],
                         "REGISTERED_COMMAND_OPTIONS_UNVERIFIED_CURRENT_LEGALITY")

    def test_command_menu_can_hard_switch_into_preselected_bench(self):
        state=new_replay(self.assets)
        state.update(phase="BATTLE",selected_three=list(OWN[:3]),
                     lead=OWN[0],opponent_six=list(OPP))
        _,out=advance_observation(
            state,self.assets,
            frame("COMMAND_MENU",our_active="Pikachu",opponent_active="Meowscarada",
                  visible_party=[]),
            lambda context,*args: {"action_kind":"SWITCH","move":"",
                                   "switch_target":"Garchomp","warning":""},
            elapsed_seconds=lambda:1.0)
        self.assertEqual(out["status"],"TEST_ONLY")
        self.assertEqual(out["recommendation"],"ガブリアスに交代。")
        self.assertFalse(out["selected_bench_unconfirmed"])

    def test_command_menu_rejects_unknown_moves_unselected_switch_and_lock(self):
        state=new_replay(self.assets)
        state.update(phase="BATTLE",selected_three=list(OWN[:3]),
                     lead=OWN[0],opponent_six=list(OPP))
        choices=[
            ({"action_kind":"MOVE","move":"UnregisteredMove",
              "switch_target":"","warning":""},"MOVE_NOT_REGISTERED"),
            ({"action_kind":"SWITCH","move":"","switch_target":"Gengar",
              "warning":""},"SWITCH_TARGET_NOT_SELECTED"),
        ]
        for answer,reason in choices:
            with self.subTest(reason=reason):
                _,out=advance_observation(
                    state,self.assets,
                    frame("COMMAND_MENU",our_active="Pikachu",
                          opponent_active="Meowscarada"),
                    lambda *args:answer,elapsed_seconds=lambda:1.0)
                self.assertEqual(out["status"],"BLOCKED")
                self.assertEqual(out["reason"],reason)
        state["battle_evidence"]["choice_lock"]={
            "pokemon":"ピカチュウ","move":"まもる"}
        _,out=advance_observation(
            state,self.assets,
            frame("COMMAND_MENU",our_active="Pikachu",opponent_active="Meowscarada"),
            lambda *args:{"action_kind":"MOVE","move":"10まんボルト",
                          "switch_target":"","warning":""},
            elapsed_seconds=lambda:1.0)
        self.assertEqual(out["reason"],"CHOICE_LOCK_VIOLATION")

    def test_command_menu_excludes_fainted_bench_and_rejects_switch(self):
        state=new_replay(self.assets)
        state.update(phase="BATTLE",selected_three=list(OWN[:3]),
                     lead=OWN[0],opponent_six=list(OPP))
        state["battle_evidence"]["hp_by_pokemon"]["own"][OWN[1]]="0/180"
        captured=[]
        def decision(context,*args):
            captured.append(context)
            return {"action_kind":"SWITCH","switch_target":OWN[1],
                    "move":"","warning":""}
        _,out=advance_observation(
            state,self.assets,frame("COMMAND_MENU",our_active=OWN[0],
                opponent_active=OPP[0]),decision,elapsed_seconds=lambda:1.0)
        self.assertEqual(captured[0]["action_candidates_count"],5)
        self.assertEqual(captured[0]["selected_switch_options"],[OWN[2]])
        self.assertEqual(out["status"],"BLOCKED")
        self.assertEqual(out["reason"],"SWITCH_TARGET_FAINTED")

    def test_field_effects_survive_events_to_next_action_context(self):
        state=new_replay(self.assets)
        state.update(phase="BATTLE",selected_three=list(OWN[:3]),
                     lead=OWN[0],opponent_six=list(OPP))
        event=frame("ACTION_RESOLUTION",our_active=OWN[0],
                    opponent_active=OPP[0],our_hp="65/120",
                    opponent_hp="40%",
                    our_status="まひ",
                    opponent_stat_changes="こうげき+2",
                    battlefield_weather="雨",
                    battlefield_terrain="エレキフィールド",
                    battlefield_effects="ひかりのかべが継続",
                    messages=["雨が降り始めた"])
        state,out=advance_observation(
            state,self.assets,event,
            lambda *a: self.fail("Event must not trigger tactical AI"),
            elapsed_seconds=lambda:1.0)
        self.assertEqual(out["status"],"OBSERVED")
        self.assertEqual(state["battlefield"]["weather"],"雨")
        seen=[]
        def choose_action(context,*args):
            seen.append(context)
            return {"action_kind":"MOVE","move":"10まんボルト",
                    "switch_target":"","warning":""}
        _,out=advance_observation(
            state,self.assets,frame("COMMAND_MENU",our_active=OWN[0],
                opponent_active=OPP[0]),choose_action,
            elapsed_seconds=lambda:1.0)
        self.assertEqual(out["status"],"TEST_ONLY")
        self.assertEqual(seen[0]["battlefield"]["terrain"],"エレキフィールド")
        self.assertEqual(seen[0]["battlefield"]["effects"],"ひかりのかべが継続")
        self.assertEqual(seen[0]["battle_evidence"]["status_by_pokemon"]["own"][OWN[0]],"まひ")
        self.assertEqual(seen[0]["observed_hp"]["own"],"65/120")

    def test_command_menu_unknown_actor_never_chooses_a_move(self):
        state=new_replay(self.assets)
        state.update(phase="BATTLE",selected_three=list(OWN[:3]),
                     lead=OWN[0],opponent_six=list(OPP))
        called=[]
        _,out=advance_observation(
            state,self.assets,frame("COMMAND_MENU",opponent_active="Meowscarada"),
            lambda *args:called.append(True),elapsed_seconds=lambda:1.0)
        self.assertEqual(out["reason"],"OWN_ACTIVE_UNCONFIRMED")
        self.assertEqual(called,[])

    def test_no_battle_decision_without_confirming_selection(self):
        self.advance(frame("TEAM_SELECTION",our_six=list(OWN),opponent_six=list(OPP)))
        out=self.advance(frame("MOVE_MENU",our_active="Pikachu",
                                visible_moves=["Thunderbolt"]))
        self.assertEqual(out["status"],"BLOCKED")
        self.assertEqual(out["reason"],"CONFIRM_ACTUAL_SELECTION_FIRST")

    def test_unverified_move_and_switch_are_never_recommended(self):
        self.advance(frame("TEAM_SELECTION",our_six=list(OWN),opponent_six=list(OPP)))
        self.state=confirm_actual_selection(self.state,self.assets,
                                            ["ピカチュウ","ギャラドス","マリルリ"],
                                            "ピカチュウ")
        obs=frame("MOVE_MENU",our_active="Pikachu",opponent_active="Meowscarada",
                  visible_moves=["Protect"])
        self.state,result=advance_observation(
            self.state,self.assets,obs,choose,elapsed_seconds=lambda:5)
        self.assertEqual(result["status"],"BLOCKED")
        self.assertEqual(result["reason"],"MOVE_NOT_VERIFIED")
        party=frame("PARTY_MENU",our_active="Pikachu",visible_party=["Azumarill"])
        self.state,result=advance_observation(
            self.state,self.assets,party,choose,elapsed_seconds=lambda:5)
        self.assertEqual(result["reason"],"SWITCH_TARGET_NOT_VISIBLE")

    def test_result_without_visible_win_loss_not_final(self):
        self.advance(frame("TEAM_SELECTION",our_six=list(OWN),opponent_six=list(OPP)))
        self.state=confirm_actual_selection(self.state,self.assets,list(OWN[:3]),OWN[0])
        self.advance(frame("COMMAND_MENU",our_active="Pikachu",
                           opponent_active="Meowscarada"))
        out=self.advance(frame("RESULT_SCREEN",match_result="UNKNOWN"))
        self.assertEqual(out["status"],"RESULT_UNCONFIRMED")
        self.assertEqual(self.state["phase"],"BATTLE")

    def test_prep_complete_only_when_all_eighteen_matchups_are_structurally_present(self):
        from champions_assist.matrix_compatibility import matrix_matches_registered_team
        original=self.assets
        team_header="# OWN={P:ピカチュウ,D:ドドゲザン,C:ガブリアス,A:マリルリ,G:ギャラドス,E:ゲンガー}\n"
        compatible=FrozenMatchAssets(
            contents=MappingProxyType({
                **original.contents,
                "Pokemon_Champions_MC_live_selection_matrix.txt":team_header,
            }),
            sha256=original.sha256,matrix_index=original.matrix_index,
            lookup_index=original.lookup_index,own_team=original.own_team,
            frozen_at=original.frozen_at,
        )
        self.assertTrue(matrix_matches_registered_team(compatible))
        state=new_replay(compatible)
        state["phase"]="SELECTION"
        state["opponent_six"]=list(OPP)
        state=confirm_actual_selection(state,compatible,list(OWN[:3]),OWN[0])
        lead_code=("ACT=attack|KO=uncertain|DPS=10まんボルト|PRI=none|"
                   "S=unknown|OUR=電気|OPP=unknown|SAFE=unknown|PROC=move|"
                   "LOCK=none|MEGA=no")
        bench_code=("S=unknown|OUR=verified|OPP=unknown|PRI=none|"
                    "SAFE=unknown|PROC=move")
        matchups=[
            {"our_pokemon":own, "opponent_pokemon":opp,
             "eval_code":lead_code if own==OWN[0] else bench_code}
            for own in OWN[:3] for opp in OPP
        ]
        response={"lead_six":[row["eval_code"] for row in matchups[:6]],
                  "bench_twelve":[row["eval_code"] for row in matchups[6:]],
                  "preserve":OWN[2],"stopper":OWN[1],"closer":OWN[0]}
        class Stream:
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def __iter__(self):
                yield SimpleNamespace(type="response.output_text.delta",
                                      delta=json.dumps(response,ensure_ascii=False))
                yield SimpleNamespace(type="response.completed")
        class ResponseEndpoint:
            def create(self,**kwargs):
                self.last=kwargs
                return Stream()
        class Client:
            def __init__(self): self.responses=ResponseEndpoint()
        client=Client()
        updated,report=run_replay_prep(
            state=state,assets=compatible,access_token="dummy",model="mock",
            client_factory=lambda **kwargs:client)
        self.assertEqual(report["status"],"PREP_STRUCTURE_OK")
        self.assertEqual(report["matchups_evaluated"],18)
        self.assertEqual(updated["prep_status"],"STRUCTURE_COMPLETE")
        self.assertEqual(len(updated["prep"]["matchups_18"]),18)
        self.assertEqual(client.responses.last["stream"],True)
        response["lead_six"] = response["lead_six"][:-1]
        missing, missing_report = run_replay_prep(
            state=state, assets=compatible, access_token="dummy", model="mock",
            client_factory=lambda **kwargs: client)
        self.assertEqual(missing_report["status"], "PREP_INCOMPLETE")
        self.assertEqual(missing_report["reason"], "MATCHUPS_18_INCOMPLETE")
        self.assertIsNone(missing["prep"])

        self.assertEqual(client.responses.last["reasoning"]["effort"],"low")
        # Ordered compact wire: six lead, then twelve bench entries; exact
        # pairs and all mandatory tags are still reconstructed/validated.
        prompt=json.loads(client.responses.last["input"][0]["content"][0]["text"])
        self.assertEqual(prompt["response_order"]["lead_six"][:11],
                         "six strings")
        self.assertEqual(len(prompt["selected_team"]),3)
        self.assertNotIn("frozen_gate",prompt)
        self.assertEqual(
            [(row["our_pokemon"],row["opponent_pokemon"])
             for row in updated["prep"]["matchups_18"]],
            [(own,opp) for own in OWN[:3] for opp in OPP])


    def test_timeout_suppresses_tactical_model_calls(self):
        calls=[]
        self.state=new_replay(self.assets)
        obs=frame("MATCHMAKING")
        self.advance(obs)
        self.state["phase"]="BATTLE"
        self.state["selected_three"]=list(OWN[:3])
        _,out=advance_observation(
            self.state,self.assets,
            frame("MOVE_MENU",our_active="Pikachu",visible_moves=["Thunderbolt"]),
            lambda *args:calls.append(args),elapsed_seconds=lambda:35.0,
        )
        self.assertEqual(out["status"],"DEADLINE_EXCEEDED")
        self.assertEqual(calls,[])

    def test_selection_confirmation_requires_six_confirmed_opponents(self):
        self.state["phase"]="SELECTION"
        with self.assertRaisesRegex(ValueError,"相手6匹"):
            confirm_actual_selection(self.state,self.assets,list(OWN[:3]),OWN[0])

if __name__=="__main__":
    unittest.main()
