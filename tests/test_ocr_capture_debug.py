"""Issue #128: opt-in OCR screenshots and ROI captures remain local/bounded."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from champions_assist.ocr_capture_debug import OcrCaptureDebugWriter
from tests.test_local_event_worker import fixture


class OcrCaptureDebugTests(unittest.TestCase):
    def test_rejected_frame_is_saved_with_actual_shape_and_sample_roi(self):
        profile,image,_=fixture()
        # Wrong aspect ratio: this must be inspectable even if OCR offers fail.
        wrong=np.zeros((36,64,3),dtype=np.uint8)
        with TemporaryDirectory() as tmp:
            writer=OcrCaptureDebugWriter(
                tmp,match_id="capture-"+"a"*32,max_samples=2)
            self.assertTrue(writer.offer(
                wrong,frame_id="capture-9",label="BATTLE_EVENT",
                reason="ASPECT_RATIO_MISMATCH",profile=profile))
            self.assertFalse(writer.offer(
                wrong,frame_id="capture-10",label="BATTLE_EVENT",
                reason="ASPECT_RATIO_MISMATCH",profile=profile))
            writer.close()
            writer._worker.join(timeout=4)
            self.assertFalse(writer._worker.is_alive())
            root=writer.path
            metadata=list(root.glob("frame-*.json"))
            self.assertEqual(len(metadata),1)
            doc=json.loads(metadata[0].read_text(encoding="utf-8"))
            self.assertEqual(doc["image_shape"],[36,64,3])
            self.assertEqual(doc["offer_reason"],"ASPECT_RATIO_MISMATCH")
            self.assertFalse(doc["profile_compatible"])
            self.assertEqual(doc["profile_reference_resolution"],[64,32])
            self.assertTrue(doc["image_saved"])
            self.assertGreaterEqual(len(doc["regions"]),1)
            self.assertTrue(list(root.glob("frame-*.png")))
            self.assertLessEqual(sum(p.stat().st_size for p in root.iterdir()),
                                 writer.max_bytes)

    def test_saves_ocr_text_only_with_explicit_writer_instance(self):
        profile,image,_=fixture()
        from champions_assist.local_event_worker import LocalFieldRead
        row=LocalFieldRead("screen_text.battle_message","recognized",
                           "capture-21",1.0,0.95,"battle_message",
                           "テスト文字","ROI_OCR_UNVERIFIED")
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.assertFalse((root/"debug-logs").exists())
            writer=OcrCaptureDebugWriter(
                root,match_id="capture-"+"b"*32,max_samples=1)
            self.assertTrue(writer.offer(
                image,frame_id="capture-21",label="BATTLE_EVENT",
                reason="ENQUEUED",profile=profile))
            self.assertFalse(writer.offer(
                image,frame_id="capture-22",label="TURN_ACTION",
                reason="ENQUEUED",profile=profile))
            writer.record_ocr("capture-21",(row,))
            writer.close()
            writer._worker.join(timeout=4)
            self.assertFalse(writer._worker.is_alive())
            rows=(writer.path/"ocr-results.jsonl").read_text(encoding="utf-8")
            self.assertIn("テスト文字",rows)
            self.assertEqual(len(list(writer.path.glob("frame-*.json"))),1)


if __name__ == "__main__":
    unittest.main()
