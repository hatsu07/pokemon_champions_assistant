"""Local observer frame/ROI provenance is append-only; no real OCR proof."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

import numpy as np

from champions_assist.capture import Frame
from champions_assist.capture_provenance import (
    capture_frame_receipt, matches_original_frame,
)
from champions_assist.fsm import MatchController
from champions_assist.live_state_updates import apply_local_ocr
from tests.test_live_state_updates import assets as local_assets, read as local_read, state as local_state
from champions_assist.observation_events import TextObservationParser
from champions_assist.observer_pipeline import ObserverPipeline
from champions_assist.ocr import MappingOcrEngine, image_digest
from champions_assist.store import BattleStore
from champions_assist.vision import DetectedUI, TemplateUiClassifier
from tests.test_phase_4 import roi_config, synthetic_image, write_masters


class FrameReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "masters"
        root.mkdir()
        write_masters(root)
        self.store = BattleStore(Path(self.tmp.name) / "local-ledger.sqlite3")
        self.session = MatchController(root, store=self.store).start_match(
            "receipt-test-match", now_ms=0)
        picture = synthetic_image(DetectedUI.COMMAND_MENU)
        self.frame = Frame(
            frame_id="source-frame-001",capture_seq=1,captured_at_ms=100,
            image=picture,source="SYNTHETIC_TEST",reference=None,
        )
        self.pipeline = ObserverPipeline(
            session=self.session,store=self.store,roi_config=roi_config(),
            ui_classifier=TemplateUiClassifier({},threshold=0.99),
            ocr_engine=MappingOcrEngine({}),
            text_parser=TextObservationParser(()),
            ocr_roi_names=("ROI_BATTLE_LOG","ROI_UI_CONTROLS"),
            classification_roi_name=None,
            stable_frames=2,
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _raw(self):
        return self.store.export_match(self.session.match_id)["raw_observations"]

    def test_frame_hash_is_written_at_ingest_with_unverified_clock_and_source(self):
        version=self.session.tactical_state_version
        obs_seq=self.session.observation_seq
        result=self.pipeline.process_frame(self.frame)
        records=self._raw()
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]["observation_id"],result.raw_observation_id)
        body=records[0]["payload"]
        receipt=body["capture_receipt"]
        self.assertEqual(receipt["frame_id"],self.frame.frame_id)
        self.assertEqual(receipt["match_id"],self.session.match_id)
        self.assertEqual(receipt["raw_observation_id"],result.raw_observation_id)
        self.assertEqual(receipt["capture_seq"],1)
        self.assertEqual(receipt["captured_at_ms"],100)
        self.assertEqual(receipt["image_rgb_sha256"],image_digest(self.frame.image))
        self.assertEqual(receipt["image_width"],self.frame.image.shape[1])
        self.assertEqual(receipt["image_height"],self.frame.image.shape[0])
        self.assertEqual(receipt["source_claim"],"SYNTHETIC_TEST")
        self.assertEqual(receipt["tactical_state_version_before_ingest"],version)
        self.assertEqual(receipt["observation_seq_before_ingest"],obs_seq)
        self.assertIsNone(receipt["decision_window_id_before_ingest"])
        for field in ("capture_origin_authenticated","capture_timestamp_authenticated",
                      "actor_identity_authenticated","game_observation_verified",
                      "ready_for_engine"):
            self.assertIs(receipt[field],False,field)
        expected_roi=roi_config().crop("ROI_BATTLE_LOG",self.frame.image)
        self.assertEqual(body["rois"]["ROI_BATTLE_LOG"]["roi_rgb_sha256"],
                         image_digest(expected_roi))
        self.assertEqual(body["rois"]["ROI_UI_CONTROLS"]["roi_rgb_sha256"],
                         image_digest(roi_config().crop("ROI_UI_CONTROLS",self.frame.image)))
        self.assertNotIn("image",body)
        self.assertNotIn("pixels",body)
        self.assertNotIn("rgb",body)

    def test_image_change_alters_digest_even_with_same_claimed_frame_identity(self):
        receipt=capture_frame_receipt(
            self.frame,self.session,"receipt-test-match:frame:1:source-frame-001")
        same_pixels=Frame(
            self.frame.frame_id,1,100,self.frame.image.copy(),"SYNTHETIC_TEST")
        self.assertTrue(matches_original_frame(receipt,same_pixels,self.session.match_id))
        changed=self.frame.image.copy()
        changed[0,0,0] ^= 1
        changed_frame=Frame(self.frame.frame_id,1,100,changed,"SYNTHETIC_TEST")
        self.assertFalse(matches_original_frame(receipt,changed_frame,self.session.match_id))
        self.assertFalse(matches_original_frame(receipt,same_pixels,"different-match"))
        wrong_time=Frame(self.frame.frame_id,1,99,self.frame.image.copy(),"SYNTHETIC_TEST")
        self.assertFalse(matches_original_frame(receipt,wrong_time,self.session.match_id))

    def test_reanalysis_retains_original_receipt_not_current_window(self):
        self.pipeline.process_frame(self.frame)
        original=self._raw()[0]["payload"]["capture_receipt"]
        self.session.update_observed_state("new-revision",{"synthetic_change":True})
        self.assertGreater(self.session.tactical_state_version,
                           original["tactical_state_version_before_ingest"])
        events=self.pipeline.reanalyze_buffered_frame(self.frame.frame_id)
        self.assertEqual(events,())
        records=self._raw()
        self.assertEqual(len(records),2)
        later=records[1]["payload"]
        self.assertEqual(later["kind"],"OCR_REANALYSIS")
        self.assertEqual(later["original_capture_receipt"],original)
        self.assertEqual(later["original_raw_observation_id"],
                         records[0]["observation_id"])
        self.assertTrue(later["historical_frame_not_authenticated_as_current_window"])
        self.assertEqual(later["reanalyzed_after_state_version"],
                         self.session.tactical_state_version)

    def test_mutated_pixel_buffer_blocks_later_reanalysis(self):
        self.pipeline.process_frame(self.frame)
        before=len(self._raw())
        # Frame is frozen but ndarray is intentionally mutable, so detect
        # an accidental buffer overwrite before the image is reinterpreted.
        self.frame.image[4,4,1] ^= 1
        with self.assertRaisesRegex(ValueError,"changed since"):
            self.pipeline.reanalyze_buffered_frame(self.frame.frame_id)
        self.assertEqual(len(self._raw()),before)

    def test_duplicate_raw_frame_identity_cannot_replace_original_record(self):
        self.pipeline.process_frame(self.frame)
        original=self._raw()[0]
        with self.assertRaisesRegex(ValueError,"duplicate raw frame"):
            self.pipeline.process_frame(self.frame)
        self.assertEqual(self._raw(),[original])
        changed=self.frame.image.copy()
        changed[0,0,2] ^= 1
        reused=Frame(self.frame.frame_id,1,100,changed,source="SYNTHETIC_TEST")
        with self.assertRaisesRegex(ValueError,"duplicate raw frame"):
            self.pipeline.process_frame(reused)
        self.assertEqual(self._raw(),[original])

    def _claimed_receipt(self, frame_id, seq):
        copied=Frame(frame_id,seq,seq*100,
                     self.frame.image.copy(),source="SYNTHETIC_TEST")
        return capture_frame_receipt(
            copied,self.session,f"{self.session.match_id}:frame:{seq}:{frame_id}")

    def test_accepted_local_ocr_fields_link_only_explicit_claimed_frames(self):
        receipts={
            f"frame-{seq}":self._claimed_receipt(f"frame-{seq}",seq)
            for seq in (1,2,3)
        }
        initial=local_state()
        initial["match_id"]=self.session.match_id
        result,audit=apply_local_ocr(initial,(
            local_read("opponent_active_name","ガブリアス",1),
            local_read("opponent_hp_percent","73%",1,
                       {"percent_displayed":73}),
            local_read("battle_message","相手のガブリアスはじしんをつかった!",2),
            local_read("battle_message","マスカーニャはまひになった",3),
        ),local_assets(),capture_receipts=receipts)
        self.assertEqual(result["battle_evidence"]["hp_by_pokemon"]["opponent"]["ガブリアス"],"73%")
        self.assertEqual(result["battle_evidence"]["moves_by_pokemon"]["opponent"]["ガブリアス"],["じしん"])
        self.assertEqual(result["battle_evidence"]["status_by_pokemon"]["own"]["マスカーニャ"],"まひ")
        applied=[row for row in audit if row.get("status")=="APPLIED"]
        self.assertEqual(len(applied),4,audit)
        provenance=result["battle_evidence"]["update_provenance"]
        for kind,side,actor,field,fid in (
            ("HP","opponent","ガブリアス","hp_by_pokemon","frame-1"),
            ("MOVE","opponent","ガブリアス","moves_by_pokemon","frame-2"),
            ("STATUS","own","マスカーニャ","status_by_pokemon","frame-3"),
        ):
            row=next(p for p in provenance if p["kind"]==kind)
            self.assertEqual((row["side"],row["actor"],row["field"],row["frame_id"]),
                             (side,actor,field,fid))
            self.assertGreaterEqual(row["after_revision"],row["before_revision"])
            link=row["claimed_frame_link"]
            self.assertEqual(link["image_rgb_sha256"],receipts[fid].image_rgb_sha256)
            self.assertEqual(link["raw_observation_id"],receipts[fid].raw_observation_id)
            self.assertEqual((link["actor"],link["field"]),(actor,field))
            for k in ("capture_origin_authenticated","actor_identity_authenticated",
                      "capture_timestamp_authenticated","roi_digest_verified",
                      "current_window_binding_verified","ready_for_engine"):
                self.assertIs(link[k],False)

    def test_missing_mismatched_frame_receipt_never_applies_ocr_update(self):
        from dataclasses import replace
        event=local_read("battle_message","相手のガブリアスはじしんをつかった!",1)
        origin=self._claimed_receipt("frame-1",1)
        for claims in (
            {},
            {"frame-1":replace(origin,match_id="previous-match")},
            {"frame-1":replace(origin,frame_id="different")},
            {"frame-1":replace(origin,image_rgb_sha256="sha256:"+"0"*63)},
            {"frame-1":replace(origin,raw_observation_id="wrong-window")},
        ):
            with self.subTest(claims=repr(claims)):
                initial=local_state()
                initial["match_id"]=self.session.match_id
                output,audit=apply_local_ocr(initial,(event,),local_assets(),
                                             capture_receipts=claims)
                self.assertNotIn("ガブリアス",output["battle_evidence"]["moves_by_pokemon"]["opponent"])
                self.assertEqual(audit[0]["reason"],"FRAME_RECEIPT_UNAVAILABLE_OR_MISMATCH")
                self.assertEqual(output["_live_state_revision"],0)
        initial=local_state()  # missing match identity cannot bind receipt
        output,audit=apply_local_ocr(initial,(event,),local_assets(),
                                     capture_receipts={"frame-1":origin})
        self.assertEqual(audit[0]["reason"],"FRAME_RECEIPT_UNAVAILABLE_OR_MISMATCH")
        self.assertEqual(output["_live_state_revision"],0)

    def test_unlinked_legacy_local_ocr_remains_unverified_and_supported(self):
        initial=local_state()
        output,audit=apply_local_ocr(initial,(
            local_read("battle_message","相手のガブリアスはじしんをつかった!",1),
        ),local_assets())
        self.assertEqual(audit[0]["status"],"APPLIED")
        origin=output["battle_evidence"]["update_provenance"][0]
        self.assertNotIn("claimed_frame_link",origin)
        self.assertEqual((origin["actor"],origin["field"]),
                         ("ガブリアス","moves_by_pokemon"))

    def test_frame_digest_is_not_a_claim_of_observation_authenticity(self):
        # A made-up source label cannot produce a trusted game observation.
        forge=Frame("faked",2,999,self.frame.image.copy(),source="REAL_GAME_CAPTURE")
        receipt=capture_frame_receipt(
            forge,self.session,"receipt-test-match:frame:2:faked")
        self.assertEqual(receipt.source_claim,"REAL_GAME_CAPTURE")
        self.assertFalse(receipt.capture_origin_authenticated)
        self.assertFalse(receipt.capture_timestamp_authenticated)
        self.assertFalse(receipt.actor_identity_authenticated)
        self.assertFalse(receipt.ready_for_engine)


if __name__=="__main__":
    unittest.main()
