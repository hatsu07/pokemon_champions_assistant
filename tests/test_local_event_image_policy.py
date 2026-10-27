"""Text-first event image fallback never hides unread critical battle events."""
from __future__ import annotations

import unittest

from champions_assist.local_event_image_policy import event_image_plan


def observation(frame,confidence=0.98,roi="battle_message"):
    return {"frame_id":frame,"certainty":"CANDIDATE",
            "game_validated":False,"roi":roi,
            "raw_text":"相手のこうげきが上がった","confidence":confidence}


def metrics(**kwargs):
    result={"ocr_jobs_enqueued":2,"ocr_jobs_completed":2,
            "ocr_queue_pending":0,"ocr_results_dropped":0,
            "ocr_errors":0,"overflow":0}
    result.update(kwargs)
    return result


class EventTextFirstPolicyTests(unittest.TestCase):
    def plan(self,frames,readings,**kwargs):
        config={"reader_status":"READY","roi_validated":True,
                "ocr_counters":metrics()}
        config.update(kwargs)
        return event_image_plan(frames,readings,**config)

    def test_all_event_text_is_read_so_only_current_menu_image_is_needed(self):
        plan=self.plan(["capture-10","capture-11"],
                       [observation("capture-10"),observation("capture-11")])
        self.assertFalse(plan.include_event_images)
        self.assertEqual(plan.reason,"EVENT_TEXT_RECOGNIZED")
        self.assertEqual(plan.recognized,2)

    def test_one_unread_event_requires_image_fallback(self):
        plan=self.plan(["capture-10","capture-11"],[observation("capture-10")])
        self.assertTrue(plan.include_event_images)
        self.assertEqual(plan.recognized,1)

    def test_low_confidence_non_event_or_unvalidated_profile_requires_image(self):
        for row in (observation("capture-10",0.89),
                    observation("capture-10",roi="turn_command_timer")):
            self.assertTrue(self.plan(["capture-10"],[row]).include_event_images)
        self.assertTrue(self.plan(["capture-10"],[observation("capture-10")],
                                  roi_validated=False).include_event_images)

    def test_queue_backlog_overflow_or_ocr_error_never_suppresses_image(self):
        for counters in (metrics(ocr_queue_pending=1),
                         metrics(ocr_jobs_completed=1),
                         metrics(overflow=1),
                         metrics(ocr_results_dropped=1),
                         metrics(ocr_errors=1)):
            self.assertTrue(self.plan(
                ["capture-10"],[observation("capture-10")],
                ocr_counters=counters).include_event_images)

    def test_missing_tesseract_does_not_drop_event_images(self):
        plan=self.plan(["capture-10"],[observation("capture-10")],
                       reader_status="JAPANESE_TESSERACT_RUNTIME_UNAVAILABLE")
        self.assertTrue(plan.include_event_images)

    def test_no_event_frames_require_only_current_screen(self):
        plan=self.plan([],[],reader_status="NOT_RUNNING")
        self.assertFalse(plan.include_event_images)


if __name__=="__main__":
    unittest.main()
