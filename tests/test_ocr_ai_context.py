"""Regression for the actual Gengar -> Kingambit OCR history.

The app selects OCR evidence by region/time only; interpretation is a
single tactical AI response and legal-state reconciliation.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from champions_assist.ai_ocr_state_updates import apply_ai_ocr_updates
from champions_assist.battle_state_tracker import LocalBattleStateTracker
from champions_assist.local_event_worker import LocalFieldRead
from champions_assist.match_replay import new_replay, run_replay_text_action
from champions_assist.ocr_ai_evidence import select_ai_ocr_evidence
from tests.test_match_replay import fixture_assets


_MESSAGES={
    494:("戻れ /",0.921453285),
    496:("グン ガー —_- 戻れ /",0.766075576),
    505:("= 3 J ゆけ っ / ドド グ ゲ ザ ン / se = ンー$ ン <a",
         0.5776480382352941),
    546:("あい て 記 — — am 相手 の マス カー ニヤ は "
         "ドド グ ザ ン の オ ボ ン の み を は た き 落 と し た / ED |",
         0.8108449875757575),
}


def captured_history():
    """81 chronological observed frames, with HUD/ROI noise between messages."""
    tracker=LocalBattleStateTracker()
    for frame in range(475,556):
        message=_MESSAGES.get(frame)
        text,confidence=(message if message else
            (f"雑音候補 {frame}",0.54))
        field=("screen_text.battle_message" if message else
               "screen_text.opponent_effect_popup" if frame%3==0 else
               "screen_text.opponent_hp_percent")
        record=LocalFieldRead(
            field=field,value=text,frame_id=f"capture-{frame}",
            timestamp=919045.578+(frame-494)*0.3125,
            confidence=confidence,roi=field.rsplit(".",1)[-1],
            source="test_fixture",text=text)
        tracker.ingest((record,),turn_epoch=2)
    return tracker


def switch():
    return {
        "kind":"SWITCH","side":"own","pokemon":"ドドゲザン",
        "from_pokemon":"ゲンガー","value":"","stat":"",
        "delta":0,"certainty":"CONFIRMED",
        "evidence_frames":["capture-505","capture-546"],
    }


class OcrAiContextRegression(unittest.TestCase):
    def setUp(self):
        self.assets=fixture_assets()
        self.state=new_replay(self.assets)
        self.state.update(
            phase="BATTLE",our_active="ゲンガー",
            opponent_active="マスカーニャ",
            selected_three=["ゲンガー","ドドゲザン","マリルリ"],
            lead="ゲンガー",selection_assumed=False)
        self.tracker=captured_history()
        self.snapshot=self.tracker.snapshot(limit=144)["local_observations"]

    def test_preserve_important_event_frames_beyond_old_12_limit(self):
        self.assertGreater(len(self.snapshot),36)
        selected,meta=select_ai_ocr_evidence(self.snapshot)
        frames=[r["frame_id"] for r in selected]
        for frame in ("capture-494","capture-496","capture-505","capture-546"):
            self.assertIn(frame,frames)
        self.assertEqual(len(selected),36)
        self.assertGreater(meta["dropped_count"],0)
        self.assertGreaterEqual(meta["battle_messages_sent"],4)
        self.assertEqual(frames,sorted(frames,
            key=lambda x:int(x.split("-")[-1])))

    def test_worker_does_not_reapply_old_first_twelve_truncation(self):
        from champions_assist.match_replay_dialog import _ReplayWorker
        evidence,_=select_ai_ocr_evidence(self.snapshot)
        worker=_ReplayWorker(
            app_root="unused",selected_model="mock",
            state=self.state,assets=self.assets,
            local_field_candidates=evidence)
        self.assertEqual(len(worker.local_field_candidates),len(evidence))
        self.assertIn("capture-546",{
            r["frame_id"] for r in worker.local_field_candidates})

    def test_low_confidence_sendout_with_strong_later_message_is_valid(self):
        selected,_=select_ai_ocr_evidence(self.snapshot)
        state,audit=apply_ai_ocr_updates(
            self.state,[switch()],selected,self.assets)
        self.assertEqual(state["our_active"],"ドドゲザン")
        self.assertEqual(audit[0]["status"],"APPLIED")

    def test_single_weak_sendout_cannot_force_switch(self):
        selected,_=select_ai_ocr_evidence(self.snapshot)
        proposal=switch()
        proposal["evidence_frames"]=["capture-505"]
        state,audit=apply_ai_ocr_updates(
            self.state,[proposal],selected,self.assets)
        self.assertEqual(state["our_active"],"ゲンガー")
        self.assertEqual(audit[0]["reason"],"SWITCH_NEEDS_SECOND_FRAME")

    def test_unrelated_hp_is_not_sufficient_switch_evidence(self):
        selected,_=select_ai_ocr_evidence(self.snapshot)
        proposal=switch()
        proposal["evidence_frames"]=["capture-501","capture-504"]
        state,audit=apply_ai_ocr_updates(
            self.state,[proposal],selected,self.assets)
        self.assertEqual(state["our_active"],"ゲンガー")
        self.assertEqual(audit[0]["status"],"REJECTED")

    def test_ai_receives_both_messages_then_validates_post_switch_action(self):
        evidence,_=select_ai_ocr_evidence(self.snapshot)
        response={
            "state_updates":[switch()],
            "interpreted_own_active":"ドドゲザン",
            "interpreted_opponent_active":"マスカーニャ",
            "action":{"action_kind":"SWITCH","switch_target":"ドドゲザン",
                      "move":"","warning":""},
        }
        seen=[]
        def reply(_client,**kwargs):
            seen.append(kwargs)
            return response
        with patch("champions_assist.match_replay._stream_json",side_effect=reply):
            state,report=run_replay_text_action(
                state=self.state,assets=self.assets,
                access_token="unused",model="test",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=evidence,
                client_factory=lambda **kwargs:object())
        sent=json.loads(seen[0]["content"][0]["text"])[
            "ocr_candidates_unverified"]
        frames={x["frame_id"] for x in sent}
        self.assertLessEqual(len(sent),36)
        self.assertTrue({"capture-505","capture-546"}<=frames)
        self.assertEqual(report["images_sent"],0)
        self.assertEqual(state["our_active"],"ドドゲザン")
        self.assertEqual(report["status"],"BLOCKED")
        self.assertEqual(report["reason"],"SWITCH_TARGET_NOT_SELECTED")

    def test_already_applied_switch_evidence_is_not_a_new_actor_conflict(self):
        evidence,_=select_ai_ocr_evidence(self.snapshot)
        updated,audit=apply_ai_ocr_updates(
            self.state,[switch()],evidence,self.assets)
        self.assertEqual(audit[0]["status"],"APPLIED")
        response={
            "state_updates":[switch()],
            "interpreted_own_active":"ドドゲザン",
            "interpreted_opponent_active":"マスカーニャ",
            "action":{"action_kind":"MOVE","move":"じしん",
                      "switch_target":"","warning":""},
        }
        with patch("champions_assist.match_replay._stream_json",
                   return_value=response):
            after,report=run_replay_text_action(
                state=updated,assets=self.assets,
                access_token="unused",model="test",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=evidence,
                client_factory=lambda **kwargs:object())
        self.assertEqual(after["our_active"],"ドドゲザン")
        self.assertNotEqual(report.get("reason"),
                            "AI_OCR_ACTIVE_STATE_UNVERIFIED")

    def test_expired_observations_not_sent(self):
        evidence=self.snapshot+ [{
            "frame_id":"capture-old","field":"screen_text.battle_message",
            "raw_text":"old event","confidence":.99,
            "observed_at":818000}]
        selected,_=select_ai_ocr_evidence(evidence)
        self.assertNotIn("capture-old",{x["frame_id"] for x in selected})


if __name__=="__main__":
    unittest.main()
