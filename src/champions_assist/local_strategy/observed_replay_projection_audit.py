"""Fail-closed comparison of *claimed* engine replay projections with observed facts.

This is a detached, read-only study of the gap between an observed Champions
window and an externally supplied complete Showdown replay. The projection's
origin, actual Battle JSON, request, RNG and p2-name/engine-ID mapping cannot
be authenticated by a Python dataclass. Even matching ALL explicitly
comparable facts NEVER authorizes SIMULATE, ENUMERATE_P2 or LIVE.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .joint_set_intake import SourcedJointSetCandidate
from .models import FrozenBattleState, fingerprint
from .observed_calculation_scenarios import stage_observed_calculation_scenarios

_ID = re.compile(r"[a-z0-9]+\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_SCOPE = "EXTERNAL_REPLAY_PROJECTION_UNATTESTED"


@dataclass(frozen=True)
class ReplayProjectedMember:
    side: str
    slot: int
    # Own is the frozen team display name. Opponent is a *claimed* binding
    # of that visible display name to an engine side/slot, not verified.
    display_name: str
    species_id: str
    move_ids: tuple[str, ...]
    hp: int
    maxhp: int
    fainted: bool
    boosts: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ReplayProjectionCandidate:
    source: str
    hypothesis_id: str
    match_id: str
    decision_window_id: str
    state_revision: int
    snapshot_sha256: str
    engine_sha: str
    battle_sha256: str
    p1_active_slot: int
    p2_active_slot: int
    # 3 own members and 3 opponent members. No inferred invisible slots.
    members: tuple[ReplayProjectedMember, ...]


@dataclass(frozen=True)
class ReplayProjectionCaseAudit:
    hypothesis_id: str
    battle_sha256: str
    checked_fields: tuple[str, ...]
    unverified: tuple[str, ...]


@dataclass(frozen=True)
class ReplayProjectionAudit:
    status: str
    reason: str
    snapshot_sha256: str = ""
    cases: tuple[ReplayProjectionCaseAudit, ...] = ()
    full_state_reconstructed: bool = False
    engine_battle_authenticated: bool = False
    opponent_slot_mapping_authenticated: bool = False
    exhaustive_opponent_policy: bool = False
    ready_for_engine: bool = False


def _no(reason: str) -> ReplayProjectionAudit:
    return ReplayProjectionAudit("NO_REPLAY_PROJECTION_MATCH", reason)


def _projection_valid(p: ReplayProjectionCandidate) -> bool:
    if (p.source != _SCOPE or not _SHA.fullmatch(p.battle_sha256)
            or not _SHA.fullmatch(p.snapshot_sha256)
            or not isinstance(p.hypothesis_id, str) or not p.hypothesis_id
            or type(p.state_revision) is not int or p.state_revision < 0
            or p.p1_active_slot not in (1, 2, 3)
            or p.p2_active_slot not in (1, 2, 3)
            or not isinstance(p.members, tuple) or len(p.members) != 6):
        return False
    seen: set[tuple[str, int]] = set()
    names: dict[str, set[str]] = {"p1": set(), "p2": set()}
    for m in p.members:
        if (not isinstance(m, ReplayProjectedMember)
                or m.side not in ("p1", "p2")
                or type(m.slot) is not int or m.slot not in (1, 2, 3)
                or not isinstance(m.display_name, str) or not m.display_name
                or m.display_name != m.display_name.strip()
                or not isinstance(m.species_id, str)
                or not _ID.fullmatch(m.species_id)
                or not isinstance(m.move_ids, tuple) or not 1 <= len(m.move_ids) <= 4
                or any(not isinstance(x, str) or not _ID.fullmatch(x)
                       for x in m.move_ids)
                or len(set(m.move_ids)) != len(m.move_ids)
                or type(m.hp) is not int or type(m.maxhp) is not int
                or m.maxhp < 1 or not 0 <= m.hp <= m.maxhp
                or type(m.fainted) is not bool
                or m.fainted != (m.hp == 0)
                or not isinstance(m.boosts, tuple)
                or len(set(k for k, _ in m.boosts)) != len(m.boosts)
                or any(k not in ("atk","def","spa","spd","spe","accuracy","evasion")
                       or type(v) is not int or not -6 <= v <= 6
                       for k, v in m.boosts)
                or (m.side, m.slot) in seen
                or m.display_name in names[m.side]):
            return False
        seen.add((m.side, m.slot))
        names[m.side].add(m.display_name)
    return len(seen) == 6


def audit_observed_replay_projections(
    snapshot: FrozenBattleState, assets: Any, *,
    joint_candidates: tuple[SourcedJointSetCandidate, ...],
    projections: tuple[ReplayProjectionCandidate, ...],
) -> ReplayProjectionAudit:
    """Compare every sourced hypothesis to one supplied replay projection.

    All 3+3 members and the *claimed* current slots must be present. Only
    known exact own HP, observed exact opponent HP/faint, literal own
    Showdown IDs and observed boost integers are comparable. Opponent OCR
    percentages, statuses, types, hidden sets, identity mapping, engine
    Battle source and RNG remain unverified, never silently filled in.
    A single mismatch rejects ALL candidate projections.
    """
    if (not isinstance(snapshot, FrozenBattleState)
            or not isinstance(joint_candidates, tuple) or not joint_candidates
            or not isinstance(projections, tuple) or not projections
            or len(projections) != len(joint_candidates)
            or len(projections) > 16):
        return _no("EXPLICIT_EQUAL_SOURCED_PROJECTIONS_REQUIRED")
    staged = stage_observed_calculation_scenarios(
        snapshot, assets, joint_candidates=joint_candidates)
    if (staged.status != "PARTIAL_SCENARIOS_NOT_RUNNABLE"
            or staged.ready_for_engine or staged.full_state_reconstructed
            or len(staged.scenarios) != len(projections)):
        return _no("OBSERVED_STAGE_OR_JOINT_BATCH_INVALID")
    try:
        import json
        identifiers = [s.hypothesis_id for s in staged.scenarios]
        names = [p.hypothesis_id for p in projections]
        if (names != identifiers or len(set(names)) != len(names)):
            return _no("HYPOTHESIS_ORDER_OR_COVERAGE_MISMATCH")
        cases = []
        for scenario, candidate in zip(staged.scenarios, projections):
            if not isinstance(candidate, ReplayProjectionCandidate):
                return _no("INVALID_EXTERNAL_REPLAY_PROJECTION")
            if (not _projection_valid(candidate)
                    or candidate.hypothesis_id != scenario.hypothesis_id
                    or candidate.match_id != staged.match_id
                    or candidate.decision_window_id != staged.decision_window_id
                    or candidate.state_revision != staged.state_revision
                    or candidate.snapshot_sha256 != staged.snapshot_sha256
                    or candidate.engine_sha != snapshot.window.simulator_sha):
                return _no("STALE_OR_MALFORMED_REPLAY_PROJECTION")
            observed = json.loads(scenario.observed_constraints_json)
            roster = json.loads(scenario.opponent_joint_assumptions_json)["opponent_sets"]
            own = observed["own"]
            active = observed["opponent_active"]
            others = observed["other_observed_opponents"]
            if (len(own) != 3 or not isinstance(active, dict)
                    or set(roster) !=
                    {m.display_name for m in candidate.members if m.side == "p2"}
                    or set(roster) != set(json.loads(
                        scenario.opponent_joint_assumptions_json)["opponent_sets"])):
                return _no("REPLAY_OPPONENT_JOINT_OR_ROSTER_MISMATCH")
            by_side = {
                side: {m.slot: m for m in candidate.members if m.side == side}
                for side in ("p1", "p2")
            }
            if (observed["own_active_slot"] != candidate.p1_active_slot
                    or by_side["p1"][candidate.p1_active_slot].display_name
                    != snapshot.window.active_pokemon
                    or by_side["p2"][candidate.p2_active_slot].display_name
                    != active["name"]):
                return _no("REPLAY_ACTIVE_SLOT_MISMATCH")
            checked: list[str] = []
            for idx, obs in enumerate(own, start=1):
                member = by_side["p1"][idx]
                registered = tuple(row[1] for row in obs["registered_move_ids"])
                if (member.display_name != obs["name"]
                        or member.species_id != obs["registered_species_id"]
                        or member.move_ids != registered):
                    return _no("FROZEN_P1_ROSTER_OR_MOVE_ID_MISMATCH")
                checked.extend((f"p1:{idx}.species_id",f"p1:{idx}.move_ids"))
                if not _compare_observed_member(obs,member,f"p1:{idx}",checked):
                    return _no("OBSERVED_EXACT_STATE_MISMATCH")
            known = (active, *others)
            seen_obs = set()
            for obs in known:
                name = obs["name"]
                if name in seen_obs or name not in roster:
                    return _no("OBSERVED_OPPONENT_NOT_IN_JOINT")
                seen_obs.add(name)
                member = next((m for m in by_side["p2"].values()
                               if m.display_name == name), None)
                if member is None:
                    return _no("OPPONENT_DISPLAY_TO_SLOT_MISMATCH")
                if not _compare_observed_member(obs,member,
                        f"p2:{member.slot}",checked):
                    return _no("OBSERVED_EXACT_STATE_MISMATCH")
            # A read-only *claim* cannot attest full engine history, a
            # Japanese name -> species ID mapping, unobserved p2 moves,
            # RNG, PP/locks, or Champions parity even if checked facts match.
            unknown = (
                "EXTERNAL_BATTLE_SHA_AND_ENGINE_ORIGIN_UNATTESTED",
                "EXTERNAL_P2_DISPLAY_TO_SLOT_AND_SPECIES_ID_UNATTESTED",
                "OPPONENT_HIDDEN_MOVES_PP_RNG_EFFECTS_NOT_VERIFIED",
                "OPPONENT_POLICY_NOT_EXHAUSTIVE",
                "CHAMPIONS_SHOWDOWN_PARITY_UNVERIFIED",
            )
            if any(m.get("raw_hp_percent") is not None for m in known):
                unknown += ("RAW_OPPONENT_HP_PERCENT_NOT_EXACT",)
            if any(m.get("raw_status") is not None or m.get("raw_type") is not None
                   or m.get("raw_item") is not None or m.get("raw_ability") is not None
                   or m.get("raw_form") is not None or m.get("observed_moves_display")
                   for m in (*own, *known)):
                unknown += ("RAW_STATUS_TYPE_ITEM_ABILITY_MOVE_IDS_NOT_MAPPED",)
            cases.append(ReplayProjectionCaseAudit(
                candidate.hypothesis_id, candidate.battle_sha256,
                tuple(checked), unknown,
            ))
        return ReplayProjectionAudit(
            "PARTIAL_REPLAY_CONSTRAINTS_MATCH",
            "COMPARE_EXACT_RECORDED_FIELDS_ONLY_NOT_EXECUTABLE",
            staged.snapshot_sha256, tuple(cases),
        )
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return _no("MALFORMED_REPLAY_PROJECTION_OR_OBSERVATION")


def _compare_observed_member(
    observed: dict[str, Any], projected: ReplayProjectedMember,
    prefix: str, checked: list[str],
) -> bool:
    hp, mx, faint = (
        observed.get("hp"), observed.get("maxhp"), observed.get("fainted"))
    if hp is not None:
        if projected.hp != hp:
            return False
        checked.append(prefix+".hp")
    if mx is not None:
        if projected.maxhp != mx:
            return False
        checked.append(prefix+".maxhp")
    if faint is not None:
        if projected.fainted is not faint:
            return False
        checked.append(prefix+".fainted")
    boost = observed.get("boosts")
    if not isinstance(boost, list):
        return False
    actual = dict(projected.boosts)
    for row in boost:
        if (not isinstance(row, list) or len(row) != 2
                or actual.get(row[0],0) != row[1]):
            return False
        checked.append(prefix+".boosts."+row[0])
    return True
