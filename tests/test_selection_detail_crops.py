"""Offline regression for side-labelled selection crops and strict team mismatch."""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace
import unittest

import cv2
import numpy as np

from champions_assist.match_replay import new_replay, run_replay_image
from champions_assist.selection_vision import selection_image_content, selection_summary_ja
from tests.test_match_replay import fixture_assets, frame, OWN, OPP


class SelectionDetailImageTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((624, 1048, 3), dtype=np.uint8)
        self.image[:, :400, :] = (220, 30, 30)
        self.image[:, 648:, :] = (20, 30, 220)

    @staticmethod
    def decode(uri):
        raw=base64.b64decode(uri.partition(",")[2])
        return cv2.imdecode(np.frombuffer(raw,dtype=np.uint8),cv2.IMREAD_COLOR)

    def test_side_crops_preserve_original_and_left_right_orientation(self):
        inputs=selection_image_content(self.image,introduction="Test")
        images=[item["image_url"] for item in inputs if item["type"]=="input_image"]
        self.assertEqual(len(images),3)
        full,left,right=map(self.decode,images)
        self.assertEqual(full.shape[:2],(624,1048))
        self.assertEqual(left.shape[:2],(624,int(1048*.45)))
        self.assertEqual(right.shape[:2],(624,int(1048*.45)))
        # cv2 works in BGR after decoding the known R/G/B test bands.
        self.assertGreater(int(left[250,150,2]),int(left[250,150,0]))
        self.assertGreater(int(right[250,150,0]),int(right[250,150,2]))
        descriptions=[item["text"] for item in inputs if item["type"]=="input_text"]
        self.assertIn("PLAYER_LEFT_SIX_ONLY",descriptions[1])
        self.assertIn("OPPONENT_RIGHT_SIX_ONLY",descriptions[2])

    def test_small_or_non_landscape_frame_uses_one_image(self):
        for shape in ((180,300,3),(900,400,3)):
            image=np.zeros(shape,dtype=np.uint8)
            images=[item for item in selection_image_content(image,introduction="Test")
                    if item["type"]=="input_image"]
            self.assertEqual(len(images),1)

    def test_selection_still_blocks_wrong_six_and_reports_exact_mismatch(self):
        observed=frame("TEAM_SELECTION",our_six=["Mimikyu",*OWN[1:]],
                       opponent_six=list(OPP))
        events=[SimpleNamespace(type="response.output_text.delta",
                                delta=json.dumps(observed,ensure_ascii=False)),
                SimpleNamespace(type="response.completed")]
        class Stream:
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def __iter__(self):return iter(events)
        class Endpoint:
            def __init__(self):self.calls=[]
            def create(self,**kwargs):
                self.calls.append(kwargs)
                return Stream()
        endpoint=Endpoint()
        client=SimpleNamespace(responses=endpoint)
        assets=fixture_assets()
        original=new_replay(assets)
        updated,result=run_replay_image(
            self.image,state=original,assets=assets,model="mock",access_token="dummy",
            client_factory=lambda **kwargs:client)
        self.assertEqual(result["status"],"BLOCKED")
        self.assertEqual(result["reason"],"REGISTERED_TEAM_MISMATCH")
        self.assertEqual(result["only_in_image"],["ミミッキュ"])
        self.assertEqual(result["only_in_registered_team"],["ピカチュウ"])
        self.assertIn("画像AI側のみ",selection_summary_ja(result))
        self.assertEqual(len(endpoint.calls),1)  # never calls tactical model
        data=endpoint.calls[0]["input"][0]["content"]
        self.assertEqual(sum(x["type"]=="input_image" for x in data),3)
        self.assertEqual(updated["selected_three"],[])
        self.assertFalse(updated["opponent_six"])


if __name__ == "__main__":
    unittest.main()
