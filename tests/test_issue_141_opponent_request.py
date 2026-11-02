"""Synthetic Showdown P2 request candidates, not engine legality tests."""
from __future__ import annotations

from dataclasses import replace
import unittest

from champions_assist.local_strategy.models import canonical_json, fingerprint
from champions_assist.local_strategy.opponent_request import (
    P2RequestEnvelope, enumerate_simple_p2_request,
)
from tests.test_issue_141_one_turn import current


def request(*, trapped=False, extra=None, force=False, members=None):
    roster = members or [
        {"active": True, "condition": "100/100"},
        {"active": False, "condition": "58/100"},
        {"active": False, "condition": "0 fnt"},
    ]
    side = {"id": "p2", "pokemon": roster}
    if force:
        return {"forceSwitch": [True], "side": side}
    a = {"moves": [
        {"id": "extremespeed", "pp": 4, "disabled": False},
        {"id": "earthquake", "pp": 0, "disabled": True},
        {"id": "dragonclaw", "pp": 12, "disabled": False},
    ], "trapped": trapped}
    if extra:
        a.update(extra)
    return {"active": [a], "side": side}


def envelope(snapshot, req=None, battle=None):
    battle = battle if battle is not None else {
        "fixture": "detached-complete-synthetic-battle",
        "rng": {"seed": [1, 2, 3, 4]},
        "turn": 6,
    }
    return P2RequestEnvelope(
        canonical_json(battle), canonical_json(req or request()),
        fingerprint(battle), snapshot.snapshot_sha256,
        snapshot.window.simulator_sha,
    )


class OpponentRequestChoiceTests(unittest.TestCase):
    def test_simple_single_move_request_indices_and_safe_switches(self):
        snap = current()
        result = enumerate_simple_p2_request(snap, envelope(snap))
        self.assertEqual(result.status, "PROVISIONAL_REQUEST_OPTIONS")
        self.assertEqual(result.commands, ("move 1", "move 3", "switch 2"))
        self.assertEqual(result.reason, "REQUEST_DERIVED_NOT_ENGINE_ATTESTED")
        self.assertFalse(result.exhaustive_legal_choices_verified)

    def test_trapped_forbids_normal_switch_and_forced_switch_is_separate(self):
        s = current()
        res = enumerate_simple_p2_request(s, envelope(s,request(trapped=True)))
        self.assertEqual(res.commands, ("move 1", "move 3"))
        forced = enumerate_simple_p2_request(s, envelope(s, request(force=True)))
        self.assertEqual(forced.status, "PROVISIONAL_REQUEST_OPTIONS")
        self.assertEqual(forced.commands, ("switch 2",))
        self.assertEqual(forced.reason, "FORCED_SWITCH_REQUEST_ONLY")

    def test_complex_mechanics_and_unknown_disable_never_guess_legal_choices(self):
        s=current()
        for extra in (
            {"canMegaEvo": True}, {"canMegaEvoX": True},
            {"canMegaEvoY": True}, {"canZMove": [{"move": "test"}]},
            {"canDynamax": True}, {"canTerastallize": "Dragon"},
            {"maybeDisabled": True}, {"maybeTrapped": True},
            {"maybeLocked": True}, {"canUltraBurst": True},
            {"maxMoves": {"maxMoves": [{"move": "test"}]}},
        ):
            with self.subTest(extra=extra):
                out = enumerate_simple_p2_request(s,envelope(s,request(extra=extra)))
                self.assertEqual(out.status, "NO_CHOICES")
                self.assertEqual(out.reason,
                                 "P2_COMPLEX_CHOICE_REQUIRES_ENGINE_VALIDATION")
                self.assertEqual(out.commands, ())

    def test_not_a_legal_engine_attestation_even_with_matched_hashes(self):
        s = current()
        result = enumerate_simple_p2_request(s, envelope(s))
        self.assertFalse(result.exhaustive_legal_choices_verified)
        self.assertEqual(result.battle_sha256, fingerprint({
            "fixture": "detached-complete-synthetic-battle",
            "rng": {"seed": [1, 2, 3, 4]}, "turn": 6,
        }))

    def test_stale_snapshot_engine_and_replay_hash_rejected(self):
        s = current()
        e = envelope(s)
        for invalid in (
            replace(e, snapshot_sha256="0"*64),
            replace(e, simulator_sha="old-engine"),
            replace(e, provenance="OCR_GUESSED"),
        ):
            with self.subTest(invalid=invalid):
                out=enumerate_simple_p2_request(s,invalid)
                self.assertEqual(out.reason,"STALE_OR_UNTRUSTED_REQUEST_BINDING")
        bad = replace(e,battle_sha256="0"*64)
        self.assertEqual(
            enumerate_simple_p2_request(s,bad).reason,
            "P2_REQUEST_BINDING_INVALID")

    def test_request_not_matched_to_side_p2_or_one_active_is_rejected(self):
        s=current()
        r=request()
        r["side"]["id"]="p1"
        self.assertEqual(enumerate_simple_p2_request(s,envelope(s,r)).reason,
                         "P2_SINGLES_ROSTER_UNVERIFIED")
        r=request()
        r["side"]["pokemon"][1]["active"]=True
        self.assertEqual(enumerate_simple_p2_request(s,envelope(s,r)).reason,
                         "P2_SINGLES_ROSTER_UNVERIFIED")
        r=request()
        r["side"]["pokemon"][2]["condition"]="?"
        self.assertEqual(enumerate_simple_p2_request(s,envelope(s,r)).reason,
                         "P2_SINGLES_ROSTER_UNVERIFIED")
        r=request()
        r["side"]["pokemon"].append({"active":False,"condition":"100/100"})
        self.assertEqual(enumerate_simple_p2_request(s,envelope(s,r)).reason,
                         "P2_SINGLES_ROSTER_UNVERIFIED")

    def test_malformed_or_ambiguous_moves_fail_entire_response(self):
        s=current()
        variants=(
            [{"id":"earthquake","pp":4}, {"id":"earthquake","pp":4}],
            [{"id":"fake-move","pp":4}],
            [{"id":"earthquake","pp":0,"disabled":False}],
            [{"id":"earthquake","pp":4,"disabled":"hidden"}],
            [{"id":"earthquake","pp":True}],
            [{"id":"earthquake","pp":4}] * 5,
            [],
        )
        for m in variants:
            r=request()
            r["active"][0]["moves"]=m
            with self.subTest(moves=m):
                out=enumerate_simple_p2_request(s,envelope(s,r))
                self.assertEqual(out.status,"NO_CHOICES")
                self.assertEqual(out.reason,"P2_MOVE_REQUEST_INVALID")

    def test_wait_preview_forced_switch_ambiguity_and_no_bench(self):
        s=current()
        for bad in (
            {"wait":True,"side":request()["side"]},
            {"teamPreview":True,"side":request()["side"]},
            {"forceSwitch":[True,False],"side":request()["side"]},
            {**request(), "forceSwitch":[True]},
        ):
            with self.subTest(bad=bad):
                self.assertEqual(
                    enumerate_simple_p2_request(s,envelope(s,bad)).status,
                    "NO_CHOICES")
        no_bench=[
            {"active":True,"condition":"100/100"},
            {"active":False,"condition":"0 fnt"},
            {"active":False,"condition":"0 fnt"},
        ]
        out=enumerate_simple_p2_request(s,envelope(s,request(force=True,members=no_bench)))
        self.assertEqual(out.reason,"P2_NO_VERIFIED_REQUEST_OPTIONS")


if __name__ == "__main__":
    unittest.main()
