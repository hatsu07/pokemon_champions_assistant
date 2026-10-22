"""Build v2.3 model payloads from Phase 2's frozen session state.

This bridge maps observed/session data to the contract; it does not score any
action, infer an unknown game fact, or access the filesystem.
"""

from __future__ import annotations

from typing import Any

from .fsm import MatchPhase, MatchSession, WindowKind, WindowStatus
from .model_adapter import ModelAdapter, ModelOutcome, ModelOutcomeStatus
from .prep import PrepMatchup, PrepResult
from .wire import AllowedActions, PROTOCOL_VERSION


_BATTLE_PHASES = frozenset(item.value for item in (WindowKind.NORMAL_COMMAND, WindowKind.PIVOT_TARGET, WindowKind.FAINT_REPLACEMENT, WindowKind.EFFECT_REPLACEMENT))


def _none(value: str | None) -> str | None:
    return value or None


def _team_for_wire(session: MatchSession) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for member in session.assets.own_team:
        result.append(
            {
                "name": member["name"],
                "moves": [member[f"move{number}"] for number in range(1, 5)],
                "item": _none(member.get("item")),
                "ability": _none(member.get("ability")),
                "nature": _none(member.get("nature")),
                "evs": _none(member.get("ap_h_a_b_c_d_s")),
                "role": _none(member.get("role")),
                "speed_note": _none(member.get("runtime_note")),
                "preserve_priority": _none(member.get("role")),
                "mega_form": _none(member.get("form_change")),
            }
        )
    return result


def _prep_for_wire(result: PrepResult | None) -> dict[str, Any]:
    if result is None:
        return {"status": "NOT_STARTED", "matchups_18": [], "preserve": None, "stopper": None, "closer": None}
    return {
        "status": "COMPLETE" if result.prep_status == "COMPLETE" else "INCOMPLETE",
        "matchups_18": [item.eval_code for item in result.matchups_18],
        "preserve": result.preserve,
        "stopper": result.stopper,
        "closer": result.closer,
    }


def _state_value(facts: dict[str, Any], key: str, default: Any) -> Any:
    value = facts.get(key, default)
    return value if isinstance(value, type(default)) else default


def _battle_state(session: MatchSession, selected: tuple[str, ...]) -> dict[str, Any]:
    facts = session.state_facts
    # These values come from future observer/reducer adapters. Empty arrays mean
    # "no observed entries", not an invented absence of a game mechanic.
    return {
        "our_active": facts.get("our_active", session.actual_lead),
        "opponent_active": facts.get("opponent_active"),
        "our_remaining": facts.get("our_remaining", list(selected)),
        "opponent_revealed": facts.get("opponent_revealed", []),
        "opponent_unrevealed_count": facts.get("opponent_unrevealed_count", None),
        "hp_status": _state_value(facts, "hp_status", []),
        "stat_stages": _state_value(facts, "stat_stages", []),
        "field_effects": _state_value(facts, "field_effects", []),
        "statuses": _state_value(facts, "statuses", []),
        "forms_and_types": _state_value(facts, "forms_and_types", []),
        "choice_locks": _state_value(facts, "choice_locks", []),
        "speed_observations": _state_value(facts, "speed_observations", []),
        "known_moves_items_abilities": _state_value(facts, "known_moves_items_abilities", []),
        "recent_confirmed_events": _state_value(facts, "recent_confirmed_events", []),
        "unresolved_event_ids": _state_value(facts, "unresolved_event_ids", []),
        "uncertainties": _state_value(facts, "uncertainties", []),
    }


def _job_deadline(session: MatchSession, phase: str, now_ms: int) -> int:
    if phase == "SELECT" and session.selection_deadline is not None:
        return session.selection_deadline.remaining_ms(now_ms) or 0
    if phase == "PREP" and session.prep_deadline is not None:
        return session.prep_deadline.remaining_ms(now_ms) or 0
    if phase in _BATTLE_PHASES and session.current_window is not None:
        return session.current_window.deadline.remaining_ms(now_ms) or 0
    # Review has no live input UI; this is an explicit bounded job budget.
    return 60_000


def _actions_for_phase(session: MatchSession, phase: str) -> AllowedActions:
    if phase in _BATTLE_PHASES:
        window = session.current_window
        if window is None or window.kind.value != phase:
            raise ValueError("the requested battle phase has no matching current window")
        return window.allowed_actions
    if phase == "SELECT":
        return AllowedActions(completeness="COMPLETE", team_candidates=session.assets.own_team_names)
    return AllowedActions(completeness="COMPLETE")


