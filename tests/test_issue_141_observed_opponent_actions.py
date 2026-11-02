"""Stage #141 sourced joint opponent move/switch names, without p2 legality.

All fixtures are synthetic. No Champions usage marginal becomes a joint set.
"""
from __future__ import annotations

from dataclasses import replace
import unittest

from champions_assist.local_strategy.models import canonical_json
from champions_assist.local_strategy.observed_calculation_scenarios import (
    stage_observed_calculation_scenarios,
)
from champions_assist.local_strategy.observed_opponent_actions import (
    stage_observed_opponent_action_frontier,
)
from tests.test_issue_140_foundation import VERIFIED_ASSETS, frozen
from tests.test_issue_141_observed_id_bridge import with_state, details
from tests.test_issue_141_joint_set_intake import record, members


def _source_pair():
    one = members()
    one["カイリュー"].update({
        "current_type": "ひこう", "status": "やけど"})
    two = {k: dict(v) for k, v in one.items()}
    two["カイリュー"]["moves"] = ["しんそく", "かみなりパンチ"]
    return (
        record("Joint-1", party=one),
        record("Joint-2", source_ref="synthetic://second-joint", party=two),
    )


def _current():
    return with_state(frozen(), edits=details)


class OpponentActionFrontierTests(unittest.TestCase):
    def test_two_sourced_joint_options_keep_moves_and_switches_separate(self):
        snapshot=_current()
        sources=_source_pair()
        old=snapshot.observed_state_json
        stage=stage_observed_calculation_scenarios(
            snapshot,VERIFIED_ASSETS,joint_candidates=sources)
        out=stage_observed_opponent_action_frontier(
            snapshot,VERIFIED_ASSETS,joint_candidates=sources,
            observed_batch=stage)
        self.assertEqual(out.status,"PROVISIONAL_OPPONENT_ACTION_NAMES",out.reason)
        self.assertEqual(out.snapshot_sha256,snapshot.snapshot_sha256)
        self.assertEqual(out.decision_window_id,snapshot.window.decision_window_id)
        self.assertEqual(len(out.cases),2)
        self.assertEqual([c.hypothesis_id for c in out.cases],
                         ["Joint-1","Joint-2"])
        first,second=out.cases
        self.assertEqual(
            [(p.action_kind,p.display_name) for p in first.possibilities],
            [("MOVE_NAME","しんそく"),("MOVE_NAME","じしん"),
             ("SWITCH_TARGET_NAME","サーフゴー"),
             ("SWITCH_TARGET_NAME","ドドゲザン")])
        self.assertEqual(
            [(p.action_kind,p.display_name) for p in second.possibilities],
            [("MOVE_NAME","しんそく"),("MOVE_NAME","かみなりパンチ"),
             ("SWITCH_TARGET_NAME","サーフゴー"),
             ("SWITCH_TARGET_NAME","ドドゲザン")])
        self.assertEqual(first.possibilities[0].basis,
                         "OBSERVED_MOVE_AND_JOINT")
        self.assertEqual(first.possibilities[1].basis,
                         "JOINT_SOURCE_UNREVEALED_MOVE")
        self.assertEqual(first.possibilities[2].condition,
                         "TRAP_SWITCH_LEGALITY_UNVERIFIED")
        self.assertEqual(first.possibilities[3].condition,
                         "BENCH_ALIVE_NOT_VERIFIED")
        self.assertIn("SOURCE_KIND:CHAMPIONS_COOCCURRING_JOINT_RECORD",
                      first.source_evidence)
        self.assertTrue(out.possibilities_sha256)
        self.assertEqual(len(out.possibilities_sha256),64)
        self.assertTrue(all(p.legal_status=="UNKNOWN" for c in out.cases
                            for p in c.possibilities))
        self.assertEqual(out.p2_commands,())
        self.assertFalse(out.exhaustive_legal_choices_verified)
        self.assertFalse(out.executable_battle_reconstructed)
        self.assertFalse(out.ready_for_engine)
        self.assertEqual(snapshot.observed_state_json,old)

    def test_no_joint_set_leaves_one_unknown_case_and_no_fabricated_moves(self):
        snap=_current()
        result=stage_observed_opponent_action_frontier(snap,VERIFIED_ASSETS)
        self.assertEqual(result.status,"PROVISIONAL_OPPONENT_ACTION_NAMES")
        self.assertEqual(len(result.cases),1)
        self.assertIsNone(result.cases[0].hypothesis_id)
        self.assertFalse(result.cases[0].possibilities)
        self.assertIn("OPPONENT_JOINT_SET_UNKNOWN",result.cases[0].unresolved)
        self.assertFalse(result.ready_for_engine)

    def test_stale_or_modified_scenario_batch_fails_without_subset(self):
        snap=_current()
        sources=_source_pair()
        batch=stage_observed_calculation_scenarios(
            snap,VERIFIED_ASSETS,joint_candidates=sources)
        bad=replace(batch,scenarios=(batch.scenarios[0],))
        self.assertEqual(stage_observed_opponent_action_frontier(
            snap,VERIFIED_ASSETS,joint_candidates=sources,
            observed_batch=bad).reason,"STAGED_SCENARIOS_STALE_OR_ALTERED")
        different=with_state(snap,field="turn_probe",value=1)
        self.assertEqual(stage_observed_opponent_action_frontier(
            different,VERIFIED_ASSETS,joint_candidates=sources,
            observed_batch=batch).reason,"STAGED_SCENARIOS_STALE_OR_ALTERED")
        promoted=replace(batch,ready_for_engine=True)
        self.assertEqual(stage_observed_opponent_action_frontier(
            snap,VERIFIED_ASSETS,joint_candidates=sources,
            observed_batch=promoted).reason,"STAGED_SCENARIOS_STALE_OR_ALTERED")
        self.assertEqual(stage_observed_opponent_action_frontier(
            snap,VERIFIED_ASSETS,joint_candidates=sources,
            observed_batch=replace(batch,scenarios=(
                replace(batch.scenarios[0],p2_commands=("move 1",)),
                batch.scenarios[1]))).reason,
            "STAGED_SCENARIOS_STALE_OR_ALTERED")

    def test_bad_joint_member_rejects_whole_batch_not_only_one_case(self):
        snap=_current()
        bad=members()
        bad["カイリュー"]["item"]="こだわりハチマキ"
        out=stage_observed_opponent_action_frontier(
            snap,VERIFIED_ASSETS,
            joint_candidates=(_source_pair()[0],
                record("Bad",source_ref="synthetic://bad",party=bad)))
        self.assertEqual(out.status,"NO_ACTION_FRONTIER")
        self.assertEqual(out.cases,())
        self.assertIn("JOINT_CANDIDATES_",out.reason)
        marg=stage_observed_opponent_action_frontier(
            snap,VERIFIED_ASSETS,
            joint_candidates=(record(source="MARGINAL_STATS"),))
        self.assertEqual(marg.status,"NO_ACTION_FRONTIER")
        self.assertFalse(marg.ready_for_engine)

    def test_exact_fainted_bench_does_not_appear_as_switch_candidate(self):
        def mark(state):
            details(state)
            state["battle_evidence"]["hp_by_pokemon"]["opponent"][
                "サーフゴー"]="0/120"
        snap=with_state(frozen(),edits=mark)
        out=stage_observed_opponent_action_frontier(
            snap,VERIFIED_ASSETS,joint_candidates=(_source_pair()[0],))
        self.assertEqual(out.status,"PROVISIONAL_OPPONENT_ACTION_NAMES",
                         out.reason)
        self.assertNotIn("サーフゴー",
            [p.display_name for p in out.cases[0].possibilities
             if p.action_kind=="SWITCH_TARGET_NAME"])
        self.assertIn("ドドゲザン",
            [p.display_name for p in out.cases[0].possibilities
             if p.action_kind=="SWITCH_TARGET_NAME"])

    def test_missing_opponent_active_never_fabricates_attack_actor(self):
        snap=with_state(frozen(),field="opponent_active",value="")
        out=stage_observed_opponent_action_frontier(snap,VERIFIED_ASSETS)
        self.assertEqual(out.status,"NO_ACTION_FRONTIER")
        self.assertEqual(out.reason,"OPPONENT_ACTIVE_NOT_OBSERVED")

    def test_source_mismatch_and_wrong_frozen_assets_reject_without_adoption(self):
        snap=_current()
        batch=stage_observed_calculation_scenarios(snap,VERIFIED_ASSETS)
        wrong=stage_observed_opponent_action_frontier(
            snap,object(),observed_batch=batch)
        self.assertEqual(wrong.status,"NO_ACTION_FRONTIER")
        self.assertFalse(wrong.ready_for_engine)

    def test_repeated_calls_are_deterministic_and_never_emit_showdown_commands(self):
        snap=_current()
        sources=_source_pair()
        one=stage_observed_opponent_action_frontier(
            snap,VERIFIED_ASSETS,joint_candidates=sources)
        two=stage_observed_opponent_action_frontier(
            snap,VERIFIED_ASSETS,joint_candidates=sources)
        self.assertEqual(one,two)
        self.assertNotIn("move 1",canonical_json({
            "cases":[[(p.action_kind,p.display_name,p.legal_status)
                      for p in c.possibilities] for c in one.cases],
        }))
        self.assertIn("SHOWDOWN_REQUEST_AND_RNG_MISSING",
                      one.cases[0].unresolved)


if __name__ == "__main__":
    unittest.main()
