"""Battle state must use visible evidence, never model recommendations."""
from __future__ import annotations
from types import MappingProxyType
import unittest

from champions_assist.battle_evidence import update_battle_evidence
from champions_assist.match_replay import (
    new_replay, advance_observation, validate_battle_choice, OBSERVATION_SCHEMA,
)
from champions_assist.preload import FrozenMatchAssets


def assets():
    return FrozenMatchAssets(
        contents=MappingProxyType({}),
        sha256=MappingProxyType({}),
        matrix_index=MappingProxyType({}),
        lookup_index=MappingProxyType({}),
        own_team=(
            {"name":"マスカーニャ","item":"こだわりスカーフ","choice_lock":"Y",
             "move1":"トリックフラワー","move2":"はたきおとす",
             "move3":"とんぼがえり","move4":"トリプルアクセル"},
            {"name":"アシレーヌ","item":"たべのこし","choice_lock":"N",
             "move1":"ムーンフォース","move2":"うたかたのアリア",
             "move3":"アンコール","move4":"アクアジェット"},
            {"name":"ルカリオ → メガルカリオZ","item":"ルカリオナイトZ","choice_lock":"N",
             "move1":"わるだくみ","move2":"はどうだん",
             "move3":"ラスターカノン","move4":"あくのはどう"},
        ),
        frozen_at="fixture")


def obs(ui, **changes):
    result=dict(
        visual_ui=ui,our_six=[],opponent_six=[],our_active="",
        opponent_active="",visible_moves=[],visible_party=[],our_hp="",
        opponent_hp="",messages=[],observed_event="NONE",
        match_result="UNKNOWN",uncertain_slots=[],
    )
    result.update({name:"" for name in OBSERVATION_SCHEMA["properties"]
                   if name not in result})
    result.update(changes)
    return result


class BattleEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.assets=assets()
        self.state=new_replay(self.assets)
        self.state["phase"]="BATTLE"
        self.state["matrix_compatible"]=False
        self.state["selected_three"]=[
            "マスカーニャ","アシレーヌ","ルカリオ → メガルカリオZ"]
        self.state["lead"]="マスカーニャ"

    def advance(self, screen, **kwargs):
        # Test-only calls use no network. AI action is not observed execution.
        self.state,_=advance_observation(
            self.state,self.assets,obs(screen, **kwargs),
            lambda *args:{"action_kind":"NO_ACTION","move":"",
                          "switch_target":"","warning":""},
            elapsed_seconds=lambda:0,
        )

    def test_visible_hp_items_abilities_forms_stats_persist_for_each_member(self):
        self.advance("ACTION_RESOLUTION",our_active="マスカーニャ",opponent_active="ガブリアス",
                     our_hp="92/145",opponent_hp="73%",
                     our_item_revealed="こだわりスカーフ",
                     opponent_item_revealed="いのちのたま",
                     opponent_ability_revealed="さめはだ",
                     our_stat_changes="攻撃+1",opponent_stat_changes="素早さ+1",
                     our_status="まひ",opponent_status="やけど")
        self.advance("ACTION_RESOLUTION",our_active="アシレーヌ",
                     opponent_active="サーフゴー",our_hp="160/160",
                     our_ability_revealed="げきりゅう")
        facts=self.state["battle_evidence"]
        self.assertEqual(facts["hp_by_pokemon"]["own"]["マスカーニャ"],"92/145")
        self.assertEqual(facts["hp_by_pokemon"]["opponent"]["ガブリアス"],"73%")
        self.assertEqual(facts["item_by_pokemon"]["opponent"]["ガブリアス"],"いのちのたま")
        self.assertEqual(facts["ability_by_pokemon"]["opponent"]["ガブリアス"],"さめはだ")
        self.assertEqual(facts["status_by_pokemon"]["own"]["マスカーニャ"],"まひ")
        self.assertNotIn("マスカーニャ",facts["boosts"]["own"])
        self.assertEqual(facts["boosts"]["opponent"]["ガブリアス"],"素早さ+1")
        self.advance("ACTION_RESOLUTION",our_active="メガルカリオZ",
                     our_current_form="メガルカリオZ",
                     our_hp="140/140",opponent_active="サーフゴー")
        self.assertEqual(self.state["our_active"],"メガルカリオZ")
        self.assertEqual(facts["form_by_pokemon"]["own"]["メガルカリオZ"],"メガルカリオZ")
        self.assertEqual(facts["hp_by_pokemon"]["own"]["メガルカリオZ"],"140/140")

    def test_choice_lock_requires_observed_resolution_and_blocks_different_move(self):
        self.advance("MOVE_MENU",our_active="マスカーニャ",
                     visible_moves=["トリックフラワー","はたきおとす"])
        evidence=self.state["battle_evidence"]
        self.assertFalse(evidence["choice_lock"]["move"])
        self.advance("ACTION_RESOLUTION",our_active="マスカーニャ",
                     our_used_move="トリックフラワー")
        self.assertEqual(evidence["choice_lock"]["move"],"トリックフラワー")
        action=obs("MOVE_MENU",our_active="マスカーニャ",
                   visible_moves=["トリックフラワー","はたきおとす"])
        forbidden={"action_kind":"MOVE","move":"はたきおとす",
                   "switch_target":"","warning":""}
        reason,_=validate_battle_choice(forbidden,action,self.state,self.assets)
        self.assertEqual(reason,"CHOICE_LOCK_VIOLATION")
        allowed={**forbidden,"move":"トリックフラワー"}
        reason,_=validate_battle_choice(allowed,action,self.state,self.assets)
        self.assertEqual(reason,"OK")

    def test_switch_clears_lock_and_does_not_infer_a_move(self):
        self.advance("ACTION_RESOLUTION",our_active="マスカーニャ",
                     our_used_move="トリックフラワー")
        self.advance("ACTION_RESOLUTION",our_active="アシレーヌ")
        self.assertFalse(self.state["battle_evidence"]["choice_lock"]["move"])
        self.advance("ACTION_RESOLUTION",our_active="マスカーニャ")
        self.assertFalse(self.state["battle_evidence"]["choice_lock"]["move"])

    def test_changed_item_or_faint_clears_lock(self):
        self.advance("ACTION_RESOLUTION",our_active="マスカーニャ",
                     our_used_move="トリックフラワー")
        self.advance("ACTION_RESOLUTION",our_active="マスカーニャ",
                     our_item_revealed="たべのこし")
        self.assertFalse(self.state["battle_evidence"]["choice_lock"]["move"])
        self.advance("ACTION_RESOLUTION",our_active="マスカーニャ",
                     observed_event="OUR_FAINTED")
        self.assertFalse(self.state["battle_evidence"]["choice_lock"]["move"])

    def test_stat_changes_are_observed_not_guessed(self):
        self.advance("ACTION_RESOLUTION",our_active="マスカーニャ",
                     opponent_active="ガブリアス",messages=["能力が変化した"])
        self.assertEqual(self.state["battle_evidence"]["boosts"]["own"],{})
        self.assertEqual(self.state["battle_evidence"]["boosts"]["opponent"],{})
        self.assertFalse(self.state["battle_evidence"]["last_confirmed_our_move"])


if __name__=="__main__":
    unittest.main()
