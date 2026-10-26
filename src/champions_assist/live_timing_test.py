"""Explicit off-match shared-clock PREP and turn (30 s) API timing benchmarks.

Both tests call the SAME Champions replay code used by the live observer.
No invented game actions, no screenshot writes, and no tactical rules/AI
fallback. Input images are supplied explicitly and remain in memory.
A *timely and legal* response is required for PASS; "unknown"/late/NO_ACTION
are not counted as successful turn advice. Results are diagnostics only.
"""
from __future__ import annotations

from copy import deepcopy
from time import perf_counter
from typing import Callable

from .match_replay import (
    new_replay, confirm_actual_selection, run_replay_prep, run_replay_batch)
from .prep import (
    build_prep_input, PrepMatchup, PrepResult, validate_prep_result,
    validate_partial_prep_result,
)

PREP_LIMIT_SECONDS = 87.0  # Team-selection 90s clock minus 3s for player input.
# PREP has no independent 40s deadline: the caller gives it the remaining
# budget from the ORIGINAL selection start, including authentication and UI.
TURN_TARGET_SECONDS = 30.0
TURN_GAME_LIMIT_SECONDS = 45.0
ADVICE_STATUSES = frozenset({"ADVICE", "PROVISIONAL_ADVICE"})


def selection_test_prep_state(assets, result: dict) -> dict:
    """Make a labelled simulation of a proposed selection (NOT actual picks).

    Opponents must come from the verified image observation; selected team
    must be the three actually recommended, without guessing another team.
    """
    if result.get("status") not in ("OK", "PROVISIONAL_ADVICE", "PARTIAL_ADVICE"):
        raise ValueError("VALID_SELECTION_RESULT_REQUIRED")
    opponents = tuple(result.get("opponent_six") or ())
    if len(opponents) != 6 or len(set(opponents)) != 6:
        raise ValueError("SIX_OPPONENTS_REQUIRED")
    partial=result["status"]=="PARTIAL_ADVICE"
    unknown=tuple(name for i,name in enumerate(opponents,1)
                  if name==f"未判明相手{i}")
    if partial:
        known=tuple(name for name in opponents if name not in unknown)
        if (len(unknown) not in (1,2) or len(known) not in (4,5)
                or any(name not in assets.matrix_index for name in known)):
            raise ValueError("PARTIAL_OPPONENTS_INVALID")
    else:
        if any(name not in assets.matrix_index for name in opponents):
            raise ValueError("SIX_OPPONENTS_REQUIRED")
        known=opponents
    chosen = result.get("selected_three") or []
    lead = result.get("lead") or ""
    state = new_replay(assets)
    state["phase"] = "SELECTION"
    state["opponent_six"] = list(opponents)
    state["visual_unknown_opponents"] = list(unknown)
    state["matrix_rows_six"] = (
        [assets.matrix_index[name] for name in known]
        if partial else list(assets.matrix_rows_for(opponents)))
    # This confirmation is ONLY inside the replay test harness, not in the
    # running game: player choices are never silently confirmed.
    return confirm_actual_selection(state, assets, list(chosen), lead)


def _elapsed_ms(started: float, clock: Callable[[], float]) -> int:
    return round(max(0, clock() - started) * 1000)


def _response_limit(started: float, limit: float, clock: Callable[[], float]) -> float:
    return max(0.0, limit - max(0.0, clock() - started))


