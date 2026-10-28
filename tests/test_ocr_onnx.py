"""Unit tests for shared PP-OCRv6 ONNX inference without downloading models."""
import tempfile
import unittest
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from champions_assist.ocr import OcrExecutionError
from champions_assist.ocr_onnx import (
    PPocrV6SmallOnnx, _ctc_decode, _boxes, _rec_tensor, _new_session, _verify_bundle, bundled_model_directory,
)


class OnnxCoreTests(unittest.TestCase):
    def test_selected_gpu_index_is_passed_to_onnx_runtime(self):
        calls=[]
        class FakeSession:
            def get_providers(self):
                return ["DmlExecutionProvider","CPUExecutionProvider"]
        def factory(path,**kwargs):
            calls.append(kwargs)
            return FakeSession()
        session,provider=_new_session(
            Path("any.onnx"),factory=factory,
            available={"DmlExecutionProvider","CPUExecutionProvider"},
            device="directml",gpu_id=3)
        self.assertEqual(provider,"DmlExecutionProvider")
        self.assertEqual(calls[0]["providers"][0],
                         ("DmlExecutionProvider",{"device_id":3}))
        self.assertIsNotNone(session)
        with self.assertRaises(OcrExecutionError) as ctx:
            _new_session(Path("any.onnx"),factory=factory,
                         available={"CPUExecutionProvider"},
                         device="directml",gpu_id=3)
        self.assertEqual(ctx.exception.code,"ONNX_OCR_GPU_UNAVAILABLE")

    def test_warmup_uses_same_gpu_index_for_detection_and_recognition(self):
        """Both ONNX sessions must honor the screen classifier's GPU index."""
        class FakeSession:
            def __init__(self, name):
                self.name=name
            def get_inputs(self):
                return [SimpleNamespace(name="input")]
            def run(self, *_args, **_kwargs):
                if self.name == "rec":
                    return [np.zeros((1, 4, 1003), np.float32)]
                return [np.zeros((1, 1, 8, 8), np.float32)]

        session_calls=[]
        def new_session(path, **kwargs):
            name="rec" if "rec" in path.name else "det"
            session_calls.append((name,kwargs["device"],kwargs["gpu_id"]))
            return FakeSession(name),"DmlExecutionProvider"

        fake_ort=SimpleNamespace(InferenceSession=object,
                                 get_available_providers=lambda: ["DmlExecutionProvider"])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ("pp-ocrv6-small-det.onnx","pp-ocrv6-small-rec.onnx"):
                (root/name).write_bytes(b"fixture")
            (root/"ppocrv6_dict.txt").write_text(
                "\n".join(f"char{i}" for i in range(1001)),encoding="utf-8")
            with patch("champions_assist.ocr_onnx._verify_bundle"), \
                    patch("champions_assist.ocr_onnx._new_session",side_effect=new_session), \
                    patch.dict("sys.modules",{"onnxruntime":fake_ort}):
                reader=PPocrV6SmallOnnx(model_dir=root,device="directml",gpu_id=3)
                reader.warmup()
        self.assertEqual(session_calls,[("det","directml",3),("rec","directml",3)])
        self.assertEqual(reader.provider,"DmlExecutionProvider")

    def test_gpu_id_validation_fails_closed(self):
        with self.assertRaises(OcrExecutionError) as ctx:
            _new_session(Path("any.onnx"),factory=lambda *_a,**_k:None,
                         available={"CPUExecutionProvider"},
                         device="auto",gpu_id=8)
        self.assertEqual(ctx.exception.code,"ONNX_OCR_GPU_INDEX_INVALID")

    def test_staged_bytes_must_match_sha256_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            names=("pp-ocrv6-small-det.onnx","pp-ocrv6-small-rec.onnx",
                   "ppocrv6_dict.txt","config.json","PP-OCRv6-LICENSE.txt")
            for name in names:
                (root/name).write_bytes(b"test-"+name.encode())
            manifest={"schema":1,
                      "sha256":{name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                                for name in names}}
            (root/"model_manifest.json").write_text(json.dumps(manifest))
            _verify_bundle(root)
            (root/"ppocrv6_dict.txt").write_text("tampered")
            with self.assertRaises(OcrExecutionError) as ctx:
                _verify_bundle(root)
            self.assertEqual(ctx.exception.code,"ONNX_OCR_BUNDLE_INTEGRITY_FAILED")

    def test_cpu_smoke_rejects_empty_decoding(self):
        from champions_assist.ocr import OcrRead
        from champions_assist.ocr_smoke import verify_real_cpu_ocr
        class FakeReader:
            provider="CPUExecutionProvider"
            def __init__(self,**kwargs):
                pass
            def read(self,*args,**kwargs):
                return OcrRead("",0.99,"pp-ocrv6-small-onnx")
        with patch("champions_assist.ocr_smoke.PPocrV6SmallOnnx",FakeReader):
            with self.assertRaisesRegex(RuntimeError,"EXPECTED_TEXT_MISSING"):
                verify_real_cpu_ocr()

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
