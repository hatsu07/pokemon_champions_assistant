"""Source-bound opponent action *possibilities* from partial Champions facts.

Never emit a Showdown p2 command or claim legal/exhaustive coverage.
The existing pinned-engine ENUMERATE_P2 operation requires an independently
supplied, complete FULL_REPLAY_SYNTHETIC Battle with RNG; do not construct one
from a live-OCR frame, one opponent's revealed moves, or marginal usage data.
"""
from __future__ import annotations

from dataclasses import dataclass
from json import loads
from typing import Any

from .joint_set_intake import SourcedJointSetCandidate
from .models import FrozenBattleState, canonical_json, fingerprint
from .observed_calculation_scenarios import (
    ObservedScenarioBatch, stage_observed_calculation_scenarios,
)

_MAX_CASES = 16
_MAX_CHOICES = 7  # at most four moves and two bench possibilities + unknowns


@dataclass(frozen=True)
class OpponentPossibility:
    # These are *game-visible display names*, never simulator command IDs.
    action_kind: str
    display_name: str
    basis: str
    condition: str
    legal_status: str = "UNKNOWN"


@dataclass(frozen=True)
class OpponentHypothesisOptions:
    hypothesis_id: str | None
    source_evidence: tuple[str, ...]
    opponent_active_display: str
    # Each action is a conditional possibility, NOT a legal choice.
    possibilities: tuple[OpponentPossibility, ...] = ()
    unresolved: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpponentActionFrontier:
    status: str
    reason: str
    match_id: str = ""
    decision_window_id: str = ""
    state_revision: int = -1
    snapshot_sha256: str = ""
    possibilities_sha256: str = ""
    cases: tuple[OpponentHypothesisOptions, ...] = ()
    # Intentionally incompatible with ReplayOpponentScenario.
    p2_commands: tuple[str, ...] = ()
    exhaustive_legal_choices_verified: bool = False
    executable_battle_reconstructed: bool = False
    ready_for_engine: bool = False


def _blocked(reason: str) -> OpponentActionFrontier:
    return OpponentActionFrontier("NO_ACTION_FRONTIER", reason)


