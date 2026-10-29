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
                         "battle_message",
                         "opponent_effect_popup", "own_item_popup"):
            self.assertIn(expected, names)

    def test_unused_screen_rois_are_not_ocrd_even_with_old_calibration(self):
        excluded = {
            "system_message", "selection_own_list", "selection_opponent_list",
            "switch_party_list", "switch_detail_panel", "turn_command_timer",
        }
        self.assertIn("system_message",{r.name for r in self.profile.regions})
        self.assertTrue((excluded-{"system_message"}).isdisjoint(
            {r.name for r in self.profile.regions}))
        legacy_regions = (
            FieldRegion("system_message", "MESSAGE", (317,47,667,116),
                        ("BATTLE_EVENT", "TURN_ACTION")),
            FieldRegion("selection_own_list", "TEXT", (49,89,310,474),
                        ("TEAM_SELECTION",)),
            FieldRegion("selection_opponent_list", "TEXT", (766,88,941,477),
                        ("TEAM_SELECTION",)),
            FieldRegion("switch_party_list", "TEXT", (26,123,307,500),
                        ("SWITCH_SELECTION",)),
            FieldRegion("switch_detail_panel", "TEXT", (320,58,729,508),
                        ("SWITCH_SELECTION",)),
            FieldRegion("turn_command_timer", "TIMER", (790,123,946,166),
                        ("TURN_ACTION",)),
        )
        legacy = ScreenFieldProfile(
            self.profile.reference_resolution,
            tuple(r for r in self.profile.regions if r.name!="system_message") + legacy_regions,
            "OLD_CALIBRATION",
            False,
        )
        # A legacy profile can roundtrip without reviving disallowed OCR.
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"legacy.json"
            path.write_text(json.dumps(legacy.as_dict()),encoding="utf-8")
            legacy=ScreenFieldProfile.from_json(path)
        engine=MappingOcrEngine.for_images([])
        for label in ("TEAM_SELECTION","SWITCH_SELECTION","TURN_ACTION","BATTLE_EVENT"):
            engine.calls.clear()
            observe_screen_fields(self.image, label=label, frame_id="f",
                                  ocr_engine=engine, profile=legacy)
            self.assertTrue(excluded.isdisjoint(engine.calls))

    def test_scaled_full_frame_capture_has_compatible_ocr_crops(self):
        import cv2
        self.image[412:505,141:540]=150
        large=cv2.resize(self.image,(3343,2001))
        self.assertTrue(self.profile.compatible(large))
        roi=next(r for r in self.profile.regions if r.name=="battle_message")
        scaled=self.profile.crop(large,roi)
        # Preserve twice the sample-region detail (capped for bounded OCR)
        # instead of downsizing the native game capture to 960x572 glyphs.
        sample_h=roi.bounds_px[3]-roi.bounds_px[1]
        sample_w=roi.bounds_px[2]-roi.bounds_px[0]
        self.assertEqual(scaled.shape[:2],(2*sample_h,2*sample_w))
        self.assertGreater(float(scaled.mean()),0.0)
        self.assertFalse(self.profile.compatible(np.zeros((900,1600,3),
                                                          dtype=np.uint8)))

    def test_fraction_and_percent_remain_unconfirmed(self):
        regions = {r.name: r for r in self.profile.regions}
        own = parse_field_text(regions["own_hp_fraction"], "117/131", frame_id="capture-12")
        opp = parse_field_text(regions["opponent_hp_percent"], "97%", frame_id="capture-12")
        self.assertEqual(own.value, {"current":117,"maximum":131})
        self.assertEqual(opp.value, {"percent_displayed":97})
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
