"""Offline ONNX OCR contract and tactical regression tests.

The Windows EXE smoke additionally executes the bundled real models on CPU.
Actual DirectML GPU throughput needs a Windows GPU machine.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from champions_assist.ocr import OcrExecutionError, OcrRead
from champions_assist.ocr_onnx import (
    ENGINE_NAME, MODEL_DET, MODEL_REC, PPocrV6SmallOnnx,
    _ctc_decode, _det_tensor, _rec_tensor, make_onnx_ocr)
from champions_assist.ai_ocr_state_updates import apply_ai_ocr_updates
from champions_assist.match_replay import run_replay_text_action
from tests.test_ai_ocr_state_updates import fixture_state
from tests.test_match_replay import fixture_assets


class OnnxContract(unittest.TestCase):
    def test_models_and_engine_remain_pp_ocrv6_small(self):
        self.assertEqual(MODEL_DET, "PP-OCRv6_small_det")
        self.assertEqual(MODEL_REC, "PP-OCRv6_small_rec")
        self.assertEqual(make_onnx_ocr().engine_name, ENGINE_NAME)

    def test_onnx_session_is_loaded_once_and_raw_text_is_returned(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("pp-ocrv6-small-det.onnx", "pp-ocrv6-small-rec.onnx"):
                (root / name).write_bytes(b"fake-onnx-only-for-injected-session")
            # The test dictionary has 1,000 codepoints; the production asset
            # script validates the actual official dictionary (18,708 rows).
            (root / "ppocrv6_dict.txt").write_text(
                "\n".join(chr(0x4e00 + i) for i in range(1000)) + "\n",
                encoding="utf-8")
            created = []

            class Session:
                def __init__(self, path, **kwargs):
                    self.kind = "det" if "det.onnx" in path else "rec"
                    created.append((self.kind, kwargs))
                def get_providers(self):
                    return ["CPUExecutionProvider"]
                def get_inputs(self):
                    return [SimpleNamespace(name="x")]
                def run(self, _outputs, tensors):
                    assert tensors["x"].shape[0] == 1
                    if self.kind == "det":
                        m = np.zeros((1, 1, 64, 128), dtype=np.float32)
                        m[0, 0, 14:34, 10:110] = 0.99
                        return [m]
                    m = np.zeros((1, 4, 1002), dtype=np.float32)
                    m[0, :, 0] = 0.01
                    m[0, 0, 1] = 0.95
                    m[0, 1, 1] = 0.95
                    m[0, 2, 0] = 0.95
                    m[0, 3, 2] = 0.95
                    return [m]

            reader = PPocrV6SmallOnnx(model_dir=root, device="cpu",
                        session_factory=Session,
                        available_providers=["CPUExecutionProvider"])
            sample = np.full((64,128,3),255,dtype=np.uint8)
            first = reader.read(sample,roi_name="battle_message")
            second = reader.read(sample,roi_name="battle_message")
            self.assertEqual(first.engine,ENGINE_NAME)
            self.assertEqual(first.text, "一丁")
            self.assertEqual(second.text,first.text)
            self.assertEqual(reader.provider,"CPUExecutionProvider")
            self.assertEqual(len(created),2)

    def test_missing_packaged_models_is_diagnostic_not_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            reader=PPocrV6SmallOnnx(model_dir=folder)
            with self.assertRaises(OcrExecutionError) as err:
                reader.warmup()
            self.assertEqual(err.exception.code,"ONNX_OCR_MODELS_MISSING")

    def test_ctc_rejects_dictionary_mismatch(self):
        with self.assertRaises(OcrExecutionError) as err:
            _ctc_decode(np.zeros((1,4,5),np.float32),["あ"])
        self.assertEqual(err.exception.code,"ONNX_OCR_REC_OUTPUT_INVALID")

    def test_reader_factory_uses_onnx_without_external_python(self):
        from champions_assist.local_event_worker import AsyncBattleEventReader
        from tests.test_local_event_worker import fixture
        profile,image,_=fixture()
        class FakeOnnx:
            provider="DmlExecutionProvider"
            def read(self,*args,**kw):
                return OcrRead("",None,ENGINE_NAME)
        with patch("champions_assist.ocr_onnx.make_onnx_ocr",
                   return_value=FakeOnnx()) as factory:
            reader=AsyncBattleEventReader.with_onnx_ocr(profile)
            try:
                self.assertTrue(reader.enabled)
                factory.assert_called_once_with(device="auto")
            finally:
                reader.stop()


class NoStaleAdviceGuiRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
        from champions_assist.desktop import ensure_application
        cls.app=ensure_application()

    def test_blocked_text_reply_cannot_show_last_switch(self):
        from tempfile import TemporaryDirectory
        from champions_assist.match_replay_dialog import MatchReplayDialog
        with TemporaryDirectory() as tmp:
            gui=MatchReplayDialog(model="mock",master_directory=tmp,
                                  team_path=tmp,store_root=tmp)
            try:
                gui.state=fixture_state(fixture_assets())
                gui.state["our_active"]="ドドゲザン"
                gui._last_action_text="ドドゲザンに交代。"
                gui._render({"status":"BLOCKED","reason":"test"})
                self.assertNotIn("ドドゲザンに交代",gui.action_banner.text())
                self.assertEqual(gui._last_action_text,"")
            finally:
                gui.close()

class UnknownOpponentRegression(unittest.TestCase):
    def setUp(self):
        self.assets=fixture_assets()
        self.state=fixture_state(self.assets)
        self.state["opponent_six"]=["マスカーニャ","ルカリオ"]

    @staticmethod
    def event(kind,pokemon,frames,prior=""):
        return {"kind":kind,"side":"opponent","pokemon":pokemon,
                "from_pokemon":prior,"value":"","stat":"",
                "delta":0,"certainty":"CONFIRMED",
                "evidence_frames":frames}

    def test_switch_out_unknown_then_lucario_enters(self):
        reads=[
            {"field":"screen_text.battle_message","frame_id":"f375",
             "raw_text":"マスカーニャは もどった", "confidence":.714,
             "observed_at":1.0},
            {"field":"screen_text.battle_message","frame_id":"f377",
             "raw_text":"ルカ リオ が とびだした","confidence":.529,
             "observed_at":2.0}
        ]
        out=self.event("SWITCH_OUT","マスカーニャ",["f375","f377"])
        incoming=self.event("SWITCH","ルカリオ",["f375","f377"],"マスカーニャ")
        result,audit=apply_ai_ocr_updates(
            self.state,[out,incoming],reads,self.assets)
        self.assertEqual([a["status"] for a in audit],["APPLIED","APPLIED"])
        self.assertEqual(result["opponent_active"],"ルカリオ")
        self.assertFalse(result["opponent_active_unknown"])
        self.assertEqual(self.state["opponent_active"],"マスカーニャ")

    def test_unknown_opponent_does_not_block_legal_move(self):
        self.state["opponent_active"]=""
        self.state["opponent_active_unknown"]=True
        response={
            "state_updates":[],
            "interpreted_own_active":"ガブリアス",
            "interpreted_opponent_active":"",
            "processed_ocr_frames":[],
            "action":{"action_kind":"MOVE","move":"じしん",
                      "switch_target":"","warning":"相手不明の暫定判断"}}
        with patch("champions_assist.match_replay._stream_json",
                   return_value=response):
            after,report=run_replay_text_action(
                state=self.state,assets=self.assets,
                access_token="test",model="mock",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=[],
                client_factory=lambda **kw:object())
        self.assertNotEqual(report["status"],"BLOCKED",report)
        self.assertIn("じしん",report.get("recommendation",""))

    def test_opponent_only_rejected_event_cannot_stop_own_move(self):
        bad=self.event("SWITCH","ルカリオ",["not-sent"],"マスカーニャ")
        response={
            "state_updates":[bad],
            "interpreted_own_active":"ガブリアス",
            "interpreted_opponent_active":"",
            "processed_ocr_frames":[],
            "action":{"action_kind":"MOVE","move":"じしん",
                      "switch_target":"","warning":"交代先未確定"}}
        with patch("champions_assist.match_replay._stream_json",
                   return_value=response):
            after,report=run_replay_text_action(
                state=self.state,assets=self.assets,
                access_token="test",model="mock",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=[],
                client_factory=lambda **kw:object())
        self.assertNotEqual(report["status"],"BLOCKED",report)
        self.assertTrue(after.get("opponent_active_unknown"))


if __name__=="__main__":
    unittest.main()
