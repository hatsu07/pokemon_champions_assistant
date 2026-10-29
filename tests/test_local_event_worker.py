"""Offline tests for event-time OCR: no timer, no network, no turn blocking."""
from __future__ import annotations

from threading import Event
from time import monotonic, sleep
import unittest
from unittest.mock import patch

import numpy as np

from champions_assist.calibrated_screen_fields import FieldRegion, ScreenFieldProfile
from champions_assist.local_event_worker import (
    AsyncBattleEventReader, LocalFieldRead,
)
from champions_assist.battle_state_tracker import LocalBattleStateTracker
from champions_assist.ocr import OcrRead, MappingOcrEngine


def fixture():
    profile=ScreenFieldProfile(
        (64,32),(
            FieldRegion("battle_message","MESSAGE",(2,2,34,15),("BATTLE_EVENT",)),
            FieldRegion("own_hp_fraction","HP_FRACTION",(2,16,34,28),
                        ("BATTLE_EVENT","TURN_ACTION")),
            FieldRegion("turn_command_timer","TIMER",(36,2,63,15),
                        ("BATTLE_EVENT","TURN_ACTION")),
            FieldRegion("own_active_name","TEXT",(36,16,63,28),("BATTLE_EVENT",)),
        ),"SYNTHETIC_OCR_TEST",False)
    frame=np.zeros((32,64,3),dtype=np.uint8)
    frame[2:15,2:34]=180
    frame[16:28,2:34]=85
    frame[2:15,36:63]=70
    frame[16:28,36:63]=35
    readings=[
        ("battle_message",profile.crop(frame,profile.regions[0]),
         OcrRead("ピカチュウの10まんボルト",0.99,"test")),
        ("own_hp_fraction",profile.crop(frame,profile.regions[1]),
         OcrRead("65/120",0.99,"test")),
    ]
    return profile,frame,MappingOcrEngine.for_images(readings)


def completed(reader, expected=1, seconds=3.0):
    deadline=monotonic()+seconds
    found=[]
    while monotonic()<deadline:
        found.extend(reader.drain())
        if len(found)>=expected:
            break
        sleep(0.005)
    return found


