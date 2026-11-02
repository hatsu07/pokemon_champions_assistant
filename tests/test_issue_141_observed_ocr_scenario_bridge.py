"""Issue #141: actual observer ROI->local reducer and non-runnable scenario boundary.

Fixtures are synthetic; no claims about Android game pixels or RNG.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from types import SimpleNamespace

from champions_assist.battle_evidence import empty_battle_evidence
from champions_assist.capture import Frame
from champions_assist.capture_provenance import capture_frame_receipt
from champions_assist.fsm import MatchController
from champions_assist.observed_capture_bridge import LinkedObserverState
from champions_assist.observer_pipeline import ObserverPipeline
from champions_assist.observation_events import TextObservationParser
from champions_assist.ocr import MappingOcrEngine, OcrRead, image_digest
from champions_assist.store import BattleStore
from champions_assist.vision import DetectedUI, TemplateUiClassifier
from champions_assist.local_strategy.models import canonical_json
from champions_assist.local_strategy.observed_calculation_scenarios import (
    stage_observed_calculation_scenarios,
    stage_linked_observer_calculation_scenarios,
)
from tests.test_phase_4 import roi_config, synthetic_image, write_masters
from tests.test_issue_140_foundation import VERIFIED_ASSETS, frozen
from tests.test_issue_141_observed_id_bridge import with_state, details
from tests.test_issue_141_joint_set_intake import record, members


class ObserverToReducerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name) / "masters"
        root.mkdir()
        write_masters(root)
        self.store=BattleStore(Path(self.temp.name)/"events.sqlite3")
        self.session=MatchController(root, store=self.store).start_match(
            "real-in-process-synthetic", now_ms=0)
        self.bridge=LinkedObserverState(
            match_id=self.session.match_id,assets=self.session.assets,
            initial_state={
                "match_id":self.session.match_id,
                "phase":"BATTLE","our_active":"Own1",
                "opponent_active":"Opp1",
                "selected_three":["Own1","Own2","Own3"],
                "opponent_six":list(("Opp1","Opp2","Opp3","Opp4","Opp5","Opp6")),
                "battle_evidence":empty_battle_evidence(),
                "observed_hp":{},
                "_live_state_revision":0,
            },
            roi_fields={"ROI_UI_CONTROLS":"own_active_name",
                        "ROI_BATTLE_LOG":"own_hp_fraction"},
        )
        a=synthetic_image(DetectedUI.COMMAND_MENU,controls_code=15,log_code=0)
        b=synthetic_image(DetectedUI.COMMAND_MENU,controls_code=15,log_code=35)
        self.f1=Frame("source-1",1,100,a,source="SYNTHETIC_TEST")
        self.f2=Frame("source-2",2,200,b,source="SYNTHETIC_TEST")
        ocr=MappingOcrEngine.for_images([
            ("ROI_UI_CONTROLS",roi_config().crop("ROI_UI_CONTROLS",a),
             OcrRead("Own1",.99,"SYNTHETIC_OCR")),
            ("ROI_BATTLE_LOG",roi_config().crop("ROI_BATTLE_LOG",b),
             OcrRead("35/100",.99,"SYNTHETIC_OCR")),
        ])
        self.pipeline=ObserverPipeline(
            session=self.session,store=self.store,roi_config=roi_config(),
            ui_classifier=TemplateUiClassifier({},threshold=.99),
            ocr_engine=ocr,text_parser=TextObservationParser(()),
            ocr_roi_names=("ROI_UI_CONTROLS","ROI_BATTLE_LOG"),
            linked_local_ocr_bridge=self.bridge,
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_actual_observer_to_local_ocr_hp_with_real_pixel_and_roi_receipts(self):
        x=self.pipeline.process_frame(self.f1)
        y=self.pipeline.process_frame(self.f2)
        self.assertEqual(self.pipeline.last_linked_ocr_result.status,
                         "LINKED_CANDIDATE_STATE")
        self.assertEqual(self.bridge.state["battle_evidence"]["hp_by_pokemon"]
                         ["own"]["Own1"],"35/100")
        self.assertEqual(self.bridge.last_capture_seq,2)
        self.assertEqual(self.bridge.required_window_revision,
                         self.session.tactical_state_version)
        self.assertGreater(self.session.tactical_state_version,0)
        self.assertEqual(self.session.state_facts["linked_local_ocr_candidate"]
                         ["raw_observation_id"],y.raw_observation_id)
        self.assertEqual(len(self.store.export_match(self.session.match_id)
                             ["raw_observations"]),2)
        provenance=self.bridge.state["battle_evidence"]["update_provenance"]
        hp=next(p for p in provenance if p["kind"]=="HP")
        self.assertEqual(hp["actor"],"Own1")
        self.assertEqual(hp["field"],"hp_by_pokemon")
        link=hp["claimed_frame_link"]
        self.assertEqual(link["raw_observation_id"],y.raw_observation_id)
        self.assertEqual(link["image_rgb_sha256"],image_digest(self.f2.image))
        self.assertFalse(link["actor_identity_authenticated"])
        self.assertFalse(link["ready_for_engine"])
        self.assertNotEqual(x.raw_observation_id,y.raw_observation_id)

    def test_stable_name_reocr_allows_hp_after_two_seconds(self):
        self.pipeline.process_frame(self.f1)
        late=Frame("same-actor-hp-after-3s",2,3500,
                   self.f2.image.copy(),source="SYNTHETIC_TEST")
        self.pipeline.process_frame(late)
        result=self.pipeline.last_linked_ocr_result
        self.assertEqual(result.status,"LINKED_CANDIDATE_STATE", result.audit)
        self.assertNotIn("ACTOR_HUD_NOT_ATTESTED",
                         {row.get("reason") for row in result.audit})
        self.assertEqual(self.bridge.state["battle_evidence"]["hp_by_pokemon"]
                         ["own"]["Own1"],"35/100")
        rows=self.store.export_match(self.session.match_id)["raw_observations"]
        name=rows[1]["payload"]["rois"]["ROI_UI_CONTROLS"]
        self.assertFalse(name["changed"])
        self.assertTrue(name["actor_refresh_same_frame"])
        self.assertEqual(name["text"],"Own1")
        self.assertEqual(name["roi_rgb_sha256"],
                         image_digest(roi_config().crop("ROI_UI_CONTROLS",late.image)))

    def test_switch_frame_hud_cannot_be_attributed_to_new_actor(self):
        self.pipeline.process_frame(self.f1)
        late=Frame("late-own1",2,3500,self.f2.image.copy(),
                   source="SYNTHETIC_TEST")
        self.pipeline.process_frame(late)
        switch_image=synthetic_image(DetectedUI.COMMAND_MENU,
                                     controls_code=70,log_code=48)
        stable_image=synthetic_image(DetectedUI.COMMAND_MENU,
                                     controls_code=70,log_code=66)
        self.pipeline.ocr_engine=MappingOcrEngine.for_images([
            ("ROI_UI_CONTROLS",roi_config().crop("ROI_UI_CONTROLS",switch_image),
             OcrRead("Own2",.99,"SYNTHETIC_OCR")),
            ("ROI_BATTLE_LOG",roi_config().crop("ROI_BATTLE_LOG",switch_image),
             OcrRead("84/100",.99,"SYNTHETIC_OCR")),
            ("ROI_BATTLE_LOG",roi_config().crop("ROI_BATTLE_LOG",stable_image),
             OcrRead("20/100",.99,"SYNTHETIC_OCR")),
        ])
        switched=Frame("switch-transition",3,3600,switch_image,
                       source="SYNTHETIC_TEST")
        self.pipeline.process_frame(switched)
        self.assertEqual(self.bridge.state["our_active"],"Own2")
        self.assertNotIn("Own2",self.bridge.state["battle_evidence"]
                         ["hp_by_pokemon"]["own"])
        self.assertIn("SAME_FRAME_STABLE_ACTOR_NAME_REQUIRED",
                      {a.get("reason") for a in
                       self.pipeline.last_linked_ocr_result.audit})
        after=Frame("stable-new-actor",4,3700,stable_image,
                    source="SYNTHETIC_TEST")
        self.pipeline.process_frame(after)
        self.assertEqual(self.bridge.state["battle_evidence"]["hp_by_pokemon"]
                         ["own"]["Own1"],"35/100")
        self.assertEqual(self.bridge.state["battle_evidence"]["hp_by_pokemon"]
                         ["own"]["Own2"],"20/100")
        self.assertEqual(self.pipeline.last_linked_ocr_result.status,
                         "LINKED_CANDIDATE_STATE")

    def test_same_name_pixels_cannot_promote_conflicting_actor_ocr(self):
        self.pipeline.process_frame(self.f1)
        self.pipeline.ocr_engine=MappingOcrEngine.for_images([
            ("ROI_UI_CONTROLS",roi_config().crop("ROI_UI_CONTROLS",self.f2.image),
             OcrRead("Own2",.99,"SYNTHETIC_OCR")),
            ("ROI_BATTLE_LOG",roi_config().crop("ROI_BATTLE_LOG",self.f2.image),
             OcrRead("35/100",.99,"SYNTHETIC_OCR")),
        ])
        late=Frame("ocr-drift-same-name-pixels",2,3500,
                   self.f2.image.copy(),source="SYNTHETIC_TEST")
        self.pipeline.process_frame(late)
        self.assertEqual(self.bridge.state["our_active"],"Own1")
        self.assertNotIn("Own1",self.bridge.state["battle_evidence"]
                         ["hp_by_pokemon"]["own"])
        self.assertNotIn("Own2",self.bridge.state["battle_evidence"]
                         ["hp_by_pokemon"]["own"])
        reasons={a.get("reason") for a in
                 self.pipeline.last_linked_ocr_result.audit}
        self.assertIn("UNSTABLE_ACTOR_OCR_ON_UNCHANGED_PIXELS",reasons)
        self.assertIn("SAME_FRAME_STABLE_ACTOR_NAME_REQUIRED",reasons)

    def test_pp_after_two_seconds_uses_same_frame_name(self):
        bridge=LinkedObserverState(
            match_id=self.session.match_id,assets=self.session.assets,
            initial_state=self.bridge.state,
            roi_fields={"ROI_UI_CONTROLS":"own_active_name",
                        "ROI_PARTY_CARDS":"own_pp_move1"},
        )
        first=synthetic_image(DetectedUI.COMMAND_MENU,controls_code=15,
                               party_code=0)
        changed=synthetic_image(DetectedUI.COMMAND_MENU,controls_code=15,
                                 party_code=35)
        engine=MappingOcrEngine.for_images([
            ("ROI_UI_CONTROLS",roi_config().crop("ROI_UI_CONTROLS",first),
             OcrRead("Own1",.99,"SYNTHETIC_OCR")),
            ("ROI_PARTY_CARDS",roi_config().crop("ROI_PARTY_CARDS",changed),
             OcrRead("6/10",.99,"SYNTHETIC_OCR")),
        ])
        pipeline=ObserverPipeline(
            session=self.session,store=self.store,roi_config=roi_config(),
            ui_classifier=TemplateUiClassifier({},threshold=.99),
            ocr_engine=engine,text_parser=TextObservationParser(()),
            ocr_roi_names=("ROI_UI_CONTROLS","ROI_PARTY_CARDS"),
            linked_local_ocr_bridge=bridge,
        )
        pipeline.process_frame(Frame("name-pp",1,100,first,
                                    source="SYNTHETIC_TEST"))
        pipeline.process_frame(Frame("late-pp",2,3500,changed,
                                    source="SYNTHETIC_TEST"))
        self.assertEqual(pipeline.last_linked_ocr_result.status,
                         "LINKED_CANDIDATE_STATE",
                         pipeline.last_linked_ocr_result.audit)
        self.assertEqual(bridge.state["battle_evidence"]["pp_by_pokemon"]
                         ["own"]["Own1"]["Own1-m1"],6)
        self.assertTrue(self.store.export_match(self.session.match_id)
                        ["raw_observations"][1]["payload"]["rois"]
                        ["ROI_UI_CONTROLS"]["actor_refresh_same_frame"])

    def test_stale_or_pixel_modified_frame_does_not_change_detached_state(self):
        self.pipeline.process_frame(self.f1)
        self.pipeline.process_frame(self.f2)
        previous=canonical_json(self.bridge.state)
        duplicate=self.bridge.ingest(
            self.f1,
            capture_frame_receipt(
                self.f1,self.session,
                f"{self.session.match_id}:frame:1:source-1"),
            {},roi_config())
        self.assertEqual(duplicate.status,"NO_LINKED_UPDATE")
        self.assertEqual(canonical_json(self.bridge.state),previous)
        altered=self.f2.image.copy()
        altered[0,0,0]^=1
        forged=Frame(self.f2.frame_id,3,200,altered,source="SYNTHETIC_TEST")
        receipt=capture_frame_receipt(
            self.f2,self.session,
            f"{self.session.match_id}:frame:2:source-2")
        mismatch=self.bridge.ingest(forged,receipt,{},roi_config())
        self.assertEqual(mismatch.reason,"STALE_OR_UNMATCHED_FRAME")
        self.assertEqual(canonical_json(self.bridge.state),previous)

    def test_roi_digest_mismatch_refused_before_any_partial_mutation(self):
        image=self.f1
        receipt=capture_frame_receipt(
            image,self.session,
            f"{self.session.match_id}:frame:1:source-1")
        bad={"ROI_UI_CONTROLS":{"text":"Own2","confidence":.99,
               "roi_rgb_sha256":"sha256:"+"0"*64}}
        result=self.bridge.ingest(image,receipt,bad,roi_config())
        self.assertEqual(result.reason,"ROI_PIXELS_NOT_BOUND_TO_FRAME")
        self.assertEqual(self.bridge.last_capture_seq,-1)
        self.assertEqual(self.bridge.state["_live_state_revision"],0)

    def test_switch_cannot_reuse_prior_frame_and_foreign_match_is_rejected(self):
        f=self.f1
        foreign=replace(capture_frame_receipt(
            f,self.session,
            f"{self.session.match_id}:frame:1:source-1"),match_id="other-match")
        self.assertEqual(self.bridge.ingest(
            f,foreign,{},roi_config()).status,"NO_LINKED_UPDATE")
        with self.assertRaisesRegex(ValueError,"match and frozen assets"):
            ObserverPipeline(
                session=self.session,store=self.store,roi_config=roi_config(),
                ui_classifier=TemplateUiClassifier({}),
                ocr_engine=MappingOcrEngine({}),
                text_parser=TextObservationParser(()),
                linked_local_ocr_bridge=LinkedObserverState(
                    match_id="different",assets=self.session.assets,
                    initial_state={"match_id":"different"},
                    roi_fields={"ROI_BATTLE_LOG":"battle_message"}))

    def test_unconfigured_pipeline_does_not_update_linked_state(self):
        plain=ObserverPipeline(
            session=self.session,store=self.store,roi_config=roi_config(),
            ui_classifier=TemplateUiClassifier({}),
            ocr_engine=MappingOcrEngine({}),
            text_parser=TextObservationParser(()),
            ocr_roi_names=("ROI_BATTLE_LOG",))
        plain.process_frame(self.f1)
        self.assertIsNone(plain.last_linked_ocr_result)
        self.assertEqual(self.bridge.last_capture_seq,-1)


class CalculationScenarioTests(unittest.TestCase):
    def test_observer_to_scenario_requires_exact_staged_current_snapshot(self):
        base=frozen()
        state=base.state
        state["match_id"]=base.window.match_id
        state["observed_hp"]={}
        bridge=LinkedObserverState(
            match_id=base.window.match_id,assets=VERIFIED_ASSETS,
            initial_state=state,
            roi_fields={"ROI_BATTLE_LOG":"battle_message"},
        )
        img=synthetic_image(DetectedUI.ACTION_RESOLUTION,log_code=21)
        frame=Frame("observed-frame-1",1,100,img,source="SYNTHETIC_TEST")
        fake_session=SimpleNamespace(
            match_id=base.window.match_id,current_window=None,
            game_turn_seq="4",observation_seq=0,tactical_state_version=2)
        receipt=capture_frame_receipt(
            frame,fake_session,
            f"{base.window.match_id}:frame:1:{frame.frame_id}")
        observed=bridge.ingest(
            frame,receipt,{
                "ROI_BATTLE_LOG":{
                    "text":"ガブリアスはじしんをつかった!",
                    "confidence":.99,
                    "roi_rgb_sha256":image_digest(
                        roi_config().crop("ROI_BATTLE_LOG",img)),
                },
            },roi_config())
        self.assertEqual(observed.status,"LINKED_CANDIDATE_STATE",
                         observed.audit)
        linked=replace(
            base,observed_state_json=canonical_json(bridge.state))
        not_acknowledged=stage_linked_observer_calculation_scenarios(
            bridge,linked,VERIFIED_ASSETS)
        self.assertEqual(not_acknowledged.reason,
                         "LINKED_WINDOW_REVISION_NOT_ACKNOWLEDGED")
        bridge.record_authoritative_revision(linked.window.state_revision)
        staged=stage_linked_observer_calculation_scenarios(
            bridge,linked,VERIFIED_ASSETS)
        self.assertEqual(staged.status,"PARTIAL_SCENARIOS_NOT_RUNNABLE",
                         staged.reason)
        self.assertEqual(staged.snapshot_sha256,linked.snapshot_sha256)
        self.assertEqual(json.loads(staged.scenarios[0].observed_constraints_json)
                         ["own"][0]["observed_moves_display"],["じしん"])
        stale=stage_linked_observer_calculation_scenarios(
            bridge,base,VERIFIED_ASSETS)
        self.assertEqual(stale.reason,"LINKED_OBSERVATION_CHANGED_OR_STALE")
        wrong=stage_linked_observer_calculation_scenarios(
            bridge,linked,object())
        self.assertEqual(wrong.status,"NO_SCENARIOS")
        different=replace(
            linked,window=replace(linked.window,match_id="other-match"))
        self.assertEqual(stage_linked_observer_calculation_scenarios(
            bridge,different,VERIFIED_ASSETS).status,"NO_SCENARIOS")

    def test_visible_own_ids_are_staged_but_never_fabricate_battle_rng(self):
        snapshot=with_state(frozen(),edits=details)
        original=snapshot.observed_state_json
        out=stage_observed_calculation_scenarios(snapshot,VERIFIED_ASSETS)
        self.assertEqual(out.status,"PARTIAL_SCENARIOS_NOT_RUNNABLE")
        self.assertEqual(out.snapshot_sha256,snapshot.snapshot_sha256)
        self.assertEqual(len(out.scenarios),1)
        s=out.scenarios[0]
        self.assertIsNone(s.hypothesis_id)
        self.assertIsNone(s.battle_json)
        self.assertIsNone(s.rng_state)
        self.assertFalse(s.runnable)
        self.assertFalse(out.ready_for_engine)
        self.assertFalse(out.full_state_reconstructed)
        parsed=json.loads(s.observed_constraints_json)
        self.assertEqual(parsed["own"][0]["registered_species_id"],"garchomp")
        self.assertEqual(parsed["own"][0]["hp"],63)
        self.assertEqual(parsed["own"][0]["maxhp"],184)
        self.assertEqual(parsed["opponent_active"]["name"],"カイリュー")
        self.assertIsNone(parsed["opponent_active"]["registered_species_id"])
        self.assertEqual(parsed["opponent_active"]["observed_moves_display"],
                         ["しんそく"])
        self.assertIn("SIMULATOR_RNG_NOT_OBSERVED",s.unknowns)
        self.assertEqual(snapshot.observed_state_json,original)

    def test_sourced_joint_scenarios_preserve_individual_hypotheses(self):
        snapshot=with_state(frozen(),edits=details)
        party=members()
        party["カイリュー"].update({
            "current_type":"ひこう", "status":"やけど",
        })
        a=record(party=party)
        b=record("Joint-2",source_ref="synthetic://other",party=party)
        out=stage_observed_calculation_scenarios(
            snapshot,VERIFIED_ASSETS,joint_candidates=(a,b))
        self.assertEqual(out.status,"PARTIAL_SCENARIOS_NOT_RUNNABLE",out.reason)
        self.assertEqual([s.hypothesis_id for s in out.scenarios],
                         ["Joint-1","Joint-2"])
        self.assertEqual(out.scenarios[0].observed_constraints_json,
                         out.scenarios[1].observed_constraints_json)
        for c in out.scenarios:
            self.assertIn("SOURCE_KIND:CHAMPIONS_COOCCURRING_JOINT_RECORD",
                          c.opponent_source_evidence)
            self.assertFalse(c.runnable)
            self.assertIsNone(c.battle_json)

    def test_ambiguous_hp_and_bad_joint_records_fail_closed(self):
        def percent(state):
            details(state)
            state["battle_evidence"]["hp_by_pokemon"]["opponent"]["カイリュー"]="73%"
        s=with_state(frozen(),edits=percent)
        self.assertEqual(stage_observed_calculation_scenarios(
            s,VERIFIED_ASSETS).status,"NO_SCENARIOS")
        good=with_state(frozen(),edits=details)
        bad=record(source="MARGINAL_STATS")
        out=stage_observed_calculation_scenarios(
            good,VERIFIED_ASSETS,joint_candidates=(record(),bad))
        self.assertEqual(out.status,"NO_SCENARIOS")
        self.assertFalse(out.scenarios)

    def test_unfrozen_ids_or_unattested_hypothesis_cannot_become_scenario(self):
        snap=frozen()
        out=stage_observed_calculation_scenarios(snap,object())
        self.assertEqual(out.status,"NO_SCENARIOS")
        from champions_assist.local_strategy.models import Hypothesis
        altered=replace(snap,hypotheses=(
            Hypothesis.from_assumptions("guessed",{"moves":["unknown"]}),))
        out=stage_observed_calculation_scenarios(altered,VERIFIED_ASSETS)
        self.assertEqual(out.reason,"UNATTESTED_EXISTING_HYPOTHESES")


if __name__ == "__main__":
    unittest.main()