def stage_observed_opponent_action_frontier(
    snapshot: FrozenBattleState, assets: Any, *,
    joint_candidates: tuple[SourcedJointSetCandidate, ...] = (),
    observed_batch: ObservedScenarioBatch | None = None,
) -> OpponentActionFrontier:
    """Use the ENTIRE revalidated joint batch; never silently drop a case.

    A supplied stage-② batch must be byte-for-byte equal to a fresh staging
    of the same snapshot/assets/source bundles. This prevents independently
    edited 'partial' scenario fields or stale windows being accepted as a
    more favorable opponent policy.
    """
    if not isinstance(snapshot, FrozenBattleState):
        return _blocked("FROZEN_SNAPSHOT_REQUIRED")
    staged = stage_observed_calculation_scenarios(
        snapshot, assets, joint_candidates=joint_candidates)
    if (staged.status != "PARTIAL_SCENARIOS_NOT_RUNNABLE"
            or staged.ready_for_engine or staged.full_state_reconstructed
            or staged.exhaustive_opponent_policy):
        return _blocked("OBSERVED_SCENARIO_STAGE_REFUSED:" + staged.reason)
    if observed_batch is not None and (
            not isinstance(observed_batch, ObservedScenarioBatch)
            or observed_batch != staged):
        return _blocked("STAGED_SCENARIOS_STALE_OR_ALTERED")
    if (not 1 <= len(staged.scenarios) <= _MAX_CASES
            or staged.match_id != snapshot.window.match_id
            or staged.decision_window_id != snapshot.window.decision_window_id
            or staged.state_revision != snapshot.window.state_revision
            or staged.snapshot_sha256 != snapshot.snapshot_sha256):
        return _blocked("STAGED_SCENARIO_IDENTITY_INVALID")

    cases: list[OpponentHypothesisOptions] = []
    try:
        for scenario in staged.scenarios:
            if (scenario.runnable or scenario.battle_json is not None
                    or scenario.rng_state is not None or scenario.p2_commands):
                return _blocked("PARTIAL_SCENARIO_PROMOTED_TO_ENGINE")
            observed = loads(scenario.observed_constraints_json)
            if (not isinstance(observed, dict)
                    or observed.get("match_id") != staged.match_id
                    or observed.get("decision_window_id") != staged.decision_window_id
                    or observed.get("state_revision") != staged.state_revision
                    or observed.get("snapshot_sha256") != staged.snapshot_sha256):
                return _blocked("OBSERVED_CONSTRAINTS_NOT_BOUND_TO_WINDOW")
            active = observed.get("opponent_active")
            if active is None:
                return _blocked("OPPONENT_ACTIVE_NOT_OBSERVED")
            if (not isinstance(active, dict)
                    or not isinstance(active.get("name"), str)
                    or not active["name"]):
                return _blocked("INVALID_OPPONENT_ACTIVE")
            actor = active["name"]
            known = {actor: active}
            prior = observed.get("other_observed_opponents")
            if not isinstance(prior, list):
                return _blocked("INVALID_OBSERVED_OPPONENT_HISTORY")
            for row in prior:
                if (not isinstance(row, dict)
                        or not isinstance(row.get("name"), str)
                        or not row["name"] or row["name"] in known):
                    return _blocked("INVALID_OBSERVED_OPPONENT_HISTORY")
                known[row["name"]] = row
            if scenario.hypothesis_id is None:
                if (scenario.opponent_joint_assumptions_json is not None
                        or scenario.opponent_source_evidence):
                    return _blocked("UNKNOWN_CASE_HAS_INVENTED_SET")
                cases.append(OpponentHypothesisOptions(
                    None, (), actor, (),
                    ("OPPONENT_JOINT_SET_UNKNOWN",
                     "MOVE_AND_SWITCH_CHOICES_UNKNOWN",
                     "OPPONENT_MOVE_PP_AND_TRAP_STATUS_UNKNOWN",
                     "SHOWDOWN_REQUEST_AND_RNG_MISSING"),
                ))
                continue
            if not scenario.opponent_source_evidence:
                return _blocked("JOINT_CASE_SOURCE_MISSING")
            joint = loads(scenario.opponent_joint_assumptions_json)
            if (not isinstance(joint, dict) or set(joint) != {"opponent_sets"}
                    or not isinstance(joint["opponent_sets"], dict)
                    or len(joint["opponent_sets"]) != 3
                    or actor not in joint["opponent_sets"]):
                return _blocked("JOINT_CASE_ACTOR_OR_PARTY_MISMATCH")
            roster = joint["opponent_sets"]
            member = roster[actor]
            if (not isinstance(member, dict)
                    or not isinstance(member.get("moves"), list)
                    or not 1 <= len(member["moves"]) <= 4):
                return _blocked("JOINT_CASE_MOVES_MISSING")
            visible = active.get("observed_moves_display")
            if not isinstance(visible, list) or any(
                    not isinstance(m, str) for m in visible):
                return _blocked("OBSERVED_MOVE_LIST_INVALID")
            if any(m not in member["moves"] for m in visible):
                return _blocked("JOINT_CASE_CONTRADICTS_REVEALED_MOVE")
            action_options: list[OpponentPossibility] = []
            if active.get("fainted") is not True:
                for move in member["moves"]:
                    if not isinstance(move, str) or not move:
                        return _blocked("JOINT_MOVE_NAME_INVALID")
                    action_options.append(OpponentPossibility(
                        "MOVE_NAME", move,
                        ("OBSERVED_MOVE_AND_JOINT" if move in visible
                         else "JOINT_SOURCE_UNREVEALED_MOVE"),
                        "PP_DISABLE_LOCK_PRIORITY_UNVERIFIED",
                    ))
            for bench in roster:
                if bench == actor:
                    continue
                info = known.get(bench, {})
                if info.get("fainted") is True:
                    continue
                action_options.append(OpponentPossibility(
                    "SWITCH_TARGET_NAME", bench, "CO_RECORDED_JOINT_THREE",
                    ("BENCH_ALIVE_NOT_VERIFIED"
                     if info.get("fainted") is None
                     else "TRAP_SWITCH_LEGALITY_UNVERIFIED"),
                ))
            if len(action_options) > _MAX_CHOICES:
                return _blocked("OPPONENT_OPTION_BUDGET_EXCEEDED")
            cases.append(OpponentHypothesisOptions(
                scenario.hypothesis_id, scenario.opponent_source_evidence,
                actor, tuple(action_options),
                ("NOT_EXHAUSTIVE_OPPONENT_POLICY",
                 "OPPONENT_CURRENT_PP_DISABLE_AND_LOCK_UNKNOWN",
                 "SWITCH_TRAP_MEGA_AND_SPECIAL_ACTIONS_UNKNOWN",
                 "SHOWDOWN_REQUEST_AND_RNG_MISSING",
                 "CHAMPIONS_PARITY_NOT_VERIFIED"),
            ))
    except (ValueError, TypeError, KeyError, AttributeError):
        return _blocked("INVALID_PARTIAL_SCENARIO_CONTENT")

    return OpponentActionFrontier(
        "PROVISIONAL_OPPONENT_ACTION_NAMES",
        "SOURCED_JOINT_AND_OBSERVED_CONSTRAINTS_NOT_LEGAL_CHOICES",
        staged.match_id, staged.decision_window_id, staged.state_revision,
        staged.snapshot_sha256,
        fingerprint({
            "snapshot": staged.snapshot_sha256,
            "cases": [
                {"id": c.hypothesis_id, "source": list(c.source_evidence),
                 "active": c.opponent_active_display,
                 "options": [
                     (x.action_kind, x.display_name, x.basis, x.condition)
                     for x in c.possibilities
                 ], "unresolved": list(c.unresolved)}
                for c in cases
            ],
        }),
        tuple(cases),
    )
