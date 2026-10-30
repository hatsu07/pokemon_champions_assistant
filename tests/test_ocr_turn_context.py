"""Issue #102/C: GPT-directed OCR relevance, no arbitrary row-count cutoff."""
import json
import unittest
import numpy as np
from unittest.mock import patch
from tests.ocr_wire_fixture import reply_with_wire_ids

from champions_assist.ocr_turn_context import (
    build_ocr_turn_context, sanitize_pending_questions,
)
from champions_assist.ocr_ai_evidence import select_ai_ocr_evidence
from champions_assist.match_replay import run_replay_text_action, run_replay_batch, new_replay, _text_action_eligible
from tests.test_ocr_ai_context import fixture_assets
from tests.test_match_replay import frame


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

    def test_previous_unresolved_survives_temporary_ocr_snapshot_gap(self):
        previous=unresolved("capture-0012")
        state={"_ai_ocr_unresolved":[dict(previous,verified=False)],
               "_ai_ocr_presented_keys":["prior-hash"]}
        sent,pending,stats=build_ocr_turn_context(state,[])
        self.assertEqual(sent,[])
        self.assertEqual(len(pending),1)
        self.assertEqual(pending[0]["source_frames"],["capture-0012"])
        self.assertEqual(pending[0]["verified"],False)
        self.assertEqual(stats["ledger_rows_available"],0)

    def test_forced_replacement_unknown_actor_can_abstain_without_false_error(self):
        assets=fixture_assets()
        state=battle_state(assets)
        state["our_active"]=""
        state["awaiting_replacement"]=True
        state["battle_evidence"]["hp_by_pokemon"]["own"]["ゲンガー"]="0/120"
        with patch("champions_assist.match_replay._stream_json",
                   return_value=model_reply([],own="")):
            after,report=run_replay_text_action(
                state=state,assets=assets,access_token="unused",model="test",
                expected_screen_label="SWITCH_SELECTION",
                local_field_candidates=rows(2),
                client_factory=lambda **kwargs:object())
        self.assertEqual(report["status"],"NO_ACTION")
        self.assertEqual(report["images_sent"],0)
        self.assertEqual(after["our_active"],"")

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
            return reply_with_wire_ids(
                kwargs,model_reply([unresolved(proof)]),raw)
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
            return reply_with_wire_ids(
                kwargs,model_reply([unresolved(proof)]),raw+rows(6,365))
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

    def test_image_fallback_uses_same_gpt_selected_context_and_one_call(self):
        assets=fixture_assets()
        state=battle_state(assets)
        ledger=rows()
        keep="capture-0358"
        seen=[]
        def reply(_client,**kwargs):
            prompt=json.loads(kwargs["content"][0]["text"])
            seen.append((kwargs,prompt))
            return {
                "observation":frame("COMMAND_MENU",
                                    our_active="ゲンガー",
                                    opponent_active="マスカーニャ"),
                "action":{"action_kind":"NO_ACTION","move":"",
                          "switch_target":"","warning":""},
                "selection":{"selected_three":[],"lead":""},
                "processed_ocr_frames":[],
                "pending_ocr_questions":[unresolved(keep)],
            }
        img=np.zeros((80,120,3),dtype=np.uint8)
        with patch("champions_assist.match_replay._stream_json",side_effect=reply):
            after,report=run_replay_batch(
                [img],state=state,assets=assets,access_token="unused",
                model="test",expected_screen_label="TURN_ACTION",
                local_field_candidates=ledger,
                client_factory=lambda **kwargs:object())
        self.assertEqual(len(seen),1)
        self.assertIn("pending_ocr_questions",seen[0][0]["schema"]["required"])
        self.assertEqual(len(seen[0][1]["local_field_candidates_unverified"]),365)
        self.assertEqual(len([x for x in seen[0][0]["content"] if x["type"]=="input_image"]),1)
        self.assertEqual(report["images_sent"],1)
        self.assertEqual(after["_ai_ocr_unresolved"][0]["source_frames"],[keep])
        self.assertEqual(after.get("_ai_ocr_ack_frames",[]),[])
        # Next turn crosses to text-only without re-sending 364 old rows.
        def text_reply(_client,**kwargs):
            seen.append((kwargs,json.loads(kwargs["content"][0]["text"])))
            return reply_with_wire_ids(
                kwargs,model_reply([unresolved(keep)]),ledger+rows(5,365))
        with patch("champions_assist.match_replay._stream_json",side_effect=text_reply):
            following,_=run_replay_text_action(
                state=after,assets=assets,access_token="unused",
                model="test",expected_screen_label="TURN_ACTION",
                local_field_candidates=ledger+rows(5,365),
                client_factory=lambda **kwargs:object())
        self.assertEqual(len(seen),2)
        self.assertEqual(len(seen[1][1]["ocr_candidates_unverified"]),6)
        self.assertEqual(following["_ai_ocr_unresolved"][0]["source_frames"],[keep])

    def test_text_to_image_fallback_keeps_only_selected_old_ocr(self):
        assets=fixture_assets()
        state=battle_state(assets)
        ledger=rows()
        keep="capture-0200"
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=lambda _client,**kw: reply_with_wire_ids(
                       kw,model_reply([unresolved(keep)]),ledger)):
            after,_=run_replay_text_action(
                state=state,assets=assets,access_token="unused",
                model="test",expected_screen_label="TURN_ACTION",
                local_field_candidates=ledger,
                client_factory=lambda **kwargs:object())
        inspected=[]
        def img_reply(_client,**kwargs):
            inspected.append(json.loads(kwargs["content"][0]["text"]))
            return {
                "observation":frame("COMMAND_MENU",our_active="ゲンガー",
                                    opponent_active="マスカーニャ"),
                "action":{"action_kind":"NO_ACTION","move":"",
                          "switch_target":"","warning":""},
                "selection":{"selected_three":[],"lead":""},
                "processed_ocr_frames":[],
                "pending_ocr_questions":[unresolved(keep)],
            }
        with patch("champions_assist.match_replay._stream_json",side_effect=img_reply):
            following,report=run_replay_batch(
                [np.zeros((80,120,3),dtype=np.uint8)],
                state=after,assets=assets,access_token="unused",model="test",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=ledger+rows(7,365),
                client_factory=lambda **kwargs:object())
        self.assertEqual(len(inspected),1)
        self.assertEqual(len(inspected[0]["local_field_candidates_unverified"]),8)
        self.assertEqual(inspected[0]["ocr_prior_rows_not_resent"],364)
        self.assertEqual(following["_ai_ocr_unresolved"][0]["source_frames"],[keep])
        self.assertEqual(report["images_sent"],1)

    def test_rejected_tactical_action_does_not_rollback_sent_evidence(self):
        assets=fixture_assets()
        state=battle_state(assets)
        proof=rows(7)
        response=model_reply([])
        response["action"]={"action_kind":"MOVE","move":"技候補に存在しない技",
                            "switch_target":"","warning":""}
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=lambda _client,**kw:
                       reply_with_wire_ids(kw,response,proof)):
            after,report=run_replay_text_action(
                state=state,assets=assets,access_token="unused",model="test",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=proof,
                client_factory=lambda **kwargs:object())
        self.assertEqual(report["status"],"BLOCKED")
        self.assertEqual(len(after.get("_ai_ocr_presented_keys",[])),7)
        self.assertEqual(after.get("_ai_ocr_ack_frames",[]),[])
        reattempt,_,_=build_ocr_turn_context(after,proof)
        self.assertEqual(len(reattempt),0)

    def test_forced_switch_can_use_text_when_own_actor_unknown_but_fainted_known(self):
        assets=fixture_assets()
        state=battle_state(assets)
        state["our_active"]=""
        state["awaiting_replacement"]=True
        state["battle_evidence"]["hp_by_pokemon"]["own"]["ゲンガー"]="0/120"
        self.assertTrue(_text_action_eligible("SWITCH_SELECTION",state,assets))
        answer=model_reply([],own="")
        answer["action"]={"action_kind":"SWITCH","move":"",
                          "switch_target":"ドドゲザン","warning":""}
        submissions=[]
        def reply(_client,**kwargs):
            submissions.append(kwargs)
            return answer
        with patch("champions_assist.match_replay._stream_json",side_effect=reply):
            after,report=run_replay_text_action(
                state=state,assets=assets,access_token="unused",model="test",
                expected_screen_label="SWITCH_SELECTION",
                local_field_candidates=rows(3),
                client_factory=lambda **kwargs:object())
        self.assertEqual(len(submissions),1)
        self.assertEqual(report["images_sent"],0)
        self.assertEqual(report["action"]["target"],"ドドゲザン")
        self.assertNotIn("ゲンガー",
                         json.loads(submissions[0]["content"][0]["text"])[
                             "living_selected_bench_candidates"])
        self.assertEqual(after["our_active"],"")

    def test_forced_switch_blank_actor_ok_with_confirmed_faint(self):
        assets=fixture_assets()
        state=battle_state(assets)
        state["our_active"]="ゲンガー"
        state["awaiting_replacement"]=True
        state["battle_evidence"]["hp_by_pokemon"]["own"]["ゲンガー"]="0/120"
        answer=model_reply([],own="")
        answer["action"]={"action_kind":"SWITCH","move":"",
                          "switch_target":"ドドゲザン","warning":""}
        with patch("champions_assist.match_replay._stream_json",return_value=answer):
            after,report=run_replay_text_action(
                state=state,assets=assets,access_token="unused",model="test",
                expected_screen_label="SWITCH_SELECTION",
                local_field_candidates=rows(3),
                client_factory=lambda **kwargs:object())
        self.assertNotEqual(report.get("reason"),"AI_OCR_OWN_ACTIVE_UNVERIFIED")
        self.assertEqual(after["our_active"],"ゲンガー")

    def test_voluntary_switch_blank_actor_still_blocks(self):
        assets=fixture_assets()
        state=battle_state(assets)
        state["awaiting_replacement"]=False
        state["battle_evidence"]["hp_by_pokemon"]["own"]["ゲンガー"]="90/120"
        answer=model_reply([],own="")
        answer["action"]={"action_kind":"SWITCH","move":"",
                          "switch_target":"ドドゲザン","warning":""}
        events=[]
        with patch("champions_assist.match_replay._stream_json",return_value=answer):
            after,report=run_replay_text_action(
                state=state,assets=assets,access_token="unused",model="test",
                expected_screen_label="SWITCH_SELECTION",
                local_field_candidates=rows(3),
                client_factory=lambda **kwargs:object(),
                diagnostic_sink=lambda e,v:events.append(e))
        self.assertEqual(report["status"],"BLOCKED")
        self.assertEqual(report["reason"],"AI_OCR_OWN_ACTIVE_UNVERIFIED")
        self.assertEqual(after["our_active"],"ゲンガー")
        self.assertIn("AI_OCR_OWN_ACTIVE_BLOCK_DETAIL",events)

    def test_unknown_own_without_confirmed_faint_requires_visual_fallback(self):
        assets=fixture_assets()
        state=battle_state(assets)
        state["our_active"]=""
        state["awaiting_replacement"]=True
        self.assertFalse(_text_action_eligible("SWITCH_SELECTION",state,assets))
        state["awaiting_replacement"]=False
        self.assertFalse(_text_action_eligible("SWITCH_SELECTION",state,assets))

    def test_unclassified_image_fallback_with_new_ocr_keeps_model_question(self):
        assets=fixture_assets()
        state=battle_state(assets)
        recent=rows(4)
        keep=recent[-1]["frame_id"]
        calls=[]
        def reply(_client,**kwargs):
            calls.append(json.loads(kwargs["content"][0]["text"]))
            return {
                "observation":frame("ACTION_RESOLUTION",our_active="ゲンガー",
                                    opponent_active="マスカーニャ"),
                "action":{"action_kind":"NO_ACTION","move":"",
                          "switch_target":"","warning":""},
                "selection":{"selected_three":[],"lead":""},
                "processed_ocr_frames":[],
                "pending_ocr_questions":[unresolved(keep)],
            }
        with patch("champions_assist.match_replay._stream_json",side_effect=reply):
            after,report=run_replay_batch(
                [np.zeros((80,120,3),dtype=np.uint8)],
                state=state,assets=assets,access_token="unused",
                model="test",expected_screen_label=None,
                local_field_candidates=recent,
                client_factory=lambda **kwargs:object())
        self.assertEqual(len(calls),1)
        self.assertEqual(len(calls[0]["local_field_candidates_unverified"]),4)
        self.assertEqual(after["_ai_ocr_unresolved"][0]["source_frames"],[keep])
        self.assertEqual(report["images_sent"],1)

    def test_unconfirmed_opponent_in_forced_switch_uses_visual_fallback(self):
        assets=fixture_assets()
        state=battle_state(assets)
        state["our_active"]=""
        state["opponent_active"]=""
        state["awaiting_replacement"]=True
        state["battle_evidence"]["hp_by_pokemon"]["own"]["ゲンガー"]="0/120"
        self.assertFalse(_text_action_eligible("SWITCH_SELECTION",state,assets))

    def test_image_non_action_screen_keeps_previous_unresolved(self):
        assets=fixture_assets()
        state=battle_state(assets)
        state["_ai_ocr_unresolved"]=[dict(unresolved("capture-0001"),verified=False)]
        state["_ai_ocr_presented_keys"]=["previous-key"]
        def reply(_client,**_kwargs):
            return {
                "observation":frame("ACTION_RESOLUTION",
                                    our_active="ゲンガー",
                                    opponent_active="マスカーニャ"),
                "action":{"action_kind":"NO_ACTION","move":"",
                          "switch_target":"","warning":""},
                "selection":{"selected_three":[],"lead":""},
                "processed_ocr_frames":[],"pending_ocr_questions":[],
            }
        with patch("champions_assist.match_replay._stream_json",side_effect=reply):
            after,_=run_replay_batch(
                [np.zeros((80,120,3),dtype=np.uint8)],
                state=state,assets=assets,access_token="unused",
                model="test",expected_screen_label=None,
                local_field_candidates=[],client_factory=lambda **kwargs:object())
        self.assertEqual(after["_ai_ocr_unresolved"],state["_ai_ocr_unresolved"])
        self.assertEqual(after["_ai_ocr_presented_keys"],["previous-key"])

    def test_legacy_reply_without_relevance_decision_does_not_retire_ocr(self):
        assets=fixture_assets()
        state=battle_state(assets)
        raw=rows(12)
        # An HTTP response proves the 12 OCR rows were sent even if
        # its obsolete schema cannot make an OCR relevance decision.
        legacy={"action_kind":"NO_ACTION","move":"",
                "switch_target":"","warning":""}
        with patch("champions_assist.match_replay._stream_json",
                   return_value=legacy):
            after,report=run_replay_text_action(
                state=state,assets=assets,access_token="unused",
                model="test",expected_screen_label="TURN_ACTION",
                local_field_candidates=raw,
                client_factory=lambda **kwargs:object())
        self.assertEqual(len(after.get("_ai_ocr_presented_keys",[])),12)
        self.assertEqual(after.get("_ai_ocr_ack_frames",[]),[])
        selected,_,_=build_ocr_turn_context(after,raw)
        self.assertEqual(len(selected),0)
        self.assertEqual(report["status"],"NO_ACTION")

    def test_legacy_ack_is_independent_from_relevance_compression(self):
        assets=fixture_assets()
        state=battle_state(assets)
        raw=rows(4)
        response={
            "state_updates":[],
            "interpreted_own_active":"ゲンガー",
            "interpreted_opponent_active":"マスカーニャ",
            "processed_ocr_frames":[raw[0]["frame_id"]],
            "action":{"action_kind":"NO_ACTION","move":"",
                      "switch_target":"","warning":""},
        }
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=lambda _client,**kw:
                       reply_with_wire_ids(kw,response,raw)):
            after,_=run_replay_text_action(
                state=state,assets=assets,access_token="unused",
                model="test",expected_screen_label="TURN_ACTION",
                local_field_candidates=raw,
                client_factory=lambda **kwargs:object())
        self.assertEqual(len(after.get("_ai_ocr_presented_keys",[])),4)
        self.assertEqual(after["_ai_ocr_ack_frames"],[raw[0]["frame_id"]])
        self.assertEqual(len(raw),4)

    def test_turn_action_with_unknown_opponent_remains_text_eligible(self):
        assets=fixture_assets()
        state=battle_state(assets)
        state["opponent_active"]=""
        state["opponent_active_unknown"]=True
        self.assertTrue(_text_action_eligible("TURN_ACTION",state,assets))
        state["our_active"]=""
        self.assertFalse(_text_action_eligible("TURN_ACTION",state,assets))
        # A generic OCR entry is not necessarily enough to identify own lead.
        self.assertFalse(_text_action_eligible(
            "TURN_ACTION",state,assets,local_rows=rows(1)))
        self.assertTrue(_text_action_eligible(
            "TURN_ACTION",state,assets,local_rows=[{
                "field":"screen_text.own_active_name",
                "frame_id":"own-name-1","raw_text":"ゲンガー",
                "observed_at":1.0,"confidence":0.9}]))

    def test_processed_frame_cannot_ack_gpt_retained_uncertainty(self):
        assets=fixture_assets()
        raw=rows(3)
        proof=raw[2]["frame_id"]
        response=model_reply([unresolved(proof)],processed=[proof])
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=lambda _client,**kw:
                       reply_with_wire_ids(kw,response,raw)):
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
                   side_effect=lambda _client,**kw:
                       reply_with_wire_ids(kw,response,raw)):
            after,report=run_replay_text_action(
                state=battle_state(assets),assets=assets,
                access_token="unused",model="test",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=raw,
                client_factory=lambda **kwargs:object())
        self.assertEqual(report["reason"],"AI_OCR_OWN_ACTIVE_UNVERIFIED")
        self.assertEqual(after.get("_ai_ocr_ack_frames",[]),[])
        self.assertEqual(len(after.get("_ai_ocr_presented_keys",[])),4)
        self.assertEqual(after["_ai_ocr_unresolved"][0]["source_frames"],[proof])
        self.assertEqual(after["our_active"],"ゲンガー")


if __name__=="__main__":
    unittest.main()
