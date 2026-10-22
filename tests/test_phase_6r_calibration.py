"""Offline tests for real-frame calibration diagnostics (no user media)."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

import numpy as np

from champions_assist.ui_calibration import LocalUiCalibration


def image(marker: int) -> np.ndarray:
    frame = np.full((240, 400, 3), 15, dtype=np.uint8)
    frame[20:90, 20:160] = (marker, 40, 190)
    frame[100:180, 190:370] = (10, 155, 40)
    for x in range(20, 160, 12):
        frame[20:90, x:x+2] = (255, 255, 255)
    return frame


class LocalCalibrationTests(unittest.TestCase):
    def test_register_then_detect_on_independent_frame(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LocalUiCalibration(Path(temp))
            first = image(160)
            other = image(163)
            store.add_template(first, label="MOVE_MENU", anchor=(.05, .08, .4, .375))
            self.assertEqual(store.classify(first).status, "TRAINING_FRAME_REUSED")
            result = store.classify(other)
            self.assertEqual(result.template_count, 1)
            self.assertIn(result.status, {"CANDIDATE_ONLY","LOW_CONFIDENCE"})
            self.assertEqual(store.list_templates()[0]["label"], "MOVE_MENU")

    def test_reject_invalid_roi_and_unsupported_label(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LocalUiCalibration(Path(temp))
            with self.assertRaises(ValueError):
                store.add_template(image(120), label="UNKNOWN", anchor=(.1,.1,.9,.9))
            with self.assertRaises(ValueError):
                store.add_template(image(120), label="PARTY_MENU", anchor=(.5,.5,.5,.6))
            self.assertEqual(store.list_templates(), [])

    def test_explicit_viewport_crop(self):
        frame = image(110)
        cropped = LocalUiCalibration.crop_viewport(frame,(0.1,0.1,0.9,0.9))
        self.assertEqual(cropped.shape[:2], (192, 320))
        with self.assertRaises(ValueError):
            LocalUiCalibration.crop_viewport(frame,None)


if __name__ == "__main__":
    unittest.main()
