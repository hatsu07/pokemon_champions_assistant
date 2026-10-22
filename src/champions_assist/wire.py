"""Local v2.3 wire primitives used before any network adapter is introduced."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from .reducer import canonical_json


PROTOCOL_VERSION = "2.3.0"


@dataclass(frozen=True)
class AllowedActions:
    """Evidence-backed choices only; this object never ranks a choice."""

    completeness: str
    moves: tuple[str, ...] = ()
    pivot_moves: tuple[str, ...] = ()
    switch_targets: tuple[str, ...] = ()
    team_candidates: tuple[str, ...] = ()
    mega_available: bool = False
    choice_lock: str | None = None
    active_form: str | None = None

    def as_wire(self) -> dict[str, Any]:
        return {
            "completeness": self.completeness,
            "moves": list(self.moves),
            "pivot_moves": list(self.pivot_moves),
            "switch_targets": list(self.switch_targets),
            "team_candidates": list(self.team_candidates),
            "mega_available": self.mega_available,
            "choice_lock": self.choice_lock,
            "active_form": self.active_form,
        }

    def digest(self, phase: str) -> str:
        return allowed_action_digest(phase, self.as_wire())


def allowed_action_digest(phase: str, allowed: dict[str, Any]) -> str:
    """Implement the exact v2.3 normalized legal-action fingerprint."""
    keys = ("moves", "pivot_moves", "switch_targets", "team_candidates")
    if allowed["completeness"] not in {"COMPLETE", "PARTIAL", "UNKNOWN"}:
        raise ValueError("invalid allowed-action completeness")
    normalized: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "phase": phase,
        "completeness": allowed["completeness"],
        "mega_available": allowed["mega_available"],
        "choice_lock": allowed["choice_lock"],
        "active_form": allowed["active_form"],
    }
    for key in keys:
        values = allowed[key]
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate legal-choice identifier in {key}")
        normalized[key] = sorted(values)
    if any(move not in allowed["moves"] for move in allowed["pivot_moves"]):
        raise ValueError("pivot_moves must be a subset of moves")
    return "sha256:" + hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()
