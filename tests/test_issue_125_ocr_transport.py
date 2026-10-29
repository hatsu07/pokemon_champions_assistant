"""Issue #125 transport/first-turn OCR regressions. No network, no image model."""
import json
import unittest
from unittest.mock import patch

from champions_assist.match_replay import (
    new_replay, run_replay_text_action, _text_action_eligible,
)
from champions_assist.ocr_turn_context import (
    OcrSentLedger, build_ocr_turn_context, compact_ocr_transport,
)
from tests.test_match_replay import fixture_assets


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

    def test_compression_requires_a_visual_episode_and_keeps_original_proof(self):
        records = [
            ocr("f1", "ゆけっ！ ガブリアス！", display_id="message:visual-7"),
            ocr("f2", "ゆげっ！ガブリアス！", display_id="message:visual-7",
                timestamp=10.4),
            ocr("f3", "ゆけっ！ガブリアス！", display_id="message:visual-7",
                timestamp=10.8),
            ocr("f4", "ゆけっ！ガブリアス！", display_id="message:visual-8",
                timestamp=11.1),
            ocr("f5", "185/185", field="screen_text.own_hp_fraction",
                timestamp=11.2),
            ocr("f6", "185/185", field="screen_text.own_hp_fraction",
                timestamp=11.6),
        ]
        original = json.dumps(records, ensure_ascii=False)
        compact = compact_ocr_transport(records)
        self.assertEqual(len(compact), 4)
        self.assertEqual(compact[0]["source_frames"], ["f1", "f2", "f3"])
        self.assertEqual(compact[0]["observation_count"], 3)
        self.assertIn("ゆげっ！ガブリアス！", compact[0]["variants"])
        self.assertEqual(compact[1]["frame_id"], "f4")
        self.assertEqual(compact[2]["frame_id"], "f5")
        self.assertEqual(compact[3]["frame_id"], "f6")
        self.assertEqual(json.dumps(records, ensure_ascii=False), original)

    def test_different_episode_even_same_message_is_not_merged(self):
        records = [
            ocr("turn-1", "ガブリアスのじしん！", display_id="m:1"),
            ocr("turn-2", "ガブリアスのじしん！", display_id="m:2"),
        ]
        self.assertEqual(len(compact_ocr_transport(records)), 2)

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
        ]
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
        self.assertEqual(len(prompt["ocr_candidates_unverified"]), 2)
        self.assertEqual(len(prompt["registered_selected_moves"]), 3)
        self.assertEqual(report["images_sent"], 0)
        self.assertEqual(after["our_active"], "ゲンガー")
        self.assertEqual(after["opponent_active"], "マスカーニャ")
        self.assertIn(report["status"], ("TEST_ONLY", "ACTION", "RECOMMENDED"))

    def test_unknown_actor_without_ocr_still_needs_image(self):
        assets, state = battle()
        self.assertFalse(_text_action_eligible(
            "TURN_ACTION", state, assets, local_rows=[]))


if __name__ == "__main__":
    unittest.main()
