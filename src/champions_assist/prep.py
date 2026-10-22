"""PREP input construction and completion validation, without tactical scoring."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Iterable


LEAD_TAGS = ("ACT", "KO", "DPS", "PRI", "S", "OUR", "OPP", "SAFE", "PROC", "LOCK", "MEGA")
BENCH_TAGS = ("S", "OUR", "OPP", "PRI", "SAFE", "PROC")
UNKNOWN_EVALUATION = frozenset({"?", "未判定", "SKIPPED"})


@dataclass(frozen=True)
class PrepInput:
    selected_three: tuple[str, str, str]
    lead: str
    opponent_preview_six: tuple[str, str, str, str, str, str]
    matrix_rows_six: tuple[str, str, str, str, str, str]

    @property
    def expected_pairs(self) -> frozenset[tuple[str, str]]:
        return frozenset(product(self.selected_three, self.opponent_preview_six))


@dataclass(frozen=True)
class PrepMatchup:
    our_pokemon: str
    opponent_pokemon: str
    eval_code: str


@dataclass(frozen=True)
class PrepResult:
    prep_status: str
    matchups_18: tuple[PrepMatchup, ...]
    preserve: str | None
    stopper: str | None
    closer: str | None
    unfinished_reason: str | None = None


@dataclass(frozen=True)
class PrepValidation:
    complete: bool
    reason: str


def build_prep_input(
    selected_three: tuple[str, str, str],
    lead: str,
    opponent_preview_six: tuple[str, str, str, str, str, str],
    matrix_rows_six: tuple[str, str, str, str, str, str],
) -> PrepInput:
    if len(set(selected_three)) != 3 or lead not in selected_three:
        raise ValueError("PREP requires three distinct selected Pokémon and a selected lead")
    if len(opponent_preview_six) != 6 or len(matrix_rows_six) != 6:
        raise ValueError("PREP requires exactly six cached opponent matrix rows")
    return PrepInput(selected_three, lead, opponent_preview_six, matrix_rows_six)


def _parse_eval_code(code: str) -> dict[str, str] | None:
    values: dict[str, str] = {}
    for part in code.split("|"):
        key, separator, value = part.partition("=")
        if not separator or not key or not value or key in values:
            return None
        values[key] = value
    return values


def validate_prep_result(prep_input: PrepInput, result: PrepResult) -> PrepValidation:
    """Check the wire's completion claim; never manufacture a missing evaluation."""
    if result.prep_status != "COMPLETE":
        return PrepValidation(False, result.unfinished_reason or "PREP_NOT_COMPLETE")
    if not result.preserve or not result.stopper or not result.closer:
        return PrepValidation(False, "WIN_CONDITIONS_MISSING")
    pairs = {(item.our_pokemon, item.opponent_pokemon) for item in result.matchups_18}
    if len(result.matchups_18) != 18 or pairs != prep_input.expected_pairs:
        return PrepValidation(False, "MATCHUPS_18_INCOMPLETE")
    for item in result.matchups_18:
        parsed = _parse_eval_code(item.eval_code)
        if parsed is None:
            return PrepValidation(False, "EVAL_CODE_INVALID")
        required = LEAD_TAGS if item.our_pokemon == prep_input.lead else BENCH_TAGS
        if any(tag not in parsed for tag in required):
            return PrepValidation(False, "LEAD_SIX_INCOMPLETE" if item.our_pokemon == prep_input.lead else "BENCH_TWELVE_INCOMPLETE")
        if parsed["PROC"] in UNKNOWN_EVALUATION:
            return PrepValidation(False, "PROCESSOR_NOT_ASSESSED")
        if item.our_pokemon == prep_input.lead and any(parsed[tag] in UNKNOWN_EVALUATION for tag in ("ACT", "DPS")):
            return PrepValidation(False, "LEAD_ACTION_NOT_ASSESSED")
    return PrepValidation(True, "OK")


def mocked_complete_prep(prep_input: PrepInput) -> PrepResult:
    """A labelled simulation fixture, not a battle evaluation or tactical heuristic."""
    lead_code = "ACT=SIMULATED|KO=?|DPS=SIMULATED|PRI=?|S=?|OUR=?|OPP=?|SAFE=?|PROC=SIMULATED|LOCK=?|MEGA=?"
    bench_code = "S=?|OUR=?|OPP=?|PRI=?|SAFE=?|PROC=SIMULATED"
    rows = tuple(
        PrepMatchup(own, opponent, lead_code if own == prep_input.lead else bench_code)
        for own, opponent in product(prep_input.selected_three, prep_input.opponent_preview_six)
    )
    return PrepResult("COMPLETE", rows, "SIMULATED_PRESERVE", "SIMULATED_STOPPER", "SIMULATED_CLOSER")
