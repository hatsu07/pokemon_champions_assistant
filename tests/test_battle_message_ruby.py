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
    frame[434:453, 150:540] = (10, 30, 220)
    frame[466:487, 180:540] = (10, 210, 30)
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
        self.assertEqual(data["preprocess"], "RUBY_MASK_PREVIEW_ONLY")
        self.assertEqual(data["ruby_mask_rois"], [
            [141, 423, 540, 430], [141, 454, 540, 464]])
        self.assertEqual(crop.shape, (93, 399, 3))
        original = sample().crop(frame, _REGION)
        # All main body pixels, including the full last punctuation at X539,
        # must remain byte-for-byte unchanged. Only ruby bands may change.
        self.assertTrue(np.array_equal(crop[22:42], original[22:42]))
        self.assertTrue(np.array_equal(crop[54:77], original[54:77]))
        self.assertTrue(np.any(np.all(crop == (10, 30, 220), axis=2)))
        self.assertTrue(np.any(np.all(crop == (10, 210, 30), axis=2)))
        self.assertFalse(np.any(np.all(crop == (245, 10, 10), axis=2)))
        self.assertTrue(np.array_equal(crop[35, 539 - 141], frame[447, 539]))
        changed = np.any(crop != original, axis=2)
        protected = np.ones(changed.shape, dtype=bool)
        protected[423-412:430-412] = False
        protected[454-412:464-412] = False
        self.assertFalse(np.any(changed & protected))
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
                self.assertEqual(data["preprocess"], "RUBY_MASK_CALIBRATED")
                self.assertEqual(len(data["ruby_mask_rois"]), 2)
                self.assertEqual(data["ruby_mask_rois"], [
                    [round(v * scale) for v in (141, 423, 540, 430)],
                    [round(v * scale) for v in (141, 454, 540, 464)]])
                self.assertEqual(crop.shape, sample(True).crop(img, _REGION).shape)
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

    def test_light_background_requires_distinct_body_strokes(self):
        # A bright, uniform UI background is not a line of white body glyphs.
        # This regresses the six BODY_ANCHOR_MISMATCH failures in CI #1173.
        blank = np.full((572, 960, 3), 180, np.uint8)
        _, missing = prepare_battle_message(blank, sample(True), _REGION)
        self.assertEqual(missing["preprocess"], "BODY_ANCHOR_MISMATCH")
        self.assertFalse(missing["ruby_excluded"])
        for background in (70, 180, 215):
            with self.subTest(background=background):
                frame = synthetic_frame()
                frame[np.all(frame == 180, axis=2)] = background
                cropped, parsed = prepare_battle_message(
                    frame, sample(True), _REGION)
                self.assertEqual(parsed["preprocess"], "RUBY_MASK_CALIBRATED")
                self.assertTrue(parsed["ruby_excluded"])
                self.assertEqual(cropped.shape[:2], (93, 399))

    def test_second_line_only_shifted_out_of_body_band_falls_back(self):
        # Review P1: first line is still perfectly aligned and must not
        # validate a damaged/moved second line. Full viewport stays 960x572.
        frame = synthetic_frame()
        shifted = frame.copy()
        line = shifted[464:489, 141:540].copy()
        shifted[464:489, 141:540] = 180
        shifted[493:518, 141:540] = line
        self.assertTrue(sample(True).compatible(shifted))
        original = sample(True).crop(shifted, _REGION)
        cropped, info = prepare_battle_message(
            shifted, sample(True), _REGION)
        self.assertEqual(info["preprocess"], "BODY_ANCHOR_MISMATCH")
        self.assertFalse(info["ruby_excluded"])
        self.assertTrue(np.array_equal(cropped, original))

    def test_legitimate_single_line_masks_both_ruby_rows_without_body_loss(self):
        # One-line messages get the same two-band mask, not the legacy
        # full-ROI fallback. An absent second line is not a failure.
        frame = synthetic_frame()
        frame[454:489, 141:540] = 180
        crop, info = prepare_battle_message(frame, sample(True), _REGION)
        self.assertEqual(info["preprocess"], "RUBY_MASK_CALIBRATED")
        self.assertTrue(info["ruby_excluded"])
        self.assertEqual(len(info["ruby_mask_rois"]), 2)
        self.assertEqual(crop.shape, (93, 399, 3))
        self.assertTrue(np.array_equal(
            crop[434-412:454-412], frame[434:454, 141:540]))
        engine = CountingEngine("効果はいまひとつだ")
        reader = AsyncBattleEventReader(sample(True), ocr_engine=engine)
        try:
            self.assertTrue(reader.offer(frame, label="BATTLE_EVENT"))
            deadline = monotonic() + 3
            records = ()
            while monotonic() < deadline and not records:
                records = reader.drain()
                if not records:
                    sleep(0.01)
            self.assertEqual(len(records), 1)
            self.assertEqual(len(engine.calls), 1)
            self.assertEqual(engine.calls[0][1].shape[:2], (93, 399))
            self.assertEqual(records[0].observations[0].status, "CANDIDATE")
            self.assertEqual(records[0].observations[0].preprocess_status,
                             "RUBY_MASK_CALIBRATED")
        finally:
            reader.stop()

    def test_body_boundary_y430_through_y434_cannot_be_overwritten(self):
        # P1 regression: previously Y431 was included in first ruby mask.
        frame = synthetic_frame()
        frame[430:435, 240:260] = 255
        image_before = sample(True).crop(frame, _REGION)
        image_after, info = prepare_battle_message(frame, sample(True), _REGION)
        self.assertEqual(info["preprocess"], "RUBY_MASK_CALIBRATED")
        self.assertTrue(np.array_equal(
            image_before[430-412:435-412, 240-141:260-141],
            image_after[430-412:435-412, 240-141:260-141]))

    def test_second_body_shifted_right_into_ruby_band_must_fall_back(self):
        # P1 regression: left anchor shows only one line but second-line
        # main-sized glyphs are present outside the left anchor at X240:360.
        frame = synthetic_frame()
        frame[454:489, 141:540] = 180
        frame[455:463, 240:360] = 255
        cropped, info = prepare_battle_message(frame, sample(True), _REGION)
        self.assertFalse(info["ruby_excluded"])
        self.assertEqual(info["preprocess"], "RUBY_BAND_BODY_RISK")
        self.assertTrue(np.array_equal(cropped, sample(True).crop(frame, _REGION)))

    def test_second_body_shifted_up_into_ruby_band_falls_back(self):
        # A changed line baseline must NOT be erased by the second mask.
        frame = synthetic_frame()
        moved = frame.copy()
        main = moved[466:487, 141:540].copy()
        moved[466:487, 141:540] = 180
        moved[455:476, 141:540] = main
        crop, info = prepare_battle_message(moved, sample(True), _REGION)
        self.assertEqual(info["preprocess"], "BODY_ANCHOR_MISMATCH")
        self.assertFalse(info["ruby_excluded"])
        self.assertTrue(np.array_equal(crop, sample(True).crop(moved, _REGION)))

    def test_mask_preserves_main_body_and_punctuation_for_one_and_two_lines(self):
        for one_line in (True, False):
            with self.subTest(one_line=one_line):
                frame = synthetic_frame()
                if one_line:
                    frame[454:489, 141:540] = 180
                frame[445, 538:540] = 255  # rightmost punctuation
                before = sample(True).crop(frame, _REGION)
                after, data = prepare_battle_message(frame, sample(True), _REGION)
                self.assertTrue(data["ruby_excluded"])
                # Any change outside the two ruby masks is forbidden.
                allowed = np.zeros(before.shape[:2], dtype=bool)
                allowed[423-412:430-412] = True
                allowed[454-412:464-412] = True
                changed = np.any(before != after, axis=2)
                self.assertFalse(np.any(changed & ~allowed))
                self.assertEqual(after[445-412, -1, 0], 255)

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
                         "RUBY_MASK_PREVIEW_ONLY")
        self.assertEqual(engine.calls[0][1].shape[:2], (93, 399))
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
            (True, (93, 399), "RUBY_MASK_CALIBRATED"),
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
