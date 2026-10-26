"""Diagnostics must be correlated and safe to share without raw battle images."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from champions_assist.match_debug_log import MatchDebugLog


class MatchDebugDiagnosticsTests(unittest.TestCase):
    def test_classifier_and_ocr_counters_are_serialized_as_numbers(self):
        with tempfile.TemporaryDirectory() as root:
            logfile=MatchDebugLog(root)
            self.assertTrue(logfile.write(
                "SCREEN_CLASSIFIER_CHANGED",
                frame_seq=34,turn_epoch=2,
                screen_label="TURN_ACTION",
                classifier_source="ONNX_GPU_DML",
                confidence=0.973,margin=0.822,latency_ms=3.5))
            self.assertTrue(logfile.write(
                "LOCAL_OCR_TURN_SUMMARY",
                turn_epoch=2,frame_seq=34,ocr_status="READY",
                ocr_jobs_enqueued=5,ocr_jobs_completed=4,
                ocr_rois_attempted=7,ocr_rois_parsed=5,
                ocr_results_dropped=1,ocr_queue_pending=0,
                ocr_candidates_sent=4,ocr_last_latency_ms=88))
            rows=[json.loads(s) for s in logfile.path.read_text(encoding="utf-8").splitlines()]
            classified=rows[-2]
            self.assertEqual(classified["frame_seq"],34)
            self.assertIsInstance(classified["confidence"],float)
            self.assertAlmostEqual(classified["margin"],0.822)
            self.assertEqual(classified["classifier_source"],"ONNX_GPU_DML")
            ocr=rows[-1]
            self.assertEqual(ocr["ocr_jobs_enqueued"],5)
            self.assertEqual(ocr["ocr_results_dropped"],1)
            self.assertEqual(ocr["ocr_candidates_sent"],4)

    def test_unsafe_content_is_stripped_from_disk_and_only_codes_remain(self):
        with tempfile.TemporaryDirectory() as root:
            logfile=MatchDebugLog(root)
            logfile.write(
                "AI_RESULT_DISPLAYED",turn_epoch=3,
                frame_seq=45,ai_request_attempt=7,
                reason="ReadTimeout",status="ERROR",
                raw_text="個人的なバトルログ",
                image_pixels="private base64 image data",
                prompt="private prompt",token="secret access token",
                screenshot="/tmp/private/game.png?auth=secret",
                candidate={"name":"private team"},
                ocr_jobs_completed=2,
            )
            raw=logfile.path.read_text(encoding="utf-8")
            row=json.loads(raw.splitlines()[-1])
            self.assertEqual(row["reason"],"ReadTimeout")
            self.assertEqual(row["turn_epoch"],3)
            self.assertEqual(row["ai_request_attempt"],7)
            self.assertNotIn("raw_text",row)
            self.assertNotIn("image_pixels",row)
            self.assertNotIn("candidate",row)
            self.assertNotIn("token",row)
            self.assertNotIn("prompt",row)
            self.assertNotIn("screenshot",row)
            self.assertNotIn("private",raw)


if __name__=="__main__":
    unittest.main()
