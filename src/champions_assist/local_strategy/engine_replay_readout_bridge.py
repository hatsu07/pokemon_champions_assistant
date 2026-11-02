"""Convert claimed pinned-engine readouts to PR #190 replay projection checks.

The upstream Node extractor reads a *synthetic complete* Showdown Battle.
A JSON object that merely LOOKS like its response is NOT a trusted engine
receipt. This adapter checks consistency ONLY. The output always retains
engine_battle_authenticated=False and ready_for_engine=False.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .joint_set_intake import SourcedJointSetCandidate
from .models import FrozenBattleState
from .observed_calculation_scenarios import stage_observed_calculation_scenarios
from .observed_replay_projection_audit import (
    ReplayProjectedMember, ReplayProjectionCandidate, ReplayProjectionAudit,
    audit_observed_replay_projections,
)

PIN = "ccfaa20f32af363569cd21a9d501b2bc1ded26b1"
SOURCE = "PINNED_SHOWDOWN_COMPLETE_SYNTHETIC_REPLAY"
PROJECTION_SOURCE = "EXTERNAL_REPLAY_PROJECTION_UNATTESTED"
_SHA = __import__("re").compile(r"[a-f0-9]{64}\Z")
_ID = __import__("re").compile(r"[a-z0-9]+\Z")
_BOOSTS = ("atk", "def", "spa", "spd", "spe", "accuracy", "evasion")
_ROOT = {
    "status", "source", "engine_sha", "battle_sha256", "active", "members",
    "full_state_reconstructed", "ready_for_engine",
    "opponent_slot_mapping_authenticated", "unverified",
}
_MEMBER = {"side", "slot", "species_id", "move_ids", "hp", "maxhp",
           "fainted", "boosts"}


@dataclass(frozen=True)
class ClaimedEngineReadoutCase:
    hypothesis_id: str
    # Caller-provided, untrusted JSON-decoded response from the JS helper.
    readout: Mapping[str, Any]
    # A separate UNVERIFIED opponent display-name -> engine slot claim.
    opponent_display_by_slot: tuple[str, str, str]
    # Bound by the independent window owner AT CAPTURE TIME. The adapter
    # must never re-stamp an old readout with a newer snapshot identity.
    match_id: str
    decision_window_id: str
    state_revision: int
    snapshot_sha256: str
    battle_sha256: str


def _no(reason: str) -> ReplayProjectionAudit:
    return ReplayProjectionAudit("NO_REPLAY_PROJECTION_MATCH", reason)


def _valid_readout(readout: Any, expected_sha: str) -> bool:
    if (not isinstance(readout, dict) or set(readout) != _ROOT
            or readout["status"] != "SYNTHETIC_ENGINE_REPLAY_READOUT"
            or readout["source"] != SOURCE
            or readout["engine_sha"] != PIN or expected_sha != PIN
            or type(readout["full_state_reconstructed"]) is not bool
            or readout["full_state_reconstructed"]
            or type(readout["ready_for_engine"]) is not bool
            or readout["ready_for_engine"]
            or type(readout["opponent_slot_mapping_authenticated"]) is not bool
            or readout["opponent_slot_mapping_authenticated"]
            or not isinstance(readout["battle_sha256"], str)
            or _SHA.fullmatch(readout["battle_sha256"]) is None
            or not isinstance(readout["unverified"], list)
            or not all(isinstance(x, str) and x for x in readout["unverified"])
            or not isinstance(readout["active"], dict)
            or set(readout["active"]) != {"p1", "p2"}
            or any(type(slot) is not int or slot not in (1,2,3)
                   for slot in readout["active"].values())
            or not isinstance(readout["members"], list)
            or len(readout["members"]) != 6):
        return False
    slots: set[tuple[str, int]] = set()
    for m in readout["members"]:
        if (not isinstance(m, dict) or set(m) != _MEMBER
                or m["side"] not in ("p1", "p2")
                or type(m["slot"]) is not int or m["slot"] not in (1,2,3)
                or (m["side"],m["slot"]) in slots
                or not isinstance(m["species_id"], str)
                or _ID.fullmatch(m["species_id"]) is None
                or not isinstance(m["move_ids"], list)
                or not 1 <= len(m["move_ids"]) <= 4
                or any(not isinstance(x, str) or _ID.fullmatch(x) is None
                       for x in m["move_ids"])
                or len(set(m["move_ids"])) != len(m["move_ids"])
                or type(m["hp"]) is not int or type(m["maxhp"]) is not int
                or m["maxhp"] < 1 or not 0 <= m["hp"] <= m["maxhp"]
                or type(m["fainted"]) is not bool
                or m["fainted"] != (m["hp"] == 0)
                or not isinstance(m["boosts"], list)
                or len(m["boosts"]) != len(_BOOSTS)):
            return False
        for idx, row in enumerate(m["boosts"]):
            if (not isinstance(row,list) or len(row)!=2
                    or row[0]!=_BOOSTS[idx] or type(row[1]) is not int
                    or not -6 <= row[1] <= 6):
                return False
        slots.add((m["side"],m["slot"]))
    return len(slots)==6


def audit_claimed_engine_readouts(
    snapshot: FrozenBattleState, assets: Any, *,
    joint_candidates: tuple[SourcedJointSetCandidate, ...],
    readouts: tuple[ClaimedEngineReadoutCase, ...],
) -> ReplayProjectionAudit:
    """Check ALL claimed synthetic readouts against the frozen observations.

    This method does not have a live Node handle, engine-signed provenance,
    complete RNG, or a trusted Champions p2 display→slot identity mapping.
    No success here permits downstream tactical or simulator operations.
    """
    if (not isinstance(snapshot, FrozenBattleState)
            or not isinstance(joint_candidates, tuple)
            or not joint_candidates or not isinstance(readouts, tuple)
            or not 1 <= len(readouts) == len(joint_candidates) <= 16):
        return _no("COMPLETE_SOURCED_ENGINE_READOUT_BATCH_REQUIRED")
    staged = stage_observed_calculation_scenarios(
        snapshot, assets, joint_candidates=joint_candidates)
    if (staged.status != "PARTIAL_SCENARIOS_NOT_RUNNABLE"
            or staged.ready_for_engine or staged.full_state_reconstructed
            or len(staged.scenarios) != len(readouts)):
        return _no("INVALID_STAGED_OBSERVED_JOINT_BATCH")
    try:
        import json
        from copy import deepcopy
        candidate_projections: list[ReplayProjectionCandidate] = []
        for scenario, case in zip(staged.scenarios, readouts):
            if (not isinstance(case, ClaimedEngineReadoutCase)
                    or case.hypothesis_id != scenario.hypothesis_id
                    or case.match_id != snapshot.window.match_id
                    or case.decision_window_id != snapshot.window.decision_window_id
                    or type(case.state_revision) is not int
                    or case.state_revision != snapshot.window.state_revision
                    or case.snapshot_sha256 != snapshot.snapshot_sha256
                    or not isinstance(case.battle_sha256, str)
                    or _SHA.fullmatch(case.battle_sha256) is None
                    or not isinstance(case.opponent_display_by_slot, tuple)
                    or len(case.opponent_display_by_slot)!=3
                    or any(not isinstance(n,str) or not n or n!=n.strip()
                           for n in case.opponent_display_by_slot)
                    or len(set(case.opponent_display_by_slot))!=3):
                return _no("READOUT_HYPOTHESIS_OR_DISPLAY_BINDING_INVALID")
            row=deepcopy(case.readout)
            if (not _valid_readout(row,snapshot.window.simulator_sha)
                    or row["battle_sha256"] != case.battle_sha256):
                return _no("UNTRUSTED_OR_MALFORMED_ENGINE_READOUT")
            observed=json.loads(scenario.observed_constraints_json)
            own=observed["own"]
            if len(own)!=3:
                return _no("FROZEN_P1_PARTY_INCOMPLETE")
            by_side = {(m["side"],m["slot"]):m for m in row["members"]}
            members=[]
            for label,names in (("p1",tuple(m["name"] for m in own)),
                                ("p2",case.opponent_display_by_slot)):
                for slot,display in enumerate(names,1):
                    m=by_side[label,slot]
                    members.append(ReplayProjectedMember(
                        label,slot,display,m["species_id"],
                        tuple(m["move_ids"]),m["hp"],m["maxhp"],m["fainted"],
                        tuple((k,v) for k,v in m["boosts"]),
                    ))
            candidate_projections.append(ReplayProjectionCandidate(
                PROJECTION_SOURCE,case.hypothesis_id,
                snapshot.window.match_id,snapshot.window.decision_window_id,
                snapshot.window.state_revision,snapshot.snapshot_sha256,
                snapshot.window.simulator_sha,row["battle_sha256"],
                row["active"]["p1"],row["active"]["p2"],tuple(members),
            ))
        return audit_observed_replay_projections(
            snapshot, assets, joint_candidates=joint_candidates,
            projections=tuple(candidate_projections))
    except (TypeError,ValueError,KeyError,IndexError,AttributeError):
        return _no("READOUT_CONVERSION_UNAVAILABLE")
