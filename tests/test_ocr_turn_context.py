"""Issue #102/C: bounded turn transport, local-ledger preservation and single AI call."""
import json
import unittest
from unittest.mock import patch

from champions_assist.ocr_turn_context import (
    MAX_TURN_OCR_ROWS, build_ocr_turn_context, sanitize_pending_questions,
)
from champions_assist.ocr_ai_evidence import select_ai_ocr_evidence
from champions_assist.match_replay import run_replay_text_action, new_replay
from tests.test_ocr_ai_context import fixture_assets


def rows(n=365):
    return [
        {"frame_id":f"capture-{i:04d}",
         "field":"screen_text.battle_message",
         "raw_text":f"未検証の戦闘イベント {i}",
         "confidence":0.93,
         "observed_at":1000.0+i*0.5}
        for i in range(n)
    ]


class BoundedOcrTurnContextTests(unittest.TestCase):
    def test_365_row_view_is_bounded_and_evidence_stays_lossless(self):
        ledger=rows()
        before=json.dumps(ledger,ensure_ascii=False)
        selected,carry,stats=build_ocr_turn_context({},ledger)
        self.assertEqual(len(ledger),365)
        self.assertEqual(json.dumps(ledger,ensure_ascii=False),before)
        self.assertLessEqual(len(selected),MAX_TURN_OCR_ROWS)
        self.assertEqual(stats["ledger_rows_available"],365)
        self.assertEqual(stats["rows_deferred"],365-len(selected))
        self.assertLess(stats["api_json_bytes"],stats["ledger_json_bytes"])
        self.assertEqual(carry,[])
        self.assertEqual(len(select_ai_ocr_evidence(ledger)[0]),365)

    def test_unverified_model_questions_keep_original_source_and_recheck_rule(self):
        ledger=rows()
        urgent=ledger[-1]["frame_id"]
        question={"source_frames":[urgent],
                  "question":"こだわり技固定が未確認",
                  "importance":"CRITICAL",
                  "recheck_when":"次に技名が表示された時"}
        first,_,meta=build_ocr_turn_context({},ledger)
        self.assertIn(urgent,{row["frame_id"] for row in first})
        state={"_ai_ocr_unresolved":[question],
               "_ai_ocr_presented_keys":meta["presented_keys"]}
        second,pending,stats=build_ocr_turn_context(state,ledger)
        self.assertEqual(pending[0]["verified"],False)
        self.assertEqual(pending[0]["source_frames"],[urgent])
        self.assertIn(urgent,{row["frame_id"] for row in second})
        self.assertLessEqual(stats["api_rows_sent"],112)
        self.assertGreater(stats["new_rows_sent"],0)

    def test_unacknowledged_previously_shown_rows_can_be_revisited(self):
        ledger=rows(12)
        first,_,meta=build_ocr_turn_context({},ledger)
        self.assertEqual(len(first),12)
        second,pending,stats=build_ocr_turn_context(
            {"_ai_ocr_presented_keys":meta["presented_keys"]},ledger)
        self.assertEqual(len(second),12)
        self.assertEqual(stats["new_rows_sent"],0)
        self.assertEqual(pending,[])
        self.assertEqual({r["frame_id"] for r in second},
                         {r["frame_id"] for r in ledger})

    def test_unpresented_backlog_reaches_all_365_frames(self):
        ledger=rows()
        seen=set()
        presented=set()
        for _ in range(5):
            batch,_,meta=build_ocr_turn_context(
                {"_ai_ocr_presented_keys":sorted(presented)},ledger)
            seen.update(row["frame_id"] for row in batch)
            presented.update(meta["presented_keys"])
        self.assertEqual(len(seen),365)

    def test_old_conflict_cannot_disappear_behind_newer_ocr(self):
        ledger=rows()
        ledger[38]["status"]="CONFLICT"
        batch,_,stats=build_ocr_turn_context({},ledger)
        self.assertIn(ledger[38]["frame_id"],
                      {r["frame_id"] for r in batch})
        self.assertLessEqual(stats["api_rows_sent"],MAX_TURN_OCR_ROWS)
        self.assertEqual(len(ledger),365)

    def test_many_pending_frames_leave_capacity_for_new_turn(self):
        ledger=rows()
        pending=[
            {"source_frames":[f"capture-{i:04d}"],
             "question":f"未解決{i}","importance":"IMPORTANT",
             "recheck_when":"next observation"}
            for i in range(16)]
        batch,pending_questions,stats=build_ocr_turn_context(
            {"_ai_ocr_unresolved":pending},ledger)
        self.assertEqual(len(pending_questions),16)
        self.assertIn("capture-0364",{r["frame_id"] for r in batch})
        self.assertLessEqual(stats["api_rows_sent"],MAX_TURN_OCR_ROWS)

    def test_pending_cannot_fabricate_source_or_confirm_state(self):
        known={"capture-1"}
        q=[
            {"source_frames":["not-real"],"question":"an unverified claim",
             "importance":"CRITICAL","recheck_when":"next turn"},
            {"source_frames":["capture-1"],"question":"?", "importance":"IMPORTANT",
             "recheck_when":""},
        ]
        safe=sanitize_pending_questions(q,known)
        self.assertEqual(len(safe),1)
        self.assertIs(safe[0]["verified"],False)

    def test_one_tactical_call_receives_bounded_prompt_and_carries_pending(self):
        assets=fixture_assets()
        state=new_replay(assets)
        state.update(phase="BATTLE",our_active="ゲンガー",
                     opponent_active="マスカーニャ",
                     selected_three=["ゲンガー","ドドゲザン","マリルリ"],
                     lead="ゲンガー",selection_assumed=False)
        ledger=rows()
        last=ledger[-1]["frame_id"]
        requests=[]
        def answer(_client, **kwargs):
            requests.append(json.loads(kwargs["content"][0]["text"]))
            return {
                "state_updates":[],
                "interpreted_own_active":"ゲンガー",
                "interpreted_opponent_active":"マスカーニャ",
                "processed_ocr_frames":[],
                "pending_ocr_questions":[{
                    "source_frames":[last],
                    "question":"未確認の相手行動",
                    "importance":"CRITICAL",
                    "recheck_when":"次のターンのOCR",
                }],
                "action":{"action_kind":"NO_ACTION","move":"",
                          "switch_target":"","warning":"未確定"},
            }
        with patch("champions_assist.match_replay._stream_json",side_effect=answer):
            after,report=run_replay_text_action(
                state=state,assets=assets,access_token="unused",
                model="test",expected_screen_label="TURN_ACTION",
                local_field_candidates=ledger,
                client_factory=lambda **kwargs:object())
        self.assertEqual(len(requests),1)
        self.assertLessEqual(len(requests[0]["ocr_candidates_unverified"]),112)
        self.assertEqual(requests[0]["ocr_deferred_rows_local_only"],253)
        self.assertEqual(after["_ai_ocr_unresolved"][0]["source_frames"],[last])
        self.assertIs(after["_ai_ocr_unresolved"][0]["verified"],False)
        self.assertEqual(after.get("_ai_ocr_ack_frames",[]),[])
        self.assertEqual(len(ledger),365)
        self.assertEqual(report["images_sent"],0)


if __name__=="__main__":
    unittest.main()
