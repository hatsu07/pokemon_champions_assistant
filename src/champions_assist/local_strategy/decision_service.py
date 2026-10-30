"""Fail-closed display adoption. This does not choose a tactical move.

A calculated candidate may be shown only if it still belongs to the exact
current input window, its source state is fresh, and it is currently LEGAL.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from time import monotonic
from typing import Any

from .legal_actions import revalidate_candidate
from .models import (
    CandidateAction, CapabilityStatus, FrozenBattleState,
    LegalStatus, SimulationResult, action_digest,
)


@dataclass(frozen=True)
class DecisionOutcome:
    status: str
    reason: str
    action: CandidateAction | None = None


def accept_result(submitted: FrozenBattleState, current: FrozenBattleState,
                  result: SimulationResult, candidate: CandidateAction | None,
                  assets: Any, *, now: float | None = None) -> DecisionOutcome:
    """No adoption on ABA windows, stale turns, wrong revisions or uncertainty."""
    clock = monotonic() if now is None else now
    if clock >= current.window.deadline_monotonic:
        return DecisionOutcome("NO_RECOMMENDATION", "INPUT_DEADLINE_PASSED")
    if submitted.window.identity() != current.window.identity():
        return DecisionOutcome("NO_RECOMMENDATION", "STALE_WINDOW_OR_REVISION")
    if submitted.snapshot_sha256 != current.snapshot_sha256:
        return DecisionOutcome("NO_RECOMMENDATION", "OBSERVATIONS_CHANGED")
    if (action_digest(current.verified_action_ids)
            != current.window.allowed_action_digest):
        return DecisionOutcome("NO_RECOMMENDATION", "ALLOWED_ACTIONS_CHANGED")
    try:
        reported_identity = json.loads(result.identity_json)
    except (ValueError, TypeError):
        return DecisionOutcome("NO_RECOMMENDATION", "INVALID_WORKER_IDENTITY")
    if (result.request_id != current.window.request_id
            or result.snapshot_sha256 != submitted.snapshot_sha256
            or reported_identity != current.window.identity()):
        return DecisionOutcome("NO_RECOMMENDATION", "STALE_OR_UNBOUND_WORKER_RESPONSE")
    if result.hypothesis_id and result.hypothesis_id not in {
            h.hypothesis_id for h in submitted.hypotheses}:
        return DecisionOutcome("NO_RECOMMENDATION", "UNBOUND_HYPOTHESIS")
    if result.capability_status != CapabilityStatus.CONFIRMED:
        return DecisionOutcome("NO_RECOMMENDATION", "CALCULATION_NOT_VERIFIED")
    if candidate is None:
        return DecisionOutcome("NO_RECOMMENDATION", "NO_CANDIDATE")
    checked = revalidate_candidate(candidate, current, assets)
    if checked.legal_status != LegalStatus.LEGAL:
        return DecisionOutcome("NO_RECOMMENDATION",
                               "ACTION_" + checked.reason)
    return DecisionOutcome("LOCAL_VERIFIED", "CURRENT_WINDOW_LEGAL", checked)
