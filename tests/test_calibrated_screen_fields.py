"""Synthetic coverage for ROI placements inferred from eight user screenshots.

No private screenshot bytes are committed to the repository.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from champions_assist.calibrated_screen_fields import (
    FieldRegion, ScreenFieldProfile, parse_field_text,
    observe_screen_fields, candidates_for_prompt,
)
from champions_assist.ocr import MappingOcrEngine, OcrRead


class SampleScreenFieldTests(unittest.TestCase):
    def setUp(self):
        self.profile = ScreenFieldProfile.from_user_samples_960x572()
        self.image = np.zeros((572, 960, 3), dtype=np.uint8)

    def test_private_profile_roundtrip_and_invalid_coordinates(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "fields.json"
            original = self.profile.as_dict()
            path.write_text(json.dumps(original), encoding="utf-8")
            restored = ScreenFieldProfile.from_json(path)
            self.assertEqual(restored.reference_resolution, (960,572))
            self.assertEqual(restored.regions, self.profile.regions)
            original["regions"][0]["bounds_px"] = [-1, 5, 20, 30]
            path.write_text(json.dumps(original), encoding="utf-8")
            with self.assertRaises(ValueError):
                ScreenFieldProfile.from_json(path)

    def test_grounded_geometry_and_screenshot_shape_guard(self):
        self.assertTrue(self.profile.compatible(self.image))
        self.assertFalse(self.profile.compatible(np.zeros((540, 960, 3), dtype=np.uint8)))
        names = {r.name for r in self.profile.regions}
        for expected in ("own_hp_fraction", "opponent_hp_percent",
                         "selection_opponent_list", "battle_message",
                         "opponent_effect_popup", "own_item_popup"):
            self.assertIn(expected, names)

    def test_fraction_percent_and_timer_remain_unconfirmed(self):
        regions = {r.name: r for r in self.profile.regions}
        own = parse_field_text(regions["own_hp_fraction"], "117/131", frame_id="capture-12")
        opp = parse_field_text(regions["opponent_hp_percent"], "97%", frame_id="capture-12")
        timer = parse_field_text(regions["turn_command_timer"], "COMMAND 45", frame_id="capture-12")
        self.assertEqual(own.value, {"current":117,"maximum":131})
        self.assertEqual(opp.value, {"percent_displayed":97})
        self.assertEqual(timer.value, 45)
        self.assertEqual(own.certainty, "CANDIDATE")
        self.assertIsNone(parse_field_text(regions["own_hp_fraction"], "250/100", frame_id="x"))
        self.assertIsNone(parse_field_text(regions["opponent_hp_percent"], "128%", frame_id="x"))
        self.assertIsNone(parse_field_text(regions["opponent_hp_percent"], "88% 97%", frame_id="x"))

    def test_event_message_is_not_promoted_to_mechanic(self):
        region = next(r for r in self.profile.regions if r.name == "opponent_effect_popup")
        observed = parse_field_text(region, "マスカーニャの へんげんじざい", frame_id="f")
        self.assertEqual(observed.value, "マスカーニャの へんげんじざい")
        self.assertEqual(observed.field, "screen_text.opponent_effect_popup")
        self.assertEqual(observed.certainty, "CANDIDATE")
        self.assertNotIn("ability", str(observed.value).lower())

    def test_roi_ocr_uses_screen_label_and_caps_attempts(self):
        selected = [r for r in self.profile.regions if "TURN_ACTION" in r.applicable_labels][:2]
        readings = []
        for region, raw in zip(selected, ("112/112", "100%")):
            readings.append((region.name,self.profile.crop(self.image,region),OcrRead(raw,0.95,"TEST")))
        engine = MappingOcrEngine.for_images(readings)
        observations = observe_screen_fields(
            self.image, label="TURN_ACTION", frame_id="f-1",
            ocr_engine=engine,profile=self.profile,max_rois=2,
        )
        self.assertEqual(len(observations), 2)
        self.assertEqual(len(engine.calls), 2)
        self.assertEqual(len(candidates_for_prompt(observations)), 2)
        engine.calls.clear()
        numbers_without_class = observe_screen_fields(
            self.image, label="HUD_UNCLASSIFIED", frame_id="f-unclassified",
            ocr_engine=engine, profile=self.profile,
        )
        self.assertEqual(len(numbers_without_class), 2)
        self.assertEqual(engine.calls, ["own_hp_fraction", "opponent_hp_percent"])
        engine.calls.clear()
        self.assertEqual(observe_screen_fields(
            self.image,label="UNKNOWN",frame_id="f-2",
            ocr_engine=engine,profile=self.profile), ())
        self.assertEqual(engine.calls, [])
        self.assertEqual(observe_screen_fields(
            self.image[:500],label="TURN_ACTION",frame_id="f-3",
            ocr_engine=engine,profile=self.profile), ())


if __name__ == "__main__":
    unittest.main()
