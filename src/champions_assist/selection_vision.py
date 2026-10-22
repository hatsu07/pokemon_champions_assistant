"""Fail-closed selection planning for explicitly supplied selection-screen observations.

Two distinct model calls may be needed: vision resolves twelve candidates,
then the sole tactical model chooses three using the six frozen LIVE rows.
The 35-second limit applies to the complete pipeline, not either call alone.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
from time import perf_counter
from typing import Any, Callable, Mapping
from .preload import FrozenMatchAssets

class SelectionBlocked(ValueError):
    pass

@dataclass(frozen=True)
class SelectionDecision:
    status: str
    selected_three: tuple[str, ...] = ()
    lead: str | None = None
    opponent_six: tuple[str, ...] = ()
    reason: str = ""
    elapsed_ms: int = 0

    def as_dict(self) -> dict:
        return dict(status=self.status, selected_three=list(self.selected_three),
                    lead=self.lead, opponent_six=list(self.opponent_six),
                    reason=self.reason, elapsed_ms=self.elapsed_ms)

def verify_selection_observation(raw: Mapping[str, Any], assets: FrozenMatchAssets) -> tuple[str, ...]:
    if raw.get("visual_ui") != "TEAM_SELECTION":
        raise SelectionBlocked("NOT_TEAM_SELECTION")
    own = raw.get("our_six")
    opponent = raw.get("opponent_six")
    if not isinstance(own, list) or len(own) != 6 or not all(isinstance(v, str) for v in own):
        raise SelectionBlocked("OWN_SIX_INCOMPLETE")
    if len(set(own)) != 6 or set(own) != set(assets.own_team_names):
        raise SelectionBlocked("REGISTERED_TEAM_MISMATCH")
    if not isinstance(opponent, list) or len(opponent) != 6 or not all(isinstance(v, str) for v in opponent):
        raise SelectionBlocked("OPPONENT_SIX_INCOMPLETE")
    if len(set(opponent)) != 6:
        raise SelectionBlocked("OPPONENT_SIX_DUPLICATES")
    if raw.get("uncertain_slots"):
        raise SelectionBlocked("UNCERTAIN_TEAM_SLOT")
    try:
        assets.matrix_rows_for(tuple(opponent))
    except (ValueError, KeyError) as exc:
        raise SelectionBlocked("OPPONENT_MATRIX_MISSING") from exc
    return tuple(opponent)

def selection_response(raw: Mapping[str, Any], *, own_names: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
    chosen = raw.get("selected_three")
    lead = raw.get("lead")
    if not isinstance(chosen, list) or len(chosen) != 3 or len(set(chosen)) != 3:
        raise SelectionBlocked("INVALID_SELECTION")
    if any(name not in own_names for name in chosen):
        raise SelectionBlocked("SELECTION_OUTSIDE_REGISTERED_TEAM")
    if lead not in chosen:
        raise SelectionBlocked("LEAD_NOT_SELECTED")
    return tuple(chosen), lead

def decision_context(assets: FrozenMatchAssets, opponents: tuple[str, ...]) -> dict:
    return {
        "own_team": [dict(item) for item in assets.own_team],
        "opponent_six": list(opponents),
        "matrix_rows_six": list(assets.matrix_rows_for(opponents)),
        "frozen_gate": assets.contents["Pokemon_Champions_MC_live_decision_gate.txt"],
        "instructions": "Select three members of own_team and one of them as lead. Only use the frozen Champions rules and exactly six matrix rows. Never infer another game's mechanics.",
    }

class SelectionPlanner:
    """One tactical call after a separately validated visual observation."""
    def __init__(self, *, clock: Callable[[], float] = perf_counter):
        self.clock = clock

    def choose(self, observation: Mapping[str, Any], assets: FrozenMatchAssets,
               tactical_call: Callable[[dict, float], Mapping[str, Any]],
               *, started_at: float, max_total_seconds: float = 35.0) -> SelectionDecision:
        try:
            opponents = verify_selection_observation(observation, assets)
        except SelectionBlocked as exc:
            return SelectionDecision("BLOCKED", reason=str(exc),
                                     elapsed_ms=round((self.clock()-started_at)*1000))
        available = max_total_seconds-(self.clock()-started_at)
        if available <= 1.5:
            return SelectionDecision("DEADLINE_EXCEEDED", opponent_six=opponents,
                                     elapsed_ms=round((self.clock()-started_at)*1000))
        try:
            answer = tactical_call(decision_context(assets, opponents), available-1.0)
            chosen, lead = selection_response(answer, own_names=assets.own_team_names)
            elapsed = round((self.clock()-started_at)*1000)
            if elapsed > max_total_seconds*1000:
                return SelectionDecision("DEADLINE_EXCEEDED", opponent_six=opponents, elapsed_ms=elapsed)
            return SelectionDecision("OK", chosen, lead, opponents, elapsed_ms=elapsed)
        except SelectionBlocked as exc:
            return SelectionDecision("BLOCKED", opponent_six=opponents, reason=str(exc),
                                     elapsed_ms=round((self.clock()-started_at)*1000))
        except Exception as exc:
            return SelectionDecision("ERROR", opponent_six=opponents, reason=type(exc).__name__,
                                     elapsed_ms=round((self.clock()-started_at)*1000))
