"""Regression gates for text-only local-OCR live state and decision legality."""
from __future__ import annotations
from copy import deepcopy
from types import SimpleNamespace
import unittest

from champions_assist.battle_evidence import empty_battle_evidence, update_battle_evidence
from champions_assist.battle_state_tracker import LocalBattleStateTracker
from champions_assist.live_state_updates import apply_local_ocr
from champions_assist.local_event_worker import LocalFieldRead
from champions_assist.match_replay import validate_battle_choice


def assets():
    return SimpleNamespace(
        own_team=(
            {"name":"マリルリ","move1":"アクアジェット",
             "move2":"じゃれつく","move3":"まもる","move4":"アンコール",
             "item":"オボンのみ","choice_lock":"N"},
            {"name":"マスカーニャ","move1":"トリックフラワー",
             "move2":"とんぼがえり","move3":"まもる","move4":"はたきおとす",
             "item":"こだわりスカーフ","choice_lock":"Y"},
            {"name":"ゲンガー","move1":"シャドーボール","move2":"まもる",
             "move3":"ヘドロばくだん","move4":"ちょうはつ",
             "item":"きあいのタスキ","choice_lock":"N"},
        ),
        matrix_index={"ウルガモス":"ROW-V","ガブリアス":"ROW-G",
                      "ゲンガー":"ROW-X","カイリュー":"ROW-D",
                      "サーフゴー":"ROW-S","ドドゲザン":"ROW-K"},
    )


def state():
    return {
        "phase":"BATTLE","our_active":"マスカーニャ",
        "opponent_active":"ガブリアス",
        "selected_three":["マスカーニャ","マリルリ","ゲンガー"],
        "opponent_six":["ガブリアス","ゲンガー","カイリュー",
                        "サーフゴー","ドドゲザン","未判明相手6"],
        "visual_unknown_opponents":["未判明相手6"],"matrix_rows_six":[],
        "observed_hp":{},"awaiting_replacement":False,
        "battle_evidence":empty_battle_evidence(),
        "battlefield":{"weather":"","terrain":"","effects":""},
        "history":[],"step_number":2,"prep":{"source":"FROZEN_CACHE"},
        "_live_state_revision":0,
    }


def read(field, text, seq, value=None, confidence=.96):
    return LocalFieldRead(
        field="screen_text."+field,frame_id="frame-"+str(seq),
        timestamp=float(seq),confidence=confidence,roi=field,
        source="fixture",text=text,
        value=text if value is None else value)


