"""Issue #124: deterministic ruby crop, one OCR, conservative provenance.

Synthetic frames only. Actual Champions screenshot accuracy is a separate test.
"""
from __future__ import annotations

import unittest
from time import monotonic, sleep

import cv2
import numpy as np

from champions_assist.battle_message_ruby import prepare_battle_message
from champions_assist.calibrated_screen_fields import FieldRegion, ScreenFieldProfile
from champions_assist.local_event_worker import AsyncBattleEventReader
from champions_assist.ocr import OcrRead
from champions_assist.ocr_diagnostics import inspect_battle_event_image


_REGION = FieldRegion("battle_message", "MESSAGE", (141, 412, 540, 505),
                      ("BATTLE_EVENT",))


def sample(validated=False):
    return ScreenFieldProfile((960, 572), (_REGION,),
                              "SYNTHETIC_ISSUE_124", validated, validated)


def synthetic_frame():
    frame = np.full((572, 960, 3), 180, np.uint8)
    # Ruby color signature must not reach OCR.
    frame[424:430, 200:440] = (245, 10, 10)
    frame[456:463, 200:440] = (245, 10, 10)
    # Long lines, punctuation at right edge, and second line survive.
    frame[431:453, 150:950] = (10, 30, 220)
    frame[465:487, 180:950] = (10, 210, 30)
    # Bright neutral leading glyphs used by the per-frame layout safety gate.
    frame[434:450, 160:190] = 235
    frame[467:482, 160:190] = 235
    return frame


class CountingEngine:
    engine_name = "injected-local"
    provider = "CPUExecutionProvider"

    def __init__(self, text="との勝負に負けた！"):
        self.text = text
        self.calls = []

    def read(self, image, *, roi_name):
        self.calls.append((roi_name, image.copy()))
        return OcrRead(self.text, 0.93, self.engine_name)