def prep_timing_test(
    *, state: dict, assets, access_token: str, model: str,
    reasoning_effort: str = "auto",
    started_at: float | None = None,
    limit_seconds: float = PREP_LIMIT_SECONDS,
    clock: Callable[[], float] = perf_counter,
    prep_runner: Callable = run_replay_prep,
    client_factory=None,
) -> tuple[dict, dict]:
    """Include token retrieval (if started_at was provided) in the hard cap."""
    started = clock() if started_at is None else started_at
    cap = max(0.0, min(PREP_LIMIT_SECONDS, limit_seconds))
    remaining = _response_limit(started, cap, clock)
    if remaining <= 1.0:
        return deepcopy(state), {
            "status": "PREP_TIMING_FAIL", "reason": "AUTH_OR_QUEUE_BUDGET",
            "elapsed_ms": _elapsed_ms(started, clock), "limit_ms": round(cap*1000),
            "lead_evaluated": 0, "bench_evaluated": 0,
            "win_conditions": 0, "pass": False,
        }
    params = {
        "state": state, "assets": assets, "access_token": access_token,
        "model": model, "reasoning_effort": reasoning_effort,
        "limit_seconds": remaining,
    }
    if client_factory is not None:
        params["client_factory"] = client_factory
    try:
        updated, report = prep_runner(**params)
    except Exception as exc:
        return deepcopy(state), {
            "status": "PREP_TIMING_FAIL", "reason": type(exc).__name__,
            "elapsed_ms": _elapsed_ms(started, clock), "limit_ms": round(cap*1000),
            "lead_evaluated": 0, "bench_evaluated": 0,
            "win_conditions": 0, "pass": False,
        }
    elapsed = _elapsed_ms(started, clock)
    prep = updated.get("prep") or {}
    selected = tuple(state.get("selected_three") or ())
    lead = state.get("lead")
    six = tuple(state.get("opponent_six") or ())
    partial=bool(state.get("visual_unknown_opponents"))
    unknown_slots=tuple(state.get("visual_unknown_opponents") or ())
    known=tuple(name for name in six if name not in unknown_slots)
    rows = prep.get("matchups_known" if partial else "matchups_18") or []
    lead_rows = [r for r in rows if r.get("our_pokemon") == lead]
    bench_rows = [r for r in rows if r.get("our_pokemon") in selected and r.get("our_pokemon") != lead]
    required = ("preserve", "stopper", "closer")
    win_roles = sum(bool(prep.get(key) in selected) for key in required)
    # Independently validate the model's complete 18-pair + required-tag
    # structure, even when a synthetic/changed replay runner claims COMPLETE.
    structurally_valid = False
    if len(six)==6 and len(selected)==3 and lead in selected:
        try:
            parsed=PrepResult(
                "PARTIAL" if partial else "COMPLETE",
                tuple(PrepMatchup(
                    str(r["our_pokemon"]),str(r["opponent_pokemon"]),
                    str(r["eval_code"])) for r in rows),
                prep.get("preserve"),prep.get("stopper"),prep.get("closer"))
            check=(
                validate_partial_prep_result(
                    selected,lead,known,unknown_slots,parsed)
                if partial else validate_prep_result(
                    build_prep_input(selected,lead,six,tuple(
                        state.get("matrix_rows_six") or assets.matrix_rows_for(six))),
                    parsed))
            structurally_valid=check.complete
        except (ValueError,KeyError,TypeError):
            structurally_valid=False
    complete = (
        not partial and structurally_valid
        and report.get("status") in ("PREP_STRUCTURE_OK", "PREP_PROVISIONAL")
        and updated.get("prep_status") == "STRUCTURE_COMPLETE"
        and len(six) == 6 and len(set(six)) == 6
        and len(rows) == 18 and len(lead_rows) == 6 and len(bench_rows) == 12
        and len({(r.get("our_pokemon"), r.get("opponent_pokemon")) for r in rows}) == 18
        and win_roles == 3
    )
    passed = complete and elapsed <= round(cap*1000)
    partial_ready=(
        partial and structurally_valid
        and report.get("status")=="PREP_PARTIAL"
        and updated.get("prep_status")=="PARTIAL_STRUCTURE_COMPLETE"
        and len(rows)==3*len(known)
        and len(lead_rows)==len(known) and len(bench_rows)==2*len(known)
        and win_roles==3
        and elapsed<=round(cap*1000)
    )
    return (updated if passed or partial_ready else deepcopy(state)), {
        "status": ("PREP_TIMING_PASS" if passed else
                   "PREP_TIMING_PARTIAL" if partial_ready else "PREP_TIMING_FAIL"),
        "source_status": report.get("status", "UNKNOWN"),
        "source_reason": report.get("reason", ""),
        "source_elapsed_ms": report.get("elapsed_ms"),
        "source_stage": report.get("request_stage"),
        "source_model_elapsed_ms": report.get("model_elapsed_ms"),
        "source_prompt_chars": report.get("prompt_chars"),
        "reason": "OK" if passed else "KNOWN_ONLY_UNCONFIRMED_OPPONENT" if partial_ready else (
            "DEADLINE_EXCEEDED" if elapsed > round(cap*1000) else
            (report.get("reason") if report.get("reason") not in ("OK","") else None)
            or "INCOMPLETE_18_OR_ROLES"),
        "elapsed_ms": elapsed, "limit_ms": round(cap*1000),
        "lead_evaluated": len(lead_rows),
        "bench_evaluated": len(bench_rows),
        "win_conditions": win_roles,
        "known_opponents":len(known),
        "unknown_opponents":len(unknown_slots),
        "unknown_matchups":3*len(unknown_slots),
        "partial_ready":partial_ready,
        "pass": passed,
        "evidence_scope": ("AI_PROVISIONAL_NOT_TACTICALLY_CERTIFIED"
                           if state.get("matrix_provisional")
                           else "STRUCTURE_ONLY_NOT_TACTICALLY_CERTIFIED"),
    }


