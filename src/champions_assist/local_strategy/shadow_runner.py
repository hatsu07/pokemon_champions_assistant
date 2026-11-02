"""Phase 2 isolated one-turn Showdown orchestration; not a live recommendation.

Consumes *explicit* complete synthetic/replay scenarios. This module does not
invent an opponent set, move, switch, RNG state, or a probability. It never
reads files, fetches the network, launches game controls or edits observations.
The Phase 1 worker and one_turn comparator enforce the separate boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass
from json import loads, JSONDecodeError
from time import monotonic
from typing import Any, Callable

from .legal_actions import generate_candidates, showdown_choice_for_candidate
from .models import (
    FrozenBattleState, LegalStatus, CapabilityStatus, SimulationResult,
    action_digest, canonical_json, joint_hypothesis_ids,
)
from .one_turn import BranchObservation, ShadowTurnRanking, compare_shadow_turn
from .simulator_adapter import WorkerUnavailable, _verified_roster_binding

MAX_BRANCHES = 240
MAX_P2_CHOICES_PER_HYPOTHESIS = 24


@dataclass(frozen=True)
class ReplayOpponentScenario:
    """A caller-provided joint hypothesis, initial Battle and opponent choices.

    `battle_json` is the serialized *complete* starting Showdown state,
    including its RNG. Never use an OCR-only or guessed midbattle state here.
    `p2_choices` are actual Showdown commands from the same starting state;
    their legality remains the responsibility of the worker engine.
    """
    hypothesis_id: str
    battle_json: str
    p2_choices: tuple[str, ...]
    source: str = "FULL_REPLAY_SYNTHETIC"


def _blocked(reason: str) -> ShadowTurnRanking:
    return ShadowTurnRanking("NO_RANKING", reason)


def _valid_worker_result(value: Any) -> bool:
    """Reject malformed external branch results before using any attributes.

    Semantic verification of the SHA, window, original request and opponent
    scenario remains in compare_shadow_turn; this gate is a boundary check
    that keeps None/dicts/incomplete objects from breaking the caller.
    """
    if not isinstance(value, SimulationResult):
        return False
    required_text = (
        value.request_id, value.identity_json, value.snapshot_sha256,
        value.hypothesis_id, value.payload_json, value.reason,
        value.branch_request_id, value.branch_input_sha256,
        value.candidate_action_id, value.submitted_request_json,
    )
    if not all(isinstance(v, str) for v in required_text):
        return False
    if not all((
        value.request_id, value.identity_json, value.snapshot_sha256,
        value.payload_json, value.branch_request_id,
        value.branch_input_sha256, value.candidate_action_id,
        value.submitted_request_json,
    )):
        return False
    if not isinstance(value.capability_status, CapabilityStatus):
        return False
    if (len(value.branch_request_id) != 32
            or any(ch not in "0123456789abcdef"
                   for ch in value.branch_request_id)
            or len(value.branch_input_sha256) != 64
            or any(ch not in "0123456789abcdef"
                   for ch in value.branch_input_sha256)):
        return False
    return True


def run_shadow_turn(
    snapshot: FrozenBattleState,
    assets: Any,
    worker: Any,
    scenarios: tuple[ReplayOpponentScenario, ...],
    *,
    preserve: str,
    stopper: str,
    current_snapshot: Callable[[], FrozenBattleState] | None = None,
    reserve_seconds: float = 0.5,
    clock: Callable[[], float] = monotonic,
) -> ShadowTurnRanking:
    """Evaluate identical full-replay joint branches for every legal action.

    A failed/late/incomplete branch invalidates the *entire* ranking. No
    fallback based on whichever candidates finished first. `current_snapshot`
    is an optional in-memory revision supplier; callers that use live capture
    must supply it and never do filesystem/OCR I/O through this callback.
    """
    if (not isinstance(snapshot, FrozenBattleState)
            or not isinstance(scenarios, tuple)
            or not isinstance(reserve_seconds, (float, int))
            or isinstance(reserve_seconds, bool)
            or not 0 <= reserve_seconds <= 10):
        return _blocked("INVALID_RUN_INPUT")
    window = snapshot.window
    if clock() + reserve_seconds >= window.deadline_monotonic:
        return _blocked("INPUT_DEADLINE_PASSED")
    if action_digest(snapshot.verified_action_ids) != window.allowed_action_digest:
        return _blocked("ALLOWED_ACTION_DIGEST_CHANGED")
    try:
        _verified_roster_binding(snapshot, assets)
        candidates = generate_candidates(snapshot, assets)
        eligible = [candidate for candidate in candidates
                    if candidate.legal_status == LegalStatus.LEGAL]
        if (not eligible or len({c.action_id for c in eligible}) != len(eligible)
                or any(action_id not in {c.action_id for c in eligible}
                       for action_id in snapshot.verified_action_ids)):
            return _blocked("NO_COMPLETE_CURRENT_LEGAL_ACTION_SET")
        choices = {candidate.action_id:
                   showdown_choice_for_candidate(candidate, snapshot, assets)
                   for candidate in eligible}
    except (WorkerUnavailable, ValueError, TypeError, KeyError, AttributeError) as exc:
        return _blocked("ACTION_OR_ROSTER_UNVERIFIED:" + str(exc)[:100])
    try:
        hypotheses = set(joint_hypothesis_ids(snapshot.hypotheses))
    except ValueError:
        return _blocked("DUPLICATE_OR_INVALID_HYPOTHESIS_ID")
    if any(not isinstance(s, ReplayOpponentScenario) for s in scenarios):
        return _blocked("UNVERIFIED_REPLAY_OR_P2_CHOICE_SET")
    if (not scenarios or len(scenarios) != len(hypotheses)
            or {s.hypothesis_id for s in scenarios} != hypotheses
            or len({s.hypothesis_id for s in scenarios}) != len(scenarios)):
        return _blocked("JOINT_HYPOTHESIS_SET_INCOMPLETE")
    prepared: list[tuple[str, dict[str, Any], tuple[str, ...]]] = []
    total_choices = 0
    for scenario in scenarios:
        if (not isinstance(scenario, ReplayOpponentScenario)
                or scenario.source != "FULL_REPLAY_SYNTHETIC"
                or not isinstance(scenario.battle_json, str)
                or not isinstance(scenario.p2_choices, tuple)
                or not 1 <= len(scenario.p2_choices) <= MAX_P2_CHOICES_PER_HYPOTHESIS
                or any(not isinstance(c, str) or not c or c != c.strip()
                       or len(c) > 80 or "\n" in c or "\r" in c
                       for c in scenario.p2_choices)
                or len(set(scenario.p2_choices)) != len(scenario.p2_choices)):
            return _blocked("UNVERIFIED_REPLAY_OR_P2_CHOICE_SET")
        try:
            battle = loads(scenario.battle_json)
            if not isinstance(battle, dict):
                raise ValueError("not an object")
            # Round-trip through the same canonical JSON format each time,
            # so no worker can modify a subsequent branch's input.
            frozen_battle = canonical_json(battle)
        except (ValueError, TypeError, JSONDecodeError):
            return _blocked("INVALID_COMPLETE_REPLAY_STATE")
        prepared.append((scenario.hypothesis_id,
                         loads(frozen_battle), scenario.p2_choices))
        total_choices += len(scenario.p2_choices)
    if total_choices * len(eligible) > MAX_BRANCHES:
        return _blocked("BRANCH_BUDGET_EXCEEDED")
    initial_snapshot_sha = snapshot.snapshot_sha256
    branches: list[BranchObservation] = []
    for hypothesis, battle, p2_choices in prepared:
        for p2 in p2_choices:
            for candidate in eligible:
                if clock() + reserve_seconds >= window.deadline_monotonic:
                    return _blocked("BRANCH_DEADLINE_EXCEEDED")
                if current_snapshot is not None:
                    try:
                        current = current_snapshot()
                        if (current.window.identity() != window.identity()
                                or current.snapshot_sha256 != initial_snapshot_sha):
                            return _blocked("CURRENT_OBSERVATION_CHANGED")
                    except Exception:
                        # A session swap or an invalidated state store may
                        # raise RuntimeError rather than an attribute error.
                        # Do not let a shadow callback break the LIVE UI.
                        return _blocked("CURRENT_SNAPSHOT_UNAVAILABLE")
                try:
                    # The worker receives a distinct detached Battle JSON per
                    # call; the underlying plan reuses one identical start.
                    result = worker.simulate(
                        snapshot, loads(canonical_json(battle)),
                        choices[candidate.action_id], p2,
                        hypothesis_id=hypothesis,
                        candidate_action_id=candidate.action_id, assets=assets,
                    )
                except Exception:
                    # The worker is an external boundary: even unexpected
                    # runtime exceptions abort this complete grid safely.
                    return _blocked("WORKER_BRANCH_UNAVAILABLE")
                if not _valid_worker_result(result):
                    return _blocked("WORKER_BRANCH_UNAVAILABLE")
                if result.capability_status != CapabilityStatus.PARTIAL:
                    return _blocked("WORKER_BRANCH_NOT_PARTIAL")
                branches.append(BranchObservation(
                    candidate.action_id, hypothesis, p2, result))
    if current_snapshot is not None:
        try:
            current = current_snapshot()
            if (current.window.identity() != window.identity()
                    or current.snapshot_sha256 != initial_snapshot_sha):
                return _blocked("CURRENT_OBSERVATION_CHANGED")
        except Exception:
            return _blocked("CURRENT_SNAPSHOT_UNAVAILABLE")
    if clock() + reserve_seconds >= window.deadline_monotonic:
        return _blocked("BRANCH_DEADLINE_EXCEEDED")
    try:
        return compare_shadow_turn(
            snapshot, assets, tuple(branches),
            preserve=preserve, stopper=stopper, now=clock(),
        )
    except Exception:
        # Defensive isolation if a structurally valid but malformed worker
        # payload trips an unforeseen downstream parsing path. Never publish
        # a partial rank or propagate a crash to the existing GPT/LIVE app.
        return _blocked("COMPARISON_UNAVAILABLE")
