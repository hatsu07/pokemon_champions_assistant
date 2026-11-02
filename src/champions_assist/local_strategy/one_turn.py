"""Phase 2 one-turn Showdown branch comparison (shadow-only, ordinal).

All evaluated candidates must use exactly the same joint opponent
hypothesis/choice scenarios. No arbitrary independence assumption, fake
likelihood, winner probability, opponent clairvoyance or missing branch
promotion. Results here are model predictions, never game observations.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from time import monotonic
import json
from typing import Any

from .legal_actions import generate_candidates, showdown_choice_for_candidate
from .models import (
    CapabilityStatus, FrozenBattleState, LegalStatus, PROTOCOL_VERSION,
    SimulationResult, fingerprint, joint_hypothesis_ids,
)
from .simulator_adapter import _verified_roster_binding, WorkerUnavailable
from .opponent_evidence_gate import check_observed_opponent_hypotheses


@dataclass(frozen=True)
class BranchObservation:
    candidate_action_id: str
    hypothesis_id: str
    opponent_choice_id: str
    result: SimulationResult


@dataclass(frozen=True)
class ShadowTurnRanking:
    status: str
    reason: str
    best_action_id: str = ""
    nondominated_action_ids: tuple[str, ...] = ()
    evaluated_branch_count: int = 0
    # These comparisons are ordinal and must not be displayed as win rates.
    ordinal_by_candidate: tuple[tuple[str, tuple[tuple[int, ...], ...]], ...] = ()


def _parse_state(next_state: Any, own_species: tuple[str, ...]
                 ) -> tuple[tuple[tuple[str, int, bool], ...],
                            tuple[tuple[str, int, bool], ...]]:
    if not isinstance(next_state, dict):
        raise ValueError("MISSING_SHOWDOWN_NEXT_STATE")
    sides = next_state.get("side_hp")
    if (not isinstance(sides, list) or len(sides) != 2
            or not all(isinstance(side, list) and len(side) == 3
                       for side in sides)):
        raise ValueError("INCOMPLETE_THREE_V_THREE_RESULT")
    parsed = []
    for side in sides:
        members = []
        for member in side:
            if not isinstance(member, dict):
                raise ValueError("INVALID_HP_ROW")
            species, hp, fainted = (
                member.get("species"), member.get("hp"), member.get("fainted"))
            if (not isinstance(species, str) or not species
                    or not isinstance(hp, (int, float)) or isinstance(hp, bool)
                    or not isfinite(hp) or hp < 0 or int(hp) != hp
                    or not isinstance(fainted, bool)
                    or fainted != (hp == 0)):
                raise ValueError("MALFORMED_OR_CONTRADICTORY_HP")
            members.append((species, int(hp), fainted))
        parsed.append(tuple(members))
    if tuple(mon[0] for mon in parsed[0]) != own_species:
        raise ValueError("OWN_ROSTER_REORDERED")
    return parsed[0], parsed[1]


def _ordinal(own: tuple[tuple[str, int, bool], ...],
             opponent: tuple[tuple[str, int, bool], ...],
             preserve_index: int, stopper_index: int) -> tuple[int, ...]:
    own_alive = sum(not row[2] for row in own)
    other_alive = sum(not row[2] for row in opponent)
    # A simultaneous last-mon KO is not victory; a real remaining Pokémon
    # capable of winning matters more than damage while losing.
    terminal = 2 if other_alive == 0 and own_alive > 0 else (
        -2 if own_alive == 0 else 0)
    return (
        terminal,
        int(not own[stopper_index][2]),
        int(not own[preserve_index][2]),
        own_alive,
        -other_alive,
        sum(row[1] for row in own),
        -sum(row[1] for row in opponent),
    )


def compare_shadow_turn(snapshot: FrozenBattleState, assets: Any,
                        branches: tuple[BranchObservation, ...],
                        *, preserve: str, stopper: str,
                        now: float | None = None) -> ShadowTurnRanking:
    """Determine dominance only, with no fabricated opponent policy.

    If two actions trade off on different opponent choices, both remain
    nondominated and this function declines to choose. This is preferable to
    treating the opponent as if it sees our chosen move first or inventing
    independent probabilities for unknown ability/item/move combinations.
    """
    clock = monotonic() if now is None else now
    if clock >= snapshot.window.deadline_monotonic:
        return ShadowTurnRanking("NO_RANKING", "INPUT_DEADLINE_PASSED")
    try:
        roster = _verified_roster_binding(snapshot, assets)
    except WorkerUnavailable as exc:
        return ShadowTurnRanking("NO_RANKING", str(exc))
    order = tuple(item["name"] for item in roster)
    species = tuple(item["species_id"] for item in roster)
    if (preserve not in order or stopper not in order
            or len(branches) > 240):
        return ShadowTurnRanking("NO_RANKING", "ROLES_OR_BRANCH_BUDGET_INVALID")
    actions = {candidate.action_id: candidate
               for candidate in generate_candidates(snapshot, assets)
               if candidate.legal_status == LegalStatus.LEGAL}
    if not actions:
        return ShadowTurnRanking("NO_RANKING", "NO_POSITIVELY_LEGAL_ACTIONS")
    if not branches:
        return ShadowTurnRanking("NO_RANKING", "NO_SIMULATED_BRANCHES")
    # Do not collapse distinct joint conditions carrying duplicate IDs.
    # The comparator is public and may be called without the orchestrator.
    try:
        expected_hypotheses = set(joint_hypothesis_ids(snapshot.hypotheses))
    except ValueError:
        return ShadowTurnRanking("NO_RANKING", "DUPLICATE_OR_INVALID_HYPOTHESIS_ID")
    evidence_gate = check_observed_opponent_hypotheses(snapshot)
    if evidence_gate.status != "CLEAR":
        return ShadowTurnRanking("NO_RANKING", evidence_gate.reason)
    scenarios: dict[str, dict[tuple[str, str], tuple[int, ...]]] = {
        action_id: {} for action_id in actions
    }
    p2_order: dict[tuple[str, str], tuple[str, ...]] = {}
    # Labels alone are NOT scenario evidence. Compare actual submitted
    # p2_choice and exact full starting Battle JSON (including its RNG state)
    # across the actions. All choices within one opponent hypothesis must
    # start from the same Battle snapshot.
    scenario_input: dict[tuple[str, str], tuple[str, str]] = {}
    initial_battle_by_hypothesis: dict[str, str] = {}
    used_branch_requests = set()
    for branch in branches:
        result = branch.result
        key = (branch.hypothesis_id, branch.opponent_choice_id)
        if (branch.candidate_action_id not in actions
                or branch.hypothesis_id not in expected_hypotheses
                or not branch.opponent_choice_id
                or key in scenarios[branch.candidate_action_id]):
            return ShadowTurnRanking("NO_RANKING", "INVALID_OR_DUPLICATE_SCENARIO")
        try:
            bound = json.loads(result.identity_json)
            payload = json.loads(result.payload_json)
            submitted = json.loads(result.submitted_request_json)
        except (ValueError, TypeError):
            return ShadowTurnRanking("NO_RANKING", "INVALID_BRANCH_PAYLOAD")
        if (not isinstance(submitted, dict)
                or submitted.get("protocol") != PROTOCOL_VERSION
                or submitted.get("op") != "SIMULATE"
                or submitted.get("request_id") != snapshot.window.request_id
                or submitted.get("snapshot_sha256") != snapshot.snapshot_sha256
                or submitted.get("identity") != snapshot.window.identity()
                or submitted.get("branch_request_id") != result.branch_request_id
                or not isinstance(submitted.get("payload"), dict)):
            return ShadowTurnRanking("NO_RANKING", "MISSING_OR_UNBOUND_SUBMISSION")
        request = submitted["payload"]
        # Scenario identity is the *actual Showdown opponent command*, not
        # a caller-written semantic label such as "attack" or "switch".
        if branch.opponent_choice_id != request.get("p2_choice"):
            return ShadowTurnRanking("NO_RANKING", "OPPONENT_CHOICE_NOT_SUBMITTED")
        if (request.get("source") != "FULL_REPLAY_SYNTHETIC"
                or request.get("hypothesis_id") != branch.hypothesis_id
                or request.get("candidate_action_id") != branch.candidate_action_id
                or request.get("expected_p1_roster") != roster
                or request.get("expected_p1_active") != snapshot.window.active_pokemon
                or not isinstance(request.get("p2_choice"), str)
                or not request["p2_choice"]
                or not isinstance(request.get("battle"), dict)
                or not isinstance(request.get("branch_input_sha256"), str)
                or request["branch_input_sha256"] != result.branch_input_sha256
                or request["branch_input_sha256"] != fingerprint({
                    key: value for key, value in request.items()
                    if key != "branch_input_sha256"
                })):
            return ShadowTurnRanking("NO_RANKING", "SUBMITTED_BRANCH_HASH_OR_INPUT_MISMATCH")
        try:
            expected_p1_choice = showdown_choice_for_candidate(
                actions[branch.candidate_action_id], snapshot, assets)
        except (ValueError, TypeError, KeyError):
            return ShadowTurnRanking("NO_RANKING", "ACTION_SLOT_NOT_BOUND")
        if request.get("p1_choice") != expected_p1_choice:
            return ShadowTurnRanking("NO_RANKING", "ACTION_SLOT_NOT_BOUND")
        starting_battle_sha = fingerprint(request["battle"])
        compared_scenario = (request["p2_choice"], starting_battle_sha)
        if (key in scenario_input
                and scenario_input[key] != compared_scenario):
            return ShadowTurnRanking("NO_RANKING", "SCENARIO_INPUT_MISMATCH")
        if (branch.hypothesis_id in initial_battle_by_hypothesis
                and initial_battle_by_hypothesis[branch.hypothesis_id]
                    != starting_battle_sha):
            return ShadowTurnRanking("NO_RANKING", "SCENARIO_INPUT_MISMATCH")
        scenario_input[key] = compared_scenario
        initial_battle_by_hypothesis[branch.hypothesis_id] = starting_battle_sha
        if (result.capability_status != CapabilityStatus.PARTIAL
                or result.request_id != snapshot.window.request_id
                or result.snapshot_sha256 != snapshot.snapshot_sha256
                or bound != snapshot.window.identity()
                or result.hypothesis_id != branch.hypothesis_id
                or result.candidate_action_id != branch.candidate_action_id
                or not result.branch_request_id
                or result.branch_request_id in used_branch_requests
                or len(result.branch_input_sha256) != 64):
            return ShadowTurnRanking("NO_RANKING", "STALE_OR_UNBOUND_BRANCH")
        used_branch_requests.add(result.branch_request_id)
        try:
            own, opponent = _parse_state(payload.get("next"), species)
        except ValueError as exc:
            return ShadowTurnRanking("NO_RANKING", str(exc))
        theirs = tuple(row[0] for row in opponent)
        if key in p2_order and theirs != p2_order[key]:
            return ShadowTurnRanking("NO_RANKING", "OPPONENT_ROSTER_DIFFERS_ACROSS_CHOICES")
        p2_order[key] = theirs
        scenarios[branch.candidate_action_id][key] = _ordinal(
            own, opponent, order.index(preserve), order.index(stopper))
    # Every candidate must be evaluated on exactly the same set of joint
    # hypotheses/choices; otherwise a favorable omission could win.
    coverage = set(next(iter(scenarios.values())))
    if (not coverage or {hyp for hyp, _ in coverage} != expected_hypotheses
            or any(set(data) != coverage for data in scenarios.values())):
        return ShadowTurnRanking("NO_RANKING", "UNEQUAL_OR_INCOMPLETE_SCENARIO_COVERAGE")
    nondominated = []
    for action, outcomes in scenarios.items():
        dominated = any(
            all(other[key] >= outcomes[key] for key in coverage)
            and any(other[key] > outcomes[key] for key in coverage)
            for other_action, other in scenarios.items()
            if other_action != action
        )
        if not dominated:
            nondominated.append(action)
    nondominated.sort()
    # These are predictions produced under the Showdown implementation
    # assumption. No result is adopted as a real-game CONFIRMED result.
    chosen = nondominated[0] if len(nondominated) == 1 else ""
    return ShadowTurnRanking(
        "MODEL_PROVISIONAL" if chosen else "AMBIGUOUS_OPPONENT_POLICY",
        "JOINT_SCENARIO_DOMINANCE_NOT_WIN_PROBABILITY",
        chosen, tuple(nondominated), len(branches),
        tuple(sorted((action, tuple(data[key] for key in sorted(coverage)))
                     for action, data in scenarios.items())),
    )
