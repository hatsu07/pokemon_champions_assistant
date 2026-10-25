"""Offline regression for side-labelled selection crops and strict team mismatch."""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace
import unittest

import cv2
import numpy as np

from champions_assist.match_replay import new_replay, run_replay_image
from champions_assist.selection_vision import selection_image_content
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

    def test_only_opponent_right_crop_plus_original_image(self):
        inputs=selection_image_content(self.image,introduction="Test")
        images=[item["image_url"] for item in inputs if item["type"]=="input_image"]
        self.assertEqual(len(images),2)
        full,right=map(self.decode,images)
        self.assertEqual(full.shape[:2],(624,1048))
        self.assertEqual(right.shape[:2],(624,int(1048*.45)))
        self.assertGreater(int(right[250,150,0]),int(right[250,150,2]))
        descriptions=[item["text"] for item in inputs if item["type"]=="input_text"]
        self.assertEqual(len(descriptions),2)
        self.assertIn("OPPONENT_RIGHT_SIX_ONLY",descriptions[1])
        self.assertNotIn("PLAYER_LEFT_SIX_ONLY",str(descriptions))

    def test_small_or_non_landscape_frame_uses_one_image(self):
        for shape in ((180,300,3),(900,400,3)):
            image=np.zeros(shape,dtype=np.uint8)
            images=[item for item in selection_image_content(image,introduction="Test")
                    if item["type"]=="input_image"]
            self.assertEqual(len(images),1)

    @staticmethod
    def fake_client(replies):
        class Stream:
            def __init__(self, value):self.value=value
            def __enter__(self):return self
            def __exit__(self,*_):return False
            def __iter__(self):
                yield SimpleNamespace(type="response.output_text.delta",
                                      delta=json.dumps(self.value,ensure_ascii=False))
                yield SimpleNamespace(type="response.completed")
        class Endpoint:
            def __init__(self):self.calls=[]
            def create(self,**kwargs):
                self.calls.append(kwargs)
                return Stream(replies[len(self.calls)-1])
        endpoint=Endpoint()
        return SimpleNamespace(responses=endpoint),endpoint

    def test_opponent_retry_new_model_request_then_registered_selection(self):
        wrong=frame("TEAM_SELECTION",
                    our_six=["Iron Valiant",*OWN[1:]],
                    opponent_six=[OPP[0],OPP[0],*OPP[2:]],
                    uncertain_slots=["opponent_slot_2"])
        corrected=frame("TEAM_SELECTION",
                        our_six=["Mimikyu",*OWN[1:]],
                        opponent_six=list(OPP))
        chosen={"selected_three":list(OWN[:3]),"lead":OWN[0]}
        client,endpoint=self.fake_client([wrong,corrected,chosen])
        state,result=run_replay_image(
            self.image,state=new_replay(fixture_assets()),assets=fixture_assets(),
            model="mock",access_token="dummy",client_factory=lambda **_:client)
        self.assertEqual(result["status"],"TEST_ONLY")
        self.assertEqual(result["opponent_recognition_attempts"],2)
        self.assertEqual(result["selected_three"],list(OWN[:3]))
        self.assertEqual(state["opponent_six"],list(OPP))
        self.assertEqual(result["observation"]["our_six"],[])
        self.assertEqual(len(endpoint.calls),3)  # initial + correction + tactical
        self.assertIn("FAILED: OPPONENT_SIX_DUPLICATES",
                      endpoint.calls[1]["instructions"])
        self.assertIn("OPPONENT_RIGHT_SIX_ONLY",
                      str(endpoint.calls[1]["input"][0]["content"][2]["text"]))
        self.assertEqual(sum(x["type"]=="input_image" for x in
                             endpoint.calls[1]["input"][0]["content"]),2)

    def test_two_stages_share_bounded_socket_timeouts_and_explicit_fast_effort(self):
        observed=frame("TEAM_SELECTION",our_six=[],opponent_six=list(OPP))
        selected={"selected_three":list(OWN[:3]),"lead":OWN[0]}
        class Stream:
            def __init__(self,value): self.value=value
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def __iter__(self):
                yield SimpleNamespace(type="response.output_text.delta",
                    delta=json.dumps(self.value,ensure_ascii=False))
                yield SimpleNamespace(type="response.completed")
        class BoundedClient:
            def __init__(self):
                self.timeouts=[]
                self.calls=[]
                self.responses=self
            def with_options(self,*,timeout):
                self.timeouts.append(timeout)
                return self
            def create(self,**kwargs):
                self.calls.append(kwargs)
                return Stream([observed,selected][len(self.calls)-1])
        client=BoundedClient()
        state,result=run_replay_image(
            self.image,state=new_replay(fixture_assets()),assets=fixture_assets(),
            model="mock",access_token="dummy",reasoning_effort="high",
            client_factory=lambda **_:client)
        self.assertEqual(result["status"],"TEST_ONLY")
        self.assertEqual(result["live_reasoning_effort"],"low")
        self.assertEqual(len(client.timeouts),2)
        self.assertLessEqual(client.timeouts[0],18)
        self.assertLessEqual(client.timeouts[1],16)
        self.assertTrue(all(call["reasoning"]=={"effort":"low"}
                            for call in client.calls))

    def test_two_unreadable_opponent_attempts_never_trigger_tactics(self):
        unreadable=frame("TEAM_SELECTION",
                         our_six=list(OWN),
                         opponent_six=list(OPP[:5]),
                         uncertain_slots=["opponent_slot_6"])
        client,endpoint=self.fake_client([unreadable,unreadable])
        state,result=run_replay_image(
            self.image,state=new_replay(fixture_assets()),assets=fixture_assets(),
            model="mock",access_token="dummy",client_factory=lambda **_:client)
        self.assertEqual(result["status"],"BLOCKED")
        self.assertEqual(result["reason"],"OPPONENT_SIX_INCOMPLETE")
        self.assertEqual(result["opponent_recognition_attempts"],2)
        self.assertEqual(state["selected_three"],[])
        self.assertEqual(state["opponent_six"],[])
        self.assertEqual(len(endpoint.calls),2)  # no tactical third request

    def test_six_clear_opponents_ignores_hallucinated_own_names(self):
        readable=frame("TEAM_SELECTION",
                       our_six=["Iron Valiant",*OWN[1:]],
                       opponent_six=list(OPP))
        chosen={"selected_three":list(OWN[:3]),"lead":OWN[0]}
        client,endpoint=self.fake_client([readable,chosen])
        state,result=run_replay_image(
            self.image,state=new_replay(fixture_assets()),assets=fixture_assets(),
            model="mock",access_token="dummy",client_factory=lambda **_:client)
        self.assertEqual(result["status"],"TEST_ONLY")
        self.assertEqual(result["opponent_recognition_attempts"],1)
        self.assertEqual(result["selected_three"],list(OWN[:3]))
        self.assertEqual(len(endpoint.calls),2)



if __name__ == "__main__":
    unittest.main()
