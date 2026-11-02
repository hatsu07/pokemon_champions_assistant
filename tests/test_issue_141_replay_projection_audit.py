"""Synthetic comparison: fully specified replay *projection* versus frozen facts.

No real Showdown Battle, RNG, game capture or opponent ID attestation is
present. A matching external projection must NEVER be promoted to replay.
"""
from __future__ import annotations

from dataclasses import replace
import unittest

from champions_assist.local_strategy.models import canonical_json
from champions_assist.local_strategy.observed_replay_projection_audit import (
    ReplayProjectedMember, ReplayProjectionCandidate,
    audit_observed_replay_projections,
)
from champions_assist.local_strategy.observed_calculation_scenarios import (
    stage_observed_calculation_scenarios,
)
from tests.test_issue_140_foundation import VERIFIED_ASSETS, frozen
from tests.test_issue_141_observed_id_bridge import with_state, details
from tests.test_issue_141_joint_set_intake import record, members


def sourced():
    party=members()
    party["カイリュー"].update({"current_type":"ひこう","status":"やけど"})
    return (record("Joint-1",party=party),
            record("Joint-2",source_ref="synthetic://joint-2",party=party))


def snapshot():
    return with_state(frozen(),edits=details)


def replay(snapshot_state=None, *, hypothesis_id="Joint-1"):
    s=snapshot_state or snapshot()
    stage=stage_observed_calculation_scenarios(
        s,VERIFIED_ASSETS,joint_candidates=sourced())
    assert stage.status=="PARTIAL_SCENARIOS_NOT_RUNNABLE",stage.reason
    import json
    observed=json.loads(stage.scenarios[0].observed_constraints_json)
    own=tuple(ReplayProjectedMember(
        "p1",i,m["name"],m["registered_species_id"],
        tuple(pair[1] for pair in m["registered_move_ids"]),
        m["hp"] if m["hp"] is not None else 80,
        m["maxhp"] if m["maxhp"] is not None else 200,
        m["fainted"] if m["fainted"] is not None else False,
        tuple(tuple(b) for b in m["boosts"]))
        for i,m in enumerate(observed["own"],1))
    # Explicitly caller-claimed binding; NOT inferred by the application.
    # Slot one is switched-out Gholdengo, slot two is active Dragonite.
    opponent=(
        ReplayProjectedMember("p2",1,"サーフゴー","gholdengo",
                              ("makeit rain".replace(" ",""),),
                              20,120,False),
        ReplayProjectedMember("p2",2,"カイリュー","dragonite",
                              ("extremespeed","earthquake"),99,166,False,
                              (("spe",2),)),
        ReplayProjectedMember("p2",3,"ドドゲザン","kingambit",
                              ("kowtowcleave","suckerpunch"),100,100,False),
    )
    return ReplayProjectionCandidate(
        "EXTERNAL_REPLAY_PROJECTION_UNATTESTED",hypothesis_id,
        s.window.match_id,s.window.decision_window_id,s.window.state_revision,
        s.snapshot_sha256,s.window.simulator_sha,"a"*64,1,2,own+opponent)


