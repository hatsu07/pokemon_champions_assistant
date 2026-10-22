"""Synthetic, explicitly non-game Phase 3 mock-model trace for the offline CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .fsm import MatchController, ObservationEvent, VisualUI
from .model_adapter import MockResponsesTransport, ModelAdapter, TransportRequest
from .session_payloads import request_session_model_job
from .settings import ModelSettings
from .store import BattleStore
from .wire import AllowedActions


def _response_base(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_version": payload["protocol_version"],
        "request_id": payload["request_id"],
        "match_id": payload["match_id"],
        "decision_window_id": payload["decision_window_id"],
        "tactical_state_version": payload["tactical_state_version"],
        "allowed_action_digest": payload["allowed_action_digest"],
        "decision_status": "RECOMMENDED",
        "reason_code": "SIMULATED_FIXTURE",
        "warning": None,
    }


def _simulated_response(request: TransportRequest) -> dict[str, Any]:
    """Return predeclared fixture shapes; this is not tactical evaluation."""
    payload = request.payload
    response = _response_base(payload)
    phase = payload["phase"]
    if phase == "SELECT":
        selected = payload["allowed_actions"]["team_candidates"][:3]
        return {**response, "selected_three": selected, "lead": selected[0]}
    if phase == "PREP":
        selected = payload["context"]["selected_three"]
        lead = payload["context"]["lead"]
        opponents = payload["context"]["opponent_preview_six"]
        lead_code = "ACT=SIMULATED|KO=?|DPS=SIMULATED|PRI=?|S=?|OUR=?|OPP=?|SAFE=?|PROC=SIMULATED|LOCK=?|MEGA=?"
        bench_code = "S=?|OUR=?|OPP=?|PRI=?|SAFE=?|PROC=SIMULATED"
        return {
            **response,
            "prep_status": "COMPLETE",
            "matchups_18": [
                {"our_pokemon": own, "opponent_pokemon": opponent, "eval_code": lead_code if own == lead else bench_code}
                for own in selected
                for opponent in opponents
            ],
            "preserve": selected[0],
            "stopper": selected[1],
            "closer": selected[2],
            "unfinished_reason": None,
        }
    if phase in {"NORMAL_COMMAND", "PIVOT_TARGET", "FAINT_REPLACEMENT", "EFFECT_REPLACEMENT"}:
        moves = payload["allowed_actions"]["moves"]
        if moves:
            return {
                **response,
                "action_kind": "MOVE",
                "move": moves[0],
                "mega": False,
                "switch_target": None,
                "planned_pivot_target": None,
                "keep_back": None,
            }
        target = payload["allowed_actions"]["switch_targets"][0]
        return {
            **response,
            "action_kind": "SWITCH",
            "move": None,
            "mega": False,
            "switch_target": target,
            "planned_pivot_target": None,
            "keep_back": None,
        }
    return {
        **response,
        "outcome": "UNKNOWN",
        "selection_review": "SIMULATED_ONLY",
        "turn_reviews": [],
        "support_errors": [],
        "player_errors": [],
        "structure_issues": [],
        "improvements": [],
    }


def run_simulated_normal_trace(master_directory: str | Path) -> dict[str, Any]:
    """Run SELECT → PREP → BATTLE → REVIEW with a no-network fixture transport."""
    store = BattleStore(":memory:")
    try:
        controller = MatchController(master_directory, store=store)
        session = controller.start_match("simulated-match-001", now_ms=0)
        opponents = tuple(list(session.assets.matrix_index)[:6])
        session.receive_opponent_preview(opponents, now_ms=1)
        transport = MockResponsesTransport([_simulated_response] * 4)
        adapter = ModelAdapter(store=store, transport=transport, settings=ModelSettings(), now_ms=lambda: 0)
        select = request_session_model_job(session, adapter, phase="SELECT", request_id="simulated-select", now_ms=2)
        selected = session.selection_recommendation.selected_three
        session.record_actual_selection(selected, session.selection_recommendation.lead, now_ms=3)
        prep = request_session_model_job(session, adapter, phase="PREP", request_id="simulated-prep", now_ms=4)
        session.observe_event("simulated-lead", ObservationEvent.OPPONENT_LEAD_VISIBLE, now_ms=5)
        legal = AllowedActions(completeness="COMPLETE", moves=("SIMULATED_MOVE",))
        window = session.present_ui(VisualUI.COMMAND_MENU, legal, now_ms=6)
        assert window is not None
        battle = request_session_model_job(session, adapter, phase="NORMAL_COMMAND", request_id="simulated-battle", now_ms=7)
        session.record_actual_action("simulated-action", {"action": "SIMULATED_MOVE", "source": "synthetic"})
        session.observe_event("simulated-end", ObservationEvent.MATCH_END_CONFIRMED, now_ms=8)
        session.begin_review()
        review = request_session_model_job(session, adapter, phase="REVIEW", request_id="simulated-review", now_ms=9)
        session.finish_review()
        return {
            "mode": "SIMULATED_ONLY",
            "phase": session.phase.value,
            "preload_complete": True,
            "select_displayable": select.status.value,
            "prep_displayable": prep.status.value,
            "battle_displayable": battle.status.value,
            "review_displayable": review.status.value,
            "prep_complete": bool(session.prep_validation and session.prep_validation.complete),
            "decision_windows": session.decision_seq,
            "mock_model_jobs": len(transport.requests),
            "capture": "NOT_USED",
            "network": "NOT_USED",
            "game_control": "NOT_IMPLEMENTED",
        }
    finally:
        store.close()