class BattleMessageRubyTests(unittest.TestCase):
    def test_ruby_removed_both_body_lines_and_right_edge_preserved(self):
        frame = synthetic_frame()
        crop, data = prepare_battle_message(
            frame, sample(), _REGION, preview_unvalidated=True)
        self.assertEqual(data["preprocess"], "BODY_LINES_PREVIEW_ONLY")
        self.assertEqual(data["body_rois"], [
            [141, 430, 540, 454], [141, 464, 540, 489]])
        self.assertEqual(crop.shape, (59, 399, 3))
        self.assertTrue(np.any(np.all(crop == (10, 30, 220), axis=2)))
        self.assertTrue(np.any(np.all(crop == (10, 210, 30), axis=2)))
        self.assertFalse(np.any(np.all(crop == (245, 10, 10), axis=2)))
        self.assertTrue(np.array_equal(crop[1, 539 - 141], frame[431, 539]))
        self.assertFalse(data["game_validated"])

    def test_normalized_rows_at_three_same_aspect_resolutions(self):
        original = synthetic_frame()
        for scale in (1, 1.5, 2):
            with self.subTest(scale=scale):
                img = cv2.resize(original, (round(960 * scale),
                                 round(572 * scale)), interpolation=cv2.INTER_NEAREST)
                crop, data = prepare_battle_message(
                    img, sample(True), _REGION)
                self.assertTrue(data["ruby_excluded"])
                self.assertEqual(data["preprocess"], "BODY_LINES_CALIBRATED")
                self.assertEqual(len(data["body_rois"]), 2)
                self.assertEqual(data["body_rois"], [
                    [round(v * scale) for v in (141, 430, 540, 454)],
                    [round(v * scale) for v in (141, 464, 540, 489)]])
                self.assertLessEqual(crop.shape[1], 798)

    def test_unsafe_geometry_never_performs_body_extraction(self):
        frame = synthetic_frame()
        wrong_region = FieldRegion("battle_message", "MESSAGE",
                                   (150, 412, 540, 505), ("BATTLE_EVENT",))
        for img, profile, region, status in (
            (frame, sample(), _REGION, "UNVALIDATED_LIVE_DISABLED"),
            (frame, sample(True), wrong_region, "UNRECOGNIZED_UI_GEOMETRY"),
            (frame, ScreenFieldProfile((960, 572), (_REGION,),
                  "GENERAL_VALIDATION_ONLY", True, False), _REGION,
             "RUBY_BANDS_UNVALIDATED_LIVE_DISABLED"),
            (np.pad(frame, ((20, 20), (0, 0), (0, 0))),
             sample(True), _REGION, "VIEWPORT_MISMATCH"),
        ):
            crop, info = prepare_battle_message(img, profile, region)
            self.assertFalse(info["ruby_excluded"])
            self.assertEqual(info["preprocess"], status)
            self.assertGreater(crop.size, 0)

    def test_same_dimension_shifted_text_abstains(self):
        image = synthetic_frame()
        for delta in (-10, -5, 5, 10):
            shifted = np.roll(image, delta, axis=0)
            self.assertTrue(sample(True).compatible(shifted))
            crop, info = prepare_battle_message(shifted, sample(True), _REGION)
            self.assertFalse(info["ruby_excluded"])
            self.assertEqual(info["preprocess"], "BODY_ANCHOR_MISMATCH")
            self.assertEqual(crop.shape[:2], (93, 399))

    def test_same_size_ui_text_scale_change_abstains(self):
        image = synthetic_frame()
        transformed = image.copy()
        # Simulated game UI scale: text geometry changes, capture stays 960x572.
        transformed[430:491, 150:215] = 180
        transformed[432:456, 160:190] = 235
        transformed[464:486, 160:190] = 235
        self.assertTrue(sample(True).compatible(transformed))
        _, info = prepare_battle_message(transformed, sample(True), _REGION)
        self.assertEqual(info["preprocess"], "BODY_ANCHOR_MISMATCH")
        self.assertFalse(info["ruby_excluded"])

    def test_unchanged_message_right_animation_does_not_retrigger_ocr(self):
        image = synthetic_frame()
        engine = CountingEngine("効果はいまひとつだ")
        reader = AsyncBattleEventReader(sample(True), ocr_engine=engine)
        try:
            self.assertTrue(reader.offer(image, label="BATTLE_EVENT"))
            changed = image.copy()
            changed[430:485, 600:920] = (255, 0, 255)
            self.assertFalse(reader.offer(changed, label="BATTLE_EVENT"))
        finally:
            reader.stop()

    def test_black_bars_disable_body_extraction(self):
        frame = synthetic_frame()
        frame[:8] = 0
        frame[-8:] = 0
        _, info = prepare_battle_message(frame, sample(True), _REGION)
        self.assertEqual(info["preprocess"], "POSSIBLE_BLACK_BARS")
        self.assertFalse(info["ruby_excluded"])

    def test_diagnostic_toggle_uses_one_read_per_roi_both_modes(self):
        frame = synthetic_frame()
        engine = CountingEngine()
        enabled = inspect_battle_event_image(frame, sample(), engine=engine,
                                              ruby_preview=True)
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(enabled["rows"][0]["preprocess"],
                         "BODY_LINES_PREVIEW_ONLY")
        self.assertEqual(engine.calls[0][1].shape[:2], (59, 399))
        self.assertEqual(enabled["rows"][0]["parsed"], engine.text)
        self.assertFalse(enabled["rows"][0]["game_validated"])
        self.assertGreaterEqual(enabled["rows"][0]["preprocess_ms"], 0)
        self.assertGreaterEqual(enabled["rows"][0]["ocr_elapsed_ms"], 0)
        self.assertGreaterEqual(enabled["rows"][0]["elapsed_ms"], 0)
        self.assertAlmostEqual(enabled["rows"][0]["elapsed_ms"],
                               enabled["rows"][0]["preprocess_ms"] +
                               enabled["rows"][0]["ocr_elapsed_ms"], delta=0.02)
        engine.calls.clear()
        disabled = inspect_battle_event_image(frame, sample(), engine=engine,
                                               ruby_preview=False)
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(disabled["rows"][0]["preprocess"], "DISABLED")
        self.assertEqual(engine.calls[0][1].shape[:2], (93, 399))

    def test_live_unvalidated_is_original_then_calibrated_one_read_only(self):
        frame = synthetic_frame()
        for validated, expected_size, expected_state in (
            (False, (93, 399), "UNVALIDATED_LIVE_DISABLED"),
            (True, (59, 399), "BODY_LINES_CALIBRATED"),
        ):
            with self.subTest(validated=validated):
                engine = CountingEngine("ゲンガーとの勝負に負けた！")
                reader = AsyncBattleEventReader(sample(validated),
                                                ocr_engine=engine)
                try:
                    self.assertTrue(reader.offer(frame, label="BATTLE_EVENT",
                                                 frame_id="frame-123",
                                                 timestamp=monotonic()))
                    observed = ()
                    deadline = monotonic() + 3
                    while monotonic() < deadline and not observed:
                        observed = reader.drain()
                        if not observed:
                            sleep(0.01)
                    self.assertEqual(len(observed), 1)
                    self.assertEqual(len(engine.calls), 1)
                    self.assertEqual(engine.calls[0][1].shape[:2], expected_size)
                    fields = observed[0].observations
                    self.assertEqual(len(fields), 1)
                    self.assertEqual(fields[0].frame_id, "frame-123")
                    self.assertEqual(fields[0].preprocess_status, expected_state)
                    self.assertEqual(fields[0].status, "CANDIDATE")
                    self.assertFalse(fields[0].as_dict()["game_validated"])
                    self.assertIsNotNone(fields[0].ocr_elapsed_ms)
                finally:
                    reader.stop()

    def test_other_messages_and_faint_are_not_forced_into_result(self):
        engine = CountingEngine("ガブリアスは たおれた！")
        report = inspect_battle_event_image(synthetic_frame(), sample(),
                                            engine=engine)
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(report["rows"][0]["parsed"], "ガブリアスは たおれた！")
        # OCR quality work does not assert WIN or LOSS.
        self.assertNotIn("match_result", report)


if __name__ == "__main__":
    unittest.main()
