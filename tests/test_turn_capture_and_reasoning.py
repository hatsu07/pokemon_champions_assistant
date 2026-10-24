"""Offline tests for reasoning config, local batching, and one-request replay."""
from __future__ import annotations

import json
import tempfile
import unittest
from types import MappingProxyType,SimpleNamespace

import numpy as np

from champions_assist.reasoning_config import reasoning_request_kwargs
from champions_assist.vision_preferences import VisionPreferenceStore
from champions_assist.turn_capture import TurnCaptureBuffer
from champions_assist.match_replay import run_replay_batch, new_replay
from champions_assist.preload import FrozenMatchAssets


class MemoryAndReasoningTests(unittest.TestCase):
    def test_reasoning_is_persisted_independently_of_model(self):
        with tempfile.TemporaryDirectory() as root:
            prefs=VisionPreferenceStore(root)
            self.assertEqual(prefs.reasoning_effort(),"auto")
            prefs.save_model("gpt-5.6")
            prefs.save_reasoning_effort("low")
            prefs.save_model("gpt-6")
            fresh=VisionPreferenceStore(root)
            self.assertEqual(fresh.selected_model(),"gpt-6")
            self.assertEqual(fresh.reasoning_effort(),"low")
            self.assertEqual(reasoning_request_kwargs("auto"),{})
            self.assertEqual(reasoning_request_kwargs("high"),{"reasoning":{"effort":"high"}})
            with self.assertRaises(ValueError):
                prefs.save_reasoning_effort("unsafe")

    def test_calibrated_menu_detects_stable_transition_once(self):
        gate=TurnCaptureBuffer(stable_frames=2)
        menu=np.zeros((120,200,3),dtype=np.uint8)
        menu[60:,100:]=200
        other=np.zeros_like(menu)
        gate.calibrate("TURN_ACTION",menu)
        self.assertIsNone(gate.observe(other))
        self.assertIsNone(gate.observe(other))
        self.assertIsNone(gate.observe(menu))
        self.assertEqual(gate.observe(menu),"TURN_ACTION")
        self.assertIsNone(gate.observe(menu))
        batch=gate.batch(menu)
        self.assertLessEqual(len(batch),4)
        self.assertTrue(np.array_equal(batch[-1],menu))
        gate.observe(other)
        gate.observe(other)
        gate.observe(menu)
        self.assertEqual(gate.observe(menu),"TURN_ACTION")

    def test_explicit_menu_acknowledgement_suppresses_duplicate_menu_send(self):
        buffer=TurnCaptureBuffer(stable_frames=2)
        menu=np.zeros((120,200,3),dtype=np.uint8)
        menu[60:,100:]=200
        idle=np.zeros_like(menu)
        buffer.acknowledge_calibrated_current("TEAM_SELECTION",menu)
        self.assertIn("TEAM_SELECTION",buffer.templates)
        self.assertFalse(buffer._armed)
        self.assertIsNone(buffer.observe(menu))
        self.assertIsNone(buffer.observe(menu))
        self.assertIsNone(buffer.observe(idle))
        self.assertIsNone(buffer.observe(idle))
        self.assertIsNone(buffer.observe(menu))
        self.assertEqual(buffer.observe(menu),"TEAM_SELECTION")

    def test_batch_uses_one_stream_call_and_rejects_unsupported_action(self):
        own=["ピカチュウ","ドドゲザン","ガブリアス"]
        assets=FrozenMatchAssets(
            contents=MappingProxyType({}),
            sha256=MappingProxyType({}),
            matrix_index=MappingProxyType({}),
            lookup_index=MappingProxyType({}),
            own_team=tuple({"name":p,"move1":"10まんボルト",
                            "move2":"まもる","move3":"でんこうせっか",
                            "move4":"かみくだく"} for p in own),
            frozen_at="fixture",
        )
        state=new_replay(assets)
        state["phase"]="BATTLE"
        state["selected_three"]=own
        state["lead"]=own[0]
        state["our_active"]=own[0]
        observation={
            "visual_ui":"MOVE_MENU","our_six":[],"opponent_six":[],
            "our_active":own[0],"opponent_active":"マスカーニャ",
            "visible_moves":["10まんボルト"],"visible_party":[],
            "our_hp":"90/100","opponent_hp":"100%",
            "messages":[],"observed_event":"NONE",
            "match_result":"UNKNOWN","uncertain_slots":[],
        }
        answer={
            "observation":observation,
            "selection":{"selected_three":[],"lead":""},
            "action":{"action_kind":"MOVE","move":"まもる",
                      "switch_target":"","warning":""},
        }
        class FakeStream:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def __iter__(self):
                yield SimpleNamespace(type="response.output_text.delta",
                                      delta=json.dumps(answer,ensure_ascii=False))
                yield SimpleNamespace(type="response.completed")
        class Endpoint:
            def __init__(self):self.calls=[]
            def create(self,**kwargs):
                self.calls.append(kwargs)
                return FakeStream()
        class Client:
            def __init__(self):self.responses=Endpoint()
        client=Client()
        after,report=run_replay_batch(
            [np.zeros((40,60,3),dtype=np.uint8)],
            state=state,assets=assets,access_token="dummy",model="test",
            reasoning_effort="low",client_factory=lambda **kw:client)
        self.assertEqual(len(client.responses.calls),1)
        self.assertEqual(client.responses.calls[0]["reasoning"],{"effort":"low"})
        self.assertEqual(report["status"],"BLOCKED")
        self.assertEqual(report["reason"],"MOVE_NOT_VERIFIED")
        self.assertEqual(after["our_active"],own[0])


if __name__=="__main__":
    unittest.main()
