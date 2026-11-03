"""Synthetic-only tests for #141④⑤⑥ device packet / SQLite linkage.

These tests intentionally write FAKE binary "capture" bytes. Passing them
can NEVER prove actual game capture or independently verified tactics.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from champions_assist.real_battle_validation import SCHEMA
from champions_assist.local_strategy.device_shadow_evidence_link import (
    CapturedTurnLink, GoldenReviewClaim, audit_offline_device_shadow_link,
)
from tests.test_issue_141_phase2_456_audits import compared

TEAM_ID = "c" * 32
TEAM_FP = "d" * 64
REG = "M-C"
OWN_SHA = "a" * 64
KINDS = (
    "immediate_ko", "choice_lock_pp", "mega_form", "priority_speed",
    "processor_vs_safe_switch", "setup", "endgame_1v1", "stale_revision",
)


def fixture(directory: Path, n: int = 5):
    matches, rows, frames = [], [], []
    example = compared()
    for i in range(n):
        capture = directory / ("synthetic-not-real-game-capture-" + str(i))
        capture.write_bytes(("SYNTHETIC_ONLY_" + str(i)).encode())
        cap_sha = sha256(capture.read_bytes()).hexdigest()
        mid = "SYNTHETIC-" + str(i)
        matches.append({
            "match_id": mid,
            # These self-reported labels are deliberately not independently
            # authenticated; the audit must STILL never claim game approval.
            "source_kind": "REAL_CAPTURE",
            "opponent_kind": "HUMAN_CONFIRMED", "outcome": "WIN",
            "observed_opponent_count": 6,
            "prep_matchups_count": 18, "prep_win_roles_count": 3,
            "screen_recognition_errors": 0, "stale_advice_errors": 0,
            "capture": {"path": str(capture), "sha256": cap_sha},
            "timeline": {
                "selection_visible_ms": 0,
                "selection_displayed_ms": 10000,
                "selection_confirmed_ms": 12000,
                "prep_finished_ms": 80000,
                "opponent_lead_visible_ms": 85000,
                "turns": [
                    {"action_screen_ms": 90000,
                     "recommendation_visible_ms": 110000},
                    {"action_screen_ms": 120000,
                     "recommendation_visible_ms": 140000},
                ],
            },
        })
        for ti, start in enumerate((90000, 120000)):
            digest = sha256((mid + ":" + str(ti)).encode()).hexdigest()
            window = "win-" + str(i) + "-" + str(ti)
            row = replace(
                example, match_id=mid, decision_window_id=window,
                state_revision=ti + 1, snapshot_sha256=digest,
                team_sha256=OWN_SHA)
            rows.append(row)
            frames.append(CapturedTurnLink(
                mid, ti, window, ti + 1, digest, cap_sha, start + 2200))
    packet = {
        "schema": SCHEMA, "team_id": TEAM_ID, "team_fingerprint": TEAM_FP,
        "regulation": REG, "platform": "WINDOWS_REAL_CAPTURE",
        "human_reviewed": True, "matches": matches,
    }
    goldens = tuple(GoldenReviewClaim(
        case_id="synthetic-golden-" + str(i), kind=kind,
        match_id=frame.match_id,
        decision_window_id=frame.decision_window_id,
        state_revision=frame.state_revision,
        snapshot_sha256=frame.snapshot_sha256,
        capture_sha256=frame.capture_sha256,
        outcome="PASS",
    ) for i, (kind, frame) in enumerate(zip(KINDS, frames)))
    return packet, tuple(rows), tuple(frames), goldens


def run(packet, rows, frames, goldens):
    return audit_offline_device_shadow_link(
        packet, team_id=TEAM_ID, team_fingerprint=TEAM_FP,
        regulation=REG, frozen_team_sha256=OWN_SHA,
        shadow_rows=rows, frames=frames, golden_claims=goldens)


class OfflineDeviceShadowLinkTests(unittest.TestCase):
    def test_all_synthetic_packet_links_do_not_authenticate_game_truth(self):
        with TemporaryDirectory() as d:
            packet, rows, frames, goldens = fixture(Path(d))
            got = run(packet, rows, frames, goldens)
            self.assertEqual(got.status, "STRUCTURALLY_LINKED_UNVERIFIED",
                             got.reason)
            self.assertEqual(got.match_count, 5)
            self.assertEqual(got.bound_turns, 10)
            self.assertEqual(got.same_revision_shadow_turns, 10)
            self.assertEqual(got.nonabstaining_diagnostic_turns, 10)
            self.assertEqual(got.timed_shadow_displays, 10)
            self.assertEqual(got.late_shadow_displays, 0)
            self.assertEqual(
                (got.p50_shadow_display_ms, got.p95_shadow_display_ms,
                 got.p99_shadow_display_ms), (2200, 2200, 2200))
            self.assertEqual(got.covered_golden_kinds, tuple(sorted(KINDS)))
            self.assertEqual(got.missing_golden_kinds, ())
            self.assertFalse(got.game_capture_independently_authenticated)
            self.assertFalse(got.real_tactical_correctness_verified)
            self.assertFalse(got.phase2_accepted)
            self.assertIn("LOCAL_MODEL_DIAGNOSTIC_NOT_ACTIONABLE",
                          got.missing_evidence)
            self.assertIn("GAME_CAPTURE_TRUTH_NOT_CRYPTOGRAPHICALLY_ATTESTABLE",
                          got.missing_evidence)

    def test_all_abstention_never_passes_even_with_eight_claimed_goldens(self):
        with TemporaryDirectory() as d:
            packet, rows, frames, goldens = fixture(Path(d))
            rows = tuple(replace(row, local_status="NO_RANKING")
                         for row in rows)
            got = run(packet, rows, frames, goldens)
            self.assertEqual(got.nonabstaining_diagnostic_turns, 0)
            self.assertIn("ALL_LOCAL_SHADOW_ABSTAINED",
                          got.missing_evidence)
            self.assertFalse(got.phase2_accepted)

    def test_one_wrong_revision_team_or_capture_blocks_full_batch(self):
        with TemporaryDirectory() as d:
            packet, rows, frames, goldens = fixture(Path(d))
            bad_rows = (
                replace(rows[0], state_revision=99), rows[1:],
            )
            got = run(packet, (bad_rows[0],) + bad_rows[1], frames, goldens)
            self.assertEqual(got.status, "EVIDENCE_LINK_REFUSED")
            self.assertEqual(got.reason, "SHADOW_SQLITE_WINDOW_OR_TEAM_MISMATCH")
            wrong_team = (replace(rows[0], team_sha256="b" * 64),) + rows[1:]
            got = run(packet, wrong_team, frames, goldens)
            self.assertEqual(got.status, "EVIDENCE_LINK_REFUSED")
            wrong_capture = (
                replace(frames[0], capture_sha256="f" * 64),) + frames[1:]
            self.assertEqual(
                run(packet, rows, wrong_capture, goldens).reason,
                "FRAME_NOT_BOUND_TO_EXACT_CAPTURE_AND_TURN")

    def test_missing_duplicate_turn_and_golden_mismatch_block_all(self):
        with TemporaryDirectory() as d:
            packet, rows, frames, goldens = fixture(Path(d))
            self.assertEqual(
                run(packet, rows, frames[:-1], goldens).reason,
                "MISSING_OR_UNRELATED_DEVICE_TURN_FRAMES")
            self.assertEqual(
                run(packet, rows, frames[:-1] + (frames[0],), goldens).reason,
                "DUPLICATE_FRAME_OR_REVISION")
            bad_golden = (replace(goldens[0], capture_sha256="f" * 64),)
            self.assertEqual(
                run(packet, rows, frames, bad_golden).reason,
                "GOLDEN_NOT_BOUND_TO_EXACT_CAPTURE_AND_WINDOW")
            self.assertFalse(
                run(packet, rows, frames, ()).phase2_accepted)

    def test_missing_review_late_display_and_unsafe_claim_are_nonpass(self):
        with TemporaryDirectory() as d:
            packet, rows, frames, goldens = fixture(Path(d))
            no_eight = run(packet, rows, frames, goldens[:7])
            self.assertEqual(len(no_eight.missing_golden_kinds), 1)
            self.assertIn("REQUIRED_GOLDEN_CATEGORIES_NOT_LABELLED",
                          no_eight.missing_evidence)
            late_frame = replace(frames[0], shadow_visible_ms=90000 + 45001)
            late = run(packet, rows, (late_frame,) + frames[1:], goldens)
            self.assertEqual(late.late_shadow_displays, 1)
            self.assertIn("LOCAL_SHADOW_DISPLAYED_AFTER_45S",
                          late.missing_evidence)
            missing_frame_time = replace(frames[0], shadow_visible_ms=None)
            untimed = run(packet, rows, (missing_frame_time,) + frames[1:],
                          goldens)
            self.assertIn("SHADOW_WINDOWS_DISPLAY_TIMES_NOT_RECORDED",
                          untimed.missing_evidence)
            unsafe = (replace(goldens[0], outcome="KNOWN_UNSAFE"),) + goldens[1:]
            got = run(packet, rows, frames, unsafe)
            self.assertEqual(got.submitted_unsafe_claim_count, 1)
            self.assertIn("KNOWN_UNSAFE_GOLDEN_CLAIMS_PRESENT",
                          got.missing_evidence)

    def test_device_fake_or_undercount_is_not_accepted(self):
        with TemporaryDirectory() as d:
            packet, rows, frames, goldens = fixture(Path(d))
            self.assertEqual(
                run({**packet, "human_reviewed": False}, rows, frames,
                    goldens).reason,
                "INVALID_DEVICE_CAPTURE_OR_TIMELINE")
            packet4, rows4, frames4, goldens4 = fixture(Path(d), n=4)
            self.assertEqual(
                run(packet4, rows4, frames4, goldens4).reason,
                "DEVICE_FIVE_MATCHES_OR_90_90_45_DEADLINES_FAILED")


if __name__ == "__main__":
    unittest.main()