def turn_timing_test(
    *, image, state: dict, assets, access_token: str, model: str,
    reasoning_effort: str = "auto",
    started_at: float | None = None,
    clock: Callable[[], float] = perf_counter,
    turn_runner: Callable = run_replay_batch,
    client_factory=None,
) -> tuple[dict, dict]:
    """One real vision+AI+legal-action replay of a user-selected turn screenshot.

    Start from a *test-only* selection. PREP may have run, but the action
    must be legal on the newly read image; no state from an unobserved turn
    is hallucinated. A NO_ACTION is a failure to supply input-ready advice.
    """
    started = clock() if started_at is None else started_at
    remaining = _response_limit(started, TURN_TARGET_SECONDS, clock)
    if remaining <= 1.0:
        return deepcopy(state), {
            "status": "TURN_TIMING_FAIL", "reason": "AUTH_OR_QUEUE_BUDGET",
            "elapsed_ms": _elapsed_ms(started, clock), "target_ms": 30000,
            "game_limit_ms": 45000, "recommendation": "", "pass": False,
        }
    if (state.get("phase") not in ("PREP", "BATTLE")
            or len(state.get("selected_three") or ()) != 3
            or len(state.get("opponent_six") or ()) != 6):
        return deepcopy(state), {
            "status": "TURN_TIMING_FAIL", "reason": "TEST_SELECTION_REQUIRED",
            "elapsed_ms": _elapsed_ms(started, clock), "target_ms": 30000,
            "game_limit_ms": 45000, "recommendation": "", "pass": False,
        }
    snap = deepcopy(state)
    snap["phase"] = "BATTLE"
    params = {
        "images": [image], "state": snap, "assets": assets,
        "access_token": access_token, "model": model,
        "reasoning_effort": reasoning_effort, "total_seconds": remaining,
    }
    if client_factory is not None:
        params["client_factory"] = client_factory
    try:
        updated, report = turn_runner(**params)
    except Exception as exc:
        return deepcopy(state), {
            "status": "TURN_TIMING_FAIL", "reason": type(exc).__name__,
            "elapsed_ms": _elapsed_ms(started, clock), "target_ms": 30000,
            "game_limit_ms": 45000, "recommendation": "", "pass": False,
        }
    elapsed = _elapsed_ms(started, clock)
    action = report.get("action") or {}
    recommendation = report.get("recommendation") or ""
    legal = (
        report.get("status") in ADVICE_STATUSES
        and report.get("screen") in ("MOVE_MENU", "PARTY_MENU")
        and action.get("kind") in ("MOVE", "SWITCH", "PIVOT")
        and isinstance(recommendation, str) and bool(recommendation.strip())
    )
    passed = legal and elapsed <= 30000
    return (updated if passed else deepcopy(state)), {
        "status": "TURN_TIMING_PASS" if passed else "TURN_TIMING_FAIL",
        "source_status": report.get("status", "UNKNOWN"),
        "reason": "OK" if passed else (
            "DEADLINE_EXCEEDED" if elapsed > 30000 else
            report.get("reason") or report.get("status") or "NO_LEGAL_ACTION"),
        "elapsed_ms": elapsed, "target_ms": 30000,
        "game_limit_ms": 45000,
        "input_margin_ms": max(0, 45000 - elapsed),
        "action_kind": action.get("kind", ""),
        "recommendation": recommendation if passed else "",
        "pass": passed,
        "screen_observation": report.get("screen", "UNKNOWN"),
        "scope": "OFFMATCH_SAVED_SCREENSHOT_NOT_LIVE_GAME_CERTIFICATION",
    }
