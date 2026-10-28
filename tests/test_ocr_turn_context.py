"""Issue #102/C: GPT-directed OCR relevance, no arbitrary row-count cutoff."""
import json
import unittest
from unittest.mock import patch

from champions_assist.ocr_turn_context import (
    build_ocr_turn_context, sanitize_pending_questions,
)
from champions_assist.ocr_ai_evidence import select_ai_ocr_evidence
from champions_assist.match_replay import run_replay_text_action, new_replay
from tests.test_ocr_ai_context import fixture_assets


def rows(count=365, offset=0):
    return [
        {"frame_id":f"capture-{i:04d}",
         "field":"screen_text.battle_message",
         "raw_text":f"未検証の戦闘イベント {i}",
         "confidence":0.93,
         "observed_at":1000.0+i*0.5}
        for i in range(offset,offset+count)
    ]


def unresolved(frame,question="相手のこだわり固定が未確定"):
    return {
        "source_frames":[frame],
        "question":question,
        "importance":"CRITICAL",
        "recheck_when":"次の技使用を観測したとき",
    }


def battle_state(assets):
    state=new_replay(assets)
    state.update(phase="BATTLE",our_active="ゲンガー",
                 opponent_active="マスカーニャ",
                 selected_three=["ゲンガー","ドドゲザン","マリルリ"],
                 lead="ゲンガー",selection_assumed=False)
    return state


def model_reply(pending,own="ゲンガー",processed=None):
    return {
        "state_updates":[],
        "interpreted_own_active":own,
        "interpreted_opponent_active":"マスカーニャ",
        "processed_ocr_frames":processed if processed is not None else [],
        "pending_ocr_questions":pending,
        "action":{"action_kind":"NO_ACTION","move":"",
                  "switch_target":"","warning":"未確認事項あり"},
    }


