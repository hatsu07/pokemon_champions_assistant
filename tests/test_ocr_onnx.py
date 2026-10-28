"""Unit tests for shared PP-OCRv6 ONNX inference without downloading models."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from champions_assist.ocr import OcrExecutionError
from champions_assist.ocr_onnx import (
    PPocrV6SmallOnnx, _ctc_decode, _boxes, _rec_tensor, bundled_model_directory,
)


class OnnxCoreTests(unittest.TestCase):
    def test_ctc_collapse_blank_and_repeat(self):
        classes = ["ル", "カ", "リ", "オ", " "]
        sequence = [1, 1, 0, 2, 0, 3, 3, 4]
        scores = np.zeros((1, len(sequence), len(classes)+1), np.float32)
        for t, index in enumerate(sequence):
            scores[0, t, index] = 0.99
        text, score = _ctc_decode(scores, classes)
        self.assertEqual(text, "ルカリオ")
        self.assertGreater(score, 0.9)

    def test_rec_resize_retains_shape_and_padding(self):
        source=np.full((22,64,3),128,np.uint8)
        output=_rec_tensor(source)
        self.assertEqual(output.shape,(1,3,48,320))
        self.assertEqual(output.dtype,np.float32)

    def test_det_boxes_no_false_positive(self):
        result=_boxes(np.zeros((1,1,32,64),np.float32),120,60,(120/64,60/32))
        self.assertEqual(result,[])

    def test_fail_closed_if_models_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            reader=PPocrV6SmallOnnx(model_dir=Path(temp))
            with self.assertRaises(OcrExecutionError) as ctx:
                reader.warmup()
            self.assertEqual(ctx.exception.code,"ONNX_OCR_MODELS_MISSING")

    def test_source_mode_has_no_external_ocr_python(self):
        from champions_assist.ocr_onnx import make_onnx_ocr
        engine=make_onnx_ocr()
        self.assertIsInstance(engine,PPocrV6SmallOnnx)
        self.assertEqual(engine.device,"auto")


if __name__=="__main__":
    unittest.main()
