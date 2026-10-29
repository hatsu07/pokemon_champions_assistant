"""Issue #125 transport/first-turn OCR regressions. No network, no image model."""
import json
import unittest
from threading import Event, Thread
import numpy as np
from unittest.mock import patch

from champions_assist.match_replay import (
    new_replay, run_replay_text_action, run_replay_batch,
    _text_action_eligible,
)
from champions_assist.ocr_turn_context import (
    OcrSentLedger, build_ocr_turn_context, compact_ocr_transport, _row_key,
)
from tests.test_match_replay import fixture_assets, frame
from champions_assist.local_event_worker import LocalFieldRead
from champions_assist.battle_state_tracker import LocalBattleStateTracker


def ocr(frame, text, *, display_id="", field="screen_text.battle_message",
        confidence=0.96, timestamp=10.0):
    return {
        "frame_id": frame, "field": field, "raw_text": text,
        "confidence": confidence, "observed_at": timestamp,
        **({"display_id": display_id} if display_id else {}),
    }


def event(side, pokemon, frame):
    return {
        "kind": "SWITCH", "side": side, "pokemon": pokemon,
        "from_pokemon": "", "value": "", "stat": "", "delta": 0,
        "certainty": "CONFIRMED", "evidence_frames": [frame],
    }


def battle():
    assets = fixture_assets()
    state = new_replay(assets)
    state.update(
        phase="BATTLE", selected_three=["ゲンガー", "ドドゲザン", "マリルリ"],
        lead="ゲンガー", our_active="", opponent_active="",
        selection_assumed=False,
    )
    return assets, state