class LiveStateOcrTests(unittest.TestCase):
    def setUp(self):
        self.state=state()
        self.assets=assets()

    def apply(self,*records):
        self.state,audit=apply_local_ocr(self.state,records,self.assets)
        return audit

    def test_switch_to_azumarill_clears_stale_actor_and_lock(self):
        self.state["battle_evidence"]["choice_lock"]={
            "pokemon":"マスカーニャ","move":"トリックフラワー","source":"OBSERVED"}
        audit=self.apply(read("battle_message","ゆけっ！マリルリ！",1))
        self.assertEqual(audit[0]["status"],"APPLIED")
        self.assertEqual(self.state["our_active"],"マリルリ")
        self.assertEqual(self.state["battle_evidence"]["choice_lock"]["move"],"")
        self.assertEqual(self.state["_live_state_revision"],2)
        self.assertFalse(self.state["awaiting_replacement"])

    def test_unknown_six_filled_from_explicit_opponent_switch(self):
        self.apply(read("battle_message","相手は ウルガモスを くりだした！",1))
        self.assertEqual(self.state["opponent_active"],"ウルガモス")
        self.assertEqual(self.state["opponent_six"][-1],"ウルガモス")
        self.assertEqual(self.state["visual_unknown_opponents"],[])
        self.assertEqual(len(self.state["matrix_rows_six"]),6)
        self.assertEqual(self.state["prep"]["unrated_new_opponents"],["ウルガモス"])

    def test_known_opponent_switch_does_not_consume_unknown_slot(self):
        self.apply(read("battle_message","相手は ガブリアスを くりだした！",1))
        self.assertEqual(self.state["opponent_six"][-1],"未判明相手6")

    def test_ambiguous_message_does_not_promote_actor(self):
        before=deepcopy(self.state)
        audit=self.apply(read("battle_message","ウルガモスがでてきた",1))
        self.assertEqual(audit,[])
        self.assertEqual(self.state["our_active"],before["our_active"])
        self.assertEqual(self.state["opponent_six"],before["opponent_six"])

    def test_low_confidence_name_kept_as_candidate(self):
        audit=self.apply(read("own_active_name","マリルリ",1,confidence=.31))
        self.assertEqual(audit[0]["status"],"CANDIDATE")
        self.assertEqual(self.state["our_active"],"マスカーニャ")

    def test_registered_own_name_roi_updates_active(self):
        self.apply(read("own_active_name","マリルリ",1))
        self.assertEqual(self.state["our_active"],"マリルリ")

    def test_confident_hp_faint_status_and_status_cure(self):
        self.apply(read("own_hp_fraction","41/140",1,
                        {"current":41,"maximum":140}),
                   read("opponent_hp_percent","73%",2,
                        {"percent_displayed":73}))
        self.assertEqual(self.state["battle_evidence"]["hp_by_pokemon"]["own"]["マスカーニャ"],"41/140")
        self.assertEqual(self.state["observed_hp"]["opponent"],"73%")
        self.apply(read("battle_message","マスカーニャはまひになった",3))
        self.assertEqual(self.state["battle_evidence"]["status_by_pokemon"]["own"]["マスカーニャ"],"まひ")
        self.apply(read("battle_message","マスカーニャはまひがなおった",4))
        self.assertNotIn("マスカーニャ",self.state["battle_evidence"]["status_by_pokemon"]["own"])
        self.apply(read("battle_message","マスカーニャはたおれた!",5))
        self.assertTrue(self.state["awaiting_replacement"])
        self.assertEqual(self.state["battle_evidence"]["hp_by_pokemon"]["own"]["マスカーニャ"],"0")

    def test_observed_moves_and_items_and_abilities_by_actor(self):
        self.apply(read("battle_message","相手のガブリアスはじしんをつかった!",1))
        known=self.state["battle_evidence"]["moves_by_pokemon"]["opponent"]["ガブリアス"]
        self.assertEqual(known,["じしん"])
        self.apply(read("battle_message","相手のガブリアスはオボンのみを消費した",2))
        self.assertEqual(self.state["battle_evidence"]["item_state_by_pokemon"]["opponent"]["ガブリアス"]["state"],"SPENT_OR_LOST")
        self.apply(read("battle_message","相手のガブリアスはとくせいさめはだが発動した",3))
        self.assertEqual(self.state["battle_evidence"]["ability_by_pokemon"]["opponent"]["ガブリアス"],"さめはだ")

    def test_boost_decrements_and_switch_resets(self):
        self.apply(read("battle_message","マスカーニャのこうげきがあがった",1),
                   read("battle_message","マスカーニャのこうげきがあがった",2))
        self.assertEqual(self.state["battle_evidence"]["boosts"]["own"]["マスカーニャ"]["A"],2)
        self.apply(read("own_active_name","マリルリ",3))
        self.assertNotIn("マスカーニャ",self.state["battle_evidence"]["boosts"]["own"])

    def test_en_core_taunt_and_weather_terrain_expiration(self):
        self.apply(read("battle_message","マスカーニャはアンコールをうけた",1),
                   read("battle_message","マスカーニャはちょうはつをうけた",2),
                   read("battle_message","あめがふりはじめた",3),
                   read("battle_message","エレキフィールドになった",4))
        temp=self.state["battle_evidence"]["temporary_effects"]["own"]["マスカーニャ"]
        self.assertTrue(temp["encore"]["active"])
        self.assertIsNone(temp["taunt"]["remaining_turns"])
        self.assertEqual(self.state["battlefield"]["weather"],"あめ")
        self.assertEqual(self.state["battlefield"]["terrain"],"エレキフィールド")
        self.apply(read("battle_message","マスカーニャのアンコールがとけた",5),
                   read("battle_message","あめがやんだ",6),
                   read("battle_message","エレキフィールドがきえた",7))
        self.assertNotIn("encore",temp if temp is
                         self.state["battle_evidence"]["temporary_effects"]["own"]["マスカーニャ"]
                         else self.state["battle_evidence"]["temporary_effects"]["own"]["マスカーニャ"])
        self.assertEqual(self.state["battlefield"]["weather"],"")
        self.assertEqual(self.state["battlefield"]["terrain"],"")

    def test_pp_numeric_read_prevents_zero_pp_recommendation(self):
        self.apply(read("own_active_name","マリルリ",1))
        self.apply(read("own_pp_move1","0/20",2,{"current":0,"maximum":20}))
        self.assertEqual(self.state["battle_evidence"]["pp_by_pokemon"]["own"]["マリルリ"]["アクアジェット"],0)
        observation={"visual_ui":"COMMAND_MENU","visible_moves":[]}
        answer={"action_kind":"MOVE","move":"アクアジェット"}
        code,_=validate_battle_choice(answer,observation,self.state,self.assets)
        self.assertEqual(code,"MOVE_OUT_OF_PP")

    def test_encore_only_forbids_when_own_last_move_is_observed(self):
        self.apply(read("battle_message","マスカーニャはトリックフラワーをつかった",1))
        self.apply(read("battle_message","マスカーニャはアンコールをうけた",2))
        code,_=validate_battle_choice(
            {"action_kind":"MOVE","move":"はたきおとす"},
            {"visual_ui":"COMMAND_MENU","visible_moves":[]},
            self.state,self.assets)
        self.assertEqual(code,"ENCORE_LOCK_VIOLATION")

    def test_ingest_collect_deduplicates_identical_message(self):
        tracker=LocalBattleStateTracker()
        first=read("battle_message","マスカーニャのこうげきがあがった",1)
        second=read("battle_message","マスカーニャのこうげきがあがった",2)
        count,new=tracker.ingest((first,),collect=True)
        self.assertEqual(count,1)
        self.state,_=apply_local_ocr(self.state,new,self.assets)
        count,new=tracker.ingest((second,),collect=True)
        self.assertEqual((count,new),(0,()))
        self.state,_=apply_local_ocr(self.state,new,self.assets)
        self.assertEqual(self.state["battle_evidence"]["boosts"]["own"]["マスカーニャ"]["A"],1)

    def test_chatgpt_observed_opponent_move_is_recorded_not_inferred(self):
        update_battle_evidence(self.state,{
            "our_active":"マスカーニャ","opponent_active":"ガブリアス",
            "opponent_used_move":"じしん"},self.assets.own_team)
        memory=self.state["battle_evidence"]["moves_by_pokemon"]["opponent"]
        self.assertEqual(memory["ガブリアス"],["じしん"])
        self.assertEqual(len(memory["ガブリアス"]),1)


if __name__=="__main__":
    unittest.main()
