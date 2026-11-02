"""Staged Champions visible-evidence/ID bridge: synthetic only, no real-game data."""
from __future__ import annotations

from dataclasses import replace
import copy
import unittest

from champions_assist.local_strategy.models import canonical_json
from champions_assist.local_strategy.observed_id_bridge import (
    stage_observed_champions_ids,
)
from tests.test_issue_140_foundation import (
    VERIFIED_ASSETS, FakeAssets, frozen,
)


def with_state(snapshot, *, field=None, value=None, edits=None):
    state = snapshot.state
    if field is not None:
        state[field] = value
    if edits:
        edits(state)
    return replace(snapshot, observed_state_json=canonical_json(state))


def details(state):
    evidence = state["battle_evidence"]
    evidence["hp_by_pokemon"] = {
        "own": {"ガブリアス": "63/184", "ミミッキュ": "0"},
        "opponent": {"カイリュー": "99/166", "サーフゴー": "20/120"},
    }
    evidence["item_by_pokemon"] = {
        "own": {"ガブリアス": "いのちのたま"},
        "opponent": {"カイリュー": "ラムのみ"},
    }
    evidence["ability_by_pokemon"] = {
        "own": {}, "opponent": {"サーフゴー": "おうごんのからだ"},
    }
    evidence["form_by_pokemon"] = {
        "own": {}, "opponent": {"カイリュー": "カイリュー"},
    }
    evidence["boosts"] = {
        "own": {"ガブリアス": {"A": 1, "S": -1}},
        "opponent": {"カイリュー": {"S": 2}},
    }
    evidence["status_by_pokemon"] = {
        "own": {"ガブリアス": "まひ"},
        "opponent": {"カイリュー": "やけど"},
    }
    evidence["current_type"] = {
        "own": {"ガブリアス": "ドラゴン"},
        "opponent": {"カイリュー": "ひこう"},
    }
    evidence["moves_by_pokemon"] = {
        "own": {"ガブリアス": ["じしん", "ドラゴンクロー"]},
        "opponent": {"カイリュー": ["しんそく"],
                     "サーフゴー": ["ゴールドラッシュ"]},
    }
    state["opponent_active"] = "カイリュー"
    state["opponent_six"] = [
        "カイリュー", "サーフゴー", "ドドゲザン", "ウルガモス",
        "ロトム", "ミミッキュ",
    ]


def staged():
    return stage_observed_champions_ids(
        with_state(frozen(), edits=details), VERIFIED_ASSETS)


