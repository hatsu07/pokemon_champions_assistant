"""Issue #131: small OCR wire, complete local proof, and no false merging."""
import json
import unittest
from copy import deepcopy
from unittest.mock import patch
from champions_assist.ocr_wire import pack_ocr_wire, resolve_ocr_wire_answer
from champions_assist.match_replay import new_replay, run_replay_text_action
from tests.test_match_replay import fixture_assets


def row(frame, text, *, display="visual-1", field="screen_text.battle_message",
        confidence=.95, time=1.0):
    return {"frame_id":frame,"field":field,"raw_text":text,
            "display_id":display,"confidence":confidence,"observed_at":time}


class Issue131WireTests(unittest.TestCase):
    def test_punctuation_variations_join_with_originals_kept(self):
        raw=[row("f1","ゆけっ！ガブリアス！",time=1),
             row("f2","ゆけっ! ガブリアス!",time=1.2),
             row("f3","ゆけっ！ガブリアス。",time=1.3),
             row("f4","185/185",field="screen_text.own_hp_fraction",time=1.4)]
        original=deepcopy(raw)
        wire, mapping=pack_ocr_wire(raw)
        self.assertEqual(len(wire),2)
        self.assertEqual(raw,original)
        self.assertTrue(all(set(item)=={"id","field","text"} for item in wire))
        self.assertEqual([r["frame_id"] for r in mapping[wire[0]["id"]]],
                         ["f1","f2","f3"])
        self.assertLess(len(json.dumps(wire,ensure_ascii=False).encode()),
                        len(json.dumps(raw,ensure_ascii=False).encode()))

    def test_separate_episodes_and_meaningful_variants_never_merge(self):
        raw=[row("up","相手の攻撃が上がった！"),
             row("down","相手の攻撃が下がった！",time=1.1),
             row("name1","マスカーニャを繰り出した！",time=1.2),
             row("name2","マスカーニヤを繰り出した！",time=1.3),
             row("hp1","90%",field="screen_text.opponent_hp_percent",time=1.4),
             row("hp2","80%",field="screen_text.opponent_hp_percent",time=1.5),
             row("again","相手の攻撃が上がった！",display="visual-2",time=1.6)]
        wire,_=pack_ocr_wire(raw)
        self.assertEqual(len(wire),7)
        self.assertEqual(len({x["id"] for x in wire}),7)

    def test_missing_display_id_keeps_repetitions(self):
        wire,_=pack_ocr_wire([row("a","同じ",display=""),
                              row("b","同じ",display="",time=1.2)])
        self.assertEqual(len(wire),2)

    def test_resolve_id_without_losing_provenance_or_accepting_unknown_ids(self):
        raw=[row("a","ゆけっ！ゲンガー！",confidence=.80,time=1),
             row("b","ゆけっ!ゲンガー!",confidence=.96,time=1.1),
             row("c","ゆけっ！ゲンガー！",confidence=.93,time=1.2)]
        wire,mapping=pack_ocr_wire(raw)
        self.assertEqual(len(wire),1)
        token=wire[0]["id"]
        answer={"state_updates":[{"kind":"SWITCH","evidence_frames":[token]}],
                "processed_ocr_frames":[token],
                "pending_ocr_questions":[{"source_frames":[token]}]}
        mapped=resolve_ocr_wire_answer(answer,mapping,raw)
        self.assertEqual(mapped["state_updates"][0]["evidence_frames"],["b","c"])
        self.assertEqual(mapped["processed_ocr_frames"],["a","b","c"])
        self.assertEqual(mapped["pending_ocr_questions"][0]["source_frames"],
                         ["a","b","c"])
        self.assertEqual(answer["processed_ocr_frames"],[token])
        invalid=resolve_ocr_wire_answer(
            {"state_updates":[{"kind":"SWITCH","evidence_frames":["fake"]}]},
            mapping,raw)
        self.assertEqual(invalid["state_updates"][0]["evidence_frames"],["fake"])

    def test_turn_uses_only_short_ids_and_resolves_before_validation(self):
        assets=fixture_assets()
        state=new_replay(assets)
        state.update(phase="BATTLE",
            selected_three=["ゲンガー","ドドゲザン","マリルリ"],
            lead="ゲンガー",our_active="",opponent_active="",
            selection_assumed=False)
        source=[row("a","ゆけっ！ゲンガー！",display="own"),
                row("b","相手のマスカーニャを繰り出した！",
                    display="opponent",time=1.2)]
        called=[]
        def response(_client,**kwargs):
            prompt=json.loads(kwargs["content"][0]["text"])
            called.append(prompt)
            keys=[r["id"] for r in prompt["ocr_candidates_unverified"]]
            def ev(side,name,key):
                return {"kind":"SWITCH","side":side,"pokemon":name,
                        "from_pokemon":"","value":"","stat":"","delta":0,
                        "certainty":"CONFIRMED","evidence_frames":[key]}
            return {"state_updates":[ev("own","ゲンガー",keys[0]),
                                     ev("opponent","マスカーニャ",keys[1])],
                    "interpreted_own_active":"ゲンガー",
                    "interpreted_opponent_active":"マスカーニャ",
                    "pending_ocr_questions":[],"processed_ocr_frames":[],
                    "action":{"action_kind":"MOVE","move":"じしん",
                              "switch_target":"","warning":""}}
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=response):
            after,report=run_replay_text_action(
                state=state,assets=assets,access_token="unused",
                model="test",expected_screen_label="TURN_ACTION",
                local_field_candidates=source,
                client_factory=lambda **kwargs:object())
        self.assertEqual(len(called),1)
        self.assertTrue(all(set(r)=={"id","field","text"}
                   for r in called[0]["ocr_candidates_unverified"]))
        self.assertEqual(after["our_active"],"ゲンガー")
        self.assertEqual(after["opponent_active"],"マスカーニャ")
        self.assertEqual(report["images_sent"],0)
        self.assertNotIn("recent_history",called[0])
        self.assertNotIn("our_registered_moves",called[0])
        self.assertNotIn("known_hp_by_pokemon",called[0])


if __name__=="__main__":
    unittest.main()
