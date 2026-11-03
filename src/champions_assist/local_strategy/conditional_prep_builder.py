"""Deterministic 18-pair PREP *proposal* from frozen Champions sources.

This implements an executable, preloaded baseline for every selected matchup,
not an invented damage calculator.  It never certifies a KO, opponent maximum
damage, safe switch, Mega parity or Champions legal action.  It deliberately
produces DRAFT rather than the externally reviewed COMPLETE PrepResult used by
stage_prep18 and by Phase 2 acceptance.  Real-game adjudication is #203.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..preload import FrozenMatchAssets, REQUIRED_MASTER_FILES
from ..prep import PrepMatchup, PrepResult
from .prep18_audit import stage_prep18


@dataclass(frozen=True)
class ConditionalPrepDraft:
    status: str
    reason: str
    result: PrepResult | None = None
    proposed_pairs: int = 0
    registered_move_checks: int = 0
    tactical_accuracy_verified: bool = False
    ranking_status: str = "NO_RANKING"


def _abbreviations(assets: FrozenMatchAssets) -> dict[str, str]:
    """Read only the literal matrix's published abbreviation header.

    A malformed or conflicting mapping is not repaired from SV or another
    game. The caller can still recognize an unabbreviated registered move.
    """
    text = assets.contents[REQUIRED_MASTER_FILES[1]]
    prefix = "# MOVE_ABBR={"
    headers = [line.strip() for line in text.splitlines()
               if line.strip().startswith(prefix)]
    if len(headers) != 1 or not headers[0].endswith("}"):
        return {}
    pairs = headers[0][len(prefix):-1].split(",")
    values: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition(":")
        if not sep or not key.strip() or not value.strip() or key in values:
            return {}
        values[key.strip()] = value.strip()
    return values


def _roles(selected: tuple[str, str, str], own: dict[str, dict[str, str]]
           ) -> tuple[str, str, str]:
    """Recommend roles from *own registered* labels, never opponent guesses."""
    def pick(tokens: tuple[str, ...], fallback: str) -> str:
        return next((name for name in selected if any(
            token in own[name].get("role", "").upper()
            for token in tokens)), fallback)
    closer = pick(("ENDGAME", "CLEANER"), selected[-1])
    stopper = pick(("STOPPER", "INSURANCE"), selected[1])
    preserve = pick(("PRIMARY_MEGA", "ENDGAME"), closer)
    return preserve, stopper, closer


def build_conditional_prep_draft(
    assets: FrozenMatchAssets,
    selected_three: tuple[str, str, str],
    lead: str,
    opponents: tuple[str, str, str, str, str, str],
    *, cached_matrix_rows_six: tuple[str, ...] | None = None,
) -> ConditionalPrepDraft:
    """Construct 18 *conditional* lines using one matrix-six fetch.

    Each 'OUR' and lead 'ACT' is a real move in the registered team, selected
    from the matrix hint. It is NOT necessarily the maximum-damage move in a
    real battle. Unknown opposing moves, PP, HP, weather, form and safety
    remain unverified; only the user/game evidence can upgrade those fields.
    """
    conditional = stage_prep18(
        assets, selected_three, lead, opponents,
        cached_matrix_rows_six=cached_matrix_rows_six)
    if conditional.status != "MATRIX_18_CONDITIONAL_ONLY":
        return ConditionalPrepDraft("BLOCKED", conditional.reason)
    own = {m["name"]: m for m in assets.own_team}
    if not all(m in own for m in selected_three):
        return ConditionalPrepDraft("BLOCKED", "OWN_SELECTED_NOT_REGISTERED")
    moves = {m: tuple(own[m].get(f"move{i}", "").strip()
                      for i in range(1, 5))
             for m in selected_three}
    aliases = _abbreviations(assets)
    rows: list[PrepMatchup] = []
    count = 0
    for cell in conditional.lead_six + conditional.bench_twelve:
        # Matrix hint may be an abbreviation or full name, never an
        # independent statement that a move is legal after a form change.
        candidate = aliases.get(cell.attack_hint, cell.attack_hint)
        if candidate not in moves[cell.own] or not candidate:
            return ConditionalPrepDraft(
                "BLOCKED", "MATRIX_MOVE_NOT_REGISTERED:" +
                cell.own + ":" + cell.opponent)
        count += 1
        speed = {"G": "matrix_faster_unverified",
                 "E": "matrix_equal_unverified",
                 "L": "matrix_slower_unverified"}[cell.matrix_speed]
        safety = ("unsafe_matrix" if cell.received_damage_hint == "U"
                  else "switch_unverified")
        processor = ("matrix_explicit_processor"
                     if cell.explicit_processor else
                     "matrix_not_explicit_processor")
        priority = "opponent_priority_unverified"
        if cell.own == lead:
            parts = (
                "ACT=move:" + candidate,
                "KO=evidence_unknown",
                "DPS=" + candidate,
                "PRI=" + priority,
                "S=" + speed,
                "OUR=" + candidate,
                "OPP=evidence_unknown",
                "SAFE=" + safety,
                "PROC=" + processor,
                "LOCK=" + ("choice_lock_check" if own[cell.own].get(
                    "choice_lock", "").upper() == "Y" else "no_own_choice_item_recorded"),
                "MEGA=" + ("form_recheck_unverified" if own[cell.own].get(
                    "form_change", "") not in ("", "-", "N") else "no_form_declared"),
            )
        else:
            parts = ("S=" + speed, "OUR=" + candidate,
                     "OPP=evidence_unknown", "PRI=" + priority,
                     "SAFE=" + safety, "PROC=" + processor)
        rows.append(PrepMatchup(cell.own, cell.opponent, "|".join(parts)))
    if len(rows) != 18 or count != 18:
        return ConditionalPrepDraft("BLOCKED", "PARTIAL_CONDITIONAL_GRID")
    preserve, stopper, closer = _roles(selected_three, own)
    # A DRAFT must not masquerade as independently reviewed COMPLETE PREP.
    result = PrepResult("DRAFT", tuple(rows), preserve, stopper, closer,
                        unfinished_reason="CHAMPIONS_DAMAGE_AND_LANDING_NOT_VERIFIED")
    return ConditionalPrepDraft(
        "CONDITIONAL_18_DRAFT", "REGISTERED_ACTIONS_NO_VERIFIED_KO_OR_SAFE_SWITCH",
        result, len(rows), count)
