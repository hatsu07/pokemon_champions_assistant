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

import numpy as np
import onnxruntime as ort
from PIL import Image

from scripts import train_screen_classifier as trainer


class TrainingPipelineIntegrationTests(unittest.TestCase):
    def test_cpu_training_exports_a_loadable_seven_class_onnx_model(self):
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
            self.assertEqual(manifest["labels"],list(trainer.LABELS))
            self.assertEqual(manifest["sha256"],sha256(model_path.read_bytes()).hexdigest())
            session=ort.InferenceSession(str(model_path),providers=["CPUExecutionProvider"])
            logits=session.run(None,{"rgb":np.zeros((1,3,160,160),dtype=np.float32)})[0]
            self.assertEqual(logits.shape,(1,len(trainer.LABELS)))


if __name__=="__main__":
    unittest.main()