class Issue125TransportTests(unittest.TestCase):

    def test_exact_only_compression_preserves_every_original_row_key(self):
        records = [
            ocr(f"f{i}", "ゆけっ！ガブリアス！" if i % 2 else
                "ゆけっ！ ガブリアス！",
                display_id="message:visual-7", timestamp=10.0 + i * 0.1)
            for i in range(8)
        ]
        records += [
            ocr("f8", "ゆげっ！ガブリアス！",
                display_id="message:visual-7", timestamp=11.0),
            ocr("f9", "ゆけっ！ガブリアス！",
                display_id="message:visual-8", timestamp=11.1),
            ocr("f10", "185/185",
                field="screen_text.own_hp_fraction", timestamp=11.2),
            ocr("f11", "185/185",
                field="screen_text.own_hp_fraction", timestamp=11.6),
        ]
        original = json.dumps(records, ensure_ascii=False)
        compact = compact_ocr_transport(records)
        self.assertEqual(len(compact), 5)
        group = compact[0]
        self.assertEqual(group["source_frames"], [f"f{i}" for i in range(8)])
        self.assertEqual(group["source_row_keys"], [_row_key(r) for r in records[:8]])
        self.assertEqual(group["source_sequence_indices"], list(range(8)))
        self.assertEqual(group["observation_count"], 8)
        self.assertEqual(len(group["source_observed_at"]), 8)
        self.assertIn("ゆけっ！ ガブリアス！", [
            group["raw_text"], *group["variants"]])
        self.assertEqual(compact[1]["frame_id"], "f8")
        self.assertEqual(compact[2]["frame_id"], "f9")
        self.assertEqual(compact[3]["frame_id"], "f10")
        self.assertEqual(compact[4]["frame_id"], "f11")
        self.assertLess(len(json.dumps(compact, ensure_ascii=False)),
                        len(original))
        self.assertEqual(json.dumps(records, ensure_ascii=False), original)

    def test_same_frame_multiple_readings_keep_distinct_original_keys(self):
        texts = ["ゆけっ！ゲンガー！", "ゆけっ!ゲンガー！"]
        records = [
            ocr("shared-frame", texts[i % 2], display_id="same-display",
                timestamp=10.0 + i * 0.01)
            for i in range(12)
        ]
        self.assertEqual(len(set(r["frame_id"] for r in records)), 1)
        self.assertEqual(len(set(_row_key(r) for r in records)), 2)
        compact = compact_ocr_transport(records)
        self.assertEqual(len(compact), 1)
        self.assertEqual(compact[0]["source_frames"],
                         ["shared-frame"] * 12)
        self.assertEqual(compact[0]["source_row_keys"],
                         [_row_key(row) for row in records])

    def test_fuzzy_or_opposite_messages_never_coalesce(self):
        records = [
            ocr("up", "相手の攻撃が上がった！", display_id="same-visual"),
            ocr("down", "相手の攻撃が下がった！",
                display_id="same-visual", timestamp=10.3),
            ocr("misread", "相手の攻撃が上がつた！",
                display_id="same-visual", timestamp=10.6),
        ]
        groups = compact_ocr_transport(records)
        self.assertEqual(len(groups), 3)
        self.assertEqual([r["frame_id"] for r in groups],
                         ["up", "down", "misread"])

    def test_different_episode_even_same_message_is_not_merged(self):
        records = [
            ocr("turn-1", "ガブリアスのじしん！", display_id="m:1"),
            ocr("turn-2", "ガブリアスのじしん！", display_id="m:2"),
        ]
        self.assertEqual(len(compact_ocr_transport(records)), 2)

    def test_compaction_does_not_expand_api_json_bytes(self):
        for n in (0, 1, 2, 4, 20):
            records = [
                ocr(f"c{i}", "相手はマスカーニャを繰り出した！",
                    display_id="same-display", timestamp=i / 10)
                for i in range(n)
            ]
            original=json.dumps(records, ensure_ascii=False,
                                separators=(",", ":")).encode("utf-8")
            packed=json.dumps(compact_ocr_transport(records),
                              ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.assertLessEqual(len(packed), len(original))
            # Token count is model-dependent; report only a byte/4 proxy.
            self.assertLessEqual((len(packed)+3)//4, (len(original)+3)//4)
            if n == 20:
                self.assertLess(len(compact_ocr_transport(records)), n)

    def test_sent_keys_survive_model_failure_and_do_not_recur(self):
        assets, state = battle()
        state["our_active"] = "ゲンガー"
        state["opponent_active"] = "マスカーニャ"
        ledger = OcrSentLedger()
        records = [ocr("f1", "戦闘イベント1"), ocr("f2", "戦闘イベント2")]

        def response_then_fails(_client, **kwargs):
            kwargs["on_request_sent"]()
            raise RuntimeError("late response discarded")

        with patch("champions_assist.match_replay._stream_json",
                   side_effect=response_then_fails):
            _, result = run_replay_text_action(
                state=state, assets=assets, access_token="unused", model="test",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=records, sent_ocr_ledger=ledger,
                client_factory=lambda **kw: object(),
            )
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(len(ledger.snapshot()), 2)
        next_rows, _, meta = build_ocr_turn_context(
            state, records + [ocr("f3", "新規")], sent_ledger=ledger)
        self.assertEqual([row["frame_id"] for row in next_rows], ["f3"])
        self.assertEqual(meta["prior_observations_not_resent"], 2)
        # A known current actor can use already-verified state with new OCR.
        self.assertTrue(_text_action_eligible(
            "TURN_ACTION",state,assets,
            local_rows=records,sent_ocr_ledger=ledger))

    def test_stale_unknown_lead_never_sends_empty_ocr_text_prompt(self):
        assets, state = battle()
        ledger = OcrSentLedger()
        readings = [
            ocr("f1", "ゆけっ！ゲンガー！"),
            ocr("f2", "相手のマスカーニャを繰り出した！", timestamp=10.2),
        ]
        self.assertTrue(_text_action_eligible(
            "TURN_ACTION",state,assets,local_rows=readings,
            sent_ocr_ledger=ledger))
        # req-A was dispatched, but timeout/stale handling rejected its answer.
        ledger.mark_sent(_row_key(row) for row in readings)
        self.assertEqual(build_ocr_turn_context(
            state,readings,sent_ledger=ledger)[0],[])
        self.assertFalse(_text_action_eligible(
            "TURN_ACTION",state,assets,local_rows=readings,
            sent_ocr_ledger=ledger))
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=AssertionError("Must not create empty OCR request")):
            _, blocked = run_replay_text_action(
                state=state,assets=assets,access_token="unused",model="mock",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=readings,
                sent_ocr_ledger=ledger,
                client_factory=lambda **kw: object())
        self.assertEqual(blocked["status"],"BLOCKED")

        seen=[]
        def image_reply(_client,**kwargs):
            seen.append(kwargs)
            return {
                "observation":frame("COMMAND_MENU",our_active="ゲンガー",
                                    opponent_active="マスカーニャ"),
                "action":{"action_kind":"NO_ACTION","move":"",
                          "switch_target":"","warning":""},
                "selection":{"selected_three":[],"lead":""},
                "pending_ocr_questions":[],"processed_ocr_frames":[],
            }
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=image_reply):
            _, report = run_replay_batch(
                [np.zeros((80,120,3),dtype=np.uint8)],state=state,
                assets=assets,access_token="unused",model="mock",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=readings,
                sent_ocr_ledger=ledger,
                client_factory=lambda **kw: object())
        self.assertEqual(len(seen),1)
        self.assertEqual(sum(item["type"]=="input_image"
                             for item in seen[0]["content"]),1)
        prompt=json.loads(seen[0]["content"][0]["text"])
        self.assertEqual(prompt["local_field_candidates_unverified"],[])
        self.assertEqual(report["images_sent"],1)
        self.assertEqual(len(ledger.snapshot()),2)

    def test_transport_keys_survive_invalid_tactic(self):
        assets, state = battle()
        state["our_active"] = "ゲンガー"
        state["opponent_active"] = "マスカーニャ"
        records = [ocr("f1", "ゆけっ！ゲンガー！")]
        ledger = OcrSentLedger()
        model_response = {
            "state_updates": [],
            "interpreted_own_active": "ゲンガー",
            "interpreted_opponent_active": "マスカーニャ",
            "pending_ocr_questions": [], "processed_ocr_frames": [],
            "action": {"action_kind": "MOVE", "move": "使えない技",
                       "switch_target": "", "warning": ""},
        }
        with patch("champions_assist.match_replay._stream_json",
                   return_value=model_response):
            after, report = run_replay_text_action(
                state=state, assets=assets, access_token="unused", model="test",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=records, sent_ocr_ledger=ledger,
                client_factory=lambda **kw: object(),
            )
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(len(after["_ai_ocr_presented_keys"]), 1)
        self.assertEqual(len(ledger.snapshot()), 1)
        self.assertEqual(build_ocr_turn_context(
            after, records, sent_ledger=ledger)[0], [])

    def test_unknown_first_turn_actor_uses_one_ocr_text_request(self):
        assets, state = battle()
        records = [
            ocr("f1", "ゆけっ！ゲンガー！"),
            ocr("f2", "相手のマスカーニャを繰り出した！", timestamp=10.2),
        ] + [
            ocr(f"f{i}", "ゆけっ！ゲンガー！",
                display_id="own-intro:display-1", timestamp=10.2 + i / 10)
            for i in range(3, 15)
        ]
        self.assertEqual(len(records), 14)
        self.assertTrue(_text_action_eligible(
            "TURN_ACTION", state, assets, local_rows=records))
        reply = {
            "state_updates": [
                event("own", "ゲンガー", "f1"),
                event("opponent", "マスカーニャ", "f2"),
            ],
            "interpreted_own_active": "ゲンガー",
            "interpreted_opponent_active": "マスカーニャ",
            "pending_ocr_questions": [], "processed_ocr_frames": [],
            "action": {"action_kind": "MOVE", "move": "じしん",
                       "switch_target": "", "warning": ""},
        }
        calls = []
        def model(_client, **kwargs):
            calls.append(kwargs)
            return reply
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=model):
            after, report = run_replay_text_action(
                state=state, assets=assets, access_token="unused", model="test",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=records,
                client_factory=lambda **kw: object(),
            )
        self.assertEqual(len(calls), 1)
        prompt = json.loads(calls[0]["content"][0]["text"])
        packed = prompt["ocr_candidates_unverified"]
        self.assertLess(len(packed), 14)
        frame_proof=set()
        for item in packed:
            frame_proof.update(item.get("source_frames", [item["frame_id"]]))
        self.assertEqual(frame_proof,{f"f{i}" for i in range(1, 15)})
        self.assertEqual(len(prompt["registered_selected_moves"]), 3)
        self.assertEqual(report["images_sent"], 0)
        self.assertEqual(after["our_active"], "ゲンガー")
        self.assertEqual(after["opponent_active"], "マスカーニャ")
        self.assertEqual(report["status"], "TEST_ONLY")
        self.assertEqual(report["recommendation"], "じしん。")

    def test_newly_identified_lead_still_rejects_unregistered_move(self):
        assets, state = battle()
        source = [ocr("f1", "ゆけっ！ゲンガー！")]
        response = {
            "state_updates": [event("own", "ゲンガー", "f1")],
            "interpreted_own_active": "ゲンガー",
            "interpreted_opponent_active": "",
            "pending_ocr_questions": [], "processed_ocr_frames": [],
            "action": {"action_kind": "MOVE", "move": "登録されていない技",
                       "switch_target": "", "warning": ""},
        }
        with patch("champions_assist.match_replay._stream_json",
                   return_value=response):
            after, report = run_replay_text_action(
                state=state,assets=assets,access_token="unused",model="test",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=source,
                client_factory=lambda **kwargs: object())
        self.assertEqual(after["our_active"], "ゲンガー")
        self.assertEqual(report["status"], "BLOCKED")
        self.assertNotIn("登録されていない技",report.get("recommendation", ""))

    def test_raw_tracker_keeps_all_duplicate_frames_then_transport_compacts(self):
        tracker = LocalBattleStateTracker()
        message = "あいてのマスカーニャはとんぼがえりをつかった！"
        rows = [
            LocalFieldRead(
                field="screen_text.battle_message", value=message,
                frame_id=f"raw-{n}", timestamp=10.0 + n * .1,
                confidence=.94, roi="battle_message",
                source="fixture", text=message,
                display_id="visual-episode-9")
            for n in range(20)
        ]
        for row in rows:
            tracker.ingest((row,), turn_epoch=2)
        snapshot = tracker.snapshot()["local_observations"]
        self.assertEqual(len(snapshot), 20)
        self.assertEqual([r["frame_id"] for r in snapshot],
                         [f"raw-{n}" for n in range(20)])
        self.assertEqual(
            [r["observed_at"] for r in snapshot],
            [10.0 + n * .1 for n in range(20)])
        groups = compact_ocr_transport(snapshot)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["source_frames"],
                         [r["frame_id"] for r in snapshot])
        self.assertEqual(groups[0]["source_row_keys"],
                         [_row_key(r) for r in snapshot])

    def test_discarded_response_reinspects_only_specific_original_frames_once(self):
        assets, state = battle()
        state["our_active"] = "ゲンガー"
        state["opponent_active"] = "マスカーニャ"
        ledger = OcrSentLedger()
        records = [
            ocr(f"event-{n}", "相手のマスカーニャはとんぼがえりをつかった！",
                display_id="message-1",timestamp=10+n*.1)
            for n in range(9)
        ] + [
            ocr(f"hp-{n}", f"{185-n}/185",
                field="screen_text.own_hp_fraction",timestamp=11+n*.1)
            for n in range(10)
        ]
        state_original = dict(state)
        def request_a(_client, **kwargs):
            kwargs["on_request_sent"]()
            raise RuntimeError("discarded GPT response")
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=request_a):
            after, report = run_replay_text_action(
                state=state,assets=assets,access_token="unused",model="test",
                expected_screen_label="TURN_ACTION",ocr_request_id="req-A",
                local_field_candidates=records,sent_ocr_ledger=ledger,
                client_factory=lambda **kwargs: object())
        self.assertEqual(report["status"],"ERROR")
        self.assertEqual(len(ledger.snapshot()),len(records))
        self.assertEqual(state["our_active"],state_original["our_active"])
        self.assertEqual(state["opponent_active"],state_original["opponent_active"])
        self.assertEqual(build_ocr_turn_context(
            state,records,sent_ledger=ledger)[0],[])
        ledger.mark_stale("req-A")
        replay,_,stats=build_ocr_turn_context(
            state,records,sent_ledger=ledger)
        self.assertEqual([r["frame_id"] for r in replay],
                         ["event-0","event-8","hp-8","hp-9"])
        self.assertEqual(stats["new_rows_sent"],0)
        self.assertEqual(stats["stale_reinspection_rows"],4)
        seen=[]
        answer={
            "state_updates":[],
            "interpreted_own_active":"ゲンガー",
            "interpreted_opponent_active":"マスカーニャ",
            "processed_ocr_frames":[],"pending_ocr_questions":[],
            "action":{"action_kind":"NO_ACTION","move":"",
                      "switch_target":"","warning":""},
        }
        def request_b(_client, **kwargs):
            seen.append(json.loads(kwargs["content"][0]["text"]))
            kwargs["on_request_sent"]()
            return answer
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=request_b):
            _, next_report=run_replay_text_action(
                state=state,assets=assets,access_token="unused",model="test",
                expected_screen_label="TURN_ACTION",ocr_request_id="req-B",
                local_field_candidates=records,sent_ocr_ledger=ledger,
                client_factory=lambda **kwargs: object())
        self.assertEqual(next_report["status"],"NO_ACTION")
        self.assertEqual(len(seen),1)
        sent_frames=set()
        for group in seen[0]["ocr_candidates_unverified"]:
            sent_frames.update(group.get("source_frames",[group["frame_id"]]))
        self.assertEqual(sent_frames,
                         {"event-0","event-8","hp-8","hp-9"})
        self.assertEqual(build_ocr_turn_context(
            state,records,sent_ledger=ledger)[0],[])
        ledger.mark_stale("req-B")
        self.assertEqual(build_ocr_turn_context(
            state,records,sent_ledger=ledger)[0],[])
        self.assertEqual(len(ledger.snapshot()),len(records))

    def test_retired_worker_before_dispatch_preserves_targeted_proof(self):
        ledger=OcrSentLedger()
        rows=[ocr("f1","ゆけっ！ゲンガー！")]
        ledger.mark_stale("req-A")
        ledger.mark_sent([_row_key(rows[0])], request_id="req-A",rows=rows)
        self.assertEqual(ledger.reinspection_keys(),{_row_key(rows[0])})
        ledger.mark_sent([_row_key(rows[0])], request_id="req-B",rows=rows)
        self.assertEqual(ledger.reinspection_keys(),set())
        ledger.mark_stale("req-B")
        self.assertEqual(ledger.reinspection_keys(),set())

    def test_second_discard_blocks_text_and_requires_visual_confirmation(self):
        assets, state = battle()
        state["our_active"] = "ゲンガー"
        state["opponent_active"] = "マスカーニャ"
        ledger = OcrSentLedger()
        event = [ocr("e-1", "マスカーニャのとんぼがえり！",
                     display_id="e:1"),
                 ocr("h-1", "161/185",field="screen_text.own_hp_fraction")]
        source = [_row_key(row) for row in event]
        ledger.mark_sent(source,request_id="A",rows=event)
        ledger.mark_stale("A")
        self.assertEqual(set(_row_key(r) for r in build_ocr_turn_context(
            state,event,sent_ledger=ledger)[0]), set(source))
        ledger.mark_sent(source,request_id="B",rows=event)
        self.assertEqual(ledger.reinspection_keys(),set())
        ledger.mark_stale("B")
        self.assertTrue(ledger.needs_visual_confirmation())
        self.assertFalse(_text_action_eligible(
            "TURN_ACTION",state,assets,local_rows=event,
            sent_ocr_ledger=ledger))
        selected,_,_=build_ocr_turn_context(state,event,sent_ledger=ledger)
        self.assertEqual(selected,[])  # no third unbounded OCR retransmission
        # The GUI dispatches the existing single-current-frame image path
        # rather than issuing text from unknown boosts/locks.
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=AssertionError("text-only must be blocked")):
            _, blocked = run_replay_text_action(
                state=state,assets=assets,access_token="unused",model="test",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=event,sent_ocr_ledger=ledger,
                client_factory=lambda **kw: object())
        self.assertEqual(blocked["status"],"BLOCKED")
        captured=[]
        def image_model(_client,**kwargs):
            captured.append(kwargs)
            return {
                "observation":frame("COMMAND_MENU",our_active="ゲンガー",
                                    opponent_active="マスカーニャ"),
                "action":{"action_kind":"NO_ACTION","move":"",
                          "switch_target":"","warning":""},
                "selection":{"selected_three":[],"lead":""},
                "pending_ocr_questions":[],"processed_ocr_frames":[],
            }
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=image_model):
            _, image_report = run_replay_batch(
                [np.zeros((80,120,3),dtype=np.uint8)],state=state,
                assets=assets,access_token="unused",model="mock",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=event,
                sent_ocr_ledger=ledger,
                client_factory=lambda **kw:object())
        self.assertEqual(image_report["images_sent"],1)
        self.assertEqual(len(captured),1)
        image_prompt=json.loads(captured[0]["content"][0]["text"])
        self.assertTrue(image_prompt["unverified_stale_ocr_requires_visual"])

    def test_exclusive_reservation_old_worker_retirement_cross_schedule(self):
        assets, state = battle()
        state["our_active"] = "ゲンガー"
        state["opponent_active"] = "マスカーニャ"
        ledger = OcrSentLedger()
        readings = [
            ocr("frame-a","ゆけっ！ゲンガー！"),
            ocr("frame-b","マスカーニャのとんぼがえり！")]
        a_reservation = Event()
        continue_old = Event()
        outcomes = []
        answer = {
            "state_updates":[],
            "interpreted_own_active":"ゲンガー",
            "interpreted_opponent_active":"マスカーニャ",
            "processed_ocr_frames":[],"pending_ocr_questions":[],
            "action":{"action_kind":"NO_ACTION","move":"",
                      "switch_target":"","warning":""},
        }
        def fake_model(_client, **kw):
            a_reservation.set()  # req-A assembled prompt and reserved OCR
            self.assertTrue(continue_old.wait(timeout=5))
            kw["on_request_sent"]()  # old HTTP accepted *after* B selection
            return answer
        def worker_a():
            result = run_replay_text_action(
                state=state,assets=assets,access_token="unused",model="test",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=readings,
                sent_ocr_ledger=ledger,ocr_request_id="req-A",
                client_factory=lambda **kw:object())
            outcomes.append(result[1])
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=fake_model):
            thread = Thread(target=worker_a,daemon=True)
            thread.start()
            try:
                self.assertTrue(a_reservation.wait(timeout=5))
                ledger.mark_stale("req-A")
                self.assertTrue(ledger.needs_visual_confirmation())
                # req-B is planned while old A has not yet confirmed send.
                chosen,_,info=build_ocr_turn_context(
                    state,readings,sent_ledger=ledger,request_id="req-B")
                self.assertEqual(chosen,[])
                self.assertEqual(info["api_rows_sent"],0)
                self.assertFalse(_text_action_eligible(
                    "TURN_ACTION",state,assets,local_rows=readings,
                    sent_ocr_ledger=ledger))
            finally:
                continue_old.set()
                thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcomes),1)
        self.assertEqual(ledger.snapshot(),set(_row_key(r) for r in readings))
        self.assertEqual(ledger.reinspection_keys(),
                         set(_row_key(r) for r in readings))
        ledger.release("req-B")
        # Neither the pending reservation nor confirmed send may be claimed
        # by a fresh outgoing OCR request without explicit stale inspection.
        candidate,_,_=build_ocr_turn_context(
            state,readings,sent_ledger=ledger,request_id="req-C")
        self.assertEqual(len(candidate),len(readings))  # explicit reinspect
        ledger.release("req-C")

    def test_retired_before_http_releases_reservation_without_sending(self):
        assets, state = battle()
        state["our_active"]="ゲンガー"
        state["opponent_active"]="マスカーニャ"
        ledger=OcrSentLedger()
        proof=[ocr("one","ゆけっ！ゲンガー！")]
        # Another worker selects evidence but is retired prior to transport.
        self.assertEqual(len(ledger.reserve("A",proof)),1)
        ledger.mark_stale("A")
        ledger.release("A")
        self.assertEqual(ledger.snapshot(),set())
        self.assertEqual(len(ledger.reserve("B",proof)),1)
        ledger.release("B")

    def test_missing_display_id_burst_reinspection_is_bounded(self):
        ledger=OcrSentLedger()
        rows=[
            ocr(f"frame-{i}","マスカーニャのとんぼがえり！",
                timestamp=10+i*.05)
            for i in range(300)
        ]
        # Archive remains complete; the lack of visual display IDs must
        # neither infer one shared event nor flood a second GPT request.
        ledger.mark_sent([_row_key(r) for r in rows],
                         request_id="A",rows=rows)
        ledger.mark_stale("A")
        chosen,_,stats=build_ocr_turn_context(
            {},rows,sent_ledger=ledger)
        self.assertLessEqual(len(chosen),ledger.MAX_REINSPECT_ROWS)
        self.assertLessEqual(len(json.dumps(chosen,ensure_ascii=False,
                                            separators=(",",":")).encode("utf8")),
                             ledger.MAX_REINSPECT_BYTES)
        self.assertEqual(stats["new_rows_sent"],0)
        self.assertEqual(stats["ledger_rows_available"],300)
        self.assertTrue(ledger.needs_visual_confirmation())
        self.assertEqual(len(rows),300)

    def test_image_reconciliation_restores_text_after_two_lost_replies(self):
        """A→B both lost; current validated HUD resolves ALL selected proof."""
        assets,state=battle()
        state["our_active"]="ゲンガー"
        state["opponent_active"]="マスカーニャ"
        ledger=OcrSentLedger()
        proof=[
            ocr("own-name","ゲンガー",field="screen_text.own_active_name"),
            ocr("opp-name","マスカーニャ",
                field="screen_text.opponent_active_name"),
            ocr("own-hp","185/185",field="screen_text.own_hp_fraction"),
            ocr("opp-hp","100%",field="screen_text.opponent_hp_percent"),
        ]
        keys=[_row_key(row) for row in proof]
        ledger.mark_sent(keys,request_id="req-A",rows=proof)
        ledger.mark_stale("req-A")
        ledger.mark_sent(keys,request_id="req-B",rows=proof)
        ledger.mark_stale("req-B")
        self.assertTrue(ledger.needs_visual_confirmation())
        self.assertFalse(_text_action_eligible(
            "TURN_ACTION",state,assets,local_rows=proof,
            sent_ocr_ledger=ledger))
        screen=frame("COMMAND_MENU",our_active="ゲンガー",
                     opponent_active="マスカーニャ",
                     our_hp="185/185",opponent_hp="100%")
        field_by_row={
            "own-name":"our_active",
            "opp-name":"opponent_active",
            "own-hp":"our_hp",
            "opp-hp":"opponent_hp",
        }
        image_reply={
            "observation":screen,
            "action":{"action_kind":"NO_ACTION","move":"",
                      "switch_target":"","warning":""},
            "selection":{"selected_three":[],"lead":""},
            "pending_ocr_questions":[],"processed_ocr_frames":[],
            "stale_visual_check":{
                "confirmed_source_fields":[
                    {"source_row_key":_row_key(row),
                     "field":field_by_row[row["frame_id"]]}
                    for row in proof],
                "unresolved_source_row_keys":[],
            },
        }
        requests=[]
        def current_image_model(_client,**kwargs):
            requests.append(kwargs)
            return image_reply
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=current_image_model):
            updated,report=run_replay_batch(
                [np.zeros((80,120,3),dtype=np.uint8)],
                state=state,assets=assets,access_token="unused",model="mock",
                expected_screen_label="TURN_ACTION",
                local_field_candidates=proof,sent_ocr_ledger=ledger,
                ocr_request_id="req-C",
                client_factory=lambda **kw:object())
        self.assertEqual(len(requests),1)
        self.assertEqual(report["images_sent"],1)
        self.assertNotEqual(report["status"],"BLOCKED")
        prompt=json.loads(requests[0]["content"][0]["text"])
        self.assertTrue(prompt["unverified_stale_ocr_requires_visual"])
        self.assertEqual(
            {row["source_row_key"] for row in prompt[
                "stale_visual_proof_to_reconcile"]},set(keys))
        self.assertEqual(
            len(requests[0]["content"]) -
            sum(part["type"]!="input_image"
                for part in requests[0]["content"]),1)
        epoch=ledger.confirmed_visual_recovery_epoch(report,updated)
        self.assertIsNotNone(epoch)
        ledger.mark_adopted("req-C",visual_confirmed=True,
                            recovery_epoch=epoch)
        self.assertFalse(ledger.needs_visual_confirmation())
        self.assertEqual(ledger.reinspection_keys(),set())
        # Normal text-only tactical path can be used on the NEXT turn.
        self.assertTrue(_text_action_eligible(
            "TURN_ACTION",updated,assets,local_rows=proof,
            sent_ocr_ledger=ledger))
        self.assertEqual(len(ledger.snapshot()),len(keys))

    def test_unresolved_lost_battle_event_keeps_image_safety_gate(self):
        """Image response != proof: stale move/boost remains unknown."""
        assets,state=battle()
        state["our_active"]="ゲンガー"
        state["opponent_active"]="マスカーニャ"
        ledger=OcrSentLedger()
        move=ocr("old-move","マスカーニャのとんぼがえり！",
                 display_id="old-visual-1")
        key=_row_key(move)
        ledger.mark_sent([key],request_id="req-A",rows=[move])
        ledger.mark_stale("req-A")
        ledger.mark_sent([key],request_id="req-B",rows=[move])
        ledger.mark_stale("req-B")
        self.assertTrue(ledger.needs_visual_confirmation())
        img=frame("COMMAND_MENU",our_active="ゲンガー",
                  opponent_active="マスカーニャ",our_hp="185/185",
                  opponent_hp="100%")
        image_report={
            "status":"NO_ACTION","images_sent":1,
            "observation":img,
            "stale_visual_check":{
                "confirmed_source_fields":[],
                "unresolved_source_row_keys":[key],
            },
        }
        self.assertIsNone(
            ledger.confirmed_visual_recovery_epoch(image_report,state))
        ledger.mark_adopted("req-C",visual_confirmed=False)
        self.assertTrue(ledger.needs_visual_confirmation())
        self.assertFalse(_text_action_eligible(
            "TURN_ACTION",state,assets,local_rows=[move],
            sent_ocr_ledger=ledger))
        # A fabricated "resolved" claim tied only to an actor name also fails.
        image_report["stale_visual_check"]={
            "confirmed_source_fields":[
                {"source_row_key":key,"field":"opponent_active"}],
            "unresolved_source_row_keys":[],
        }
        self.assertIsNone(
            ledger.confirmed_visual_recovery_epoch(image_report,state))
        # Image explicitly listing a retained unresolved OCR question fails too.
        img["opponent_used_move"]="とんぼがえり"
        image_report["stale_visual_check"]={
            "confirmed_source_fields":[
                {"source_row_key":key,"field":"opponent_used_move"}],
            "unresolved_source_row_keys":[],
        }
        state_with_pending=dict(state,_ai_ocr_unresolved=[{
            "source_frames":["old-move"],
            "question":"技使用・こだわり固定が未確認",
            "importance":"CRITICAL","recheck_when":"次の画面",
            "verified":False}])
        self.assertIsNone(ledger.confirmed_visual_recovery_epoch(
            image_report,state_with_pending))
        # Missing visible UI is not a verified image even with a claimed field.
        image_report["observation"]=dict(img,visual_ui="UNKNOWN")
        self.assertIsNone(
            ledger.confirmed_visual_recovery_epoch(image_report,state))

    def test_visual_safety_recovery_rejects_old_epoch(self):
        ledger=OcrSentLedger()
        proof=ocr("own-hp","185/185",field="screen_text.own_hp_fraction")
        key=_row_key(proof)
        ledger.mark_sent([key],request_id="A",rows=[proof])
        ledger.mark_stale("A")
        ledger.mark_sent([key],request_id="B",rows=[proof])
        ledger.mark_stale("B")
        state={"phase":"BATTLE","our_active":"ゲンガー",
               "opponent_active":"マスカーニャ"}
        report={"status":"NO_ACTION","images_sent":1,
                "observation":frame("COMMAND_MENU",
                    our_active="ゲンガー",opponent_active="マスカーニャ",
                    our_hp="185/185"),
                "stale_visual_check":{
                    "confirmed_source_fields":[
                        {"source_row_key":key,"field":"our_hp"}],
                    "unresolved_source_row_keys":[]}}
        epoch=ledger.confirmed_visual_recovery_epoch(report,state)
        self.assertIsNotNone(epoch)
        ledger.mark_adopted("C",visual_confirmed=True,
                            recovery_epoch=epoch+1)
        self.assertTrue(ledger.needs_visual_confirmation())

    def test_unknown_actor_requires_actual_identification_ocr_hint(self):
        assets,state=battle()
        hp=[ocr("hp","185/185",field="screen_text.own_hp_fraction")]
        self.assertFalse(_text_action_eligible(
            "TURN_ACTION",state,assets,local_rows=hp))
        opponent=[ocr("opponent","相手はマスカーニャを繰り出した！")]
        self.assertFalse(_text_action_eligible(
            "TURN_ACTION",state,assets,local_rows=hp+opponent))
        # A sendout marker with NO species/name candidate is also insufficient.
        bare_sendout=[ocr("bare","ゆけっ！")]
        self.assertFalse(_text_action_eligible(
            "TURN_ACTION",state,assets,local_rows=bare_sendout))
        effects=[ocr("eff","相手のこうげきが上がった！",
                     field="screen_text.opponent_effect_popup")]
        self.assertFalse(_text_action_eligible(
            "TURN_ACTION",state,assets,local_rows=hp+effects))
        hud=ocr("name","ガプリアス",
                field="screen_text.own_active_name")
        self.assertTrue(_text_action_eligible(
            "TURN_ACTION",state,assets,local_rows=hp+[hud]))
        sendout=ocr("send","ゆげっ！ガプリアス！")
        self.assertTrue(_text_action_eligible(
            "TURN_ACTION",state,assets,local_rows=hp+[sendout]))
        ledger=OcrSentLedger()
        ledger.mark_sent([_row_key(hud)])
        self.assertFalse(_text_action_eligible(
            "TURN_ACTION",state,assets,local_rows=hp+[hud],
            sent_ocr_ledger=ledger))
        ledger.mark_stale("missing")  # no dispatched proof to re-present
        self.assertFalse(_text_action_eligible(
            "TURN_ACTION",state,assets,local_rows=hp+[hud],
            sent_ocr_ledger=ledger))

    def test_unknown_actor_without_ocr_still_needs_image(self):
        assets, state = battle()
        self.assertFalse(_text_action_eligible(
            "TURN_ACTION", state, assets, local_rows=[]))


if __name__ == "__main__":
    unittest.main()
