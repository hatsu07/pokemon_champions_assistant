"""Offline ROI OCR diagnostics and high-resolution no-downscale regression."""
from __future__ import annotations

import unittest
import numpy as np
from champions_assist.calibrated_screen_fields import (
    ScreenFieldProfile, FieldRegion,
)
from champions_assist.ocr import OcrRead
from champions_assist.ocr_diagnostics import inspect_battle_event_image


class StaticEngine:
    def __init__(self):
        self.calls=[]
    def read(self,image,*,roi_name):
        self.calls.append((roi_name,image.shape[:2]))
        if roi_name=="own_hp_fraction":
            return OcrRead("117/131",0.93,"FAKE")
        return OcrRead("ガブリアスは たおれた",0.72,"FAKE")


class LocalOcrDiagnosticTests(unittest.TestCase):
    def test_offline_event_diagnostic_shows_crop_text_confidence_and_parse(self):
        image=np.full((572,960,3),255,dtype=np.uint8)
        profile=ScreenFieldProfile.from_user_samples_960x572()
        engine=StaticEngine()
        report=inspect_battle_event_image(image,profile,engine=engine)
        self.assertEqual(report["status"],"OCR_DIAGNOSTIC_COMPLETE")
        self.assertTrue(engine.calls)
        hit=next(r for r in report["rows"] if r["name"]=="own_hp_fraction")
        self.assertEqual(hit["parsed"],{"current":117,"maximum":131})
        self.assertAlmostEqual(hit["confidence"],0.93)
        self.assertTrue(hit["crop"].size)
        self.assertEqual(hit["error"],"")
        self.assertTrue(any(r["text"].startswith("ガブリアス") for r in report["rows"]))

    def test_bad_aspect_ratio_is_reported_without_ocr(self):
        reader=StaticEngine()
        out=inspect_battle_event_image(
            np.zeros((250,500,3),dtype=np.uint8),
            ScreenFieldProfile.from_user_samples_960x572(),engine=reader)
        self.assertEqual(out["status"],"IMAGE_ROI_GEOMETRY_MISMATCH")
        self.assertEqual(reader.calls,[])

    def test_double_resolution_roi_keeps_glyph_details(self):
        profile=ScreenFieldProfile.from_user_samples_960x572()
        image=np.zeros((1144,1920,3),dtype=np.uint8)
        region=next(r for r in profile.regions if r.name=="battle_message")
        cropped=profile.crop(image,region)
        self.assertEqual(cropped.shape[:2],((505-412)*2,(540-141)*2))


if __name__=="__main__":
    unittest.main()
