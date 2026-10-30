"""Live diagnostics must be safely correlated and nonblocking."""
import json
import tempfile
import unittest
from champions_assist.match_debug_log import MatchDebugLog
from champions_assist.live_diagnostics import (
    redact_battle_detail,current_state_digest,ai_input_digest,ai_response_digest)


class LiveDiagnosticsTests(unittest.TestCase):
    def test_raw_ocr_redacted_and_arbitrary_fields_rejected(self):
        row=redact_battle_detail({
            "raw_text":"マリルリをくりだした Bearer abcdefghij1234",
            "token":"secret123","prompt":"unfiltered secret prompt",
            "screenshot":"/screen.png","frame_id":"capture-2",
            "confidence":0.97})
        raw=json.dumps(row,ensure_ascii=False)
        self.assertIn("マリルリ",raw)
        self.assertIn("[REDACTED]",raw)
        for secret in ("secret123","unfiltered secret prompt","/screen.png"):
            self.assertNotIn(secret,raw)

    def test_actor_hp_and_choice_projection(self):
        d=current_state_digest({
            "our_active":"マリルリ","opponent_active":"ガブリアス",
            "selected_three":["マリルリ","ルカリオ","マスカーニャ"],
            "battle_evidence":{
                "hp_by_pokemon":{"own":{"マリルリ":"180/200"}},
                "choice_lock":{"pokemon":"マリルリ","move":"アクアジェット"}}})
        self.assertEqual(d["our_active"],"マリルリ")
        self.assertEqual(d["known_hp"]["own"]["マリルリ"],"180/200")
        self.assertEqual(d["choice_lock"]["move"],"アクアジェット")

    def test_ai_input_and_raw_action_projection(self):
        i=ai_input_digest({
            "screen_kind":"TURN_ACTION","active_own":"マリルリ",
            "living_selected_bench_candidates":["ルカリオ"],
            "our_registered_moves":["アクアジェット"],
            "ocr_candidates_unverified":[{}],
            "frozen_gate":"do not log this"})
        o=ai_response_digest({"action_kind":"SWITCH",
            "switch_target":"マリルリ","warning":"ミス",
            "token":"never log"})
        self.assertEqual(i["ocr_candidate_count"],1)
        self.assertEqual(i["selected_bench"],["ルカリオ"])
        self.assertNotIn("frozen_gate",i)
        self.assertEqual(o["switch_target"],"マリルリ")
        self.assertNotIn("token",o)

    def test_current_selected_three_registered_moves_are_in_input_digest(self):
        selected={
            "ガブリアス":["じしん","げきりん","まもる","アイアンヘッド"],
            "マリルリ":["アクアジェット","じゃれつく","たきのぼり","まもる"],
            "ドドゲザン":["ドゲザン","ふいうち","つるぎのまい","アイアンヘッド"],
        }
        digest=ai_input_digest({
            "registered_selected_moves":selected,
            "our_registered_moves":["旧形式の技"],
            "selected_three":list(selected),
        })
        self.assertEqual(digest["registered_moves"],selected)
        self.assertEqual(set(digest["registered_moves"]),set(selected))
        self.assertNotIn("旧形式の技",json.dumps(digest,ensure_ascii=False))

    def test_turn_payload_size_is_exact_and_pending_is_not_tactical(self):
        from champions_assist.live_diagnostics import turn_payload_byte_breakdown
        prompt={
            "screen_kind":"TURN_ACTION",
            "selected_three":["ガブリアス","マリルリ","ドドゲザン"],
            "ocr_candidates_unverified":[{"id":"e1","text":"ゆけっ！ガブリアス！"}],
            "ocr_pending_questions_unverified":[{
                "source_frames":["p1"],"question":"持ち物は？"}],
            "ocr_prior_rows_not_resent":34,
            "registered_selected_moves":{"ガブリアス":["じしん","まもる"]},
        }
        sizes=turn_payload_byte_breakdown(prompt)
        actual=len(json.dumps(prompt,ensure_ascii=False,
                              separators=(",",":")).encode("utf-8"))
        self.assertEqual(sizes["prompt_utf8_bytes"],actual)
        self.assertEqual(sum(v for k,v in sizes.items()
                             if k!="prompt_utf8_bytes"),actual)
        for key in ("new_ocr_field_utf8_bytes",
                    "pending_ocr_field_utf8_bytes",
                    "ocr_transport_metadata_utf8_bytes"):
            self.assertGreater(sizes[key],0)
        no_pending=dict(prompt)
        no_pending["ocr_pending_questions_unverified"]=[]
        small=turn_payload_byte_breakdown(no_pending)
        self.assertEqual(sizes["non_ocr_json_bytes"],
                         small["non_ocr_json_bytes"])
        self.assertEqual(sizes["new_ocr_field_utf8_bytes"],
                         small["new_ocr_field_utf8_bytes"])
        self.assertGreater(sizes["pending_ocr_field_utf8_bytes"],
                           small["pending_ocr_field_utf8_bytes"])
        # A diagnostic recorder must retain every new integer measurement.
        from champions_assist.live_diagnostics import redact_battle_detail
        self.assertEqual(redact_battle_detail(sizes),sizes)
        self.assertEqual(turn_payload_byte_breakdown({}),
                         {"prompt_utf8_bytes":2,"non_ocr_json_bytes":2,
                          "new_ocr_field_utf8_bytes":0,
                          "pending_ocr_field_utf8_bytes":0,
                          "ocr_transport_metadata_utf8_bytes":0})

    def test_async_chain_order_and_ids(self):
        with tempfile.TemporaryDirectory() as d:
            log=MatchDebugLog(d,async_mode=True)
            log.write_diagnostic("OCR_RAW",{
                "raw_text":"ゆけっ!マリルリ!","frame_id":"capture-7"},
                turn_epoch=1)
            log.write_diagnostic("AI_INPUT",{"active_own":"マリルリ"},
                                 request_id="req-3",turn_epoch=2)
            log.write_diagnostic("AI_RESPONSE_RAW",{
                "action_kind":"SWITCH","switch_target":"マリルリ"},
                request_id="req-3",turn_epoch=2)
            log.write_diagnostic("GUI_DISPLAY",{
                "displayed":"次の行動：マリルリに交代。"},
                request_id="req-3",turn_epoch=2)
            log.close()
            rows=[json.loads(line) for line in log.path.read_text(
                encoding="utf-8").splitlines()]
            self.assertEqual([x["event"] for x in rows[1:5]],
                ["OCR_RAW","AI_INPUT","AI_RESPONSE_RAW","GUI_DISPLAY"])
            self.assertEqual(rows[3]["request_id"],rows[4]["request_id"])
            self.assertIn("DIAGNOSTIC_QUEUE_SUMMARY",[r["event"] for r in rows])

    def test_oversize_detail_truncates(self):
        d=redact_battle_detail({
            "raw_text":"あ"*900,"warning":"あ"*900,
            "displayed":"あ"*900,"evidence":"あ"*900})
        self.assertEqual(d["status"],"DETAIL_TRUNCATED")


if __name__=="__main__":
    unittest.main()
