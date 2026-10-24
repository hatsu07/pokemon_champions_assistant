"""Synthetic-only regression coverage for local calibrated visual candidates."""
from __future__ import annotations

import unittest

import cv2
import numpy as np

from champions_assist.battle_visual_fields import (
    HpBarCalibration, IconCalibration, VisualCandidateStabilizer,
    estimate_hp_bar, recognize_icon,
)
from champions_assist.roi import NormalizedROI


class LocalBattleVisualFieldsTests(unittest.TestCase):
    def test_hp_interval_is_candidate_and_not_an_exact_number(self):
        image = np.zeros((20, 100, 3), dtype=np.uint8)
        image[2:18, :60] = (20, 220, 30)
        calibration = HpBarCalibration(
            "opponent", NormalizedROI("HP", 0, 0, 1, 1),
            (30, 100, 100), (95, 255, 255),
        )
        item = estimate_hp_bar(image, calibration, frame_id="frame-1")
        self.assertIsNotNone(item)
        self.assertEqual(item.certainty, "CANDIDATE")
        self.assertEqual(item.value["percent_range"], [55, 65])
        self.assertEqual(item.evidence_ids, ("frame-1",))
        stable = VisualCandidateStabilizer(required_frames=2)
        self.assertIsNone(stable.observe(item))
        self.assertIsNone(stable.observe(item))  # same frame isn't independent
        item2 = estimate_hp_bar(image, calibration, frame_id="frame-2")
        repeated = stable.observe(item2)
        self.assertEqual(repeated.certainty, "CANDIDATE")
        self.assertEqual(repeated.evidence_ids, ("frame-1", "frame-2"))

    def test_hp_absent_or_fragmented_fill_fails_closed(self):
        calibration = HpBarCalibration(
            "own", NormalizedROI("HP", 0, 0, 1, 1),
            (30, 100, 100), (95, 255, 255),
        )
        self.assertIsNone(estimate_hp_bar(np.zeros((20, 100, 3), np.uint8), calibration, frame_id="x"))
        fragmented = np.zeros((20, 100, 3), dtype=np.uint8)
        fragmented[:, :30] = (20, 220, 30)
        fragmented[:, 50:70] = (20, 220, 30)
        self.assertIsNone(estimate_hp_bar(fragmented, calibration, frame_id="y"))

    def test_icon_requires_clear_top1_and_top2(self):
        rng = np.random.default_rng(17)
        icon = rng.integers(0, 256, size=(24, 24, 3), dtype=np.uint8)
        distractor = rng.integers(0, 256, size=(24, 24, 3), dtype=np.uint8)
        roi = NormalizedROI("ICON", 0, 0, 1, 1)
        choice = recognize_icon(
            icon, IconCalibration("opp-lead", roi, {"A": icon, "B": distractor}),
            frame_id="icon1",
        )
        self.assertEqual(choice.value, "A")
        self.assertEqual(choice.certainty, "CANDIDATE")
        ambiguous = recognize_icon(
            icon, IconCalibration("opp-lead", roi, {"A": icon, "B": icon.copy()}),
            frame_id="icon2",
        )
        self.assertIsNone(ambiguous)

    def test_flat_icon_and_bad_inputs_are_not_recognized(self):
        blank = np.zeros((24, 24, 3), dtype=np.uint8)
        roi = NormalizedROI("ICON", 0, 0, 1, 1)
        self.assertIsNone(recognize_icon(blank, IconCalibration("x", roi, {"A": blank}), frame_id="i"))
        with self.assertRaises(ValueError):
            estimate_hp_bar(blank[:, :, 0], HpBarCalibration("own", roi, (0,0,0), (179,255,255)), frame_id="bad")


if __name__ == "__main__":
    unittest.main()
