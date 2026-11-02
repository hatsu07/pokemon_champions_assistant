"""Read-only preflight for observed Champions opponent facts vs joint sets.

This check consumes ONLY already-frozen `battle_evidence`, which was produced
from visible game events. It does not guess an opponent set, generate a replay,
lookup another game's mechanics, or turn an inferred set into an observed fact.
Every explicit joint hypothesis must account for the same known evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .models import FrozenBattleState, joint_hypothesis_ids

# Retain the vocabulary of battle_evidence.py; these are literal strings
# recorded on the game screen, not Showdown identifiers.
_SCALARS = (
    ("item_by_pokemon", "item"),
    ("ability_by_pokemon", "ability"),
    ("form_by_pokemon", "form"),
    ("current_type", "current_type"),
    ("status_by_pokemon", "status"),
)
_UNKNOWN = {"", "UNKNOWN", "NONE", "?"}


@dataclass(frozen=True)
class OpponentEvidenceGate:
    status: str
    reason: str
    checked_hypotheses: tuple[str, ...] = ()
    checked_opponents: tuple[str, ...] = ()
    # A contradiction is never silently removed from the frozen snapshot.
    offending_hypothesis_id: str = ""
    offending_opponent: str = ""
    offending_field: str = ""


def _failure(reason: str, *, hypothesis: str = "", pokemon: str = "",
             field: str = "") -> OpponentEvidenceGate:
    return OpponentEvidenceGate(
        "NO_RANKING", reason, (), (), hypothesis, pokemon, field)


def _known_string(value: Any) -> str | None:
    if not isinstance(value, str):
        raise ValueError("INVALID_OBSERVED_OPPONENT_EVIDENCE")
    if not value.strip() or value.strip().upper() in _UNKNOWN:
        return None
    return value


def check_observed_opponent_hypotheses(
    snapshot: FrozenBattleState,
) -> OpponentEvidenceGate:
    """Only accept joint assumptions consistent with *all* revealed facts.

    Supported joint-assumption schema:
      {"opponent_sets": {
        "<exact game-visible Pokémon name>": {
          "moves": ["<exact revealed move name>", ...],
          "item": "<exact revealed held item>",
          "ability": "<exact revealed ability>",
          "form": "<exact revealed form>",
          "current_type": "<exact revealed current type>",
          "status": "<exact revealed status>"
        }
      }}

    A missing field is UNKNOWN, not equality. When a game-visible value
    exists and the assumption lacks it, fail closed as UNBOUND. Differences
    fail closed as CONTRADICTORY (the result is not silently filtered to
    the subset that would make the simulation favorable). We deliberately
    do not infer older-game item/move mechanics or move-set probabilities.
    """
    if not isinstance(snapshot, FrozenBattleState):
        return _failure("FROZEN_SNAPSHOT_REQUIRED")
    try:
        hypothesis_ids = joint_hypothesis_ids(snapshot.hypotheses)
    except ValueError:
        return _failure("DUPLICATE_OR_INVALID_HYPOTHESIS_ID")
    try:
        state = snapshot.state
        if not isinstance(state, dict):
            raise ValueError("invalid state")
        # Missing evidence is distinct from explicitly stored corrupt data.
        # Using `or {}` here would turn [] / "" / 0 / False / None into a
        # plausible empty memory and could promote an unevaluated opponent set.
        memory = (state["battle_evidence"]
                  if "battle_evidence" in state else {})
        if not isinstance(memory, dict):
            raise ValueError("invalid battle evidence")
        known: dict[str, dict[str, Any]] = {}
        active = _known_string(state.get("opponent_active", ""))
        if active:
            known[active] = {}
        for section, field in (
            ("moves_by_pokemon", "moves"),
            *_SCALARS,
        ):
            value = memory.get(section, {})
            if not isinstance(value, dict):
                raise ValueError("invalid observed section")
            opponent = value.get("opponent", {})
            if not isinstance(opponent, dict):
                raise ValueError("invalid opponent section")
            for name, fact in opponent.items():
                if not isinstance(name, str) or not name:
                    raise ValueError("invalid observed name")
                if field == "moves":
                    if not isinstance(fact, list) or not all(
                        isinstance(x, str) and x and x.strip() == x for x in fact
                    ):
                        raise ValueError("invalid observed move list")
                    observed = tuple(
                        move for move in fact if move.strip().upper() not in _UNKNOWN
                    )
                    if observed:
                        known.setdefault(name, {})[field] = observed
                else:
                    observed = _known_string(fact)
                    if observed:
                        known.setdefault(name, {})[field] = observed
    except (KeyError, TypeError, ValueError, AttributeError):
        return _failure("INVALID_OBSERVED_OPPONENT_EVIDENCE")

    if not known:
        # No opponent detail is shown, so this preflight cannot claim that
        # the unobserved set is true. The other runner gates still apply.
        return OpponentEvidenceGate("CLEAR", "NO_REVEALED_OPPONENT_DETAILS",
                                    hypothesis_ids)

    if not snapshot.hypotheses:
        return _failure("OBSERVED_OPPONENT_WITHOUT_JOINT_HYPOTHESES")

    for hypothesis in snapshot.hypotheses:
        identifier = hypothesis.hypothesis_id
        try:
            assumptions = json.loads(hypothesis.assumptions_json)
        except (ValueError, TypeError):
            return _failure("JOINT_ASSUMPTIONS_INVALID", hypothesis=identifier)
        if not isinstance(assumptions, dict):
            return _failure("JOINT_ASSUMPTIONS_INVALID", hypothesis=identifier)
        opponent_sets = assumptions.get("opponent_sets")
        if not isinstance(opponent_sets, dict):
            return _failure("JOINT_ASSUMPTIONS_UNBOUND", hypothesis=identifier)
        for name, observed in known.items():
            member = opponent_sets.get(name)
            if not isinstance(member, dict):
                return _failure("JOINT_ASSUMPTIONS_UNBOUND",
                                hypothesis=identifier, pokemon=name)
            for field, value in observed.items():
                hypothetical = member.get(field)
                if field == "moves":
                    if (not isinstance(hypothetical, list)
                            or not all(isinstance(x, str) and x
                                       for x in hypothetical)):
                        return _failure("JOINT_ASSUMPTIONS_UNBOUND",
                                        hypothesis=identifier, pokemon=name,
                                        field=field)
                    if any(move not in hypothetical for move in value):
                        return _failure("JOINT_ASSUMPTIONS_CONTRADICT_OBSERVATION",
                                        hypothesis=identifier, pokemon=name,
                                        field=field)
                else:
                    if not isinstance(hypothetical, str) or not hypothetical:
                        return _failure("JOINT_ASSUMPTIONS_UNBOUND",
                                        hypothesis=identifier, pokemon=name,
                                        field=field)
                    if hypothetical != value:
                        return _failure("JOINT_ASSUMPTIONS_CONTRADICT_OBSERVATION",
                                        hypothesis=identifier, pokemon=name,
                                        field=field)
    return OpponentEvidenceGate(
        "CLEAR", "ALL_JOINT_SETS_CONSISTENT_WITH_REVEALED_EVIDENCE",
        hypothesis_ids, tuple(known),
    )
