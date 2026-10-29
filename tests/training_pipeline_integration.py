"""CPU-only training pipeline integration test; run only in the Docker trainer.

This deliberately is not named ``test_*.py`` so the ordinary, dependency-light
development test suite does not install or execute the training stack.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import onnx
import onnxruntime as ort
from PIL import Image

from scripts import train_screen_classifier as trainer
from champions_assist.screen_classifier import (
    EMBEDDED_MANIFEST_KEY, ScreenClassifier, LABEL_SCHEMA_VERSION,
)


class TrainingPipelineIntegrationTests(unittest.TestCase):
    def test_cpu_training_exports_a_loadable_six_class_v3_onnx_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/"学習 data"
            for session_index in range(4):
                session=f"match20261023T130{session_index}00_abc123{session_index}"
                for label_index,label in enumerate(trainer.LABELS):
                    target=root/label/f"{session}__{label_index}.png"
                    target.parent.mkdir(parents=True,exist_ok=True)
                    pixels=np.full((24,24,3),label_index*25,dtype=np.uint8)
                    pixels[:,:,1]=(session_index*45)%256
                    Image.fromarray(pixels).save(target)
            staged=root/"_UNLABELLED"/"match20261023T130000_abc1230"/"ignored.jpg"
            staged.parent.mkdir(parents=True)
            Image.new("RGB",(24,24),(9,8,7)).save(staged)
            report=trainer.validate_dataset(root)
            self.assertTrue(report["ready"])
            self.assertTrue(report["unlabelled_directory_ignored"])
            self.assertTrue(set(report["split"]["training"]["labels"])==set(trainer.LABELS))
            output=Path(temp)/"output"
            result=trainer.train_model(SimpleNamespace(
                data_dir=root,output_dir=output,epochs=1,batch_size=32,lr=0.0003,
                seed=42,confidence=0.90,pretrained=False,
                approve_if_thresholds_pass=False,device="auto"))
            model_path=output/"champions_screen_classifier.onnx"
            manifest=json.loads((output/"champions_screen_classifier.json").read_text("utf-8"))
            self.assertEqual(result["device"],"cpu")
            self.assertFalse(result["pretrained"])
            self.assertFalse(manifest["validated"])
            self.assertEqual(manifest["schema_version"], LABEL_SCHEMA_VERSION)
            self.assertEqual(manifest["labels"],list(trainer.LABELS))
            self.assertEqual(manifest["sha256"],sha256(model_path.read_bytes()).hexdigest())
            graph=onnx.load(str(model_path))
            embedded={prop.key:prop.value for prop in graph.metadata_props}
            self.assertEqual(
                json.loads(embedded[EMBEDDED_MANIFEST_KEY]),
                {key:value for key,value in manifest.items() if key!="sha256"},
            )
            session=ort.InferenceSession(str(model_path),providers=["CPUExecutionProvider"])
            logits=session.run(None,{"rgb":np.zeros((1,3,160,160),dtype=np.float32)})[0]
            self.assertEqual(logits.shape,(1,len(trainer.LABELS)))
            probe=ScreenClassifier(model_path,output/"champions_screen_classifier.json",
                                   experimental_for_labelling=True)
            prediction=probe.predict(np.zeros((32,32,3),dtype=np.uint8),raw_label=True)
            self.assertIn(prediction.label,trainer.LABELS)
            standalone=ScreenClassifier(model_path,None,experimental_for_labelling=True)
            self.assertIn(
                standalone.predict(np.zeros((32,32,3),dtype=np.uint8),raw_label=True).label,
                trainer.LABELS,
            )

    def test_preflight_fails_before_training_when_embedding_import_is_unavailable(self):
        with patch.dict("sys.modules",{"scripts.embed_screen_classifier_metadata":None}):
            with self.assertRaisesRegex(
                trainer.TrainingPreflightError,"metadata/export dependency unavailable"
            ):
                trainer.preflight_training_export()

    def test_existing_artifacts_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            model=root/"champions_screen_classifier.onnx"
            model.write_bytes(b"old model")
            with self.assertRaisesRegex(
                trainer.TrainingPreflightError,"would be overwritten"
            ):
                trainer.ensure_fresh_output(root)
            self.assertEqual(model.read_bytes(),b"old model")

    def test_publish_rolls_back_only_new_files_if_manifest_conflicts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            staged=root/"staging"
            staged.mkdir()
            model=staged/"champions_screen_classifier.onnx"
            manifest=staged/"champions_screen_classifier.json"
            model.write_bytes(b"new model")
            manifest.write_text('{"validated":false}',encoding="utf-8")
            existing=root/"champions_screen_classifier.json"
            existing.write_bytes(b"old manifest")
            with self.assertRaises(FileExistsError):
                trainer.publish_training_export(model,manifest,root)
            self.assertFalse((root/"champions_screen_classifier.onnx").exists())
            self.assertEqual(existing.read_bytes(),b"old manifest")
            self.assertEqual(model.read_bytes(),b"new model")


if __name__=="__main__":
    unittest.main()
