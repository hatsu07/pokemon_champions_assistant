"""Synthetic current-window evidence binding; NOT capture authentication.

PR #186 slice: prove that *declared* observation records correspond to the
already frozen window and its exact stored visible values. No live OCR intake,
historical event attestation, simulator readiness or real-game provenance claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re
from typing import Any

from .models import FrozenBattleState, canonical_json, fingerprint

SOURCE = "SYNTHETIC_WINDOW_EVIDENCE"
SECTIONS = (
    "hp_by_pokemon", "boosts", "status_by_pokemon", "current_type",
    "moves_by_pokemon", "item_by_pokemon", "ability_by_pokemon",
    "form_by_pokemon",
)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


@dataclass(frozen=True)
class WindowEvidenceRecord:
    record_id: str
    source: str
    match_id: str
    decision_window_id: str
    turn_epoch: int
    state_revision: int
    screen_kind: str
    active_pokemon: str
    observation_sha256: str
    captured_at: str
    section: str
    side: str
    actor: str
    value_json: str


@dataclass(frozen=True)
class WindowEvidenceAudit:
    status: str
    reason: str
    checked_keys: tuple[str, ...] = ()
    historical_keys_unverified: tuple[str, ...] = ()
    snapshot_sha256: str = ""
    observation_sha256: str = ""
    # A self-reported source and SHA can establish consistency, NOT real capture.
    capture_origin_authenticated: bool = False
    historical_events_authenticated: bool = False
    full_state_reconstructed: bool = False
    ready_for_engine: bool = False


def _reject(reason: str) -> WindowEvidenceAudit:
    return WindowEvidenceAudit("NO_WINDOW_EVIDENCE_BINDING", reason)


def _valid_stored_fact(section: str, value: Any) -> bool:
    """A present actor key is a recorded fact, NEVER an absent observation.

    Only accept the actual shapes emitted by battle_evidence.py. Validate both
    current actors and historical entries *before* making any SHA comparison.
    HP/status/item literals remain raw; do not infer engine IDs or numeric HP.
    """
    if section == "boosts":
        return (isinstance(value, dict) and bool(value)
                and all(k in ("A", "B", "C", "D", "S", "ACC", "EVA")
                        and type(v) is int and -6 <= v <= 6
                        for k, v in value.items()))
    if section == "moves_by_pokemon":
        return (isinstance(value, list) and 1 <= len(value) <= 4
                and all(isinstance(v, str) and 0 < len(v) <= 160
                        and v == v.strip() and v.upper() not in
                        ("UNKNOWN", "NONE", "?") for v in value)
                and len(set(value)) == len(value))
    return (isinstance(value, str) and 0 < len(value) <= 160
            and value == value.strip()
            and value.upper() not in ("UNKNOWN", "NONE", "?"))


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str) or value.strip() != value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except (ValueError, TypeError, OverflowError):
        return False


def audit_synthetic_window_evidence(
    snapshot: FrozenBattleState,
    records: tuple[WindowEvidenceRecord, ...],
) -> WindowEvidenceAudit:
    """Fail closed unless each current-active stored fact has a bound record.

    This is a detached synthetic contract test for later capture ingestion.
    Records can be self-asserted, so success NEVER attests their source.
    Previously seen opponents/teammates are not authenticated by a current
    capture; their stored keys are reported separately as unverified history.
    """
    if not isinstance(snapshot, FrozenBattleState):
        return _reject("FROZEN_SNAPSHOT_REQUIRED")
    if not isinstance(records, tuple) or not records:
        return _reject("CURRENT_WINDOW_RECORDS_REQUIRED")
    try:
        w = snapshot.window
        state, observation = snapshot.state, snapshot.observation
        if not isinstance(state, dict) or not isinstance(observation, dict):
            return _reject("MALFORMED_FROZEN_STATE")
        own = state.get("our_active")
        opponent = state.get("opponent_active")
        if (not isinstance(own, str) or not own or own != w.active_pokemon
                or observation.get("visual_ui") != w.screen_kind
                or (opponent is not None and
                    (not isinstance(opponent, str) or not opponent.strip()))):
            return _reject("STALE_OR_MALFORMED_WINDOW_ACTORS")
        memory = state.get("battle_evidence")
        if not isinstance(memory, dict):
            return _reject("BATTLE_EVIDENCE_REQUIRED")
        expected: dict[tuple[str, str, str], str] = {}
        historical = []
        for section in SECTIONS:
            raw = memory.get(section, {"own": {}, "opponent": {}})
            if (not isinstance(raw, dict)
                    or any(k not in ("own", "opponent") for k in raw)):
                return _reject("MALFORMED_EVIDENCE_SECTION")
            for side, active in (("own", own), ("opponent", opponent)):
                by_name = raw.get(side, {})
                if not isinstance(by_name, dict):
                    return _reject("MALFORMED_EVIDENCE_SECTION")
                for actor, value in by_name.items():
                    if not isinstance(actor, str) or not actor.strip():
                        return _reject("MALFORMED_EVIDENCE_ACTOR")
                    # A missing actor key means unknown. An explicitly
                    # present but empty/malformed value is corrupted evidence:
                    # reject it even for a switched-out historical actor.
                    if not _valid_stored_fact(section, value):
                        return _reject("MALFORMED_STORED_EVIDENCE_VALUE")
                    if not active or actor != active:
                        historical.append(f"{section}:{side}:{actor}")
                    else:
                        expected[(section, side, actor)] = canonical_json(value)
        if not expected:
            return _reject("NO_CURRENT_ACTIVE_FACTS")
        digest = fingerprint(observation)
        seen_ids: set[str] = set()
        bound: dict[tuple[str, str, str], str] = {}
        for record in records:
            if not isinstance(record, WindowEvidenceRecord):
                return _reject("MALFORMED_PROVENANCE_RECORD")
            if (not isinstance(record.record_id, str) or
                    not _ID.fullmatch(record.record_id) or
                    record.record_id in seen_ids):
                return _reject("DUPLICATE_OR_INVALID_RECORD_ID")
            seen_ids.add(record.record_id)
            if record.source != SOURCE:
                return _reject("REAL_CAPTURE_SOURCE_NOT_ATTESTED")
            if (record.match_id != w.match_id or
                    record.decision_window_id != w.decision_window_id or
                    type(record.turn_epoch) is not int or
                    record.turn_epoch != w.turn_epoch or
                    type(record.state_revision) is not int or
                    record.state_revision != w.state_revision or
                    record.screen_kind != w.screen_kind or
                    record.active_pokemon != own or
                    record.observation_sha256 != digest or
                    not _timestamp(record.captured_at)):
                return _reject("STALE_OR_UNBOUND_OBSERVATION_RECORD")
            if (record.section not in SECTIONS or
                    record.side not in ("own", "opponent") or
                    not isinstance(record.actor, str) or
                    not record.actor or
                    record.actor != (own if record.side == "own" else opponent)):
                return _reject("UNSUPPORTED_OR_INACTIVE_EVIDENCE_ACTOR")
            key = (record.section, record.side, record.actor)
            if key in bound:
                return _reject("DUPLICATE_OBSERVATION_FIELD")
            if not isinstance(record.value_json, str):
                return _reject("MALFORMED_RECORDED_VALUE")
            value = json.loads(record.value_json)
            if canonical_json(value) != record.value_json:
                return _reject("NONCANONICAL_RECORDED_VALUE")
            bound[key] = record.value_json
        if bound != expected:
            return _reject("MISSING_EXTRA_OR_CHANGED_WINDOW_FACTS")
        if snapshot.state.get("our_active") != own:
            return _reject("MUTATED_FROZEN_STATE")
        return WindowEvidenceAudit(
            "SYNTHETIC_WINDOW_FACTS_BOUND",
            "DECLARED_CURRENT_WINDOW_VALUES_MATCH_FROZEN_STATE_ONLY",
            tuple(sorted(":".join(k) for k in bound)),
            tuple(sorted(historical)),
            snapshot.snapshot_sha256,
            digest,
        )
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return _reject("MALFORMED_OR_INCONSISTENT_OBSERVATION")