class ReplayProjectionAuditTests(unittest.TestCase):
    def audit(self,s=None,records=None,sets=None):
        s=s or snapshot()
        records=records if records is not None else (
            replay(s), replay(s,hypothesis_id="Joint-2"))
        return audit_observed_replay_projections(
            s,VERIFIED_ASSETS,joint_candidates=sourced() if sets is None else sets,
            projections=records)

    def test_all_sourced_cases_compare_exact_visible_fields_only(self):
        s=snapshot()
        before=s.observed_state_json
        out=self.audit(s)
        self.assertEqual(out.status,"PARTIAL_REPLAY_CONSTRAINTS_MATCH",out.reason)
        self.assertEqual(len(out.cases),2)
        self.assertEqual(tuple(c.hypothesis_id for c in out.cases),
                         ("Joint-1","Joint-2"))
        self.assertIn("p1:1.species_id",out.cases[0].checked_fields)
        self.assertIn("p1:1.move_ids",out.cases[0].checked_fields)
        self.assertIn("p1:1.hp",out.cases[0].checked_fields)
        self.assertIn("p1:1.boosts.atk",out.cases[0].checked_fields)
        self.assertIn("p2:2.hp",out.cases[0].checked_fields)
        self.assertIn("p2:1.maxhp",out.cases[0].checked_fields)
        self.assertIn("EXTERNAL_P2_DISPLAY_TO_SLOT_AND_SPECIES_ID_UNATTESTED",
                      out.cases[0].unverified)
        self.assertFalse(out.full_state_reconstructed)
        self.assertFalse(out.engine_battle_authenticated)
        self.assertFalse(out.opponent_slot_mapping_authenticated)
        self.assertFalse(out.exhaustive_opponent_policy)
        self.assertFalse(out.ready_for_engine)
        self.assertEqual(s.observed_state_json,before)

    def test_one_wrong_or_missing_case_rejects_entire_set(self):
        s=snapshot()
        a=replay(s)
        b=replay(s,hypothesis_id="Joint-2")
        tests=[
            ((a,), "EXPLICIT_EQUAL_SOURCED_PROJECTIONS_REQUIRED"),
            ((b,a), "HYPOTHESIS_ORDER_OR_COVERAGE_MISMATCH"),
            ((a,a), "HYPOTHESIS_ORDER_OR_COVERAGE_MISMATCH"),
            ((a,replace(b,state_revision=1)), "STALE_OR_MALFORMED_REPLAY_PROJECTION"),
            ((a,replace(b,source="REAL_OCR_CONFIRMED")),"STALE_OR_MALFORMED_REPLAY_PROJECTION"),
            ((a,replace(b,battle_sha256="not-a-real-hash")),
             "STALE_OR_MALFORMED_REPLAY_PROJECTION"),
        ]
        for cases, expected in tests:
            with self.subTest(expected=expected,cases=cases):
                res=self.audit(s,cases)
                self.assertEqual(res.status,"NO_REPLAY_PROJECTION_MATCH")
                self.assertEqual(res.reason,expected)
                self.assertFalse(res.cases)

    def test_own_frozen_team_species_move_hp_boosts_and_actor_must_match(self):
        s=snapshot()
        a=replay(s)
        b=replay(s,hypothesis_id="Joint-2")
        p=a.members
        changes=[
            (0,{"species_id":"mew"}, "FROZEN_P1_ROSTER_OR_MOVE_ID_MISMATCH"),
            (0,{"move_ids":("earthquake",)}, "FROZEN_P1_ROSTER_OR_MOVE_ID_MISMATCH"),
            (0,{"hp":62}, "OBSERVED_EXACT_STATE_MISMATCH"),
            (0,{"maxhp":199}, "OBSERVED_EXACT_STATE_MISMATCH"),
            (0,{"boosts":(("atk",0),("spe",-1))},"OBSERVED_EXACT_STATE_MISMATCH"),
            (2,{"hp":1,"fainted":False},"OBSERVED_EXACT_STATE_MISMATCH"),
            (3,{"hp":21},"OBSERVED_EXACT_STATE_MISMATCH"),
            (4,{"hp":98},"OBSERVED_EXACT_STATE_MISMATCH"),
        ]
        for ix, change, error in changes:
            with self.subTest(index=ix,change=change):
                altered=list(p)
                altered[ix]=replace(p[ix],**change)
                failed=self.audit(s,(replace(a,members=tuple(altered)),b))
                self.assertEqual(failed.reason,error,failed)
                self.assertFalse(failed.cases)
        self.assertEqual(self.audit(s,(replace(a,p1_active_slot=2),b)).reason,
                         "REPLAY_ACTIVE_SLOT_MISMATCH")
        self.assertEqual(self.audit(s,(replace(a,p2_active_slot=1),b)).reason,
                         "REPLAY_ACTIVE_SLOT_MISMATCH")

    def test_fainted_opponent_zero_percent_is_matched_without_exact_inference(self):
        def event(state):
            details(state)
            ev=state["battle_evidence"]
            ev.setdefault("fainted",{"own":{},"opponent":{}})["opponent"][
                "サーフゴー"]=True
            ev["hp_by_pokemon"]["opponent"]["サーフゴー"]="0%"
        s=with_state(frozen(),edits=event)
        a=replay(s)
        b=replay(s,hypothesis_id="Joint-2")
        one=list(a.members)
        one[3]=replace(one[3],hp=0,fainted=True)
        a=replace(a,members=tuple(one))
        b=replace(b,members=tuple(one))
        out=self.audit(s,(a,b))
        self.assertEqual(out.status,"PARTIAL_REPLAY_CONSTRAINTS_MATCH",out.reason)
        self.assertIn("p2:1.fainted",out.cases[0].checked_fields)
        self.assertNotIn("p2:1.hp",out.cases[0].checked_fields)
        self.assertIn("RAW_OPPONENT_HP_PERCENT_NOT_EXACT",out.cases[0].unverified)
        self.assertFalse(out.ready_for_engine)
        forged=list(one)
        forged[3]=replace(forged[3],hp=1,fainted=False)
        fail=self.audit(s,(replace(a,members=tuple(forged)),b))
        self.assertEqual(fail.reason,"OBSERVED_EXACT_STATE_MISMATCH")

    def test_zero_percent_without_faint_event_does_not_claim_ko(self):
        def rounded(state):
            details(state)
            state["battle_evidence"]["hp_by_pokemon"]["opponent"][
                "カイリュー"]="0%"
        s=with_state(frozen(),edits=rounded)
        out=self.audit(s)
        self.assertEqual(out.status,"PARTIAL_REPLAY_CONSTRAINTS_MATCH",out.reason)
        self.assertNotIn("p2:2.hp",out.cases[0].checked_fields)
        self.assertNotIn("p2:2.fainted",out.cases[0].checked_fields)

    def test_malformed_projection_and_unverified_batch_never_promote(self):
        s=snapshot()
        a=replay(s)
        b=replay(s,hypothesis_id="Joint-2")
        for bad in (
            replace(a,members=tuple(a.members[:-1])),
            replace(a,members=tuple((*a.members[:-1],a.members[-2]))),
            replace(a,members=(replace(a.members[0],hp=-1),*a.members[1:])),
            replace(a,members=(replace(a.members[0],fainted=True),*a.members[1:])),
            replace(a,members=(replace(a.members[0],slot=True),*a.members[1:])),
            replace(a,members=(replace(a.members[0],move_ids=("INVALID ID",)),
                               *a.members[1:])),
        ):
            with self.subTest(bad=bad):
                out=self.audit(s,(bad,b))
                self.assertEqual(out.status,"NO_REPLAY_PROJECTION_MATCH")
                self.assertFalse(out.cases)
        self.assertEqual(self.audit(s,(),()).status,"NO_REPLAY_PROJECTION_MATCH")
        self.assertEqual(audit_observed_replay_projections(
            s,VERIFIED_ASSETS,joint_candidates=(),projections=(a,)).status,
            "NO_REPLAY_PROJECTION_MATCH")

    def test_unreviewed_screen_state_revision_cannot_reuse_candidate(self):
        s=snapshot()
        a=replay(s)
        b=replay(s,hypothesis_id="Joint-2")
        changed=with_state(s,field="new_frame_after_request",value=1)
        out=self.audit(changed,(a,b))
        self.assertEqual(out.status,"NO_REPLAY_PROJECTION_MATCH")
        self.assertEqual(out.reason,"STALE_OR_MALFORMED_REPLAY_PROJECTION")


if __name__ == "__main__":
    unittest.main()
