"""#141(②) conservative observed constraints/abstention contract tests.

Uses production OCR reducer and linked observer bridge where applicable.
No gameplay authority, hidden RNG or runnable Battle is synthesized.
"""
from __future__ import annotations

from dataclasses import replace
import json
import unittest
from types import SimpleNamespace

from champions_assist.capture import Frame
from champions_assist.capture_provenance import capture_frame_receipt
from champions_assist.observed_capture_bridge import LinkedObserverState
from champions_assist.ocr import image_digest
from champions_assist.vision import DetectedUI
from champions_assist.live_state_updates import apply_local_ocr
from champions_assist.local_strategy.models import canonical_json
from champions_assist.local_strategy.observed_calculation_scenarios import (
    stage_observed_calculation_scenarios,
)
from champions_assist.local_strategy.observed_safe_branch_gate import (
    stage_safe_observed_branches,
)
from tests.test_issue_140_foundation import VERIFIED_ASSETS, frozen
from tests.test_issue_141_observed_id_bridge import details, with_state
from tests.test_issue_141_observed_opponent_actions import _source_pair
from tests.test_issue_141_joint_set_intake import record, members
from tests.test_live_state_updates import read
from tests.test_phase_4 import roi_config, synthetic_image


class ObservedSafeBranchGateTests(unittest.TestCase):
    def current(self):
        return with_state(frozen(),edits=details)

    def gate(self,s=None,*,sources=None,**kwargs):
        return stage_safe_observed_branches(
            s if s is not None else self.current(),VERIFIED_ASSETS,
            joint_candidates=_source_pair() if sources is None else sources,
            **kwargs)

    def test_all_source_joint_cases_are_retained_but_ranking_is_forbidden(self):
        s=self.current()
        before=s.observed_state_json
        out=self.gate(s)
        self.assertEqual(out.status,"PROVISIONAL_OBSERVED_BRANCH_CONSTRAINTS",out.reason)
        self.assertEqual(out.ranking_status,"NO_RANKING")
        self.assertEqual(out.snapshot_sha256,s.snapshot_sha256)
        self.assertEqual(out.decision_window_id,s.window.decision_window_id)
        self.assertEqual([x.hypothesis_id for x in out.branches],
                         ["Joint-1","Joint-2"])
        for case in out.branches:
            self.assertIn("p1:1:ガブリアス.hp",case.known_exact_fields)
            self.assertIn("p1:1:ガブリアス.registered_move_ids",
                          case.known_exact_fields)
            self.assertIn("p2:active:カイリュー.hp",case.known_exact_fields)
            self.assertIn("p2:active:カイリュー.current_pp_lock_disable",
                          case.unresolved)
            self.assertIn("SIMULATOR_RNG_NOT_OBSERVED",case.unresolved)
            self.assertIn("OPPONENT_POLICY_NOT_PROVEN_EXHAUSTIVE",case.unresolved)
            self.assertIsNone(case.battle_json)
            self.assertIsNone(case.rng_state)
            self.assertEqual(case.p2_commands,())
            self.assertFalse(case.executable)
            self.assertEqual(case.ranking_status,"NO_RANKING")
        self.assertFalse(out.ready_for_engine)
        self.assertFalse(out.full_state_reconstructed)
        self.assertFalse(out.current_game_capture_authenticated)
        self.assertEqual(s.observed_state_json,before)

    def test_no_joint_set_keeps_single_explicit_unknown_case(self):
        out=self.gate(sources=())
        self.assertEqual(out.status,"PROVISIONAL_OBSERVED_BRANCH_CONSTRAINTS",out.reason)
        self.assertEqual(len(out.branches),1)
        case=out.branches[0]
        self.assertIsNone(case.hypothesis_id)
        self.assertIsNone(case.opponent_joint_assumptions_json)
        self.assertEqual(case.opponent_source_evidence,())
        self.assertIn("opponent_joint_three_and_all_hidden_sets",case.unresolved)
        self.assertEqual(out.ranking_status,"NO_RANKING")

    def test_no_observed_hp_or_stages_is_never_made_zero_or_full(self):
        out=self.gate(frozen(),sources=())
        self.assertEqual(out.status,"PROVISIONAL_OBSERVED_BRANCH_CONSTRAINTS",out.reason)
        case=out.branches[0]
        self.assertIn("p1:1:ガブリアス.hp",case.unresolved)
        self.assertIn("p1:1:ガブリアス.maxhp",case.unresolved)
        self.assertIn("p1:1:ガブリアス.all_boost_stages",case.unresolved)
        self.assertIn("p2:active.name_and_entire_state",case.unresolved)
        self.assertNotIn("p1:1:ガブリアス.hp",case.known_exact_fields)
        self.assertEqual(case.ranking_status,"NO_RANKING")

    def test_only_observed_boost_stages_are_known(self):
        case=self.gate().branches[0]
        self.assertIn("p1:1:ガブリアス.boost.atk",case.known_exact_fields)
        self.assertIn("p1:1:ガブリアス.boost.spe",case.known_exact_fields)
        self.assertIn("p1:1:ガブリアス.unobserved_boost_stages",case.unresolved)
        self.assertNotIn("p1:1:ガブリアス.boost.def",case.known_exact_fields)
        self.assertIn("p2:active:カイリュー.raw_status_literal",
                      case.known_exact_fields)
        self.assertIn("p2:active:カイリュー.raw_status_engine_mapping",
                      case.unresolved)

    def test_stale_truncated_or_promoted_pre_staged_batch_refused_all(self):
        s=self.current()
        sources=_source_pair()
        staged=stage_observed_calculation_scenarios(
            s,VERIFIED_ASSETS,joint_candidates=sources)
        self.assertEqual(self.gate(s,observed_batch=staged).status,
                         "PROVISIONAL_OBSERVED_BRANCH_CONSTRAINTS")
        for altered in (
            replace(staged,scenarios=staged.scenarios[:1]),
            replace(staged,ready_for_engine=True),
            replace(staged,scenarios=(replace(
                staged.scenarios[0],battle_json={"fake":"battle"}),
                staged.scenarios[1])),
        ):
            with self.subTest(altered=altered):
                out=self.gate(s,observed_batch=altered)
                self.assertEqual(out.reason,"STALE_OR_MODIFIED_OBSERVED_BATCH")
                self.assertEqual(out.branches,())
        different=with_state(s,field="changed_after_capture",value=1)
        self.assertEqual(self.gate(different,observed_batch=staged).reason,
                         "STALE_OR_MODIFIED_OBSERVED_BATCH")

    def test_source_contradiction_or_budget_overrun_refuses_whole_batch(self):
        s=self.current()
        self.assertEqual(self.gate(s,max_branches=1).reason,
                         "INVALID_OR_OVER_BUDGET_BRANCH_REQUEST")
        self.assertEqual(self.gate(s,max_branches=True).reason,
                         "INVALID_OR_OVER_BUDGET_BRANCH_REQUEST")
        self.assertEqual(self.gate(s,sources=(record(source="MARGINAL_STATS"),)).branches,())
        bad=members()
        bad["カイリュー"]["item"]="こだわりハチマキ"
        mixed=(_source_pair()[0],record("Other",party=bad,
                                        source_ref="synthetic://other"))
        self.assertEqual(self.gate(s,sources=mixed).status,"NO_OBSERVED_BRANCHES")

    def test_production_reducer_percent_and_faint_history_remain_unresolved(self):
        s=self.current()
        state=s.state
        state.setdefault("observed_hp",{})
        state["battle_evidence"]["hp_by_pokemon"]["opponent"].clear()
        state["battle_evidence"].setdefault(
            "fainted",{"own":{},"opponent":{}})
        def apply(*rows):
            nonlocal state
            state,audit=apply_local_ocr(state,rows,VERIFIED_ASSETS)
            self.assertTrue(any(x.get("status")=="APPLIED" for x in audit),audit)
        apply(read("opponent_active_name","カイリュー",1),
              read("opponent_hp_percent","73%",1,{"percent_displayed":73}))
        current=replace(s,observed_state_json=canonical_json(state))
        case=self.gate(current).branches[0]
        obj=json.loads(case.observed_constraints_json)["opponent_active"]
        self.assertEqual(obj["raw_hp_percent"],"73%")
        self.assertIsNone(obj["hp"])
        self.assertIsNone(obj["maxhp"])
        self.assertIn("p2:active:カイリュー.raw_percent_not_exact_hp",
                      case.unresolved)
        apply(read("battle_message","相手のカイリューはたおれた!",2))
        apply(read("opponent_active_name","カイリュー",3),
              read("opponent_hp_percent","0%",3,{"percent_displayed":0}))
        current=replace(s,observed_state_json=canonical_json(state))
        case=self.gate(current).branches[0]
        obj=json.loads(case.observed_constraints_json)["opponent_active"]
        self.assertEqual(obj["raw_hp_percent"],"0%")
        self.assertIsNone(obj["hp"])
        self.assertTrue(obj["fainted"])
        self.assertIn("p2:active:カイリュー.hp",case.unresolved)
        self.assertIn("p2:active:カイリュー.fainted",case.known_exact_fields)
        self.assertFalse(case.executable)

    def test_linked_observer_needs_acknowledged_revision_and_exact_state(self):
        base=frozen()
        st=base.state
        st["match_id"]=base.window.match_id
        st["observed_hp"]={}
        bridge=LinkedObserverState(
            match_id=base.window.match_id,assets=VERIFIED_ASSETS,
            initial_state=st,roi_fields={"ROI_BATTLE_LOG":"battle_message"})
        img=synthetic_image(DetectedUI.ACTION_RESOLUTION,log_code=21)
        frame=Frame("branch-frame-1",1,100,img,source="SYNTHETIC_TEST")
        session=SimpleNamespace(match_id=base.window.match_id,
            current_window=None,game_turn_seq="4",observation_seq=0,
            tactical_state_version=2)
        receipt=capture_frame_receipt(
            frame,session,f"{base.window.match_id}:frame:1:{frame.frame_id}")
        outcome=bridge.ingest(frame,receipt,{"ROI_BATTLE_LOG":{
            "text":"ガブリアスはじしんをつかった!","confidence":.99,
            "roi_rgb_sha256":image_digest(
                roi_config().crop("ROI_BATTLE_LOG",img))}},roi_config())
        self.assertEqual(outcome.status,"LINKED_CANDIDATE_STATE",outcome.audit)
        linked=replace(base,observed_state_json=canonical_json(bridge.state))
        self.assertEqual(self.gate(linked,sources=(),
            linked_observer=bridge).status,"NO_OBSERVED_BRANCHES")
        bridge.record_authoritative_revision(linked.window.state_revision)
        accepted=self.gate(linked,sources=(),linked_observer=bridge)
        self.assertEqual(accepted.status,"PROVISIONAL_OBSERVED_BRANCH_CONSTRAINTS",
                         accepted.reason)
        self.assertEqual(len(accepted.branches),1)
        self.assertIn("p1:1:ガブリアス.observed_move_names",
                      accepted.branches[0].known_exact_fields)
        self.assertFalse(accepted.current_game_capture_authenticated)
        self.assertEqual(self.gate(base,sources=(),
                         linked_observer=bridge).status,"NO_OBSERVED_BRANCHES")


if __name__=="__main__":
    unittest.main()