class ObservedIdBridgeTests(unittest.TestCase):
    def test_frozen_team_ids_and_exact_display_evidence_are_distinct(self):
        snapshot = with_state(frozen(), edits=details)
        initial_state = snapshot.observed_state_json
        initial_assets = copy.deepcopy([dict(m) for m in VERIFIED_ASSETS.own_team])
        result = stage_observed_champions_ids(snapshot, VERIFIED_ASSETS)
        self.assertEqual(result.status, "PARTIAL_OBSERVATION_ID_STAGE")
        self.assertEqual(result.snapshot_sha256, snapshot.snapshot_sha256)
        self.assertEqual(result.match_id, snapshot.window.match_id)
        self.assertEqual(result.decision_window_id, snapshot.window.decision_window_id)
        self.assertEqual(result.state_revision, snapshot.window.state_revision)
        self.assertEqual(result.own_active_slot, 1)
        self.assertEqual(len(result.own_members), 3)
        first = result.own_members[0]
        self.assertEqual(first.display_name, "ガブリアス")
        self.assertEqual(first.registered_species_id, "garchomp")
        self.assertEqual(first.own_party_slot, 1)
        self.assertEqual(first.registered_move_ids[0], ("じしん", "earthquake"))
        self.assertEqual(first.revealed_move_ids, ("earthquake", "dragonclaw"))
        self.assertEqual(first.observed_moves_display, ("じしん", "ドラゴンクロー"))
        self.assertEqual((first.hp,first.maxhp,first.fainted),(63,184,False))
        self.assertEqual(first.boosts,(("atk",1),("spe",-1)))
        self.assertEqual(first.raw_status, "まひ")
        self.assertEqual(first.raw_current_type, "ドラゴン")
        self.assertEqual((result.own_members[2].hp,result.own_members[2].maxhp,
                          result.own_members[2].fainted),(0,None,True))
        self.assertEqual(result.opponent.display_name,"カイリュー")
        self.assertIsNone(result.opponent.registered_species_id)
        self.assertIsNone(result.opponent.own_party_slot)
        self.assertEqual(result.opponent.registered_move_ids,())
        self.assertEqual(result.opponent.revealed_move_ids,())
        self.assertEqual(result.opponent.observed_moves_display,("しんそく",))
        self.assertEqual(result.opponent.raw_item, "ラムのみ")
        self.assertEqual(result.opponent.raw_form, "カイリュー")
        self.assertEqual(result.own_members[0].raw_item, "いのちのたま")
        self.assertEqual(len(result.other_observed_opponents), 1)
        other=result.other_observed_opponents[0]
        self.assertEqual(other.display_name,"サーフゴー")
        self.assertEqual((other.hp,other.maxhp),(20,120))
        self.assertEqual(other.raw_ability,"おうごんのからだ")
        self.assertEqual(other.observed_moves_display,("ゴールドラッシュ",))
        self.assertIsNone(other.registered_species_id)
        self.assertIsNone(other.own_party_slot)
        self.assertIn("ITEM_ABILITY_AND_FORM_IDS_UNVERIFIED",result.unbound)
        self.assertIn("REVEALED_OPPONENT_MOVE_IDS_UNMAPPED",result.unbound)
        self.assertIn("STATUS_TRANSLATION_NOT_VERIFIED",result.unbound)
        self.assertIn("CURRENT_TYPE_TRANSLATION_NOT_VERIFIED",result.unbound)
        self.assertFalse(result.full_state_reconstructed)
        self.assertFalse(result.opponent_joint_set_verified)
        self.assertFalse(result.ready_for_engine)
        self.assertEqual(snapshot.observed_state_json, initial_state)
        self.assertEqual([dict(m) for m in VERIFIED_ASSETS.own_team],
                         initial_assets)

    def test_empty_evidence_never_defaults_hp_status_or_boosts(self):
        result=stage_observed_champions_ids(frozen(),VERIFIED_ASSETS)
        self.assertEqual(result.status,"PARTIAL_OBSERVATION_ID_STAGE")
        self.assertIsNone(result.own_members[0].hp)
        self.assertIsNone(result.own_members[0].maxhp)
        self.assertIsNone(result.own_members[0].fainted)
        self.assertIsNone(result.own_members[0].raw_status)
        self.assertEqual(result.own_members[0].boosts,())
        self.assertIsNone(result.opponent)
        self.assertFalse(result.ready_for_engine)

    def test_frozen_four_file_manifest_and_explicit_ids_required(self):
        self.assertEqual(
            stage_observed_champions_ids(frozen(),FakeAssets()).reason,
            "FROZEN_OWN_TEAM_IDS_NOT_VERIFIED")
        bad=replace(frozen(),window=replace(
            frozen().window, team_sha256="0"*64))
        self.assertEqual(
            stage_observed_champions_ids(bad,VERIFIED_ASSETS).reason,
            "FROZEN_OWN_TEAM_IDS_NOT_VERIFIED")

    def test_own_revealed_move_must_equal_frozen_slot_not_guess(self):
        def wrong(state):
            details(state)
            state["battle_evidence"]["moves_by_pokemon"]["own"]["ガブリアス"] = [
                "しんそく"]
        result=stage_observed_champions_ids(
            with_state(frozen(),edits=wrong),VERIFIED_ASSETS)
        self.assertEqual(result.status,"NO_OBSERVATION_ID_STAGE")
        self.assertEqual(result.reason,"INVALID_OR_CONTRADICTORY_VISIBLE_EVIDENCE")

    def test_unknown_own_active_and_previous_match_actor_block(self):
        for actor in ("ミミッキュ","カイリュー"):
            with self.subTest(actor=actor):
                snapshot=with_state(frozen(),field="our_active",value=actor)
                result=stage_observed_champions_ids(snapshot,VERIFIED_ASSETS)
                self.assertEqual(result.status,"NO_OBSERVATION_ID_STAGE")
        snapshot=with_state(
            frozen(),edits=lambda s:s["battle_evidence"]["hp_by_pokemon"].update(
                {"own":{"サーフゴー":"17/100"}}))
        self.assertEqual(stage_observed_champions_ids(
            snapshot,VERIFIED_ASSETS).status,"NO_OBSERVATION_ID_STAGE")

    def test_percentage_bare_positive_or_corrupt_hp_is_never_exact(self):
        for hp in ("60%","75","81/???","94/40","0/0",0,True,False,
                   "100/100 brn"," 63/184 ","63 /184"):
            with self.subTest(hp=repr(hp)):
                def change(s):
                    details(s)
                    s["battle_evidence"]["hp_by_pokemon"]["own"]["ガブリアス"]=hp
                result=stage_observed_champions_ids(
                    with_state(frozen(),edits=change),VERIFIED_ASSETS)
                self.assertEqual(result.status,"NO_OBSERVATION_ID_STAGE")

    def test_invalid_stages_never_become_zero_or_inferred(self):
        for boosts in ({"A":True},{"A":7},{"Atk":1},{"A":0,"C":6.5},
                       "A+1",{"atk":2},{}):
            with self.subTest(boosts=boosts):
                def change(s):
                    details(s)
                    s["battle_evidence"]["boosts"]["own"]["ガブリアス"]=boosts
                self.assertEqual(stage_observed_champions_ids(
                    with_state(frozen(),edits=change),VERIFIED_ASSETS).status,
                    "NO_OBSERVATION_ID_STAGE")

    def test_revealed_opponent_is_only_display_literal_not_id_or_slot(self):
        result=staged()
        self.assertEqual(result.opponent.hp,99)
        self.assertEqual(result.opponent.maxhp,166)
        self.assertEqual(result.opponent.boosts,(("spe",2),))
        self.assertEqual(result.opponent.raw_status,"やけど")
        self.assertIsNone(result.opponent.registered_species_id)
        self.assertIsNone(result.opponent.own_party_slot)
        self.assertFalse(result.ready_for_engine)

    def test_invalid_opponent_preview_or_actor_does_not_guess_selection(self):
        for edit in (
            lambda s:s.update(opponent_active="ゲンガー"),
            lambda s:s.update(opponent_six=["カイリュー"]*6),
            lambda s:s.update(opponent_six=["カイリュー"]),
            lambda s:s.update(opponent_six=[42,"サーフゴー"]),
            lambda s:s.update(opponent_six=False),
        ):
            def change(s):
                details(s)
                edit(s)
            result=stage_observed_champions_ids(
                with_state(frozen(),edits=change),VERIFIED_ASSETS)
            self.assertEqual(result.status,"NO_OBSERVATION_ID_STAGE")

    def test_present_malformed_evidence_never_becomes_missing(self):
        for bad in ([],None,False,0,"bad"):
            with self.subTest(value=repr(bad)):
                snapshot=with_state(frozen(),field="battle_evidence",value=bad)
                self.assertEqual(stage_observed_champions_ids(
                    snapshot,VERIFIED_ASSETS).status,
                    "NO_OBSERVATION_ID_STAGE")
                snapshot=with_state(frozen(),edits=lambda s:
                    s["battle_evidence"].update(hp_by_pokemon=bad))
                self.assertEqual(stage_observed_champions_ids(
                    snapshot,VERIFIED_ASSETS).status,
                    "NO_OBSERVATION_ID_STAGE")

    def test_external_opponent_and_japanese_status_are_not_translated(self):
        def change(s):
            details(s)
            s["battle_evidence"]["status_by_pokemon"]["opponent"]["カイリュー"] = "どく"
            s["battle_evidence"]["moves_by_pokemon"]["opponent"]["カイリュー"] = [
                "未知のChampions技"]
        result=stage_observed_champions_ids(
            with_state(frozen(),edits=change),VERIFIED_ASSETS)
        self.assertEqual(result.status,"PARTIAL_OBSERVATION_ID_STAGE")
        self.assertEqual(result.opponent.raw_status,"どく")
        self.assertEqual(result.opponent.observed_moves_display,
                         ("未知のChampions技",))
        self.assertEqual(result.opponent.revealed_move_ids,())
        self.assertFalse(result.ready_for_engine)


if __name__=="__main__":
    unittest.main()
