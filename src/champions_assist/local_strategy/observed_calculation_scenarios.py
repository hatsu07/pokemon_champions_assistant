"""Conservative Champions->Showdown calculation *scenario inputs*.

The output is a bounded set of typed **partial constraints**, not a runnable
Showdown Battle/RNG. Real Champions observations do not identify the unseen
party, hidden joint sets, action order, simulator RNG or the event queue.
An actual full engine battle requires a separately audited constructor; do
not pass these envelopes to run_shadow_turn/worker SIMULATE.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .joint_set_intake import SourcedJointSetCandidate, stage_sourced_joint_candidates
from .models import FrozenBattleState, canonical_json
from .observed_id_bridge import ObservationIdStage, stage_observed_champions_ids


@dataclass(frozen=True)
class ObservedCalculationScenario:
    hypothesis_id: str | None
    opponent_joint_assumptions_json: str | None
    opponent_source_evidence: tuple[str, ...]
    # Immutable JSON of only the frozen ID bridge's explicit visible facts.
    observed_constraints_json: str
    unknowns: tuple[str, ...]
    battle_json: None = None
    rng_state: None = None
    p2_commands: tuple[str, ...] = ()
    runnable: bool = False


@dataclass(frozen=True)
class ObservedScenarioBatch:
    status: str
    reason: str
    match_id: str = ""
    decision_window_id: str = ""
    state_revision: int = -1
    snapshot_sha256: str = ""
    scenarios: tuple[ObservedCalculationScenario, ...] = ()
    # Not transferable to FULL_REPLAY_SYNTHETIC or the shadow runner.
    full_state_reconstructed: bool = False
    exhaustive_opponent_policy: bool = False
    ready_for_engine: bool = False


def stage_observed_calculation_scenarios(
    snapshot: FrozenBattleState, assets: Any, *,
    joint_candidates: tuple[SourcedJointSetCandidate, ...] = (),
) -> ObservedScenarioBatch:
    """Stage bounded inputs and keep unknowns unknown.

    An empty sourced candidate set yields one *unknown opponent* case,
    never an invented default set. Source-linked candidate sets must
    independently pass the existing full-batch source/contradiction gate.
    The registered own three use only frozen explicit Champions->Showdown
    IDs. No Japanese opponent name is guessed into an engine ID.
    """
    if not isinstance(snapshot, FrozenBattleState):
        return ObservedScenarioBatch("NO_SCENARIOS", "FROZEN_SNAPSHOT_REQUIRED")
    stage: ObservationIdStage = stage_observed_champions_ids(snapshot, assets)
    if stage.status != "PARTIAL_OBSERVATION_ID_STAGE":
        return ObservedScenarioBatch("NO_SCENARIOS", stage.reason)
    if not isinstance(joint_candidates, tuple):
        return ObservedScenarioBatch("NO_SCENARIOS", "INVALID_JOINT_SET_BATCH")
    if snapshot.hypotheses and joint_candidates:
        return ObservedScenarioBatch("NO_SCENARIOS", "CONFLICTING_HYPOTHESIS_SOURCES")
    if snapshot.hypotheses:
        # Do not promote arbitrary caller-provided hypotheses to curated
        # game data without a fresh source/regulation/observation audit.
        return ObservedScenarioBatch("NO_SCENARIOS", "UNATTESTED_EXISTING_HYPOTHESES")
    admitted = ()
    if joint_candidates:
        checked = stage_sourced_joint_candidates(snapshot, joint_candidates)
        if checked.status != "PROVISIONAL_JOINT_CANDIDATES":
            return ObservedScenarioBatch("NO_SCENARIOS",
                                         "JOINT_CANDIDATES_" + checked.reason)
        admitted = checked.hypotheses
    # Preserve only ID stage's typed original observations. No synthesized
    # HP, PP, status or empty set defaults.
    def member(m: Any) -> dict[str, Any]:
        return {
            "name": m.display_name,
            "side": m.side,
            "own_slot": m.own_party_slot,
            "registered_species_id": m.registered_species_id,
            "registered_move_ids": list(map(list, m.registered_move_ids)),
            "observed_move_ids": list(m.revealed_move_ids),
            "observed_moves_display": list(m.observed_moves_display),
            "hp": m.hp, "maxhp": m.maxhp, "fainted": m.fainted,
            "boosts": list(map(list, m.boosts)),
            "raw_status": m.raw_status,
            "raw_type": m.raw_current_type,
            "raw_item": m.raw_item,
            "raw_ability": m.raw_ability,
            "raw_form": m.raw_form,
        }
    constraints = canonical_json({
        "match_id": stage.match_id,
        "decision_window_id": stage.decision_window_id,
        "state_revision": stage.state_revision,
        "snapshot_sha256": stage.snapshot_sha256,
        "own_active_slot": stage.own_active_slot,
        "own": [member(m) for m in stage.own_members],
        "opponent_active": member(stage.opponent) if stage.opponent else None,
        "other_observed_opponents": [
            member(m) for m in stage.other_observed_opponents
        ],
    })
    cases = admitted or (None,)
    scenarios = tuple(ObservedCalculationScenario(
        hypothesis_id=h.hypothesis_id if h else None,
        opponent_joint_assumptions_json=h.assumptions_json if h else None,
        opponent_source_evidence=h.evidence_refs if h else (),
        observed_constraints_json=constraints,
        unknowns=(*stage.unbound, "GAME_OBSERVATION_SOURCE_NOT_AUTHENTICATED",
                  "REAL_ENGINE_BATTLE_NOT_RECONSTRUCTED",
                  "SIMULATOR_RNG_NOT_OBSERVED",
                  "OPPONENT_ACTION_COVERAGE_NOT_PROVEN"),
    ) for h in cases)
    return ObservedScenarioBatch(
        "PARTIAL_SCENARIOS_NOT_RUNNABLE",
        "VISIBLE_OBSERVATIONS_AND_OPTIONAL_SOURCED_JOINT_HYPOTHESES",
        stage.match_id, stage.decision_window_id, stage.state_revision,
        stage.snapshot_sha256, scenarios,
    )


def stage_linked_observer_calculation_scenarios(
    linked_observer: Any, snapshot: FrozenBattleState,
    assets: Any, *,
    joint_candidates: tuple[SourcedJointSetCandidate, ...] = (),
) -> ObservedScenarioBatch:
    """Join stage ① with stage ② only for the EXACT frozen OCR state.

    The window owner must independently freeze the current match state with
    its real current actor, screen, revision and legal actions. We never
    construct a guessed DecisionWindow or equate the OCR reducer revision
    with the FSM tactical revision. Matching JSON bytes and proven same
    in-process source identity are mandatory, but neither is a proof of
    real-game OCR/source authenticity.
    """
    from ..observed_capture_bridge import LinkedObserverState
    if (not isinstance(linked_observer, LinkedObserverState)
            or not isinstance(snapshot, FrozenBattleState)
            or linked_observer.assets is not assets
            or snapshot.window.match_id != linked_observer.match_id
            or linked_observer.last_capture_seq < 0):
        return ObservedScenarioBatch("NO_SCENARIOS",
                                     "LINKED_CAPTURE_MATCH_OR_ASSETS_UNAVAILABLE")
    state = snapshot.state
    try:
        if (state.get("match_id") != linked_observer.match_id
                or canonical_json(state) != canonical_json(linked_observer.state)
                or state.get("our_active") != snapshot.window.active_pokemon):
            return ObservedScenarioBatch("NO_SCENARIOS",
                                         "LINKED_OBSERVATION_CHANGED_OR_STALE")
        provenance = state.get("battle_evidence", {}).get("update_provenance", [])
        if (not isinstance(provenance, list) or not any(
                isinstance(p, dict)
                and isinstance(p.get("claimed_frame_link"), dict)
                and p["claimed_frame_link"].get("match_id") == linked_observer.match_id
                and p["claimed_frame_link"].get("raw_observation_id")
                in linked_observer.linked_raw_observation_ids
                and p["claimed_frame_link"].get("ready_for_engine") is False
                for p in provenance)):
            return ObservedScenarioBatch("NO_SCENARIOS",
                                         "NO_EXPLICIT_LINKED_OCR_FACT")
    except (TypeError, ValueError, AttributeError):
        return ObservedScenarioBatch("NO_SCENARIOS",
                                     "INVALID_LINKED_OBSERVATION_STATE")
    return stage_observed_calculation_scenarios(
        snapshot, assets, joint_candidates=joint_candidates)
