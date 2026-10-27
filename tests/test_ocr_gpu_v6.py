"""Offline GPU-contract, OCR timing, and unknown-active regressions.

CUDA inference is tested separately on an actual NVIDIA Windows machine.
"""
from __future__ import annotations
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from champions_assist.ocr import OcrExecutionError
from champions_assist.ocr_gpu import (
    PaddleV6SmallGpuOcr, MODEL_DET,MODEL_REC,ENGINE_NAME,
    make_gpu_ocr)
from champions_assist.ai_ocr_state_updates import apply_ai_ocr_updates
from champions_assist.match_replay import run_replay_text_action
from tests.test_ai_ocr_state_updates import fixture_state
from tests.test_match_replay import fixture_assets


class GpuContract(unittest.TestCase):
    def test_small_models_are_explicit_and_gpu_only(self):
        captured=[]
        class Pipeline:
            def predict(self,arr):
                return [SimpleNamespace(json={"res":{
                    "rec_texts":["ルカ リオ","が あらわれた"],
                    "rec_scores":[.714,.529]}})]
        def factory(**kwargs):
            captured.append(kwargs)
            return Pipeline()
        engine=PaddleV6SmallGpuOcr(
            pipeline_factory=factory,gpu_check=lambda:None)
        img=np.zeros((32,160,3),dtype=np.uint8)
        first=engine.read(img,roi_name="battle_message")
        second=engine.read(img,roi_name="battle_message")
        self.assertEqual(first.engine,ENGINE_NAME)
        self.assertIn("ルカ リオ",first.text)
        self.assertAlmostEqual(first.confidence,.6215)
        self.assertEqual(len(captured),1)
        self.assertEqual(captured[0]["device"],"gpu:0")
        self.assertEqual(captured[0]["text_detection_model_name"],MODEL_DET)
        self.assertEqual(captured[0]["text_recognition_model_name"],MODEL_REC)
        self.assertFalse(captured[0]["use_doc_unwarping"])

    def test_unavailable_gpu_never_falls_back_to_cpu(self):
        def no_gpu():
            raise OcrExecutionError("GPU_OCR_CUDA_UNAVAILABLE")
        engine=PaddleV6SmallGpuOcr(gpu_check=no_gpu,
                                    pipeline_factory=lambda **kw:None)
        with self.assertRaises(OcrExecutionError) as err:
            engine.read(np.zeros((24,60,3),dtype=np.uint8),
                        roi_name="battle_message")
        self.assertEqual(err.exception.code,"GPU_OCR_CUDA_UNAVAILABLE")

    def test_gpu_reader_default_factory_is_paddle(self):
        from champions_assist.local_event_worker import AsyncBattleEventReader
        from tests.test_local_event_worker import fixture
        profile,image,_=fixture()
        class FakeGpu:
            def read(self,*args,**kw):
                from champions_assist.ocr import OcrRead
                return OcrRead("",None,ENGINE_NAME)
        with patch("champions_assist.ocr_gpu.make_gpu_ocr",
                   return_value=FakeGpu()) as maker:
            reader=AsyncBattleEventReader.with_gpu_paddle(profile)
            try:
                self.assertTrue(reader.enabled)
                maker.assert_called_once()
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
