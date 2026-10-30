"""PREP input construction and completion validation, without tactical scoring."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Iterable


LEAD_TAGS = ("ACT", "KO", "DPS", "PRI", "S", "OUR", "OPP", "SAFE", "PROC", "LOCK", "MEGA")
BENCH_TAGS = ("S", "OUR", "OPP", "PRI", "SAFE", "PROC")
# A placeholder means the field was not assessed at all. "unknown" means
# the evaluator considered it but lacks reliable battle evidence; it is not
# permission to leave every tactical field unassessed.
NOT_ASSESSED_EVALUATION = frozenset({
    "?", "未判定", "未評価", "SKIPPED", "not_assessed", "NOT_ASSESSED",
    "not_evaluated", "NOT_EVALUATED",
})
EVIDENCE_LIMITED_EVALUATION = frozenset({
    "unknown", "unverified", "uncertain", "不明", "evidence_unknown",
})
UNKNOWN_EVALUATION = NOT_ASSESSED_EVALUATION | EVIDENCE_LIMITED_EVALUATION


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


def _validate_assessed_fields(parsed: dict[str, str], *, lead: bool) -> str | None:
    """Unknown evidence is permitted; unassessed mandatory fields are not.

    Requiring at least one independently assessed tactical dimension for
    every bench row prevents PROC=unverified alone from certifying a matchup.
    The lead also needs evidence beyond simply naming an ACT and DPS.
    """
    required = LEAD_TAGS if lead else BENCH_TAGS
    if any(tag not in parsed for tag in required):
        return "LEAD_SIX_INCOMPLETE" if lead else "BENCH_TWELVE_INCOMPLETE"
    if any(parsed[tag].strip() in NOT_ASSESSED_EVALUATION for tag in required):
        return "REQUIRED_FIELD_NOT_ASSESSED"
    if parsed["PROC"].strip() in UNKNOWN_EVALUATION:
        return "PROCESSOR_NOT_ASSESSED"
    if lead and any(parsed[tag].strip() in UNKNOWN_EVALUATION for tag in ("ACT", "DPS")):
        return "LEAD_ACTION_NOT_ASSESSED"
    tactical = (
        ("KO", "PRI", "S", "OUR", "OPP", "SAFE", "LOCK", "MEGA")
        if lead else ("S", "OUR", "OPP", "PRI", "SAFE")
    )
    if not any(parsed[tag].strip() not in EVIDENCE_LIMITED_EVALUATION
               for tag in tactical):
        return "MATCHUP_NOT_ASSESSED"
    return None


def prep_evidence_status(rows: tuple[PrepMatchup, ...]) -> str:
    """Separate legitimate lack of evidence from absent assessment."""
    for row in rows:
        fields = _parse_eval_code(row.eval_code)
        if fields and any(value.strip() in EVIDENCE_LIMITED_EVALUATION
                          for value in fields.values()):
            return "ASSESSED_WITH_UNCERTAINTY"
    return "ASSESSED_CONDITIONAL"


def validate_prep_result(prep_input: PrepInput, result: PrepResult) -> PrepValidation:
    """Check the wire's completion claim; never manufacture a missing evaluation."""
    if result.prep_status != "COMPLETE":
        return PrepValidation(False, result.unfinished_reason or "PREP_NOT_COMPLETE")
    if not all(role in prep_input.selected_three for role in
               (result.preserve, result.stopper, result.closer)):
        return PrepValidation(False, "WIN_CONDITIONS_INVALID")
    pairs = {(item.our_pokemon, item.opponent_pokemon) for item in result.matchups_18}
    if len(result.matchups_18) != 18 or pairs != prep_input.expected_pairs:
        return PrepValidation(False, "MATCHUPS_18_INCOMPLETE")
    for item in result.matchups_18:
        parsed = _parse_eval_code(item.eval_code)
        if parsed is None:
            return PrepValidation(False, "EVAL_CODE_INVALID")
        failure = _validate_assessed_fields(
            parsed, lead=item.our_pokemon == prep_input.lead)
        if failure:
            return PrepValidation(False, failure)
    return PrepValidation(True, "OK")



def validate_partial_prep_result(
    selected_three: tuple[str, ...], lead: str,
    known_opponents: tuple[str, ...], unknown_slots: tuple[str, ...],
    result: PrepResult,
) -> PrepValidation:
    """Validate only observed opponents; never certify the missing 3/6 pairs.

    Each unknown opponent remains an unresolved placeholder, NOT a member of
    a fictitious matrix row. This can be useful tactical preparation without
    being equivalent to complete 18-pair PREP.
    """
    if (len(selected_three) != 3 or len(set(selected_three)) != 3
            or lead not in selected_three
            or len(known_opponents) not in (4, 5)
            or len(set(known_opponents)) != len(known_opponents)
            or len(unknown_slots) != 6-len(known_opponents)
            or len(set(unknown_slots)) != len(unknown_slots)
            or any(not slot.startswith("未判明相手") for slot in unknown_slots)):
        return PrepValidation(False, "PARTIAL_INPUT_INVALID")
    if result.prep_status != "PARTIAL":
        return PrepValidation(False, "PARTIAL_STATUS_INVALID")
    if (not all(role in selected_three
                for role in (result.preserve, result.stopper, result.closer))):
        return PrepValidation(False, "WIN_CONDITIONS_INVALID")
    expected = set(product(selected_three, known_opponents))
    pairs = [(row.our_pokemon, row.opponent_pokemon)
             for row in result.matchups_18]
    if len(pairs) != len(expected) or set(pairs) != expected:
        return PrepValidation(False, "KNOWN_MATCHUPS_INCOMPLETE")
    for row in result.matchups_18:
        parsed = _parse_eval_code(row.eval_code)
        if parsed is None:
            return PrepValidation(False, "EVAL_CODE_INVALID")
        failure = _validate_assessed_fields(
            parsed, lead=row.our_pokemon == lead)
        if failure:
            return PrepValidation(False, failure)
    return PrepValidation(True, "PARTIAL_KNOWN_ONLY")



def mocked_complete_prep(prep_input: PrepInput) -> PrepResult:
    """A labelled simulation fixture, not a battle evaluation or tactical heuristic."""
    lead_code = ("ACT=SIMULATED|KO=unknown|DPS=SIMULATED|PRI=SIMULATED|"
                 "S=unknown|OUR=SIMULATED|OPP=unknown|SAFE=unknown|"
                 "PROC=SIMULATED|LOCK=unknown|MEGA=unknown")
    bench_code = ("S=SIMULATED|OUR=SIMULATED|OPP=unknown|PRI=unknown|"
                  "SAFE=unknown|PROC=SIMULATED")
    rows = tuple(
        PrepMatchup(own, opponent, lead_code if own == prep_input.lead else bench_code)
        for own, opponent in product(prep_input.selected_three, prep_input.opponent_preview_six)
    )
    return PrepResult("COMPLETE", rows, *prep_input.selected_three)
