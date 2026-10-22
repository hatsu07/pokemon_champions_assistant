"""Canonical v2.3 wire validation and non-tactical semantic safety checks.

The JSON files under ``contracts/`` are the local source of truth.  This module
does not construct an alternative action or rank any legal action; it only
decides whether a model response may be shown for the frozen request.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .prep import PrepMatchup, PrepResult, validate_prep_result
from .preload import REQUIRED_MASTER_FILES
from .wire import PROTOCOL_VERSION, allowed_action_digest


CONTRACT_DIRECTORY = Path(__file__).resolve().parents[2] / "contracts"
_BATTLE_PHASES = frozenset({"NORMAL_COMMAND", "PIVOT_TARGET", "FAINT_REPLACEMENT", "EFFECT_REPLACEMENT"})


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    reason: str


def response_kind_for_phase(phase: str) -> str:
    if phase in _BATTLE_PHASES:
        return "battle"
    if phase in {"SELECT", "PREP", "REVIEW"}:
        return phase.lower()
    raise ValueError(f"unknown v2.3 phase: {phase}")


@lru_cache(maxsize=None)
def canonical_schema(kind: str) -> dict[str, Any]:
    filename = (
        "Pokemon_Champions_API_request_schema_v2_3.json"
        if kind == "request"
        else f"Pokemon_Champions_API_response_{kind.lower()}_schema_v2_3.json"
    )
    return json.loads((CONTRACT_DIRECTORY / filename).read_text(encoding="utf-8"))


def validate_against_canonical_schema(instance: dict[str, Any], kind: str) -> ValidationResult:
    try:
        schema = canonical_schema(kind)
        Draft202012Validator.check_schema(schema)
        errors = sorted(Draft202012Validator(schema).iter_errors(instance), key=lambda error: list(error.path))
    except Exception as exc:  # A malformed local contract must fail closed.
        return ValidationResult(False, f"SCHEMA_VALIDATOR_ERROR:{type(exc).__name__}")
    if errors:
        return ValidationResult(False, "SCHEMA_INVALID")
    return ValidationResult(True, "OK")


def validate_wire_request(payload: dict[str, Any]) -> ValidationResult:
    """Validate an immutable request before it reaches any transport."""
    schema = validate_against_canonical_schema(payload, "request")
    if not schema.accepted:
        return ValidationResult(False, "REQUEST_" + schema.reason)
    if payload["protocol_version"] != PROTOCOL_VERSION:
        return ValidationResult(False, "REQUEST_PROTOCOL_MISMATCH")
    try:
        expected_digest = allowed_action_digest(payload["phase"], payload["allowed_actions"])
    except (KeyError, TypeError, ValueError):
        return ValidationResult(False, "ACTION_DIGEST_INVALID")
    if payload["allowed_action_digest"] != expected_digest:
        return ValidationResult(False, "ACTION_DIGEST_MISMATCH")
    if payload["snapshot_quality"]["readiness"] == "BLOCKED":
        return ValidationResult(False, "SNAPSHOT_BLOCKED")
    if payload["snapshot_quality"]["critical_unknowns"]:
        return ValidationResult(False, "CRITICAL_UNKNOWN")
    phase = payload["phase"]
    actions = payload["allowed_actions"]
    if phase != "REVIEW" and actions["completeness"] != "COMPLETE":
        return ValidationResult(False, "LEGAL_ACTIONS_NOT_CONFIRMED")
    manifest = {item["name"]: item["sha256"] for item in payload["source_manifest"]["files"]}
    if not set(REQUIRED_MASTER_FILES).issubset(manifest):
        return ValidationResult(False, "FOUR_FILES_NOT_PRESENT_IN_MANIFEST")
    if any(len(value) != 64 or any(char not in "0123456789abcdef" for char in value) for value in manifest.values()):
        return ValidationResult(False, "INVALID_MANIFEST_SHA256")
    context = payload["context"]
    if phase == "SELECT":
        if len(context["own_team"]) != 6 or len(context["opponent_preview_six"]) != 6 or len(context["live_matrix_rows_six"]) != 6:
            return ValidationResult(False, "SELECT_MISSING_SIX_ROWS")
        if any(len(member["moves"]) != 4 for member in context["own_team"]):
            return ValidationResult(False, "SELECT_FOUR_MOVES_MISSING")
    elif phase != "REVIEW":
        if len(context["selected_three"]) != 3 or len(context["opponent_preview_six"]) != 6:
            return ValidationResult(False, "MISSING_SELECTED_THREE_OR_OPPONENT_SIX")
        if context["lead"] not in context["selected_three"]:
            return ValidationResult(False, "LEAD_NOT_IN_SELECTED_THREE")
        roster = {member["name"]: member for member in context["own_team"]}
        if any(name not in roster or len(roster[name]["moves"]) != 4 for name in context["selected_three"]):
            return ValidationResult(False, "OWN_SELECTED_FOUR_MOVES_MISSING")
    if phase in _BATTLE_PHASES:
        if len(context["live_matrix_rows_six"]) != 6:
            return ValidationResult(False, "LIVE_MATRIX_ROWS_MISSING")
        if not payload["battle_state"]["our_remaining"]:
            return ValidationResult(False, "OUR_REMAINING_MISSING")
        if phase == "NORMAL_COMMAND" and not payload["battle_state"]["our_active"]:
            return ValidationResult(False, "OWN_ACTIVE_NOT_IDENTIFIED")
    return ValidationResult(True, "OK")


def _identity_matches(request: dict[str, Any], response: dict[str, Any]) -> bool:
    keys = (
        "protocol_version",
        "request_id",
        "match_id",
        "decision_window_id",
        "tactical_state_version",
        "allowed_action_digest",
    )
    return all(request[key] == response[key] for key in keys)


def _is_current(request: dict[str, Any], current: dict[str, Any]) -> bool:
    keys = ("match_id", "decision_window_id", "tactical_state_version", "allowed_action_digest")
    return bool(current.get("window_open", True)) and all(request[key] == current.get(key) for key in keys)


def _validate_battle_response(request: dict[str, Any], response: dict[str, Any]) -> ValidationResult:
    phase = request["phase"]
    actions = request["allowed_actions"]
    action_kind = response["action_kind"]
    if action_kind == "MOVE":
        if phase != "NORMAL_COMMAND" or response["move"] not in actions["moves"] or response["switch_target"] is not None:
            return ValidationResult(False, "ILLEGAL_MOVE")
        if actions["choice_lock"] is not None and response["move"] != actions["choice_lock"]:
            return ValidationResult(False, "CHOICE_LOCK_VIOLATION")
        is_pivot = response["move"] in actions["pivot_moves"]
        if is_pivot and actions["switch_targets"] and response["planned_pivot_target"] is None:
            return ValidationResult(False, "PIVOT_TARGET_MISSING")
        if not is_pivot and response["planned_pivot_target"] is not None:
            return ValidationResult(False, "UNEXPECTED_PIVOT_TARGET")
    elif action_kind == "SWITCH":
        if (
            response["switch_target"] not in actions["switch_targets"]
            or response["move"] is not None
            or response["mega"]
            or response["planned_pivot_target"] is not None
        ):
            return ValidationResult(False, "ILLEGAL_SWITCH")
    else:
        return ValidationResult(False, "UNSUPPORTED_ACTION_KIND")
    if response["mega"] and not actions["mega_available"]:
        return ValidationResult(False, "ILLEGAL_MEGA")
    if response["planned_pivot_target"] is not None and response["planned_pivot_target"] not in actions["switch_targets"]:
        return ValidationResult(False, "ILLEGAL_PIVOT_TARGET")
    keep_back = response["keep_back"]
    if keep_back is not None:
        if (
            phase != "FAINT_REPLACEMENT"
            or keep_back not in request["battle_state"]["our_remaining"]
            or keep_back == response["switch_target"]
        ):
            return ValidationResult(False, "INVALID_KEEP_BACK")
    return ValidationResult(True, "OK")


def _validate_select_response(request: dict[str, Any], response: dict[str, Any]) -> ValidationResult:
    selected = response["selected_three"]
    candidates = request["allowed_actions"]["team_candidates"]
    if len(selected) != 3 or len(set(selected)) != 3 or any(name not in candidates for name in selected) or response["lead"] not in selected:
        return ValidationResult(False, "ILLEGAL_TEAM_SELECTION")
    return ValidationResult(True, "OK")


def _validate_prep_response(request: dict[str, Any], response: dict[str, Any]) -> ValidationResult:
    matchups = tuple(
        PrepMatchup(item["our_pokemon"], item["opponent_pokemon"], item["eval_code"])
        for item in response["matchups_18"]
    )
    result = PrepResult(
        response["prep_status"],
        matchups,
        response["preserve"],
        response["stopper"],
        response["closer"],
        response["unfinished_reason"],
    )
    context = request["context"]
    from .prep import build_prep_input  # Keeps the contract validator and PREP validator independent.

    prep_input = build_prep_input(
        tuple(context["selected_three"]),
        context["lead"],
        tuple(context["opponent_preview_six"]),
        tuple(context["live_matrix_rows_six"]),
    )
    validation = validate_prep_result(prep_input, result)
    if not validation.complete:
        return ValidationResult(False, f"PREP_{validation.reason}")
    selected = set(context["selected_three"])
    if any(value not in selected for value in (result.preserve, result.stopper, result.closer)):
        return ValidationResult(False, "PREP_WIN3_OUTSIDE_SELECTED_THREE")
    return ValidationResult(True, "OK")


def validate_response_for_current_request(
    request: dict[str, Any], response: dict[str, Any], *, current: dict[str, Any]
) -> ValidationResult:
    """Require local schema, echo identity, freshness, and legal semantics.

    ``NO_DECISION`` is a valid wire response but intentionally not displayable as
    an action recommendation, so it returns an unsuccessful result with that
    explicit reason.
    """
    try:
        kind = response_kind_for_phase(request["phase"])
    except (KeyError, ValueError):
        return ValidationResult(False, "REQUEST_PHASE_INVALID")
    schema = validate_against_canonical_schema(response, kind)
    if not schema.accepted:
        return ValidationResult(False, "RESPONSE_" + schema.reason)
    if not _identity_matches(request, response):
        return ValidationResult(False, "REQUEST_RESPONSE_MISMATCH")
    if not _is_current(request, current):
        return ValidationResult(False, "STALE_DECISION")
    if response["decision_status"] == "NO_DECISION":
        return ValidationResult(False, "NO_DECISION")
    if kind == "battle":
        return _validate_battle_response(request, response)
    if kind == "select":
        return _validate_select_response(request, response)
    if kind == "prep":
        return _validate_prep_response(request, response)
    return ValidationResult(True, "OK")
