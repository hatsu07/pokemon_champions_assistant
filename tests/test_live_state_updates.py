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
        self.assertEqual(self.state["_live_state_revision"],1)
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
        self.apply(read("own_active_name","マスカーニャ",1),
                   read("own_hp_fraction","41/140",1,
                        {"current":41,"maximum":140}),
                   read("opponent_active_name","ガブリアス",2),
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

    def test_taunt_forbids_category_from_frozen_champions_move_master(self):
        self.assets.move_class_index={"まもる":"変化","トリックフラワー":"物理"}
        self.apply(read("battle_message","マスカーニャはちょうはつをうけた",1))
        code,_=validate_battle_choice(
            {"action_kind":"MOVE","move":"まもる"},
            {"visual_ui":"COMMAND_MENU","visible_moves":[]},
            self.state,self.assets)
        self.assertEqual(code,"TAUNT_STATUS_MOVE_BLOCKED")
        good,_=validate_battle_choice(
            {"action_kind":"MOVE","move":"トリックフラワー"},
            {"visual_ui":"COMMAND_MENU","visible_moves":[]},
            self.state,self.assets)
        self.assertEqual(good,"OK")

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
        self.assertTrue(self.state["battlefield"]["weather_state"]["active"])
        self.assertIsNone(self.state["battlefield"]["terrain_state"]["remaining_turns"])
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


    def test_identical_message_in_next_turn_is_not_dropped(self):
        tracker=LocalBattleStateTracker()
        message="マスカーニャのこうげきがあがった"
        first=read("battle_message",message,1)
        same_turn=read("battle_message",message,2)
        next_turn=read("battle_message",message,3)
        count,rows=tracker.ingest((first,),collect=True,turn_epoch=11)
        self.assertEqual(count,1)
        self.state,_=apply_local_ocr(self.state,rows,self.assets)
        count,rows=tracker.ingest((same_turn,),collect=True,turn_epoch=11)
        self.assertEqual((count,rows),(0,()))
        count,rows=tracker.ingest((next_turn,),collect=True,turn_epoch=12)
        self.assertEqual(count,1)
        self.state,_=apply_local_ocr(self.state,rows,self.assets)
        self.assertEqual(
            self.state["battle_evidence"]["boosts"]["own"]["マスカーニャ"]["A"],2)

    def test_hud_after_switch_waits_for_confirmed_name(self):
        self.apply(read("own_active_name","マスカーニャ",1),
                   read("own_hp_fraction","71/140",1,
                        {"current":71,"maximum":140}))
        self.apply(read("battle_message","ゆけっ!マリルリ!",3))
        stale=self.apply(read("own_hp_fraction","44/140",2,
                              {"current":44,"maximum":140}))
        self.assertEqual(stale[-1]["reason"],"PRE_SWITCH_STALE_HUD")
        uncertain=self.apply(read("own_pp_move1","0/20",4,
                                 {"current":0,"maximum":20}))
        self.assertEqual(uncertain[-1]["reason"],"ACTOR_HUD_NOT_ATTESTED")
        self.apply(read("own_active_name","マリルリ",5),
                   read("own_pp_move1","0/20",5,
                        {"current":0,"maximum":20}),
                   read("own_hp_fraction","80/150",5,
                        {"current":80,"maximum":150}))
        hp=self.state["battle_evidence"]["hp_by_pokemon"]["own"]
        self.assertEqual(hp["マスカーニャ"],"71/140")
        self.assertEqual(hp["マリルリ"],"80/150")
        self.assertEqual(self.state["battle_evidence"]["pp_by_pokemon"][
            "own"]["マリルリ"]["アクアジェット"],0)

    def test_faint_prevents_pending_pp_from_old_actor(self):
        self.apply(read("own_active_name","マスカーニャ",1))
        self.apply(read("battle_message","マスカーニャはたおれた!",2))
        candidate=self.apply(read("own_pp_move1","0/20",3,
                                 {"current":0,"maximum":20}))
        self.assertEqual(candidate[-1]["status"],"CANDIDATE")
        self.assertNotIn("マスカーニャ",
                         self.state["battle_evidence"]["pp_by_pokemon"]["own"])
        self.apply(read("own_active_name","マリルリ",4),
                   read("own_hp_fraction","149/150",4,
                        {"current":149,"maximum":150}))
        self.assertFalse(self.state["awaiting_replacement"])
        self.assertEqual(self.state["observed_hp"]["own"],"149/150")

    def test_visual_numeric_rank_then_ocr_delta_and_switch(self):
        update_battle_evidence(self.state,{
            "our_active":"マスカーニャ","opponent_active":"ガブリアス",
            "our_stat_changes":"こうげき+2"},self.assets.own_team)
        boosts=self.state["battle_evidence"]["boosts"]["own"]
        self.assertEqual(boosts["マスカーニャ"]["A"],2)
        self.apply(read("battle_message","マスカーニャのこうげきがさがった",1))
        self.assertEqual(boosts["マスカーニャ"]["A"],2)  # detached state
        self.assertEqual(self.state["battle_evidence"]["boosts"][
            "own"]["マスカーニャ"]["A"],1)
        self.apply(read("own_active_name","マリルリ",2))
        self.assertNotIn("マスカーニャ",
                         self.state["battle_evidence"]["boosts"]["own"])

    def test_spent_choice_item_unblocks_lock_and_move_is_not_item(self):
        evidence=self.state["battle_evidence"]
        evidence["choice_lock"]={"pokemon":"マスカーニャ",
                                 "move":"トリックフラワー","source":"OBSERVED"}
        self.apply(read("battle_message",
                        "マスカーニャはトリックフラワーをつかった",1))
        self.assertNotIn("マスカーニャ",self.state["battle_evidence"][
            "item_state_by_pokemon"]["own"])
        self.apply(read("battle_message",
                        "マスカーニャはこだわりスカーフをなくした",2))
        memory=self.state["battle_evidence"]
        self.assertEqual(memory["choice_lock"]["move"],"")
        self.assertEqual(memory["item_state_by_pokemon"]["own"][
            "マスカーニャ"]["state"],"SPENT_OR_LOST")
        code,_=validate_battle_choice(
            {"action_kind":"MOVE","move":"はたきおとす"},
            {"visual_ui":"COMMAND_MENU","visible_moves":[]},
            self.state,self.assets)
        self.assertEqual(code,"OK")

    def test_mega_form_uses_frozen_champions_type_only(self):
        self.assets.own_team+=({
            "name":"リザードン→メガリザードンX",
            "move1":"フレアドライブ","move2":"ドラゴンクロー",
            "move3":"じしん","move4":"りゅうのまい",
            "item":"リザードナイトX","choice_lock":"N"},)
        self.assets.matrix_index["メガリザードンX"]=(
            "メガリザードンX|ほのお/ドラゴン|100|frozen")
        self.state["selected_three"]=["リザードン","マスカーニャ","マリルリ"]
        self.state["our_active"]="リザードン"
        self.apply(read("battle_message","リザードンはメガシンカした",1))
        self.assertEqual(self.state["our_current_form"],"メガリザードンX")
        self.assertEqual(self.state["battle_evidence"]["current_type"][
            "own"]["リザードン"],"ほのお/ドラゴン")
        self.assertEqual(self.state["our_active"],"リザードン")

    def test_unparsed_visual_rank_does_not_become_zero(self):
        update_battle_evidence(self.state,{
            "our_active":"マスカーニャ","our_stat_changes":"変化あり（詳細不明）"
        },self.assets.own_team)
        self.apply(read("battle_message","マスカーニャのこうげきがあがった",1))
        entry=self.state["battle_evidence"]["boosts"]["own"]["マスカーニャ"]
        self.assertTrue(entry["_prior_stage_uncertain"])
        self.assertNotIn("A",entry)


if __name__=="__main__":
    unittest.main()
