"""Phase 2: pure, preloaded Champions LIVE-matrix three-plus-lead ranking.

This is an *ordinal model suggestion*, not a certified KO, safe switch,
real-world win probability or substitute for PREP. It never opens files,
fetches environment statistics or mutates the existing GPT/LIVE path.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any

from ..matrix_compatibility import matrix_matches_registered_team, configured_matrix_team
from ..preload import FrozenMatchAssets
from ..species_name_aliases import selection_base_name

_RATES = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
_SPEED = {"G": 2, "E": 1, "L": 0}
_UNSAFE = "U"


@dataclass(frozen=True)
class LocalSelection:
    status: str
    selected_three: tuple[str, ...] = ()
    lead: str = ""
    opponent_six: tuple[str, ...] = ()
    uncovered_processors: tuple[str, ...] = ()
    matrix_rows_six: tuple[str, ...] = ()
    team_sha256: str = ""
    reason: str = ""
    # Ordinal only: never interpret as a probability or exact damage.
    quality_ordinal: tuple[int, ...] = ()


@dataclass(frozen=True)
class _Matchup:
    quality: int
    speed: int
    recv: str
    explicit_processor: bool
    insurance_only: bool


def _own_matrix_names(assets: FrozenMatchAssets) -> tuple[str, ...]:
    """Tie matrix columns to a current six without accepting duplicate aliases."""
    registered = assets.own_team_names
    matrix = configured_matrix_team(assets)
    if (len(registered) != 6 or len(set(registered)) != 6 or
            len(matrix) != 6 or len(set(matrix)) != 6):
        raise ValueError("INVALID_OWN_SIX")
    by_name = {}
    for item in registered:
        normalized = selection_base_name(item.split("→")[-1].strip())
        if normalized in by_name:
            raise ValueError("AMBIGUOUS_OWN_FORM_ALIAS")
        by_name[normalized] = item
    if set(by_name) != set(matrix):
        raise ValueError("MATRIX_TEAM_MISMATCH")
    return tuple(by_name[name] for name in matrix)


def rank_local_selection(assets: FrozenMatchAssets,
                         opponent_six: tuple[str, ...]) -> LocalSelection:
    """Rank 20 combinations × three possible leads from one matrix batch.

    The processor column is an explicit *finish/answer hint*, not a safe
    switch. Unknown, insurance-only, or unreviewed cells do not become
    processors. No stat or damage rules from older games are inferred.
    """
    if not isinstance(assets, FrozenMatchAssets):
        return LocalSelection("BLOCKED", reason="FROZEN_PRELOAD_REQUIRED")
    if (len(opponent_six) != 6 or len(set(opponent_six)) != 6 or
            not all(isinstance(name, str) and bool(name) for name in opponent_six)):
        return LocalSelection("BLOCKED", reason="OPPONENT_SIX_UNVERIFIED")
    if not matrix_matches_registered_team(assets):
        return LocalSelection("BLOCKED", reason="MATRIX_NOT_REGISTERED_OR_UNREVIEWED")
    try:
        own = _own_matrix_names(assets)
        # Fetch exactly six *frozen* matrix rows in a single call. No Web,
        # lookup master, fresh file read or independent 18-pair searches.
        rows = assets.matrix_rows_for(opponent_six)
        # Build once and reuse across all 60 possible choices.
        matchups: dict[str, dict[str, _Matchup]] = {m: {} for m in own}
        for opponent, raw in zip(opponent_six, rows):
            columns = raw.split("|")
            if len(columns) != 14 or columns[0] != opponent:
                raise ValueError("MATRIX_ROW_SCHEMA")
            explicit = {
                selection_base_name(token.strip())
                for token in columns[9].split("/")
                if token.strip() and token.strip() not in ("?", "-")
            }
            for slot, member in enumerate(own):
                cell = columns[slot + 3].split(",")
                if len(cell) != 6:
                    raise ValueError("MATRIX_CELL_SCHEMA")
                rate, speed, move, multiplier, recv, insurance = cell
                if (rate not in _RATES or speed not in _SPEED
                        or not move or recv not in ("I", "R", "N", "U")
                        or insurance not in ("0", "1")):
                    raise ValueError("MATRIX_CELL_UNASSESSED")
                normalized = selection_base_name(member.split("→")[-1].strip())
                matchups[member][opponent] = _Matchup(
                    _RATES[rate], _SPEED[speed], recv,
                    normalized in explicit and insurance == "0",
                    insurance == "1")
        best_score: tuple[int, ...] | None = None
        best: tuple[str, str, str] = ()
        lead = ""
        for selected in combinations(own, 3):
            covered = tuple(op for op in opponent_six
                            if any(matchups[member][op].explicit_processor
                                   for member in selected))
            # A role's explicit processing value is distinct from the ability
            # to withstand a hit when switching into it.
            total_quality = sum(matchups[m][op].quality
                                for op in opponent_six for m in selected)
            total_insurance = sum(matchups[m][op].insurance_only
                                  for op in opponent_six for m in selected)
            for head in selected:
                lead_quality = [matchups[head][op].quality for op in opponent_six]
                lead_speed = [matchups[head][op].speed for op in opponent_six]
                # Be wary of first-turn hard-switch risks; this is a
                # conservative lead-ranking proxy, not a safe-switch proof.
                lead_unsafe = sum(matchups[head][op].recv == _UNSAFE
                                  for op in opponent_six)
                ordinal = (
                    len(covered), min(lead_quality), sum(lead_quality),
                    -lead_unsafe, sum(lead_speed), total_quality,
                    -total_insurance,
                )
                if best_score is None or ordinal > best_score:
                    best_score, best, lead = ordinal, selected, head
        if best_score is None:
            raise ValueError("NO_SELECTION")
        uncovered = tuple(op for op in opponent_six if not any(
            matchups[member][op].explicit_processor for member in best))
        return LocalSelection(
            "MODEL_PROVISIONAL", best, lead, opponent_six, uncovered,
            rows, assets.sha256["Pokemon_Champions_MC_team_2026-10-03.csv"],
            "ORDINAL_MATRIX_RANKING_NOT_SAFE_SWITCH_OR_WIN_RATE", best_score)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        reason = str(exc)
        return LocalSelection("BLOCKED", reason=reason[:140])
