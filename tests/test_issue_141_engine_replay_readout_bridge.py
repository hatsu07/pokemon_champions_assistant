"""Synthetic adapter tests for an UNATTESTED readout from pinned Showdown.

The objects look like real JavaScript extractor results, but this Python
test deliberately cannot certify their provenance. It tests comparison only.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import unittest

from champions_assist.local_strategy.engine_replay_readout_bridge import (
    ClaimedEngineReadoutCase, audit_claimed_engine_readouts,
    PIN, SOURCE,
)
from tests.test_issue_140_foundation import VERIFIED_ASSETS
from tests.test_issue_141_replay_projection_audit import snapshot, replay, sourced
from tests.test_issue_141_observed_id_bridge import with_state

BOOSTS=("atk","def","spa","spd","spe","accuracy","evasion")


def converted(p):
    members=[]
    display=[]
    for member in p.members:
        b=dict(member.boosts)
        members.append({
            "side":member.side, "slot":member.slot,
            "species_id":member.species_id,
            "move_ids":list(member.move_ids),
            "hp":member.hp,"maxhp":member.maxhp,"fainted":member.fainted,
            "boosts":[[k,b.get(k,0)] for k in BOOSTS],
        })
        if member.side=="p2":
            display.append(member.display_name)
    data={
        "status":"SYNTHETIC_ENGINE_REPLAY_READOUT","source":SOURCE,
        "engine_sha":PIN,"battle_sha256":p.battle_sha256,
        "active":{"p1":p.p1_active_slot,"p2":p.p2_active_slot},
        "members":members,
        "full_state_reconstructed":False,"ready_for_engine":False,
        "opponent_slot_mapping_authenticated":False,
        "unverified":["CHAMPIONS_SHOWDOWN_PARITY_NOT_VERIFIED"],
    }
    return ClaimedEngineReadoutCase(
        p.hypothesis_id,data,tuple(display),
        p.match_id,p.decision_window_id,p.state_revision,
        p.snapshot_sha256,p.battle_sha256)


class ClaimedEngineReadoutAdapterTests(unittest.TestCase):
    def fixture(self,s=None):
        s=s or snapshot()
        a=converted(replay(s))
        b=converted(replay(s,hypothesis_id="Joint-2"))
        return s,a,b

    def audit(self,s,a,b,sets=None):
        return audit_claimed_engine_readouts(
            s,VERIFIED_ASSETS,joint_candidates=sourced() if sets is None else sets,
            readouts=(a,b))

    def test_valid_synthetic_all_case_bridge_still_has_no_trusted_battle(self):
        s,a,b=self.fixture()
        before=s.observed_state_json
        out=self.audit(s,a,b)
        self.assertEqual(out.status,"PARTIAL_REPLAY_CONSTRAINTS_MATCH",out.reason)
        self.assertEqual(len(out.cases),2)
        self.assertIn("p1:1.move_ids",out.cases[0].checked_fields)
        self.assertIn("p2:2.hp",out.cases[0].checked_fields)
        self.assertFalse(out.full_state_reconstructed)
        self.assertFalse(out.engine_battle_authenticated)
        self.assertFalse(out.opponent_slot_mapping_authenticated)
        self.assertFalse(out.exhaustive_opponent_policy)
        self.assertFalse(out.ready_for_engine)
        self.assertEqual(s.observed_state_json,before)

    def test_supplied_p2_display_slots_are_unattested_but_checked_for_conflict(self):
        s,a,b=self.fixture()
        swapped=replace(a,opponent_display_by_slot=(
            "カイリュー","サーフゴー","ドドゲザン"))
        out=self.audit(s,swapped,b)
        self.assertEqual(out.reason,"REPLAY_ACTIVE_SLOT_MISMATCH")
        self.assertEqual(out.cases,())
        duplicate=replace(a,opponent_display_by_slot=(
            "サーフゴー","サーフゴー","ドドゲザン"))
        self.assertEqual(self.audit(s,duplicate,b).reason,
                         "READOUT_HYPOTHESIS_OR_DISPLAY_BINDING_INVALID")

    def test_wrong_battle_state_on_one_hypothesis_blocks_everything(self):
        s,a,b=self.fixture()
        bad=deepcopy(b.readout)
        bad["members"][4]["hp"]=98
        out=self.audit(s,a,replace(b,readout=bad))
        self.assertEqual(out.reason,"OBSERVED_EXACT_STATE_MISMATCH")
        self.assertEqual(out.cases,())
        bad=deepcopy(b.readout)
        bad["active"]["p2"]=1
        self.assertEqual(self.audit(s,a,replace(b,readout=bad)).reason,
                         "REPLAY_ACTIVE_SLOT_MISMATCH")

    def test_forged_or_invalid_readout_cannot_claim_engine_certification(self):
        s,a,b=self.fixture()
        for mutate in (
            lambda d: d.update(source="UNVERIFIED_REAL_GAME"),
            lambda d: d.update(status="ENGINE_VERIFIED"),
            lambda d: d.update(engine_sha="0"*40),
            lambda d: d.update(battle_sha256="unbound"),
            lambda d: d.update(ready_for_engine=True),
            lambda d: d.update(full_state_reconstructed=True),
            lambda d: d.update(opponent_slot_mapping_authenticated=True),
            lambda d: d["active"].update(p1=True),
            lambda d: d["members"][0].update(slot=True),
            lambda d: d["members"][0].update(hp="80/200"),
            lambda d: d["members"][0].update(extra="unsafe"),
            lambda d: d["members"][0]["boosts"].pop(),
            lambda d: d["members"].pop(),
            lambda d: d["members"][0].update(move_ids=[]),
            lambda d: d.update(unknown="invented"),
        ):
            with self.subTest(mutate=mutate):
                bad=deepcopy(a.readout)
                mutate(bad)
                out=self.audit(s,replace(a,readout=bad),b)
                self.assertEqual(out.reason,
                                 "UNTRUSTED_OR_MALFORMED_ENGINE_READOUT",out)
                self.assertFalse(out.ready_for_engine)

    def test_stale_window_or_missing_joint_never_picks_a_favorable_subset(self):
        s,a,b=self.fixture()
        changed=with_state(s,field="new_frame",value=1)
        self.assertEqual(self.audit(changed,a,b).status,
                         "NO_REPLAY_PROJECTION_MATCH")
        self.assertEqual(audit_claimed_engine_readouts(
            s,VERIFIED_ASSETS,joint_candidates=sourced(),
            readouts=(a,)).reason,"COMPLETE_SOURCED_ENGINE_READOUT_BATCH_REQUIRED")
        self.assertEqual(self.audit(s,a,replace(b,hypothesis_id="Joint-1")).reason,
                         "READOUT_HYPOTHESIS_OR_DISPLAY_BINDING_INVALID")
        self.assertEqual(self.audit(s,replace(a,battle_sha256="0"*64),b).reason,
                         "UNTRUSTED_OR_MALFORMED_ENGINE_READOUT")
        self.assertEqual(self.audit(s,replace(a,state_revision=1),b).reason,
                         "READOUT_HYPOTHESIS_OR_DISPLAY_BINDING_INVALID")
        self.assertEqual(self.audit(s,replace(a,match_id="other"),b).reason,
                         "READOUT_HYPOTHESIS_OR_DISPLAY_BINDING_INVALID")
        self.assertEqual(self.audit(s,a,b,sets=()).status,
                         "NO_REPLAY_PROJECTION_MATCH")


if __name__=="__main__":
    unittest.main()
