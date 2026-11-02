"""Issue #141(②): fail-closed observed-state → *constraint-only* branch gate.

Preserves the entire current frozen OCR observation + every co-recorded joint
opponent hypothesis without manufacturing Showdown Battle/RNG or evaluating
a tactical recommendation. All accepted cases are diagnostic, nonrunnable
and explicitly NO_RANKING; no branch may be silently omitted.

A staged frozen observation is not independent proof of actual game capture.
Use linked_observer to enforce the exact linked OCR reducer/window contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from json import loads
from typing import Any

from .joint_set_intake import MAX_JOINT_RECORDS, SourcedJointSetCandidate
from .models import FrozenBattleState, fingerprint
from .observed_calculation_scenarios import (
    ObservedScenarioBatch, stage_observed_calculation_scenarios,
    stage_linked_observer_calculation_scenarios,
)

_COMMON_UNVERIFIED = (
    "ACTUAL_CHAMPIONS_BATTLE_ORIGIN_NOT_AUTHENTICATED",
    "OPPONENT_POLICY_NOT_PROVEN_EXHAUSTIVE",
    "ENGINE_EFFECTS_EVENT_ORDER_AND_MEGA_HISTORY_UNKNOWN",
    "SIMULATOR_RNG_NOT_OBSERVED",
    "CHAMPIONS_SHOWDOWN_MECHANICS_PARITY_NOT_VERIFIED",
)


@dataclass(frozen=True)
class ObservedSafeBranch:
    hypothesis_id: str | None
    opponent_source_evidence: tuple[str, ...]
    # The source-bound joint bundle stays a literal assumption, not a fact.
    opponent_joint_assumptions_json: str | None
    observed_constraints_json: str
    known_exact_fields: tuple[str, ...]
    unresolved: tuple[str, ...]
    source_case_sha256: str
    battle_json: None = None
    rng_state: None = None
    p2_commands: tuple[str, ...] = ()
    executable: bool = False
    ranking_status: str = "NO_RANKING"


@dataclass(frozen=True)
class ObservedSafeBranchGate:
    status: str
    reason: str
    match_id: str = ""
    decision_window_id: str = ""
    state_revision: int = -1
    snapshot_sha256: str = ""
    branch_batch_sha256: str = ""
    branches: tuple[ObservedSafeBranch, ...] = ()
    # Absence of a source-complete list is not simulated away.
    opponent_hypothesis_coverage: str = "NOT_PROVEN_EXHAUSTIVE"
    current_game_capture_authenticated: bool = False
    full_state_reconstructed: bool = False
    ready_for_engine: bool = False
    ranking_status: str = "NO_RANKING"


def _no(reason: str) -> ObservedSafeBranchGate:
    return ObservedSafeBranchGate("NO_OBSERVED_BRANCHES", reason)


def _inspect_member(member: Any, side: str, label: str,
                    known: list[str], unknown: list[str]) -> None:
    """Only inspect keys validated by the existing frozen ID stage.

    Missing HP, PP and boosts are UNKNOWN. Empty boost tuples do *not*
    stand for all-zero stages. Raw percentages must not become exact HP.
    """
    if not isinstance(member, dict) or member.get("side") != side:
        raise ValueError("INVALID_OBSERVED_MEMBER")
    name = member.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("INVALID_OBSERVED_MEMBER")
    prefix = label + ":" + name
    if side == "own":
        if not member.get("registered_species_id") or not member.get("registered_move_ids"):
            raise ValueError("FROZEN_OWN_IDENTITIES_NOT_VERIFIED")
        known.extend((prefix + ".species_id", prefix + ".registered_move_ids"))
    else:
        if member.get("registered_species_id") is not None or member.get("registered_move_ids"):
            raise ValueError("UNVERIFIED_OPPONENT_ENGINE_ID")
        unknown.extend((prefix + ".species_id", prefix + ".move_ids",
                        prefix + ".party_slot"))
    for field in ("hp", "maxhp", "fainted"):
        value = member.get(field)
        if value is None:
            unknown.append(prefix + "." + field)
        else:
            known.append(prefix + "." + field)
    if member.get("raw_hp_percent") is not None:
        unknown.append(prefix + ".raw_percent_not_exact_hp")
    boosts = member.get("boosts")
    if not isinstance(boosts, list):
        raise ValueError("INVALID_OBSERVED_BOOSTS")
    if boosts:
        for row in boosts:
            if not isinstance(row, list) or len(row) != 2:
                raise ValueError("INVALID_OBSERVED_BOOSTS")
            known.append(prefix + ".boost." + row[0])
        # Only these *observed* stages are known. Remaining stages
        # cannot be silently set to 0.
        unknown.append(prefix + ".unobserved_boost_stages")
    else:
        unknown.append(prefix + ".all_boost_stages")
    for field in ("raw_status", "raw_type", "raw_item", "raw_ability",
                  "raw_form"):
        # A displayed literal is observed but is not an authenticated
        # Showdown ID nor proof of the corresponding hidden state.
        if member.get(field) is not None:
            known.append(prefix + "." + field + "_literal")
        unknown.append(prefix + "." + field + "_engine_mapping")
    if member.get("observed_moves_display"):
        known.append(prefix + ".observed_move_names")
    unknown.append(prefix + ".current_pp_lock_disable")
    unknown.append(prefix + ".hidden_moves_and_conditions")


def stage_safe_observed_branches(
    snapshot: FrozenBattleState, assets: Any, *,
    joint_candidates: tuple[SourcedJointSetCandidate, ...] = (),
    linked_observer: Any | None = None,
    observed_batch: ObservedScenarioBatch | None = None,
    max_branches: int = MAX_JOINT_RECORDS,
) -> ObservedSafeBranchGate:
    """Produce a bounded *diagnostic* set, never a runnable calculation.

    Every source-linked hypothesis is retained in order. A missing joint
    source yields ONE expressly unknown-opponent case. An invalid or stale
    branch, linked OCR revision or input budget rejects the WHOLE batch.
    In particular, no partial full Battle, RNG or 'fallback' hypothesis is
    synthesized. All successful staging still has ranking=NO_RANKING.
    """
    if (not isinstance(snapshot, FrozenBattleState)
            or type(max_branches) is not int
            or not 1 <= max_branches <= MAX_JOINT_RECORDS
            or not isinstance(joint_candidates, tuple)
            or len(joint_candidates) > max_branches):
        return _no("INVALID_OR_OVER_BUDGET_BRANCH_REQUEST")
    if linked_observer is None:
        staged = stage_observed_calculation_scenarios(
            snapshot, assets, joint_candidates=joint_candidates)
    else:
        staged = stage_linked_observer_calculation_scenarios(
            linked_observer, snapshot, assets, joint_candidates=joint_candidates)
    if staged.status != "PARTIAL_SCENARIOS_NOT_RUNNABLE":
        return _no("OBSERVATION_STAGE_" + staged.reason)
    if (staged.ready_for_engine or staged.full_state_reconstructed
            or staged.exhaustive_opponent_policy
            or not 1 <= len(staged.scenarios) <= max_branches):
        return _no("UNSAFE_OR_OVER_BUDGET_OBSERVED_BATCH")
    if observed_batch is not None and (
            not isinstance(observed_batch, ObservedScenarioBatch)
            or observed_batch != staged):
        return _no("STALE_OR_MODIFIED_OBSERVED_BATCH")
    try:
        cases: list[ObservedSafeBranch] = []
        for scenario in staged.scenarios:
            if (scenario.battle_json is not None
                    or scenario.rng_state is not None
                    or scenario.p2_commands or scenario.runnable
                    or not isinstance(scenario.observed_constraints_json, str)):
                return _no("OBSERVED_BRANCH_WAS_PROMOTED")
            observed = loads(scenario.observed_constraints_json)
            if (observed["match_id"] != staged.match_id
                    or observed["decision_window_id"] != staged.decision_window_id
                    or observed["state_revision"] != staged.state_revision
                    or observed["snapshot_sha256"] != staged.snapshot_sha256):
                return _no("OBSERVED_BRANCH_WINDOW_MISMATCH")
            known: list[str] = []
            unknown: list[str] = list(_COMMON_UNVERIFIED)
            own = observed["own"]
            if not isinstance(own, list) or len(own) != 3:
                return _no("INCOMPLETE_FROZEN_OWN_PARTY")
            for i, member in enumerate(own, 1):
                _inspect_member(member, "own", "p1:" + str(i),
                                known, unknown)
            opponent = observed["opponent_active"]
            if opponent is None:
                unknown.append("p2:active.name_and_entire_state")
            else:
                _inspect_member(opponent, "opponent", "p2:active",
                                known, unknown)
            historical = observed["other_observed_opponents"]
            if not isinstance(historical, list):
                return _no("INVALID_OBSERVED_OPPONENT_HISTORY")
            for j, member in enumerate(historical, 1):
                _inspect_member(member, "opponent", "p2:history:" + str(j),
                                known, unknown)
                unknown.append("p2:history:" + str(j) + ".current_presence_unverified")
            if scenario.hypothesis_id is None:
                if scenario.opponent_joint_assumptions_json is not None:
                    return _no("UNSOURCED_JOINT_ASSUMPTIONS")
                unknown.append("opponent_joint_three_and_all_hidden_sets")
            else:
                if (not scenario.opponent_source_evidence
                        or not isinstance(scenario.opponent_joint_assumptions_json, str)):
                    return _no("UNSOURCED_JOINT_ASSUMPTIONS")
                loads(scenario.opponent_joint_assumptions_json)
                unknown.append("opponent_joint_claim_not_exhaustive_or_authenticated")
            unknown.extend(scenario.unknowns)
            source_case_sha256 = fingerprint({
                "hypothesis_id": scenario.hypothesis_id,
                "source_evidence": scenario.opponent_source_evidence,
                "joint": scenario.opponent_joint_assumptions_json,
                "observed": scenario.observed_constraints_json,
                "unresolved": list(dict.fromkeys(unknown)),
            })
            cases.append(ObservedSafeBranch(
                scenario.hypothesis_id, scenario.opponent_source_evidence,
                scenario.opponent_joint_assumptions_json,
                scenario.observed_constraints_json,
                tuple(dict.fromkeys(known)), tuple(dict.fromkeys(unknown)),
                source_case_sha256,
            ))
        if not cases:
            return _no("NO_OBSERVED_BRANCH_CASES")
        digest = fingerprint({
            "match_id": staged.match_id,
            "window": staged.decision_window_id,
            "revision": staged.state_revision,
            "snapshot": staged.snapshot_sha256,
            "branches": [c.source_case_sha256 for c in cases],
        })
        return ObservedSafeBranchGate(
            "PROVISIONAL_OBSERVED_BRANCH_CONSTRAINTS",
            "NO_RANKING_PARTIAL_OBSERVATION_AND_UNVERIFIED_RNG",
            staged.match_id, staged.decision_window_id,
            staged.state_revision, staged.snapshot_sha256,
            digest, tuple(cases),
        )
    except (ValueError, TypeError, AttributeError, KeyError, IndexError):
        return _no("MALFORMED_PARTIAL_OBSERVED_BRANCH")
