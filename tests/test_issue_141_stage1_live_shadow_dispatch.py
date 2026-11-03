"""The real UI can produce an *unverified* frozen GPT dispatch, never a Battle."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from champions_assist.local_strategy.live_shadow_dispatch import (
    CapturedShadowWindow, FrozenRevisionLatch, capture_gpt_dispatch_window,
)
from champions_assist.preload import MasterPreloader
from tests.test_issue_141_selection import make_assets


def capture(assets=None, **kwargs):
    supplied = dict(
        match_id="capture-test", request_id="req-1", turn_epoch=3,
        state_revision=5, screen_label="TURN_ACTION",
        observed_state={"our_active":"A", "opponent_active":"O1",
                        "selected_three":["A","B","C"]},
        deadline_monotonic=200.0, now=100.0)
    supplied.update(kwargs)
    return capture_gpt_dispatch_window(assets or make_assets(), **supplied)


class LiveShadowDispatchTests(unittest.TestCase):
    def test_frozen_unchanged_observation_is_never_game_legality(self):
        result = capture()
        self.assertEqual(result.status, "CAPTURED_UNVERIFIED_GAME_STATE",
                         result.reason)
        snap = result.snapshot
        self.assertEqual(snap.window.match_id, "capture-test")
        self.assertEqual(snap.window.state_revision, 5)
        self.assertEqual(snap.window.screen_kind, "UNKNOWN")
        self.assertEqual(snap.window.decision_window_id, "shadow-3-5-req-1")
        self.assertEqual(snap.verified_action_ids, ())
        self.assertFalse(snap.constraints_confirmed)
        self.assertFalse(result.game_battle_authenticated)
        self.assertFalse(result.legal_actions_verified)
        self.assertEqual(snap.state["opponent_active"], "O1")
        self.assertNotIn("pp", str(snap.observation).lower())
        latch = FrozenRevisionLatch(snap)
        self.assertEqual(latch.current().snapshot_sha256, snap.snapshot_sha256)
        latch.invalidate()
        with self.assertRaisesRegex(ValueError, "SUPERSEDED"):
            latch.current()

    def test_partial_state_and_switch_do_not_invent_party_slots(self):
        result = capture(
            screen_label="SWITCH_SELECTION",
            observed_state={"our_active":"", "opponent_active":"O1"})
        self.assertEqual(result.status, "CAPTURED_UNVERIFIED_GAME_STATE")
        self.assertEqual(result.snapshot.state["selected_three"], [])
        self.assertEqual(result.snapshot.window.window_kind.value, "NORMAL_SWITCH")
        self.assertEqual(result.snapshot.verified_action_ids, ())

    def test_refuses_stale_invalid_and_nonbattle_requests(self):
        for kwargs in (
            {"now":200.0}, {"screen_label":"TEAM_SELECTION"},
            {"observed_state":{"our_active":123}},
            {"observed_state":{"selected_three":["A"]}},
            {"state_revision":-1}, {"match_id":""},
            {"turn_epoch":True}, {"deadline_monotonic":"not a time"},
            {"deadline_monotonic":float("nan")},
            {"deadline_monotonic":float("inf")},
            {"match_id":"../private?"}, {"request_id":"x"*200},
            {"observed_state":{"our_active":"A"*400}},
        ):
            with self.subTest(kwargs=kwargs):
                result = capture(**kwargs)
                self.assertEqual(result.status, "REFUSED")
                self.assertIsNone(result.snapshot)

    def test_real_preloaded_team_manifest_is_not_simulator_approval(self):
        root = Path(__file__).resolve().parents[1] / "masters"
        assets = MasterPreloader(root).load_for_new_match()
        result = capture(assets,observed_state={
            "our_active":"マスカーニャ",
            "opponent_active":"ガブリアス",
            "selected_three":["マスカーニャ","アシレーヌ","サーフゴー"]})
        self.assertEqual(result.status, "CAPTURED_UNVERIFIED_GAME_STATE")
        self.assertFalse(result.legal_actions_verified)
        self.assertFalse(result.game_battle_authenticated)
        self.assertEqual(result.snapshot.window.team_sha256,
                         assets.sha256["Pokemon_Champions_MC_team_2026-10-03.csv"])


if __name__ == "__main__":
    unittest.main()
