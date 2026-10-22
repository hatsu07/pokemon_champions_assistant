"""Deterministic, evidence-first state projection.

The reducer consumes facts already classified by an observer or a manual correction.
It never infers a Pokémon mechanic, action, or tactic from incomplete information.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, Iterable


FIELD_PRIORITY = {
    "RULE_DERIVED": 1,
    "BATTLE_TEXT": 2,
    "UI_DIRECT": 3,
    "MANUAL_VERIFIED": 4,
}
EVIDENCE_QUALITIES = frozenset({"CONFIRMED", "CANDIDATE"})


def canonical_json(value: Any) -> str:
    """Return a stable JSON representation suitable for equality checks."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    sequence: int
    field: str
    value: Any
    source_kind: str
    quality: str
    supersedes: tuple[str, ...] = ()

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> "Evidence":
        payload = event["payload"]
        source_kind = payload["source_kind"]
        quality = payload["quality"]
        if source_kind not in FIELD_PRIORITY:
            raise ValueError(f"unsupported evidence source: {source_kind}")
        if quality not in EVIDENCE_QUALITIES:
            raise ValueError(f"unsupported evidence quality: {quality}")
        return cls(
            evidence_id=payload["evidence_id"],
            sequence=event["event_seq"],
            field=payload["field"],
            value=payload["value"],
            source_kind=source_kind,
            quality=quality,
            supersedes=tuple(payload.get("supersedes", [])),
        )


def reconcile_evidence(evidence: Iterable[Evidence]) -> dict[str, dict[str, Any]]:
    """Project append-only evidence without discarding disagreements.

    A correction supersedes only its explicitly named evidence. Equal-priority
    confirmed observations with different values remain a conflict.
    """
    records = list(evidence)
    known_ids = {item.evidence_id for item in records}
    superseded = {target for item in records for target in item.supersedes}
    if not superseded.issubset(known_ids):
        missing = sorted(superseded - known_ids)
        raise ValueError(f"evidence supersedes missing records: {missing}")

    by_field: dict[str, list[Evidence]] = defaultdict(list)
    for item in records:
        if item.evidence_id not in superseded:
            by_field[item.field].append(item)

    projection: dict[str, dict[str, Any]] = {}
    for field, entries in by_field.items():
        confirmed = [item for item in entries if item.quality == "CONFIRMED"]
        evidence_ids = [item.evidence_id for item in entries]
        if not confirmed:
            projection[field] = {
                "value": None,
                "certainty": "CANDIDATE",
                "candidates": [item.value for item in entries],
                "evidence_ids": evidence_ids,
                "disagreement": False,
            }
            continue

        highest = max(FIELD_PRIORITY[item.source_kind] for item in confirmed)
        winners = [item for item in confirmed if FIELD_PRIORITY[item.source_kind] == highest]
        winning_values = {canonical_json(item.value) for item in winners}
        all_values = {canonical_json(item.value) for item in confirmed}
        if len(winning_values) != 1:
            projection[field] = {
                "value": None,
                "certainty": "CONFLICT",
                "candidates": [item.value for item in winners],
                "evidence_ids": evidence_ids,
                "disagreement": True,
            }
            continue

        winner = max(winners, key=lambda item: item.sequence)
        projection[field] = {
            "value": winner.value,
            "certainty": "CONFIRMED",
            "candidates": [],
            "evidence_ids": evidence_ids,
            "disagreement": len(all_values) > 1,
        }
    return projection


class StateReducer:
    """Replay semantic events into a JSON-serializable current projection.

    Event payloads are intentionally generic. A future Champions-specific effect
    engine may create confirmed events only when its own evidence requirements are
    met; this class must not fill in any unknown effect values.
    """

    def replay(self, events: Iterable[dict[str, Any]]) -> dict[str, Any]:
        evidence: list[Evidence] = []
        durations: dict[str, dict[str, Any]] = {}
        pending_effect_ids: set[str] = set()
        confirmed_effect_ids: set[str] = set()
        turn_end_ids: set[str] = set()
        observation_revision = 0
        tactical_state_version = 0

        for event in events:
            event_type = event["event_type"]
            payload = event["payload"]
            if event_type == "EVIDENCE":
                evidence.append(Evidence.from_event(event))
                observation_revision += 1
                tactical_state_version += 1
            elif event_type == "PENDING_EFFECT":
                pending_effect_ids.add(payload["effect_instance_id"])
                observation_revision += 1
                tactical_state_version += 1
            elif event_type in {"RESOLVED_EFFECT", "FAILED_EFFECT", "NEGATED_EFFECT"}:
                pending_effect_ids.discard(payload["effect_instance_id"])
                observation_revision += 1
                tactical_state_version += 1
            elif event_type == "CONFIRMED_EFFECT":
                effect_id = payload["effect_instance_id"]
                if effect_id in confirmed_effect_ids:
                    raise ValueError(f"duplicate confirmed effect in event stream: {effect_id}")
                confirmed_effect_ids.add(effect_id)
                pending_effect_ids.discard(effect_id)
                observation_revision += 1
                tactical_state_version += 1
            elif event_type == "DURATION_STARTED":
                candidates = payload["remaining_candidates"]
                if not candidates or any(not isinstance(value, int) or value < 0 for value in candidates):
                    raise ValueError("duration candidates must be non-negative integers")
                durations[payload["duration_id"]] = {
                    "field": payload["field"],
                    "remaining_candidates": sorted(set(candidates)),
                    "source_event_id": event["event_id"],
                }
                tactical_state_version += 1
            elif event_type == "DURATION_CLEARED":
                durations.pop(payload["duration_id"], None)
                tactical_state_version += 1
            elif event_type == "TURN_END_CONFIRMED":
                turn_id = payload["turn_id"]
                if turn_id in turn_end_ids:
                    raise ValueError(f"duplicate turn end in event stream: {turn_id}")
                turn_end_ids.add(turn_id)
                for duration in durations.values():
                    duration["remaining_candidates"] = [
                        max(0, value - 1) for value in duration["remaining_candidates"]
                    ]
                tactical_state_version += 1
            elif event_type in {"NOTE", "ACTUAL_ACTION_CONFIRMED"}:
                # These are append-only audit events; they do not assert game state.
                continue
            else:
                raise ValueError(f"unsupported event type: {event_type}")

        return {
            "fields": reconcile_evidence(evidence),
            "durations": durations,
            "pending_effect_ids": sorted(pending_effect_ids),
            "confirmed_effect_ids": sorted(confirmed_effect_ids),
            "turn_end_ids": sorted(turn_end_ids),
            "observation_revision": observation_revision,
            "tactical_state_version": tactical_state_version,
        }

    @staticmethod
    def copy_projection(projection: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(projection)
