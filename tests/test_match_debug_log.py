"""Diagnostics must be correlated and safe to share without raw battle images."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from champions_assist.match_debug_log import MatchDebugLog, PrivateAiExchangeLog
from champions_assist.selection_vision import _stream_json, start_ai_exchange_trace, stop_ai_exchange_trace
from types import SimpleNamespace


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

    def test_private_exchange_opt_in_logs_text_but_no_image_or_api_key(self):
        with tempfile.TemporaryDirectory() as root:
            logfile=PrivateAiExchangeLog(root,match_id="capture-"+"e"*32)
            class Stream:
                def __enter__(self): return self
                def __exit__(self,*args): return False
                def __iter__(self):
                    yield SimpleNamespace(type="response.output_text.delta",
                                          delta='{"result":"WIN"}')
                    yield SimpleNamespace(type="response.completed")
            client=SimpleNamespace(responses=SimpleNamespace(
                create=lambda **kwargs:Stream()))
            trace=start_ai_exchange_trace(logfile.write)
            try:
                result=_stream_json(
                    client,model="test",name="test_result",
                    schema={"type":"object","properties":{
                        "result":{"type":"string"}},"required":["result"]},
                    instructions="Do not leak sk-verysecret01234567890",
                    content=[{"type":"input_text","text":"OCR sample"},
                             {"type":"input_image","image_url":"data:image/jpeg;base64,SUPERSECRET"}])
            finally:
                stop_ai_exchange_trace(trace)
            logfile.queue.join()
            logfile.close()
            lines=[json.loads(line) for line in logfile.path.read_text(
                encoding="utf-8").splitlines()]
            self.assertEqual(result,{"result":"WIN"})
            self.assertEqual([row["event"] for row in lines],
                             ["GPT_REQUEST","GPT_RESPONSE"])
            serialized=json.dumps(lines,ensure_ascii=False)
            self.assertIn("OCR sample",serialized)
            self.assertIn("WIN",serialized)
            self.assertNotIn("SUPERSECRET",serialized)
            self.assertNotIn("verysecret01234567890",serialized)
            self.assertIn("[REDACTED]",serialized)

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