def build_session_payload(session: MatchSession, *, phase: str, request_id: str, now_ms: int) -> dict[str, Any]:
    """Construct a full local-v2.3 request for SELECT/PREP/BATTLE/REVIEW."""
    if phase not in {"SELECT", "PREP", "REVIEW", *_BATTLE_PHASES}:
        raise ValueError(f"unsupported model phase: {phase}")
    if session.opponent_preview_six is None or session.matrix_rows_six is None:
        raise ValueError("opponent preview and frozen matrix rows are required")
    actions = _actions_for_phase(session, phase)
    selected = tuple(session.actual_selected_three or ())
    lead = session.actual_lead
    if phase == "SELECT":
        selected = ()
        lead = None
        decision_window_id = f"{session.match_id}:select"
    elif phase == "PREP":
        decision_window_id = f"{session.match_id}:prep"
    elif phase == "REVIEW":
        decision_window_id = f"{session.match_id}:review"
    else:
        assert session.current_window is not None
        decision_window_id = session.current_window.decision_window_id
    plan = session.last_pivot_plan
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "match_id": session.match_id,
        "phase": phase,
        "decision_window_id": decision_window_id,
        "tactical_state_version": session.tactical_state_version,
        "observation_revision": session.observation_seq,
        "allowed_action_digest": actions.digest(phase),
        "deadline_ms_remaining": _job_deadline(session, phase, now_ms),
        "source_manifest": {"frozen_at": session.assets.frozen_at, "files": list(session.assets.manifest)},
        "allowed_actions": actions.as_wire(),
        "context": {
            "own_team": _team_for_wire(session),
            "selected_three": list(selected),
            "lead": lead,
            "opponent_preview_six": list(session.opponent_preview_six),
            "live_matrix_rows_six": list(session.matrix_rows_six),
            "prep": _prep_for_wire(session.prep_result),
            "gate_sha256": session.assets.gate_sha256,
        },
        "battle_state": _battle_state(session, selected),
        "snapshot_quality": {
            "readiness": "READY_CONFIRMED",
            "critical_unknowns": [],
            "material_unknowns": [],
            "pending_event_ids": [],
            "evidence_seq_through": session.observation_seq,
            "reason": "phase_2_frozen_session_snapshot",
        },
        "previous_ai_plan": {
            "parent_decision_window_id": None if plan is None else plan.source_window_id,
            "planned_pivot_target": None if plan is None else plan.target,
            "based_on_version": None if plan is None else plan.based_on_state_version,
            "validated_against_current_state": bool(plan and plan.based_on_state_version == session.tactical_state_version),
        },
        "task": "Use the frozen Champions LIVE gate and this observed state to return the one required JSON response.",
    }
    return payload


def current_session_identity(session: MatchSession, *, phase: str) -> dict[str, Any]:
    """Return the current display entitlement for a previously frozen request."""
    if phase in _BATTLE_PHASES:
        window = session.current_window
        if window is None or window.kind.value != phase:
            return {"window_open": False}
        return {
            "match_id": session.match_id,
            "decision_window_id": window.decision_window_id,
            "tactical_state_version": session.tactical_state_version,
            "allowed_action_digest": window.allowed_action_digest,
            "window_open": window.status in {WindowStatus.OPEN, WindowStatus.REQUESTED},
        }
    required_phase = {"SELECT": MatchPhase.S03_TEAM_SELECTION, "PREP": MatchPhase.S04_PREP_40S, "REVIEW": MatchPhase.S07_REVIEW}[phase]
    actions = _actions_for_phase(session, phase)
    job_id = f"{session.match_id}:{phase.lower()}"
    return {
        "match_id": session.match_id,
        "decision_window_id": job_id,
        "tactical_state_version": session.tactical_state_version,
        "allowed_action_digest": actions.digest(phase),
        "window_open": session.phase is required_phase,
    }


def request_session_model_job(
    session: MatchSession,
    adapter: ModelAdapter,
    *,
    phase: str,
    request_id: str,
    now_ms: int,
    lookup_keys: tuple[str, ...] = (),
) -> ModelOutcome:
    """Run one model job and apply only a displayable, already-safe response."""
    payload = build_session_payload(session, phase=phase, request_id=request_id, now_ms=now_ms)
    outcome = adapter.decide(
        payload,
        session.assets,
        current_supplier=lambda: current_session_identity(session, phase=phase),
        lookup_keys=lookup_keys,
    )
    if outcome.status is not ModelOutcomeStatus.DISPLAYABLE or outcome.response is None:
        return outcome
    response = outcome.response
    if phase == "SELECT":
        session.record_selection_recommendation(tuple(response["selected_three"]), response["lead"])
    elif phase == "PREP":
        prep = PrepResult(
            response["prep_status"],
            tuple(PrepMatchup(item["our_pokemon"], item["opponent_pokemon"], item["eval_code"]) for item in response["matchups_18"]),
            response["preserve"],
            response["stopper"],
            response["closer"],
            response["unfinished_reason"],
        )
        session.record_prep_result(prep, now_ms=now_ms)
    elif phase in _BATTLE_PHASES:
        session.accept_validated_model_response(response)
    return outcome