class ModelSelectedOcrContextTests(unittest.TestCase):
    def test_first_turn_includes_all_365_without_arbitrary_cutoff(self):
        ledger=rows()
        before=json.dumps(ledger,ensure_ascii=False)
        sent,pending,meta=build_ocr_turn_context({},ledger)
        self.assertEqual(len(sent),365)
        self.assertEqual(meta["new_rows_sent"],365)
        self.assertEqual(meta["prior_observations_not_resent"],0)
        self.assertEqual(pending,[])
        self.assertEqual(json.dumps(ledger,ensure_ascii=False),before)
        self.assertEqual(len(select_ai_ocr_evidence(ledger)[0]),365)

    def test_next_turn_contains_only_new_and_gpt_selected_unresolved(self):
        ledger=rows()
        first,_,meta=build_ocr_turn_context({},ledger)
        self.assertEqual(len(first),365)
        critical=["capture-0008","capture-0359"]
        state={"_ai_ocr_presented_keys":meta["presented_keys"],
               "_ai_ocr_unresolved":[unresolved(x) for x in critical]}
        second,pending,stats=build_ocr_turn_context(state,ledger+rows(8,365))
        frames={r["frame_id"] for r in second}
        self.assertEqual(len(second),10)
        self.assertEqual(frames,set(critical)|{
            f"capture-{i:04d}" for i in range(365,373)})
        self.assertEqual(stats["new_rows_sent"],8)
        self.assertEqual(stats["unresolved_rows_resent"],2)
        self.assertEqual(stats["prior_observations_not_resent"],363)
        self.assertEqual(len(pending),2)
        self.assertTrue(all(q["verified"] is False for q in pending))
        self.assertEqual(len(ledger),365)

    def test_omission_does_not_make_ignored_ocr_confirmed(self):
        ledger=rows(20)
        first,_,meta=build_ocr_turn_context({},ledger)
        self.assertEqual(len(first),20)
        second,pending,stats=build_ocr_turn_context(
            {"_ai_ocr_presented_keys":meta["presented_keys"],
             "_ai_ocr_unresolved":[]},ledger)
        self.assertEqual(second,[])
        self.assertEqual(pending,[])
        self.assertEqual(stats["prior_observations_not_resent"],20)
        # The full unverified text is still available locally.
        self.assertEqual(len(select_ai_ocr_evidence(ledger)[0]),20)
        self.assertTrue(all(r.get("game_validated") is None for r in ledger))

    def test_no_locally_imposed_pending_question_quota(self):
        ledger=rows(50)
        _,_,meta=build_ocr_turn_context({},ledger)
        pending=[unresolved(f"capture-{i:04d}") for i in range(26)]
        second,questions,_=build_ocr_turn_context(
            {"_ai_ocr_presented_keys":meta["presented_keys"],
             "_ai_ocr_unresolved":pending},ledger)
        self.assertEqual(len(second),26)
        self.assertEqual(len(questions),26)

    def test_new_reading_on_old_frame_remains_new(self):
        ledger=rows(1)
        _,_,meta=build_ocr_turn_context({},ledger)
        later={**ledger[0],"raw_text":"別の戦闘イベント",
               "observed_at":ledger[0]["observed_at"]+1}
        selected,_,stats=build_ocr_turn_context(
            {"_ai_ocr_presented_keys":meta["presented_keys"]},
            ledger+[later])
        self.assertEqual(selected,[later])
        self.assertEqual(stats["new_rows_sent"],1)

    def test_critical_conflict_retained_by_gpt_not_local_text_heuristic(self):
        ledger=rows(365)
        ledger[14]["certainty"]="CONFLICT"
        _,_,meta=build_ocr_turn_context({},ledger)
        state={"_ai_ocr_presented_keys":meta["presented_keys"],
               "_ai_ocr_unresolved":[unresolved(ledger[14]["frame_id"],
                                              "矛盾した相手の素早さ情報")]}
        selected,pending,_=build_ocr_turn_context(state,ledger)
        self.assertEqual([r["frame_id"] for r in selected],
                         [ledger[14]["frame_id"]])
        self.assertEqual(pending[0]["verified"],False)

    def test_pending_source_proof_is_required(self):
        q=[unresolved("not-in-ledger"),unresolved("capture-0001")]
        pending=sanitize_pending_questions(q,{"capture-0001"})
        self.assertEqual(len(pending),1)
        self.assertEqual(pending[0]["source_frames"],["capture-0001"])
        self.assertEqual(pending[0]["verified"],False)

    def test_same_tactical_request_selects_next_turn_pending(self):
        assets=fixture_assets()
        state=battle_state(assets)
        raw=rows()
        proof=raw[-1]["frame_id"]
        submitted=[]
        def first_reply(_client,**kwargs):
            submitted.append(json.loads(kwargs["content"][0]["text"]))
            return model_reply([unresolved(proof)])
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=first_reply):
            after,report=run_replay_text_action(
                state=state,assets=assets,access_token="unused",
                model="test",expected_screen_label="TURN_ACTION",
                local_field_candidates=raw,
                client_factory=lambda **kwargs:object())
        self.assertEqual(len(submitted),1)
        self.assertEqual(len(submitted[0]["ocr_candidates_unverified"]),365)
        self.assertEqual(after["_ai_ocr_unresolved"][0]["source_frames"],[proof])
        self.assertEqual(after.get("_ai_ocr_ack_frames",[]),[])
        self.assertEqual(report["images_sent"],0)
        def second_reply(_client,**kwargs):
            submitted.append(json.loads(kwargs["content"][0]["text"]))
            return model_reply([unresolved(proof)])
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=second_reply):
            second,_=run_replay_text_action(
                state=after,assets=assets,access_token="unused",
                model="test",expected_screen_label="TURN_ACTION",
                local_field_candidates=raw+rows(6,365),
                client_factory=lambda **kwargs:object())
        self.assertEqual(len(submitted),2)
        self.assertEqual(len(submitted[1]["ocr_candidates_unverified"]),7)
        self.assertEqual(submitted[1]["ocr_prior_rows_not_resent"],364)
        self.assertEqual(len(raw),365)
        self.assertEqual(second["_ai_ocr_unresolved"][0]["source_frames"],[proof])

    def test_processed_frame_cannot_ack_gpt_retained_uncertainty(self):
        assets=fixture_assets()
        raw=rows(3)
        proof=raw[2]["frame_id"]
        response=model_reply([unresolved(proof)],processed=[proof])
        with patch("champions_assist.match_replay._stream_json",
                   return_value=response):
            after,_=run_replay_text_action(
                state=battle_state(assets),assets=assets,
                access_token="unused",model="test",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=raw,
                client_factory=lambda **kwargs:object())
        self.assertNotIn(proof,after.get("_ai_ocr_ack_frames",[]))
        self.assertEqual(after["_ai_ocr_unresolved"][0]["verified"],False)

    def test_blocked_actor_preserves_raw_evidence_and_question(self):
        assets=fixture_assets()
        raw=rows(4)
        proof=raw[-1]["frame_id"]
        response=model_reply([unresolved(proof)],own="ドドゲザン",
                             processed=[proof])
        with patch("champions_assist.match_replay._stream_json",
                   return_value=response):
            after,report=run_replay_text_action(
                state=battle_state(assets),assets=assets,
                access_token="unused",model="test",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=raw,
                client_factory=lambda **kwargs:object())
        self.assertEqual(report["reason"],"AI_OCR_OWN_ACTIVE_UNVERIFIED")
        self.assertEqual(after.get("_ai_ocr_ack_frames",[]),[])
        self.assertEqual(after.get("_ai_ocr_presented_keys",[]),[])
        self.assertEqual(after["_ai_ocr_unresolved"][0]["source_frames"],[proof])
        self.assertEqual(after["our_active"],"ゲンガー")


if __name__=="__main__":
    unittest.main()
