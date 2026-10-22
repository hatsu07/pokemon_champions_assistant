"""No-network acceptance tests for all phases of a saved-screenshot replay."""
from __future__ import annotations

from types import MappingProxyType
import unittest

from champions_assist.match_replay import (
    new_replay, advance_observation, confirm_actual_selection, validate_battle_choice,
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
        self.assertEqual(self.state["phase"],"BATTLE")
        out=self.advance(frame(
            "COMMAND_MENU",our_active="Pikachu",opponent_active="Meowscarada"))
        self.assertEqual(out["status"],"OBSERVED")
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
        obs=frame("MOVE_MENU",our_active="Pikachu",visible_moves=["Protect"])
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
        out=self.advance(frame("RESULT_SCREEN",match_result="UNKNOWN"))
        self.assertEqual(out["status"],"RESULT_UNCONFIRMED")
        self.assertEqual(self.state["phase"],"BATTLE")

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
