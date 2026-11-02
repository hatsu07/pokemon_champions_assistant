"""Issue #141 Phase 2(②/③): observed-to-synthetic full-grid DIAGNOSTIC.

This joins existing observation/whole-joint readout checks with engine-issued
hypothetical p2 choices and the existing one-turn comparator. It deliberately
does NOT produce an actionable game ranking. A caller-supplied complete
Showdown Battle/RNG, claimed Japanese-to-p2-slot map and a locally verified
worker are not proof of an actual Champions battle or a complete enemy policy.
There is no LIVE/GPT/GUI/LOCAL_SHADOW import or automatic promotion.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from time import monotonic
from typing import Any, Callable

from .joint_set_intake import (
    MAX_JOINT_RECORDS, SourcedJointSetCandidate, stage_sourced_joint_candidates,
)
from .models import FrozenBattleState, canonical_json, fingerprint
from .observed_opponent_actions import stage_observed_opponent_action_frontier
from .observed_synthetic_replay_bridge import (
    ExplicitSyntheticBattleBranch, compare_observed_to_synthetic_replays,
)
from .shadow_runner import (
    MAX_BRANCHES, ReplayOpponentScenario, run_shadow_turn,
)
from .simulator_adapter import (
    EngineP2CandidateSet, LocalSimulatorWorker, WorkerUnavailable,
)


@dataclass(frozen=True)
class ObservedFullGridDiagnostic:
    status: str
    reason: str
    match_id: str = ""
    decision_window_id: str = ""
    state_revision: int = -1
    observed_snapshot_sha256: str = ""
    observed_batch_sha256: str = ""
    # SHA of hypothetical Battle/RNG values, not real-game evidence.
    synthetic_battle_sha256: tuple[str, ...] = ()
    hypothesis_ids: tuple[str, ...] = ()
    engine_choice_counts: tuple[int, ...] = ()
    evaluated_branch_count: int = 0
    # This is a synthetic-model diagnostic, not a game action recommendation.
    hypothetical_model_status: str = ""
    hypothetical_nondominated_action_ids: tuple[str, ...] = ()
    ranking_status: str = "NO_RANKING"
    game_battle_authenticated: bool = False
    opponent_mapping_authenticated: bool = False
    exhaustive_opponent_policy: bool = False
    full_state_reconstructed: bool = False
    ready_for_engine: bool = False


def _no(reason: str) -> ObservedFullGridDiagnostic:
    # Fail the ENTIRE grid: never leak a successful prefix or one best move.
    return ObservedFullGridDiagnostic("NO_OBSERVED_TURN_DIAGNOSTIC", reason)


def _same_window(expected: FrozenBattleState, current: Any) -> bool:
    return (isinstance(current, FrozenBattleState)
            and current.window.identity() == expected.window.identity()
            and current.snapshot_sha256 == expected.snapshot_sha256)


def audit_observed_synthetic_turn(
    snapshot: FrozenBattleState, assets: Any, *,
    joint_candidates: tuple[SourcedJointSetCandidate, ...],
    complete_branches: tuple[ExplicitSyntheticBattleBranch, ...],
    worker: LocalSimulatorWorker,
    preserve: str, stopper: str,
    linked_observer: Any | None = None,
    current_snapshot: Callable[[], FrozenBattleState] | None = None,
    reserve_seconds: float = 0.5,
    clock: Callable[[], float] = monotonic,
) -> ObservedFullGridDiagnostic:
    """Offline-only comparison of ALL observed hypotheses × engine p2 × p1.

    The complete synthetic Battles and opponent p2 slot mapping are always
    *independently supplied*, never constructed from missing OCR/PP/RNG.
    We first audit every hypothetical Battle against current OCR. We then
    enumerate p2 choices from each engine-issued request, without inferring
    that this is exhaustive Champions legality. Finally use the pre-existing
    one-turn comparator only as a NONACTIONABLE synthetic model preview.
    Any mismatch, missing choice, stale observation, deadline or unsupported
    worker result discards the whole preview (no partial best move).
    """
    if (not isinstance(snapshot, FrozenBattleState)
            or not isinstance(worker, LocalSimulatorWorker)
            or not isinstance(joint_candidates, tuple)
            or not isinstance(complete_branches, tuple)
            or not 1 <= len(joint_candidates) == len(complete_branches)
                   <= MAX_JOINT_RECORDS
            or not isinstance(preserve, str) or not preserve
            or not isinstance(stopper, str) or not stopper
            or isinstance(reserve_seconds, bool)
            or not isinstance(reserve_seconds, (int, float))
            or not 0 <= reserve_seconds <= 10
            or not callable(clock)
            or (current_snapshot is not None
                and not callable(current_snapshot))):
        return _no("INVALID_CURRENT_OBSERVED_GRID_REQUEST")
    if (snapshot.hypotheses
            or clock() + reserve_seconds >= snapshot.window.deadline_monotonic):
        return _no("PRE_REGISTERED_HYPOTHESES_OR_DEADLINE")
    original_hash = snapshot.snapshot_sha256

    def current_is_frozen() -> bool:
        if current_snapshot is None:
            return True
        try:
            return _same_window(snapshot, current_snapshot())
        except Exception:
            return False

    if not current_is_frozen():
        return _no("OBSERVATION_REVISION_CHANGED")
    frontier = stage_observed_opponent_action_frontier(
        snapshot, assets, joint_candidates=joint_candidates)
    expected = tuple(c.candidate_id for c in joint_candidates)
    if (frontier.status != "PROVISIONAL_OPPONENT_ACTION_NAMES"
            or frontier.ready_for_engine
            or frontier.exhaustive_legal_choices_verified
            or tuple(c.hypothesis_id for c in frontier.cases) != expected):
        return _no("SOURCE_BOUND_ACTION_FRONTIER_REFUSED:" + frontier.reason)

    # The #194 whole-batch worker receipts and observation comparison
    # must succeed before ANY simulation or opponent-choice enumeration.
    replay = compare_observed_to_synthetic_replays(
        snapshot, assets, joint_candidates=joint_candidates,
        complete_branches=complete_branches, worker=worker,
        linked_observer=linked_observer)
    if (replay.status != "SYNTHETIC_REPLAYS_MATCH_OBSERVED_CONSTRAINTS_ONLY"
            or replay.ranking_status != "NO_RANKING"
            or replay.ready_for_engine or replay.game_battle_authenticated
            or replay.opponent_mapping_authenticated
            or replay.full_state_reconstructed
            or replay.exhaustive_opponent_policy
            or replay.hypothesis_ids != expected
            or len(replay.battle_sha256) != len(expected)):
        return _no("OBSERVED_SYNTHETIC_READOUT_REFUSED:" + replay.reason)
    if not current_is_frozen():
        return _no("OBSERVATION_REVISION_CHANGED")

    intake = stage_sourced_joint_candidates(snapshot, joint_candidates)
    if (intake.status != "PROVISIONAL_JOINT_CANDIDATES"
            or intake.coverage != "NOT_PROVEN_EXHAUSTIVE"
            or tuple(h.hypothesis_id for h in intake.hypotheses) != expected
            or any(h.likelihood is not None for h in intake.hypotheses)):
        return _no("JOINT_SOURCE_INVALID:" + intake.reason)
    # This derived snapshot is private to the hypothetical worker: the
    # original OCR snapshot and current-revision supplier stay unchanged.
    hypothetical = replace(snapshot, hypotheses=intake.hypotheses)
    scenarios: list[ReplayOpponentScenario] = []
    counts: list[int] = []
    try:
        for case, expected_battle_sha in zip(complete_branches,
                                             replay.battle_sha256):
            if (not current_is_frozen()
                    or clock() + reserve_seconds >=
                       snapshot.window.deadline_monotonic):
                return _no("OBSERVATION_CHANGED_OR_DEADLINE")
            choices: EngineP2CandidateSet = worker.enumerate_p2_from_replay(
                hypothetical, case.complete_battle,
                hypothesis_id=case.hypothesis_id, assets=assets)
            if (not isinstance(choices, EngineP2CandidateSet)
                    or choices.status != "ENGINE_CHECKED_SIMPLE_CHOICES"
                    or choices.exhaustive_legal_choices_verified
                    or choices.battle_sha256 != fingerprint(
                        dict(case.complete_battle))
                    or not 1 <= len(choices.commands) <= 24
                    or len(set(choices.commands)) != len(choices.commands)
                    or any(not isinstance(c, str) or not c
                           for c in choices.commands)):
                return _no("INCOMPLETE_OR_UNBOUND_SYNTHETIC_P2_REQUEST")
            # The SHA of the original READOUT_REPLAY UTF-8 JSON remains
            # separate from the JSON-object fingerprint for ENUMERATE_P2.
            if not expected_battle_sha or len(expected_battle_sha) != 64:
                return _no("MISSING_READOUT_REPLAY_SHA")
            scenarios.append(ReplayOpponentScenario(
                case.hypothesis_id,
                canonical_json(dict(case.complete_battle)),
                choices.commands))
            counts.append(len(choices.commands))
        if sum(counts) * len(hypothetical.verified_action_ids) > MAX_BRANCHES:
            return _no("CROSS_PRODUCT_BRANCH_LIMIT")

        def current_hypothetical() -> FrozenBattleState:
            if not current_is_frozen():
                raise RuntimeError("current OCR revision changed")
            return hypothetical

        model = run_shadow_turn(
            hypothetical, assets, worker, tuple(scenarios),
            preserve=preserve, stopper=stopper,
            current_snapshot=current_hypothetical, reserve_seconds=reserve_seconds,
            clock=clock)
        if (model.status not in ("MODEL_PROVISIONAL",
                                "AMBIGUOUS_OPPONENT_POLICY")
                or model.evaluated_branch_count !=
                   sum(counts) * len(hypothetical.verified_action_ids)
                or not current_is_frozen()
                or clock() + reserve_seconds >=
                   snapshot.window.deadline_monotonic):
            return _no("SYNTHETIC_FULL_GRID_REFUSED:" + model.reason)
    except (WorkerUnavailable, TypeError, ValueError, AttributeError,
            KeyError, OverflowError, RecursionError):
        return _no("SYNTHETIC_P2_OR_TURN_WORKER_UNAVAILABLE")

    return ObservedFullGridDiagnostic(
        "SYNTHETIC_TURN_DIAGNOSTIC_ONLY",
        "UNVERIFIED_REAL_BATTLE_MAPPING_RNG_AND_OPPONENT_POLICY",
        snapshot.window.match_id, snapshot.window.decision_window_id,
        snapshot.window.state_revision, original_hash,
        replay.branch_batch_sha256, replay.battle_sha256,
        expected, tuple(counts), model.evaluated_branch_count,
        model.status, model.nondominated_action_ids,
    )
