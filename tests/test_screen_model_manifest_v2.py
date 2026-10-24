"""Regression: five-class training output, archive upgrade and loader contract.

Synthetic ONNX bytes are intentionally used with an injected dummy runtime.
No real model weights, dataset images or network calls are required.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from scripts.train_screen_classifier import build_training_manifest
from scripts.upgrade_five_class_model_manifest import (
    inspect_legacy_five_class_model, upgrade_legacy_five_class_model,
    MODEL_NAME, MANIFEST_NAME,
)
from champions_assist.screen_classifier import (
    LABELS, LABEL_SCHEMA_VERSION, ScreenClassifier, ScreenClassifierError,
)


class DummySession:
    def get_inputs(self):
        return [type("Input", (), {"name": "rgb"})()]

    def run(self, _outputs, _feed):
        return [np.array([[8.0, 0.0, 0.0, 0.0, 0.0]], dtype=np.float32)]


class ScreenModelManifestV2Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.old = self.root / "run-archived"
        self.old.mkdir()
        self.model = self.old / MODEL_NAME
        self.manifest = self.old / MANIFEST_NAME
        self.model.write_bytes(b"synthetic model bytes, not a real ONNX")
        self.metrics = {name: {"support": 42, "recall": 0.96,
                                "precision": 0.98, "false_positives": 1}
                        for name in LABELS}

    def training_manifest(self, *, pass_gate=False, approved=False):
        return build_training_manifest(
            model_path=self.model,
            mean=[0, 0, 0], std=[1, 1, 1], metrics=self.metrics,
            held_out_sessions=2, quality_gate_passed=pass_gate,
            approve_if_thresholds_pass=approved, confidence=0.9,
            device="cuda", pretrained=True,
        )

    def legacy_manifest(self):
        old = self.training_manifest()
        old["schema_version"] = 1
        self.manifest.write_text(json.dumps(old), encoding="utf-8")
        return old

    def dummy_loader(self, directory, *, experimental=True):
        return ScreenClassifier(
            directory / MODEL_NAME, directory / MANIFEST_NAME,
            session_factory=lambda *_a, **_k: DummySession(),
            experimental_for_labelling=experimental,
        )

    def test_new_training_manifest_uses_loader_version_and_same_five_labels(self):
        manifest = self.training_manifest()
        self.assertEqual(manifest["schema_version"], LABEL_SCHEMA_VERSION)
        self.assertEqual(manifest["labels"], list(LABELS))
        self.assertEqual(manifest["sha256"], sha256(self.model.read_bytes()).hexdigest())
        self.assertFalse(manifest["validated"])
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(self.dummy_loader(self.old).labels, LABELS if isinstance(LABELS, tuple) else tuple(LABELS))
        self.assertEqual(self.dummy_loader(self.old).predict(
            np.zeros((20, 24, 3), np.uint8)
        ).label, "TEAM_SELECTION")

    def test_no_implicit_quality_approval(self):
        self.assertFalse(self.training_manifest(pass_gate=True, approved=False)["validated"])
        self.assertFalse(self.training_manifest(pass_gate=False, approved=True)["validated"])
        self.assertTrue(self.training_manifest(pass_gate=True, approved=True)["validated"])

    def test_legacy_five_class_archive_can_be_copied_without_retraining(self):
        original = self.legacy_manifest()
        original_bytes = self.manifest.read_bytes()
        new = self.root / "run-repaired"
        dry = upgrade_legacy_five_class_model(self.old, new)
        self.assertEqual(dry["status"], "DRY_RUN")
        self.assertFalse(new.exists())
        with self.assertRaisesRegex(ScreenClassifierError, "MANIFEST"):
            self.dummy_loader(self.old)
        created = upgrade_legacy_five_class_model(self.old, new, apply=True)
        self.assertEqual(created["status"], "CREATED_EXPERIMENTAL_ONLY")
        converted = json.loads((new / MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(converted["schema_version"], 2)
        self.assertEqual(converted["labels"], original["labels"])
        self.assertFalse(converted["validated"])
        self.assertEqual((new / MODEL_NAME).read_bytes(), self.model.read_bytes())
        self.assertEqual(self.manifest.read_bytes(), original_bytes)
        self.assertEqual(self.dummy_loader(new).predict(
            np.zeros((16, 16, 3), np.uint8)
        ).label, "TEAM_SELECTION")
        with self.assertRaisesRegex(ScreenClassifierError, "NOT_VALIDATED"):
            self.dummy_loader(new, experimental=False)
        with self.assertRaisesRegex(ValueError, "destination"):
            upgrade_legacy_five_class_model(self.old, new, apply=True)

    def test_upgrade_does_not_promote_even_a_legacy_validated_flag(self):
        src = self.legacy_manifest()
        src["validated"] = True
        self.manifest.write_text(json.dumps(src), encoding="utf-8")
        new = self.root / "experiment-only"
        upgrade_legacy_five_class_model(self.old, new, apply=True)
        self.assertFalse(json.loads((new / MANIFEST_NAME).read_text())["validated"])

    def test_refuse_legacy_seven_label_model_and_reordered_outputs(self):
        src = self.legacy_manifest()
        src["labels"] = ["MATCHMAKING", "TEAM_SELECTION", "MOVE_MENU",
                         "PARTY_MENU", "BATTLE_EVENT", "RESULT", "UNKNOWN"]
        self.manifest.write_text(json.dumps(src), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "seven-class"):
            inspect_legacy_five_class_model(self.old)
        src["labels"] = list(reversed(LABELS))
        self.manifest.write_text(json.dumps(src), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "reordered"):
            upgrade_legacy_five_class_model(self.old, self.root / "new", apply=True)
        self.assertFalse((self.root / "new").exists())

    def test_refuse_invalid_checksum_and_malformed_shape_and_wrong_destination(self):
        src = self.legacy_manifest()
        src["sha256"] = "0" * 64
        self.manifest.write_text(json.dumps(src), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            inspect_legacy_five_class_model(self.old)
        src["sha256"] = sha256(self.model.read_bytes()).hexdigest()
        src["input_size"] = [224, 224]
        self.manifest.write_text(json.dumps(src), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "input shape"):
            inspect_legacy_five_class_model(self.old)
        src["input_size"] = [160, 160]
        self.manifest.write_text(json.dumps(src), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sibling"):
            upgrade_legacy_five_class_model(self.old, self.old / "nested", apply=True)
        self.assertFalse((self.old / "nested").exists())


if __name__ == "__main__":
    unittest.main()