class AsyncBattleEventReaderTests(unittest.TestCase):
    def test_event_roi_reads_message_and_hp_but_never_timer(self):
        profile,image,engine=fixture()
        reader=AsyncBattleEventReader(profile,ocr_engine=engine)
        try:
            # Action-screen OCR is now asynchronous; test events here.
            self.assertFalse(reader.offer(image,label="UNKNOWN"))
            self.assertTrue(reader.offer(image,label="BATTLE_EVENT"))
            results=completed(reader)
            self.assertEqual(len(results),1)
            items=results[0].observations
            self.assertEqual({r.field for r in items},
                {"screen_text.battle_message","screen_text.own_hp_fraction"})
            self.assertIn("battle_message",engine.calls)
            self.assertIn("own_hp_fraction",engine.calls)
            self.assertNotIn("turn_command_timer",engine.calls)
            self.assertIn("own_active_name",engine.calls)
            self.assertEqual(items[0].status,"CANDIDATE")
            self.assertFalse(items[0].as_dict()["game_validated"])
            self.assertFalse(reader.offer(image,label="BATTLE_EVENT"))
            self.assertEqual(reader.drain(),())
        finally:
            reader.stop()

    def test_action_screen_reads_hp_without_event_message_or_timer(self):
        profile,image,engine=fixture()
        reader=AsyncBattleEventReader(profile,ocr_engine=engine)
        try:
            self.assertTrue(reader.offer(image,label="TURN_ACTION"))
            result=completed(reader)
            self.assertEqual(len(result),1)
            self.assertEqual(
                {r.field for r in result[0].observations},
                {"screen_text.own_hp_fraction"})
            self.assertNotIn("battle_message",engine.calls)
            self.assertNotIn("turn_command_timer",engine.calls)
        finally:
            reader.stop()

    def test_legacy_system_message_does_not_start_ocr(self):
        profile,image,engine=fixture()
        legacy_region=FieldRegion(
            "system_message","MESSAGE",(34,2,62,15),("BATTLE_EVENT",))
        legacy=ScreenFieldProfile(
            profile.reference_resolution,profile.regions+(legacy_region,),
            "OLD_CALIBRATION",False)
        reader=AsyncBattleEventReader(legacy,ocr_engine=engine)
        try:
            self.assertTrue(reader.offer(image,label="BATTLE_EVENT"))
            self.assertEqual(len(completed(reader)),1)
            self.assertNotIn("system_message",engine.calls)
        finally:
            reader.stop()

    def test_event_offer_never_waits_for_blocked_ocr_and_queue_is_bounded(self):
        profile,image,_=fixture()
        entered=Event()
        release=Event()
        class SlowOcr:
            def read(self,frame,*,roi_name):
                entered.set()
                release.wait(2.0)
                return OcrRead("65/120",0.8,"slow")
        reader=AsyncBattleEventReader(profile,ocr_engine=SlowOcr(),max_pending=1)
        try:
            self.assertTrue(reader.offer(image,label="BATTLE_EVENT"))
            self.assertTrue(entered.wait(1.0))
            start=monotonic()
            modified=image.copy()
            modified[2:15,2:34]=25
            self.assertTrue(reader.offer(modified,label="BATTLE_EVENT"))
            modified[2:15,2:34]=250
            self.assertFalse(reader.offer(modified,label="BATTLE_EVENT"))
            self.assertGreaterEqual(reader.overflow,1)
            self.assertLess(monotonic()-start,0.15)
            start=monotonic()
            reader.stop()
            self.assertLess(monotonic()-start,0.15)
        finally:
            release.set()
            reader.stop()

    def test_sidecar_failure_disables_future_ocr_and_reports_reason(self):
        from champions_assist.ocr import OcrExecutionError
        profile,image,_=fixture()
        class BrokenSidecar:
            def __init__(self):
                self.stop_calls=0
            def warmup(self):
                self.provider="DmlExecutionProvider"
            def read(self, image, *, roi_name):
                raise OcrExecutionError("OCR_SIDECAR_EXITED")
            def stop(self):
                self.stop_calls+=1
        engine=BrokenSidecar()
        reader=AsyncBattleEventReader(profile,ocr_engine=engine)
        try:
            self.assertTrue(reader.offer(image,label="BATTLE_EVENT"))
            result=completed(reader)
            self.assertEqual(len(result),1)
            self.assertEqual(result[0].observations,())
            self.assertEqual(result[0].error_code,"OCR_SIDECAR_EXITED")
            self.assertFalse(reader.enabled)
            self.assertEqual(reader.reason,"OCR_SIDECAR_EXITED")
            self.assertFalse(reader.offer(image,label="BATTLE_EVENT"))
            self.assertEqual(engine.stop_calls,1)
        finally:
            reader.stop()

    def test_diagnostic_stats_include_duration_and_queue_health_without_text(self):
        profile,image,engine=fixture()
        reader=AsyncBattleEventReader(profile,ocr_engine=engine)
        try:
            self.assertTrue(reader.offer(image,label="BATTLE_EVENT"))
            self.assertEqual(len(completed(reader)),1)
            counters=reader.diagnostic_counts()
            self.assertEqual(counters["ocr_jobs_enqueued"],1)
            self.assertEqual(counters["ocr_jobs_completed"],1)
            self.assertEqual(counters["ocr_rois_attempted"],3)
            self.assertEqual(counters["ocr_rois_parsed"],2)
            self.assertGreaterEqual(counters["ocr_last_latency_ms"],0)
            self.assertEqual(counters["ocr_queue_pending"],0)
            self.assertEqual(counters["ocr_results_pending"],0)
            self.assertNotIn("ピカチュウ",str(counters))
            self.assertNotIn("raw_text",str(counters))
        finally:
            reader.stop()

    def test_wrong_roi_geometry_abstains_and_does_not_ocr(self):
        profile,image,engine=fixture()
        reader=AsyncBattleEventReader(profile,ocr_engine=engine)
        try:
            self.assertFalse(reader.offer(np.zeros((24,24,3),dtype=np.uint8),
                                          label="BATTLE_EVENT"))
            self.assertEqual(engine.calls,[])
        finally:
            reader.stop()

    def test_missing_tesseract_is_nonfatal(self):
        profile,_,_=fixture()
        with patch("champions_assist.ocr.discover_japanese_tesseract",
                   return_value=None):
            reader=AsyncBattleEventReader.with_local_tesseract(profile)
        self.assertFalse(reader.enabled)
        self.assertEqual(reader.reason,"JAPANESE_TESSERACT_RUNTIME_UNAVAILABLE")
        reader.stop()

    def test_observations_remain_candidates_and_changing_hp_is_not_conflict(self):
        tracker=LocalBattleStateTracker(max_events=3)
        def read(field,text,n,value):
            return LocalFieldRead(field=field,value=value,frame_id=f"f{n}",
                timestamp=float(n),confidence=0.9,roi=field,source="fixture",
                text=text)
        hp="screen_text.own_hp_fraction"
        msg="screen_text.battle_message"
        self.assertEqual(tracker.ingest((read(hp,"65/120",1,{"current":65,"maximum":120}),)),1)
        # Without a visual ID, the second reading is a possible NEW event.
        self.assertEqual(tracker.ingest((read(hp,"65/120",2,{"current":65,"maximum":120}),)),1)
        self.assertEqual(tracker.ingest((read(hp,"50/120",3,{"current":50,"maximum":120}),)),1)
        self.assertEqual(tracker.conflicts,0)
        tracker.ingest((read(msg,"かみなり",4,"かみなり"),))
        tracker.ingest((read(msg,"失敗した",5,"失敗した"),))
        tracker.ingest((read(msg,"かみなり",6,"かみなり"),))
        snapshot=tracker.snapshot()
        self.assertEqual(snapshot["candidate_count"],3)
        self.assertEqual(snapshot["conflict_count"],0)
        self.assertEqual(snapshot["local_observations"][-1]["raw_text"],"かみなり")
        self.assertTrue(snapshot["no_confirmed_game_mechanics"])

    def test_distinct_visual_episodes_preserve_identical_battle_messages(self):
        tracker=LocalBattleStateTracker()
        def reading(frame, display, time):
            return LocalFieldRead(
                field="screen_text.battle_message", value="ガブリアスのじしん！",
                frame_id=frame, timestamp=time, confidence=0.96,
                roi="battle_message", source="test", text="ガブリアスのじしん！",
                display_id=display)
        # Same text and within 1.5 seconds, but they are different UI events.
        self.assertEqual(tracker.ingest(
            (reading("f1", "visual-1", 10.0),), turn_epoch=2),1)
        self.assertEqual(tracker.ingest(
            (reading("f2", "visual-2", 10.6),), turn_epoch=2),1)
        self.assertEqual([row["frame_id"] for row in
            tracker.snapshot()["local_observations"]],["f1","f2"])
        # A continued reading of the SAME visual episode remains deduplicable.
        self.assertEqual(tracker.ingest(
            (reading("f3", "visual-2", 10.9),), turn_epoch=2),0)
        self.assertEqual(tracker.duplicates,1)
        self.assertEqual([r["frame_id"] for r in
            tracker.snapshot()["local_observations"]], ["f1","f2","f3"])
        # Without display IDs, do not discard distinct OCR observations.
        self.assertEqual(tracker.ingest(
            (reading("f4", "", 11.0),), turn_epoch=2),1)
        self.assertEqual(tracker.ingest(
            (reading("f5", "", 11.4),), turn_epoch=2),1)

    def test_event_ocr_writes_no_tactical_api_calls(self):
        profile,image,engine=fixture()
        reader=AsyncBattleEventReader(profile,ocr_engine=engine)
        try:
            with patch("openai.OpenAI",side_effect=AssertionError("No AI for OCR")):
                reader.offer(image,label="BATTLE_EVENT")
                self.assertEqual(len(completed(reader)),1)
        finally:
            reader.stop()


if __name__=="__main__":
    unittest.main()
