"""Versioned, immutable boundary between observations and synthetic branches.

No inference about unobserved moves, held items, AP or Champions mechanics
is permitted. Everything in this module is inert until explicitly called.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Mapping


PROTOCOL_VERSION = "issue140.v1"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def fingerprint(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


class LegalStatus(str, Enum):
    LEGAL = "LEGAL"
    ILLEGAL = "ILLEGAL"
    UNKNOWN = "UNKNOWN"


class CapabilityStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"


class WindowKind(str, Enum):
    MOVE_COMMAND = "MOVE_COMMAND"
    NORMAL_SWITCH = "NORMAL_SWITCH"
    PIVOT_TARGET = "PIVOT_TARGET"
    FAINT_REPLACEMENT = "FAINT_REPLACEMENT"
    EFFECT_REPLACEMENT = "EFFECT_REPLACEMENT"


class ActionKind(str, Enum):
    MOVE = "MOVE"
    SWITCH = "SWITCH"
    PIVOT = "PIVOT"


@dataclass(frozen=True)
class ObservedFact:
    key: str
    value_json: str
    source: str
    captured_at: str
    confidence: str = "OBSERVED"

    @classmethod
    def from_value(cls, key: str, value: Any, source: str,
                   captured_at: str, confidence: str = "OBSERVED") -> "ObservedFact":
        if not key or not source or not captured_at:
            raise ValueError("fact needs key, source and timestamp")
        return cls(key, canonical_json(value), source, captured_at, confidence)


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    assumptions_json: str
    evidence_refs: tuple[str, ...] = ()
    likelihood: float | None = None

    @classmethod
    def from_assumptions(cls, hypothesis_id: str, assumptions: Mapping[str, Any],
                         evidence_refs: tuple[str, ...] = (),
                         likelihood: float | None = None) -> "Hypothesis":
        if not hypothesis_id or (likelihood is not None and not 0 <= likelihood <= 1):
            raise ValueError("invalid hypothesis")
        return cls(hypothesis_id, canonical_json(dict(assumptions)),
                   tuple(evidence_refs), likelihood)


def action_digest(action_ids: tuple[str, ...]) -> str:
    return fingerprint(sorted(set(action_ids)))


@dataclass(frozen=True)
class DecisionWindow:
    match_id: str
    decision_window_id: str
    window_kind: WindowKind
    turn_epoch: int
    state_revision: int
    screen_kind: str
    active_pokemon: str
    team_sha256: str
    four_master_sha256: tuple[tuple[str, str], ...]
    regulation: str
    simulator_sha: str
    allowed_action_digest: str
    request_id: str
    deadline_monotonic: float

    def __post_init__(self) -> None:
        if (not all((self.match_id, self.decision_window_id, self.screen_kind,
                     self.team_sha256, self.regulation, self.simulator_sha,
                     self.allowed_action_digest, self.request_id))
                or self.turn_epoch < 0 or self.state_revision < 0
                or len(self.four_master_sha256) != 4
                or len({k for k, _ in self.four_master_sha256}) != 4):
            raise ValueError("invalid decision window identity")
        for _, digest in self.four_master_sha256:
            if not digest:
                raise ValueError("missing frozen master SHA")

    def identity(self) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_VERSION,
            "match_id": self.match_id,
            "decision_window_id": self.decision_window_id,
            "window_kind": self.window_kind.value,
            "turn_epoch": self.turn_epoch,
            "state_revision": self.state_revision,
            "screen_kind": self.screen_kind,
            "active_pokemon": self.active_pokemon,
            "team_sha256": self.team_sha256,
            "four_master_sha256": list(map(list, self.four_master_sha256)),
            "regulation": self.regulation,
            "simulator_sha": self.simulator_sha,
            "allowed_action_digest": self.allowed_action_digest,
            "request_id": self.request_id,
        }


@dataclass(frozen=True)
class FrozenBattleState:
    """Full observed projection is kept as detached serialized bytes.

    A hypothetical simulator state never mutates this JSON nor a SQLite fact.
    """
    window: DecisionWindow
    observed_state_json: str
    observation_json: str
    facts: tuple[ObservedFact, ...] = ()
    hypotheses: tuple[Hypothesis, ...] = ()
    verified_action_ids: tuple[str, ...] = ()
    constraints_confirmed: bool = False

    @classmethod
    def freeze(cls, window: DecisionWindow, state: Mapping[str, Any],
               observation: Mapping[str, Any], *,
               facts: tuple[ObservedFact, ...] = (),
               hypotheses: tuple[Hypothesis, ...] = (),
               verified_action_ids: tuple[str, ...] = (),
               constraints_confirmed: bool = False) -> "FrozenBattleState":
        verified = tuple(sorted(set(verified_action_ids)))
        if action_digest(verified) != window.allowed_action_digest:
            raise ValueError("current allowed actions digest mismatch")
        return cls(window, canonical_json(dict(state)),
                   canonical_json(dict(observation)), tuple(facts),
                   tuple(hypotheses), verified, constraints_confirmed)

    @property
    def state(self) -> dict[str, Any]:
        return json.loads(self.observed_state_json)

    @property
    def observation(self) -> dict[str, Any]:
        return json.loads(self.observation_json)

    @property
    def snapshot_sha256(self) -> str:
        return fingerprint({
            "window": self.window.identity(),
            "state": self.observed_state_json,
            "observation": self.observation_json,
            "facts": [(f.key, f.value_json, f.source, f.captured_at,
                       f.confidence) for f in self.facts],
        })


@dataclass(frozen=True)
class CandidateAction:
    action_id: str
    kind: ActionKind
    move: str = ""
    switch_target: str = ""
    legal_status: LegalStatus = LegalStatus.UNKNOWN
    reason: str = ""
    window_id: str = ""

    def replay_answer(self) -> dict[str, str]:
        return {"action_kind": self.kind.value, "move": self.move,
                "switch_target": self.switch_target, "warning": ""}


@dataclass(frozen=True)
class SimulationResult:
    request_id: str
    identity_json: str
    snapshot_sha256: str
    hypothesis_id: str
    capability_status: CapabilityStatus
    payload_json: str
    reason: str = ""

    @classmethod
    def from_worker(cls, request_id: str, identity: Mapping[str, Any],
                    snapshot_sha256: str, hypothesis_id: str,
                    status: CapabilityStatus, payload: Mapping[str, Any],
                    reason: str = "") -> "SimulationResult":
        return cls(request_id, canonical_json(dict(identity)), snapshot_sha256,
                   hypothesis_id, status, canonical_json(dict(payload)), reason)
