"""Offline CPU model contract and menu gate regressions; no real weights/API."""
from __future__ import annotations

from hashlib import sha256
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from champions_assist.screen_classifier import (
    LABELS,ClassifiedMenuGate,ScreenClassifier,ScreenClassifierError,
    ScreenPrediction,load_bundled_classifier,
)


class DummySession:
    def __init__(self, logits=None):
        self.calls=[]
        self.logits=np.array([logits if logits is not None
            else [9,-10,-10,-10,-10]],dtype=np.float32)

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
            "schema_version":2,"labels":list(LABELS),"input_size":[160,160],
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
        self.config["labels"]=list(reversed(LABELS[:-1])) # missing OTHER
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

    def test_scene_fallback_is_bounded_without_any_model_weights(self):
        from champions_assist.turn_capture import AutomaticVisualProbe
        tick=[0.0]
        probe=AutomaticVisualProbe(clock=lambda:tick[0],min_gap=2.0,
                                   stable_frames=2,matchmaking_recheck=12.0)
        first=np.zeros((100,180,3),dtype=np.uint8)
        selection=np.full_like(first,255)
        self.assertTrue(probe.observe(first,"MATCHMAKING"))
        tick[0]=0.3
        self.assertFalse(probe.observe(selection,"MATCHMAKING"))
        tick[0]=2.1
        self.assertFalse(probe.observe(selection,"MATCHMAKING"))
        tick[0]=2.4
        self.assertTrue(probe.observe(selection,"MATCHMAKING"))
        tick[0]=2.7
        self.assertFalse(probe.observe(selection,"MATCHMAKING"))
        tick[0]=15.0
        self.assertTrue(probe.observe(selection,"MATCHMAKING"))
        self.assertFalse(probe.observe(selection,"SELECTION"))

    def test_training_split_is_session_scoped(self):
        from scripts.train_screen_classifier import split_by_session
        samples=[(Path(f"{name}/match{sid}__{k}.png"),idx,f"match{sid}")
                 for sid in range(20) for idx,name in enumerate(LABELS)
                 for k in range(2)]
        training,validation,held_out=split_by_session(samples)
        self.assertTrue(held_out)
        self.assertTrue({r[2] for r in validation}.isdisjoint({r[2] for r in training}))
        self.assertEqual({r[1] for r in validation},set(range(len(LABELS))))

    def test_cuda_device_choice_fails_closed(self):
        from scripts.train_screen_classifier import _resolve_device
        from types import SimpleNamespace
        def torch_stub(available):
            return SimpleNamespace(device=lambda kind:SimpleNamespace(type=kind),
                                   cuda=SimpleNamespace(is_available=lambda:available))
        self.assertEqual(_resolve_device(SimpleNamespace(device="cpu"),torch_stub(True)).type,"cpu")
        self.assertEqual(_resolve_device(SimpleNamespace(device="auto"),torch_stub(False)).type,"cpu")
        self.assertEqual(_resolve_device(SimpleNamespace(device="auto"),torch_stub(True)).type,"cuda")
        self.assertEqual(_resolve_device(SimpleNamespace(device="cuda"),torch_stub(True)).type,"cuda")
        with self.assertRaisesRegex(ValueError,"CUDA requested"):
            _resolve_device(SimpleNamespace(device="cuda"),torch_stub(False))

    def test_dataset_preflight_reports_session_safe_labelled_data(self):
        from scripts.train_screen_classifier import validate_dataset
        root=self.root/"学習 データ"
        for session_index in range(4):
            session=f"match20261023T120{session_index}00_abc123{session_index}"
            for label_index,label in enumerate(LABELS):
                target=root/label/f"{session}__{label_index}.png"
                target.parent.mkdir(parents=True,exist_ok=True)
                Image.new("RGB",(12,12),(label_index*20,session_index*30,80)).save(target)
        staged=root/"_UNLABELLED"/"match20261023T120000_abc12300"/"raw.jpg"
        staged.parent.mkdir(parents=True)
        Image.new("RGB",(12,12),(1,2,3)).save(staged)
        report=validate_dataset(root)
        self.assertTrue(report["ready"])
        self.assertTrue(report["unlabelled_directory_ignored"])
        self.assertEqual(report["labels"]["TEAM_SELECTION"],{"images":4,"sessions":4})
        self.assertEqual(report["split"]["validation"]["labels"]["OTHER"]["images"],1)

    def test_split_explains_when_a_label_has_only_one_session(self):
        from scripts.train_screen_classifier import split_by_session
        samples=[]
        for session_index in range(4):
            for label_index,label in enumerate(LABELS):
                if label=="TEAM_SELECTION" and session_index:
                    continue
                samples.append((Path(f"{label}/match{session_index}__{label_index}.png"),
                                label_index,f"match{session_index}"))
        with self.assertRaisesRegex(ValueError,"TEAM_SELECTION"):
            split_by_session(samples)

    def test_local_gate_detects_selection_after_two_frames_without_registration(self):
        gate=ClassifiedMenuGate(stable_frames=2)
        def predicted(label):
            return ScreenPrediction(label,0.98,0.93,4.0)
        self.assertIsNone(gate.observe(predicted("OTHER"),"MATCHMAKING"))
        self.assertIsNone(gate.observe(predicted("TEAM_SELECTION"),"MATCHMAKING"))
        self.assertEqual(gate.observe(predicted("TEAM_SELECTION"),"MATCHMAKING"),"TEAM_SELECTION")
        self.assertIsNone(gate.observe(predicted("TEAM_SELECTION"),"MATCHMAKING"))
        self.assertIsNone(gate.observe(predicted("OTHER"),"MATCHMAKING"))
        self.assertIsNone(gate.observe(predicted("UNKNOWN"),"MATCHMAKING"))
        self.assertIsNone(gate.observe(predicted("TEAM_SELECTION"),"MATCHMAKING"))
        self.assertEqual(gate.observe(predicted("TEAM_SELECTION"),"MATCHMAKING"),"TEAM_SELECTION")

    def test_local_gate_blocks_irrelevant_phase_and_rearms_for_next_battle_command(self):
        gate=ClassifiedMenuGate()
        def p(label):
            return ScreenPrediction(label,0.99,0.95,2.0)
        self.assertIsNone(gate.observe(p("TEAM_SELECTION"),"BATTLE"))
        self.assertIsNone(gate.observe(p("TURN_ACTION"),"BATTLE"))
        self.assertEqual(gate.observe(p("TURN_ACTION"),"BATTLE"),"MOVE_MENU")
        self.assertIsNone(gate.observe(p("MOVE_MENU"),"BATTLE"))
        self.assertIsNone(gate.observe(p("BATTLE_EVENT"),"BATTLE"))
        self.assertIsNone(gate.observe(p("BATTLE_EVENT"),"BATTLE"))
        self.assertIsNone(gate.observe(p("SWITCH_SELECTION"),"BATTLE"))
        self.assertEqual(gate.observe(p("SWITCH_SELECTION"),"BATTLE"),"PARTY_MENU")


if __name__=="__main__":
    unittest.main()
