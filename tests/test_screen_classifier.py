"""Offline CPU model contract and menu gate regressions; no real weights/API."""
from __future__ import annotations

from hashlib import sha256
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from champions_assist.screen_classifier import (
    LABELS,ClassifiedMenuGate,ScreenClassifier,ScreenClassifierError,
    ScreenPrediction,load_bundled_classifier,
)


class DummySession:
    def __init__(self, logits=None):
        self.calls=[]
        self.logits=np.array([logits if logits is not None
            else [-10,9,-10,-10,-10,-10,-10]],dtype=np.float32)

    def get_inputs(self):
        return [type("Input",(),{"name":"rgb"})()]

    def run(self, outputs, feeds):
        self.calls.append(feeds)
        return [self.logits]


class ScreenClassifierTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.model=self.root/"champions_screen_classifier.onnx"
        self.meta=self.root/"champions_screen_classifier.json"
        self.model.write_bytes(b"local fake ONNX fixture")
        self.config={
            "schema_version":1,"labels":list(LABELS),"input_size":[160,160],
            "sha256":sha256(self.model.read_bytes()).hexdigest(),
            "mean":[0,0,0],"std":[1,1,1],
            "min_confidence":0.90,"min_margin":0.20,
            "validated":True}
        self.write_config()

    def write_config(self):
        self.meta.write_text(json.dumps(self.config),encoding="utf-8")

    def test_cpu_prediction_uses_pinned_model_rgb_and_preprocess(self):
        session=DummySession()
        klass=ScreenClassifier(self.model,self.meta,session_factory=lambda *a,**k:session)
        self.assertEqual(klass.device,"cpu")
        image=np.full((24,36,3),120,dtype=np.uint8)
        prediction=klass.predict(image)
        self.assertEqual(prediction.label,"TEAM_SELECTION")
        self.assertGreater(prediction.confidence,0.99)
        self.assertEqual(prediction.source,"ONNX_CPU")
        self.assertGreaterEqual(prediction.latency_ms,0)
        self.assertEqual(session.calls[0]["rgb"].shape,(1,3,160,160))
        self.assertAlmostEqual(float(session.calls[0]["rgb"][0,0,0,0]),
                               120/255,places=5)

    def test_uncertain_prediction_abstains(self):
        session=DummySession([0]*len(LABELS))
        klass=ScreenClassifier(self.model,self.meta,session_factory=lambda *a,**k:session)
        self.assertEqual(klass.predict(np.zeros((40,60,3),dtype=np.uint8)).label,"UNKNOWN")

    def test_missing_or_unvalidated_weights_do_not_load(self):
        empty,status=load_bundled_classifier(model_dir=self.root/"empty")
        self.assertIsNone(empty)
        self.assertEqual(status,"MODEL_NOT_BUNDLED")
        self.config["validated"]=False
        self.write_config()
        empty,status=load_bundled_classifier(model_dir=self.root,
                     session_factory=lambda *a,**k:DummySession())
        self.assertIsNone(empty)
        self.assertIn("ScreenClassifierError",status)

    def test_checksum_or_labels_mismatch_blocks_loading(self):
        self.model.write_bytes(b"tampered model")
        with self.assertRaisesRegex(ScreenClassifierError,"CHECKSUM"):
            ScreenClassifier(self.model,self.meta,session_factory=lambda *a,**k:DummySession())
        self.model.write_bytes(b"local fake ONNX fixture")
        self.config["labels"]=list(reversed(LABELS[:-1])) # missing UNKNOWN
        self.write_config()
        with self.assertRaisesRegex(ScreenClassifierError,"LABELS"):
            ScreenClassifier(self.model,self.meta,session_factory=lambda *a,**k:DummySession())

    def test_model_data_never_needs_gpu_in_auto_mode(self):
        options=[]
        def factory(*a,**kw):
            options.extend(kw["providers"])
            return DummySession()
        ScreenClassifier(self.model,self.meta,device="auto",session_factory=factory)
        self.assertEqual(options,["CPUExecutionProvider"])

    def test_local_gate_detects_selection_after_two_frames_without_registration(self):
        gate=ClassifiedMenuGate(stable_frames=2)
        def predicted(label):
            return ScreenPrediction(label,0.98,0.93,4.0)
        self.assertIsNone(gate.observe(predicted("MATCHMAKING"),"MATCHMAKING"))
        self.assertIsNone(gate.observe(predicted("TEAM_SELECTION"),"MATCHMAKING"))
        self.assertEqual(gate.observe(predicted("TEAM_SELECTION"),"MATCHMAKING"),"TEAM_SELECTION")
        self.assertIsNone(gate.observe(predicted("TEAM_SELECTION"),"MATCHMAKING"))
        self.assertIsNone(gate.observe(predicted("UNKNOWN"),"MATCHMAKING"))
        self.assertIsNone(gate.observe(predicted("UNKNOWN"),"MATCHMAKING"))
        self.assertIsNone(gate.observe(predicted("TEAM_SELECTION"),"MATCHMAKING"))
        self.assertEqual(gate.observe(predicted("TEAM_SELECTION"),"MATCHMAKING"),"TEAM_SELECTION")

    def test_local_gate_blocks_irrelevant_phase_and_rearms_for_next_battle_command(self):
        gate=ClassifiedMenuGate()
        def p(label):
            return ScreenPrediction(label,0.99,0.95,2.0)
        self.assertIsNone(gate.observe(p("TEAM_SELECTION"),"BATTLE"))
        self.assertIsNone(gate.observe(p("MOVE_MENU"),"BATTLE"))
        self.assertEqual(gate.observe(p("MOVE_MENU"),"BATTLE"),"MOVE_MENU")
        self.assertIsNone(gate.observe(p("MOVE_MENU"),"BATTLE"))
        self.assertIsNone(gate.observe(p("BATTLE_EVENT"),"BATTLE"))
        self.assertIsNone(gate.observe(p("BATTLE_EVENT"),"BATTLE"))
        self.assertIsNone(gate.observe(p("PARTY_MENU"),"BATTLE"))
        self.assertEqual(gate.observe(p("PARTY_MENU"),"BATTLE"),"PARTY_MENU")


if __name__=="__main__":
    unittest.main()
