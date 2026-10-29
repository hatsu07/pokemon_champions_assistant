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
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/"output"
            with patch.dict("sys.modules",{"scripts.embed_screen_classifier_metadata":None}):
                with self.assertRaisesRegex(
                    trainer.TrainingPreflightError,"metadata/export dependency unavailable"
                ):
                    # Must fail before dataset scans, Torch setup or output creation.
                    trainer.train_model(SimpleNamespace(output_dir=output))
            self.assertFalse(output.exists())

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

    def test_output_mount_hard_link_failure_aborts_before_training(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/"output"
            with patch.object(trainer.os,"link",
                              side_effect=OSError("hard links unavailable on this mount")):
                with self.assertRaisesRegex(
                    trainer.TrainingPreflightError,"does not support safe hard-link"
                ):
                    # No dataset, Torch or epochs available; preflight must win.
                    trainer.train_model(SimpleNamespace(output_dir=output))
            self.assertTrue(output.is_dir())
            self.assertFalse(list(output.iterdir()),"Probe artifacts must be cleaned")
            self.assertFalse((output/"champions_screen_classifier.onnx").exists())

    def test_output_mount_hard_link_preflight_cleans_probe_on_success(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/"output"
            trainer.ensure_fresh_output(output)
            self.assertEqual(list(output.iterdir()),[])

    def test_publish_order_places_json_before_discoverable_onnx(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            staged=root/"staging"
            staged.mkdir()
            model=staged/"champions_screen_classifier.onnx"
            manifest=staged/"champions_screen_classifier.json"
            model.write_bytes(b"complete model")
            manifest.write_bytes(b'{"validated":false}')
            actual_link=trainer.os.link
            calls=[]

            def recording_link(source,target):
                # Just before the ONNX link, a forced process kill would leave
                # JSON only. An ONNX-only loader cannot discover a model yet.
                calls.append(Path(target).name)
                if len(calls)==2:
                    self.assertEqual(calls[0],"champions_screen_classifier.json")
                    self.assertTrue((root/"champions_screen_classifier.json").exists())
                    self.assertFalse((root/"champions_screen_classifier.onnx").exists())
                return actual_link(source,target)

            with patch.object(trainer.os,"link",side_effect=recording_link):
                trainer.publish_training_export(model,manifest,root)
            self.assertEqual(calls,[
                "champions_screen_classifier.json","champions_screen_classifier.onnx"
            ])
            self.assertEqual((root/"champions_screen_classifier.onnx").read_bytes(),
                             b"complete model")

    def test_publish_rolls_back_json_if_onnx_link_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            staged=root/"staging"
            staged.mkdir()
            model=staged/"champions_screen_classifier.onnx"
            manifest=staged/"champions_screen_classifier.json"
            model.write_bytes(b"safe model")
            manifest.write_bytes(b"safe JSON")
            original=trainer.os.link
            count=0

            def fail_second_link(source,target):
                nonlocal count
                count+=1
                if count==2:
                    raise OSError("hard link failed after JSON publication")
                return original(source,target)

            with patch.object(trainer.os,"link",side_effect=fail_second_link):
                with self.assertRaises(OSError):
                    trainer.publish_training_export(model,manifest,root)
            self.assertEqual(count,2)
            self.assertFalse((root/"champions_screen_classifier.json").exists())
            self.assertFalse((root/"champions_screen_classifier.onnx").exists())
            self.assertEqual(model.read_bytes(),b"safe model")
            self.assertEqual(manifest.read_bytes(),b"safe JSON")

    def test_existing_model_is_preserved_if_second_publication_link_conflicts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            staged=root/"staging"
            staged.mkdir()
            model=staged/"champions_screen_classifier.onnx"
            manifest=staged/"champions_screen_classifier.json"
            model.write_bytes(b"new model")
            manifest.write_bytes(b"new manifest")
            existing=root/"champions_screen_classifier.onnx"
            existing.write_bytes(b"old model")
            with self.assertRaises(FileExistsError):
                trainer.publish_training_export(model,manifest,root)
            self.assertEqual(existing.read_bytes(),b"old model")
            self.assertFalse((root/"champions_screen_classifier.json").exists())
            self.assertEqual(manifest.read_bytes(),b"new manifest")

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
