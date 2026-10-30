"""Single-call AI OCR state/action regression: no network, no image fallback."""
from __future__ import annotations
from unittest.mock import patch
from tests.ocr_wire_fixture import reply_with_wire_ids
import unittest
from tests.test_match_replay import fixture_assets
from champions_assist.match_replay import new_replay,run_replay_text_action
from champions_assist.ai_ocr_state_updates import apply_ai_ocr_updates


def readings():
    return [
        {"field":"screen_text.battle_message","frame_id":"capture-300",
         "raw_text":"ガブ リア ス 戻れ /",
         "confidence":0.637,"observed_at":100.0},
        {"field":"screen_text.battle_message","frame_id":"capture-301",
         "raw_text":"ゆけ っ / ドド ゲ ザ ン /",
         "confidence":0.714,"observed_at":100.3},
    ]


def fixture_state(assets):
    state=new_replay(assets)
    state.update(
        phase="BATTLE",our_active="ガブリアス",
        opponent_active="マスカーニャ",
        selected_three=["ガブリアス","ドドゲザン","マリルリ"],
        lead="ガブリアス",selection_assumed=False)
    return state


def switch_event():
    return {
        "kind":"SWITCH","side":"own","pokemon":"ドドゲザン",
        "from_pokemon":"ガブリアス","value":"","stat":"",
        "delta":0,"certainty":"CONFIRMED",
        "evidence_frames":["capture-300","capture-301"],
    }


class AiOcrStateTests(unittest.TestCase):
    def setUp(self):
        self.assets=fixture_assets()
        self.state=fixture_state(self.assets)

    def test_actual_raw_ocr_becomes_verified_actor_update(self):
        result,audit=apply_ai_ocr_updates(
            self.state,[switch_event()],readings(),self.assets)
        self.assertEqual(result["our_active"],"ドドゲザン")
        self.assertEqual(result["_live_state_revision"],1)
        self.assertEqual(audit[0]["status"],"APPLIED")
        self.assertEqual(self.state["our_active"],"ガブリアス")

    def test_single_weak_frame_is_not_switch_proof(self):
        event=switch_event()
        event["evidence_frames"]=["capture-301"]
        result,audit=apply_ai_ocr_updates(
            self.state,[event],readings(),self.assets)
        self.assertEqual(result["our_active"],"ガブリアス")
        self.assertEqual(audit[0]["reason"],"SWITCH_NEEDS_SECOND_FRAME")

    def test_mismatched_from_actor_or_forged_evidence_rejected(self):
        changed=switch_event()
        changed["from_pokemon"]="マリルリ"
        r,a=apply_ai_ocr_updates(self.state,[changed],readings(),self.assets)
        self.assertEqual(a[0]["reason"],"STALE_FROM_ACTIVE")
        changed=switch_event()
        changed["evidence_frames"]=["capture-does-not-exist"]
        r,a=apply_ai_ocr_updates(self.state,[changed],readings(),self.assets)
        self.assertEqual(a[0]["reason"],"EVIDENCE_FRAME_NOT_SENT")

    def test_one_response_updates_actor_then_blocks_self_switch(self):
        response={
            "state_updates":[switch_event()],
            "interpreted_own_active":"ドドゲザン",
            "interpreted_opponent_active":"マスカーニャ",
            "action":{"action_kind":"SWITCH","move":"",
                      "switch_target":"ドドゲザン","warning":""},
        }
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=lambda _client,**kw:
                       reply_with_wire_ids(kw,response,readings())):
            result,report=run_replay_text_action(
                state=self.state,assets=self.assets,
                access_token="unused",model="mock",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=readings(),
                client_factory=lambda **kwargs:object())
        self.assertEqual(result["our_active"],"ドドゲザン")
        self.assertEqual(report["status"],"BLOCKED")
        self.assertEqual(report["reason"],"SWITCH_TARGET_NOT_SELECTED")

    def test_ai_actor_without_supported_update_blocks_action(self):
        response={
            "state_updates":[],
            "interpreted_own_active":"ドドゲザン",
            "interpreted_opponent_active":"マスカーニャ",
            "action":{"action_kind":"MOVE","move":"じしん",
                      "switch_target":"","warning":""},
        }
        with patch("champions_assist.match_replay._stream_json",
                   return_value=response):
            result,report=run_replay_text_action(
                state=self.state,assets=self.assets,
                access_token="unused",model="mock",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=readings(),
                client_factory=lambda **kwargs:object())
        self.assertEqual(result["our_active"],"ガブリアス")
        self.assertEqual(report["reason"],"AI_OCR_OWN_ACTIVE_UNVERIFIED")

    def test_duplicate_event_is_idempotent(self):
        r,a=apply_ai_ocr_updates(self.state,[switch_event()],readings(),self.assets)
        r2,a2=apply_ai_ocr_updates(r,[switch_event()],readings(),self.assets)
        self.assertEqual(r2["_live_state_revision"],1)
        self.assertEqual(r2["our_active"],"ドドゲザン")
        self.assertEqual(a2[0]["status"],"REJECTED")


if __name__=="__main__":
    unittest.main()
