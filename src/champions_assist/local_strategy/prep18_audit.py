"""Issue #141④: frozen matrix-first 18-matchup PREP assessment (offline).

Never manufactures a move, KO, opponent set, safe switch or damage number.
The matrix is a six-row CONDITIONAL prior, not eighteen measured outcomes.
Independent evaluations may be checked using the existing PREP validator;
structural completeness must not be confused with verified tactical accuracy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..matrix_compatibility import matrix_matches_registered_team
from ..preload import FrozenMatchAssets
from ..prep import (
    PrepResult, build_prep_input, validate_prep_result, _parse_eval_code,
)
from ..species_name_aliases import selection_base_name
from .selector import _own_matrix_names

_RATES = frozenset("ABCDF")
_SPEED = frozenset("GEL")
_RECV = frozenset("IRNU")


@dataclass(frozen=True)
class Prep18Cell:
    own: str
    opponent: str
    matrix_rate: str
    matrix_speed: str
    attack_hint: str
    received_damage_hint: str
    explicit_processor: bool
    insurance_only: bool
    safe_hard_switch: str = "UNVERIFIED"
    immediate_ko: str = "UNKNOWN"
    opponent_max_hit: str = "UNKNOWN"
    # Kept separate from all conditional matrix hints.
    reviewed_eval_code: str = ""


@dataclass(frozen=True)
class Prep18Audit:
    status: str
    reason: str
    lead: str = ""
    opponents: tuple[str, ...] = ()
    lead_six: tuple[Prep18Cell, ...] = ()
    bench_twelve: tuple[Prep18Cell, ...] = ()
    preserve: str = ""
    stopper: str = ""
    closer: str = ""
    # Structural validation of external evaluated tags is NOT game accuracy.
    assessed_pairs: int = 0
    safe_switches_certified: int = 0
    damage_claims_certified: int = 0
    tactics_verified: bool = False
    ranking_status: str = "NO_RANKING"


def _no(reason: str) -> Prep18Audit:
    return Prep18Audit("NO_PREP18", reason)


def stage_prep18(
    assets: FrozenMatchAssets,
    selected_three: tuple[str, str, str],
    lead: str,
    opponents: tuple[str, str, str, str, str, str],
    *,
    reviewed_assessment: PrepResult | None = None,
    cached_matrix_rows_six: tuple[str, ...] | None = None,
) -> Prep18Audit:
    """Extract six rows exactly once; evaluate lead6 then bench12 in order.

    A reviewer may pass a *complete* independent 18-pair evaluation with
    concrete registered actions, mandatory assessed fields and three roles.
    Until it is supplied, the matrix-only result is a structural/conditional
    PREP and MUST NOT be reported as tactically complete.
    """
    if not isinstance(assets, FrozenMatchAssets):
        return _no("FROZEN_ASSETS_REQUIRED")
    if (not isinstance(selected_three, tuple)
            or len(selected_three) != 3
            or len(set(selected_three)) != 3
            or lead not in selected_three
            or not isinstance(opponents, tuple)
            or len(opponents) != 6
            or len(set(opponents)) != 6
            or any(not isinstance(n, str) or not n or n.startswith("未判明相手")
                   for n in opponents)):
        return _no("EXACT_THREE_AND_SIX_REQUIRED")
    if not matrix_matches_registered_team(assets):
        return _no("CURRENT_TEAM_MATRIX_NOT_COMPATIBLE")
    try:
        own = _own_matrix_names(assets)
        if not set(selected_three).issubset(own):
            return _no("SELECTED_TEAM_NOT_REGISTERED")
        if cached_matrix_rows_six is not None:
            # Exact source-bound reuse from the preceding selector call.
            # A supplied six-row cache must match the frozen matrix bytes;
            # never trust a caller's edited matchup or arbitrary alias.
            if (not isinstance(cached_matrix_rows_six, tuple)
                    or len(cached_matrix_rows_six) != 6
                    or any(type(row) is not str or
                           row != assets.matrix_index.get(opponent)
                           for opponent, row in zip(
                               opponents, cached_matrix_rows_six))):
                return _no("CACHED_MATRIX_SIX_NOT_SOURCE_BOUND")
            raw_rows = cached_matrix_rows_six
        else:
            raw_rows = assets.matrix_rows_for(opponents)
        slots = {name: idx for idx, name in enumerate(own)}
        ordered = (lead, *(name for name in selected_three if name != lead))
        cells: list[Prep18Cell] = []
        for member in ordered:
            for opponent, row in zip(opponents, raw_rows):
                parts = row.split("|")
                if len(parts) != 14 or parts[0] != opponent:
                    return _no("MATRIX_ROW_CHANGED_OR_INVALID")
                tokens = parts[slots[member] + 3].split(",")
                if (len(tokens) != 6 or tokens[0] not in _RATES
                        or tokens[1] not in _SPEED
                        or tokens[4] not in _RECV
                        or tokens[5] not in ("0", "1")
                        or not tokens[2]):
                    return _no("MATRIX_CELL_INVALID")
                declared = {
                    selection_base_name(s.strip())
                    for s in parts[9].split("/") if s.strip() not in ("", "-", "?")
                }
                name = selection_base_name(member.split("→")[-1].strip())
                cells.append(Prep18Cell(
                    member, opponent, tokens[0], tokens[1], tokens[2],
                    tokens[4], name in declared and tokens[5] == "0",
                    tokens[5] == "1"))
        if len(cells) != 18:
            return _no("PREP18_GRID_INCOMPLETE")
        first, bench = tuple(cells[:6]), tuple(cells[6:])
        if reviewed_assessment is None:
            return Prep18Audit(
                "MATRIX_18_CONDITIONAL_ONLY", "TACTICAL_FIELDS_NOT_ASSESSED",
                lead, opponents, first, bench)
        if not isinstance(reviewed_assessment, PrepResult):
            return _no("INVALID_EVALUATION_INPUT")
        registered = {
            m["name"]: tuple(
                m.get(f"move{i}", "").strip() for i in range(1, 5)
                if m.get(f"move{i}", "").strip())
            for m in assets.own_team if m["name"] in selected_three
        }
        raw_input = build_prep_input(
            selected_three, lead, opponents, raw_rows,
            registered_moves=registered)
        checked = validate_prep_result(raw_input, reviewed_assessment)
        if not checked.complete:
            return _no("EVALUATION_" + checked.reason)
        # Bans strong outcome claims that were not proved by a witnessed
        # game/engine state. Matching a matrix processor is NOT an automatic
        # safe switch or a guaranteed immediate KO.
        codes = {
            (entry.our_pokemon, entry.opponent_pokemon):
            _parse_eval_code(entry.eval_code)
            for entry in reviewed_assessment.matchups_18
        }
        for c in cells:
            values = codes[(c.own, c.opponent)]
            if values is None:
                return _no("EVALUATION_CODE_INVALID")
            if values["SAFE"].strip().casefold() in {
                "safe", "yes", "guaranteed", "confirmed", "安全", "確定"
            }:
                return _no("UNPROVEN_SAFE_SWITCH_CLAIM")
            if c.own == lead and values["KO"].strip().casefold() in {
                "confirmed", "guaranteed", "確1", "確定", "yes"
            }:
                return _no("UNPROVEN_KO_CLAIM")
            if values["PROC"].strip().casefold() in {"confirmed", "確定"} and not c.explicit_processor:
                return _no("UNSUPPORTED_PROCESSOR_PROMOTION")
        enriched = tuple(Prep18Cell(
            c.own, c.opponent, c.matrix_rate, c.matrix_speed,
            c.attack_hint, c.received_damage_hint, c.explicit_processor,
            c.insurance_only, reviewed_eval_code=
            next(r.eval_code for r in reviewed_assessment.matchups_18
                 if (r.our_pokemon, r.opponent_pokemon) == (c.own, c.opponent))
        ) for c in cells)
        return Prep18Audit(
            "ASSESSED_18_CONDITIONAL_ONLY",
            "STRUCTURAL_18_AND_ROLES_PASS_BUT_UNVERIFIED_DAMAGE_AND_SWITCHES",
            lead, opponents, enriched[:6], enriched[6:],
            reviewed_assessment.preserve or "",
            reviewed_assessment.stopper or "",
            reviewed_assessment.closer or "",
            assessed_pairs=18)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        return _no("MALFORMED_FROZEN_MATRIX_OR_ASSESSMENT")
