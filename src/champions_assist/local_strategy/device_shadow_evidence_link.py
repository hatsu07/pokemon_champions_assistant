"""#141④⑤⑥: OFFLINE linkage of actual-device claims to SQLite Shadow windows.

This module validates completeness/identity of externally supplied evidence
and computes measured *claimed* screen timestamps. It never authenticates
that a capture depicts a real Pokémon Champions match, adjudicates strategic
correctness, provides an action or promotes synthetic LOCAL_SHADOW output.
Independent human/game verification still required. No live/Qt callback.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
import re
from typing import Any, Iterable

from ..app_config import ConfigurationError
from ..real_battle_validation import (
    SCHEMA as DEVICE_SCHEMA, TURN_LIMIT_MS, validate_real_battle_packet,
)
from .shadow_comparison_store import ShadowComparisonRecord

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_GOLDENS = frozenset({
    "immediate_ko", "choice_lock_pp", "mega_form", "priority_speed",
    "processor_vs_safe_switch", "setup", "endgame_1v1", "stale_revision",
})
_LOCAL_STATUSES = frozenset({"NO_RANKING", "MODEL_DIAGNOSTIC"})
_GPT_STATUSES = frozenset({"GPT_ADVICE", "GPT_ABSTAIN", "GPT_UNVERIFIED"})


@dataclass(frozen=True)
class CapturedTurnLink:
    """External, game-capture-indexed annotation of a frozen Shadow window."""
    match_id: str
    turn_index: int
    decision_window_id: str
    state_revision: int
    snapshot_sha256: str
    capture_sha256: str
    # Actual Windows screen timestamp for LOCAL_SHADOW diagnostic display
    # as annotated from an independent capture (not engine elapsed time).
    shadow_visible_ms: int | None = None


@dataclass(frozen=True)
class GoldenReviewClaim:
    """Review label and source proof; never an independently verified truth."""
    case_id: str
    kind: str
    match_id: str
    decision_window_id: str
    state_revision: int
    snapshot_sha256: str
    capture_sha256: str
    outcome: str  # PASS | KNOWN_UNSAFE | UNREVIEWED
    source: str = "EXTERNALLY_SUBMITTED_CAPTURE_REVIEW"


@dataclass(frozen=True)
class DeviceShadowLinkAudit:
    status: str
    reason: str
    match_count: int = 0
    bound_turns: int = 0
    same_revision_shadow_turns: int = 0
    nonabstaining_diagnostic_turns: int = 0
    timed_shadow_displays: int = 0
    late_shadow_displays: int = 0
    p50_shadow_display_ms: int | None = None
    p95_shadow_display_ms: int | None = None
    p99_shadow_display_ms: int | None = None
    covered_golden_kinds: tuple[str, ...] = ()
    missing_golden_kinds: tuple[str, ...] = ()
    submitted_unsafe_claim_count: int = 0
    missing_evidence: tuple[str, ...] = ()
    # These are NEVER implied by a hash or an external text label.
    game_capture_independently_authenticated: bool = False
    real_tactical_correctness_verified: bool = False
    phase2_accepted: bool = False


def _blocked(reason: str) -> DeviceShadowLinkAudit:
    return DeviceShadowLinkAudit("EVIDENCE_LINK_REFUSED", reason)


def _percentile(values: list[int], percent: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, ceil(percent * len(ordered)) - 1)]


def audit_offline_device_shadow_link(
    packet: Any, *,
    team_id: str, team_fingerprint: str, regulation: str,
    frozen_team_sha256: str,
    shadow_rows: Iterable[ShadowComparisonRecord],
    frames: Iterable[CapturedTurnLink],
    golden_claims: Iterable[GoldenReviewClaim] = (),
) -> DeviceShadowLinkAudit:
    """Read-only, game-agnostic evidence pairing. Synthetic tests can run it.

    The existing device packet validator checks capture bytes/HASH, the human
    match labels and shared 90s selection/PREP, 45s turn deadlines. This
    method additionally binds each supplied turn to exactly one same-revision
    GPT/LOCAL_SHADOW SQLite entry, and each Golden to that frame's capture.
    Neither caller labels nor file SHA alone can certify game truth.
    """
    if (not isinstance(packet, dict) or packet.get("schema") != DEVICE_SCHEMA
            or not isinstance(frozen_team_sha256, str)
            or _SHA.fullmatch(frozen_team_sha256) is None):
        return _blocked("V3_PACKET_AND_REGISTERED_TEAM_SHA_REQUIRED")
    try:
        device = validate_real_battle_packet(
            packet, team_id=team_id, team_fingerprint=team_fingerprint,
            regulation=regulation)
    except (ConfigurationError, ValueError, TypeError, KeyError, OSError):
        return _blocked("INVALID_DEVICE_CAPTURE_OR_TIMELINE")
    if not device["device_evidence_passed"]:
        return _blocked("DEVICE_FIVE_MATCHES_OR_90_90_45_DEADLINES_FAILED")

    try:
        rows = tuple(shadow_rows)
        links = tuple(frames)
        goldens = tuple(golden_claims)
    except TypeError:
        return _blocked("INVALID_SHADOW_OR_REVIEW_ITERABLE")
    if (not rows or not links or len(rows) > 3000 or len(links) > 3000
            or len(goldens) > 500
            or any(not isinstance(x, ShadowComparisonRecord) for x in rows)
            or any(not isinstance(x, CapturedTurnLink) for x in links)
            or any(not isinstance(x, GoldenReviewClaim) for x in goldens)):
        return _blocked("INVALID_SHADOW_OR_REVIEW_RECORDS")
    matches = {m["match_id"]: m for m in device["matches"]}
    turn_index_keys = set()
    window_keys = set()
    frame_by_window = {}
    durations = []
    late = 0
    for frame in links:
        match = matches.get(frame.match_id)
        if (match is None
                or type(frame.turn_index) is not int
                or not 0 <= frame.turn_index < len(match["turns"])
                or type(frame.state_revision) is not int
                or frame.state_revision < 0
                or not frame.decision_window_id
                or not isinstance(frame.snapshot_sha256, str)
                or _SHA.fullmatch(frame.snapshot_sha256) is None
                or frame.capture_sha256 != match["capture_sha256"]):
            return _blocked("FRAME_NOT_BOUND_TO_EXACT_CAPTURE_AND_TURN")
        index_key = (frame.match_id, frame.turn_index)
        window_key = (frame.match_id, frame.decision_window_id,
                      frame.state_revision)
        if index_key in turn_index_keys or window_key in window_keys:
            return _blocked("DUPLICATE_FRAME_OR_REVISION")
        turn_index_keys.add(index_key)
        window_keys.add(window_key)
        frame_by_window[window_key] = frame
        # These original raw timestamps are independently annotated from
        # captured video; do not confuse local_elapsed_ms with DISPLAY delay.
        raw_times = packet["matches"][list(matches).index(frame.match_id)][
            "timeline"]["turns"][frame.turn_index]
        started = raw_times["action_screen_ms"]
        displayed = frame.shadow_visible_ms
        if displayed is not None:
            if (type(displayed) is not int or displayed < started):
                return _blocked("INVALID_SHADOW_SCREEN_TIMESTAMP")
            duration = displayed - started
            if duration > TURN_LIMIT_MS:
                late += 1
            durations.append(duration)
    expected_turn_keys = {
        (mid, i) for mid, m in matches.items()
        for i in range(len(m["turns"]))
    }
    if turn_index_keys != expected_turn_keys:
        return _blocked("MISSING_OR_UNRELATED_DEVICE_TURN_FRAMES")
    row_by_window = {}
    for row in rows:
        key = (row.match_id, row.decision_window_id, row.state_revision)
        frame = frame_by_window.get(key)
        if (key in row_by_window or frame is None
                or row.team_sha256 != frozen_team_sha256
                or row.snapshot_sha256 != frame.snapshot_sha256
                or row.local_status not in _LOCAL_STATUSES
                or row.gpt_status not in _GPT_STATUSES
                or type(row.local_elapsed_ms) is not int
                or row.local_elapsed_ms < 0
                or type(row.input_deadline_met) is not bool):
            return _blocked("SHADOW_SQLITE_WINDOW_OR_TEAM_MISMATCH")
        row_by_window[key] = row
    if set(row_by_window) != window_keys:
        return _blocked("MISSING_SAME_REVISION_GPT_LOCAL_RECORD")
    seen = set()
    covered = set()
    unsafe = 0
    for label in goldens:
        key = (label.match_id, label.decision_window_id,
               label.state_revision)
        frame = frame_by_window.get(key)
        if (not label.case_id or label.case_id in seen
                or label.kind not in _GOLDENS
                or label.outcome not in ("PASS", "KNOWN_UNSAFE", "UNREVIEWED")
                or label.source != "EXTERNALLY_SUBMITTED_CAPTURE_REVIEW"
                or frame is None
                or label.snapshot_sha256 != frame.snapshot_sha256
                or label.capture_sha256 != frame.capture_sha256):
            return _blocked("GOLDEN_NOT_BOUND_TO_EXACT_CAPTURE_AND_WINDOW")
        seen.add(label.case_id)
        if label.outcome != "UNREVIEWED":
            covered.add(label.kind)
        if label.outcome == "KNOWN_UNSAFE":
            unsafe += 1
    missing = set(_GOLDENS) - covered
    nonabstaining = sum(row.local_status == "MODEL_DIAGNOSTIC" for row in rows)
    blockers = [
        "GAME_CAPTURE_TRUTH_NOT_CRYPTOGRAPHICALLY_ATTESTABLE",
        "INDEPENDENT_CHAMPIONS_MOVE_DAMAGE_LEGALITY_NOT_ADJUDICATED",
        "LOCAL_MODEL_DIAGNOSTIC_NOT_ACTIONABLE",
        "THREE_POKEMON_TACTICAL_18_NOT_INDEPENDENTLY_CONFIRMED",
        "TRUE_GPT_LOCAL_LIVE_PARALLELISM_NOT_ATTESTED",
    ]
    if missing: blockers.append("REQUIRED_GOLDEN_CATEGORIES_NOT_LABELLED")
    if unsafe: blockers.append("KNOWN_UNSAFE_GOLDEN_CLAIMS_PRESENT")
    if not nonabstaining: blockers.append("ALL_LOCAL_SHADOW_ABSTAINED")
    if len(durations) < len(links):
        blockers.append("SHADOW_WINDOWS_DISPLAY_TIMES_NOT_RECORDED")
    if late: blockers.append("LOCAL_SHADOW_DISPLAYED_AFTER_45S")
    return DeviceShadowLinkAudit(
        "STRUCTURALLY_LINKED_UNVERIFIED",
        "EXTERNAL_DEVICE_REVIEW_PACKET_IS_NOT_INDEPENDENT_GAME_PROOF",
        len(matches), len(links), len(rows), nonabstaining,
        len(durations), late,
        _percentile(durations, .50) if durations else None,
        _percentile(durations, .95) if durations else None,
        _percentile(durations, .99) if durations else None,
        tuple(sorted(covered)), tuple(sorted(missing)), unsafe,
        tuple(blockers),
    )
