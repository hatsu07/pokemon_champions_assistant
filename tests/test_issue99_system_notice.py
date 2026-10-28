"""Synthetic-only Issue #99 viewport notice tests. Never store user frames."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from champions_assist.calibrated_screen_fields import (
    ScreenFieldProfile, confirmed_auto_move_notice, live_ocr_region_enabled,
)


NOTICE = "サーバーに入力が届かなかったため\n技が自動で選ばれました"


class SystemNoticeTests(unittest.TestCase):
    def profile(self, directory, *, validated=True):
        path=Path(directory)/"screen_fields.json"
        path.write_text(json.dumps({
            "schema_version":1,
            "reference_resolution":[960,572],
            "source":"TEST_SYNTHETIC",
            "validated_on_game_capture":validated,
            "regions":[{
                "name":"system_message","kind":"MESSAGE",
                "bounds_normalized":[0.31,0.085,0.73,0.19],
                "applicable_labels":["TURN_ACTION","BATTLE_EVENT"]
            }]
        }),encoding="utf8")
        return ScreenFieldProfile.from_json(path)

    def test_normalized_roi_crops_same_fraction_at_two_resolutions(self):
        with TemporaryDirectory() as tmp:
            profile=self.profile(tmp)
            region=profile.regions[0]
            self.assertIsNone(region.bounds_px)
            self.assertEqual(region.bounds_normalized,(0.31,0.085,0.73,0.19))
            for width,height in ((960,572),(1920,1144)):
                image=np.zeros((height,width,3),dtype=np.uint8)
                x0,x1=round(width*.31),round(width*.73)
                y0,y1=round(height*.085),round(height*.19)
                image[y0:y1,x0:x1]=127
                self.assertTrue(profile.compatible(image))
                crop=profile.crop(image,region)
                self.assertTrue(np.all(crop==127))
            self.assertEqual(ScreenFieldProfile.from_json(path := Path(tmp)/"screen_fields.json").as_dict()["regions"][0]["bounds_normalized"],[0.31,0.085,0.73,0.19])

    def test_exact_notice_with_newlines_and_whitespace_only(self):
        self.assertTrue(confirmed_auto_move_notice(NOTICE,.96,calibrated=True))
        self.assertTrue(confirmed_auto_move_notice("サーバーに入力が届かなかったため  技が自動で選ばれました",.95,calibrated=True))
        self.assertFalse(confirmed_auto_move_notice("技が自動で選ばれました",.98,calibrated=True))
        self.assertFalse(confirmed_auto_move_notice("サーバーに入力が届かなかった",.99,calibrated=True))
        self.assertFalse(confirmed_auto_move_notice(NOTICE,.70,calibrated=True))
        self.assertFalse(confirmed_auto_move_notice(NOTICE,None,calibrated=True))
        self.assertFalse(confirmed_auto_move_notice(NOTICE,.99,calibrated=False))

    def test_uncalibrated_profile_abstains_and_wrong_aspect_refused(self):
        with TemporaryDirectory() as tmp:
            profile=self.profile(tmp,validated=False)
            self.assertFalse(live_ocr_region_enabled(profile,profile.regions[0]))
            self.assertFalse(profile.compatible(np.zeros((750,1000,3),dtype=np.uint8)))
            calibrated=self.profile(tmp,validated=True)
            self.assertTrue(live_ocr_region_enabled(calibrated,calibrated.regions[0]))

    def test_rejects_invalid_normalized_bounds(self):
        with TemporaryDirectory() as tmp:
            profile=self.profile(tmp)
            data=profile.as_dict()
            data["regions"][0]["bounds_normalized"]=[.31,.2,1.1,.3]
            path=Path(tmp)/"bad.json"
            path.write_text(json.dumps(data),encoding="utf8")
            with self.assertRaises(ValueError):
                ScreenFieldProfile.from_json(path)


if __name__=="__main__":
    unittest.main()
