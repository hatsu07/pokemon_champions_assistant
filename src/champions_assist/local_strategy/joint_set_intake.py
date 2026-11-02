"""Phase 2 source-bound intake of COMPLETE, explicitly co-recorded joint sets.

Only an externally supplied, explicitly joint *three-Pokémon bundle* may enter
this staging area. It does NOT derive combinations from independent move,
item, ability or spread usage rates. It does not fetch sources, certify their
authenticity, reconstruct Showdown Battle JSON or recommend actions.

All candidates must pass as one intact batch; a rejected candidate may never
be silently removed to make a favorable ordinal ranking look complete.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from json import loads
from typing import Any

from .models import FrozenBattleState, Hypothesis, canonical_json
from .opponent_evidence_gate import check_observed_opponent_hypotheses

_ALLOWED_SOURCES = frozenset({
    "CHAMPIONS_COOCCURRING_JOINT_RECORD",
    "USER_SUPPLIED_COMPLETE_JOINT_RECORD",
})
_ALLOWED_FIELDS = frozenset({
    "moves", "item", "ability", "form", "current_type", "status",
    "nature", "ability_points",
})
_REQUIRED_FIELDS = frozenset({"moves", "item", "ability", "form"})
MAX_JOINT_RECORDS = 16


@dataclass(frozen=True)
class SourcedJointSetCandidate:
    """One *co-recorded* 3v3 opponent party; never three independent marginals.

    These fields describe a claimant-provided source, not independent proof
    that the source is genuine, current or an exhaustive opponent policy.
    `opponent_sets_json` has exactly one `opponent_sets` dictionary with
    three members, and each member holds full explicit moves/item/ability/form.
    """
    candidate_id: str
    source_kind: str
    source_ref: str
    retrieved_at: str
    regulation: str
    selected_three: tuple[str, str, str]
    opponent_sets_json: str


@dataclass(frozen=True)
class JointCandidateIntake:
    status: str
    reason: str
    hypotheses: tuple[Hypothesis, ...] = ()
    rejected_candidate_id: str = ""
    # Must NEVER be treated as a verified exhaustive opponent-set list.
    coverage: str = "NOT_PROVEN_EXHAUSTIVE"


def _refuse(reason: str, candidate_id: str = "") -> JointCandidateIntake:
    return JointCandidateIntake("NO_RANKING", reason,
                                rejected_candidate_id=candidate_id)


def _valid_nonblank(value: Any) -> bool:
    return (isinstance(value, str) and bool(value)
            and value == value.strip()
            and value.upper() not in {"UNKNOWN", "NONE", "?"})


def _valid_source_datetime(value: Any) -> bool:
    if not _valid_nonblank(value):
        return False
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return instant.utcoffset() is not None


def stage_sourced_joint_candidates(
    snapshot: FrozenBattleState,
    candidates: tuple[SourcedJointSetCandidate, ...],
) -> JointCandidateIntake:
    """Convert curated joint bundles to *provisional* match-bound hypotheses.

    The returned hypotheses are not automatically put into the frozen battle
    snapshot or used by the Showdown runner. An independent caller must still
    establish source completeness, team mapping, replay RNG and legal p2
    choices; there is no automatic promotion of partial statistics.
    """
    if not isinstance(snapshot, FrozenBattleState) or not isinstance(candidates, tuple):
        return _refuse("INVALID_JOINT_CANDIDATE_INPUT")
    if snapshot.hypotheses:
        return _refuse("ALREADY_HAS_REGISTERED_HYPOTHESES")
    if not 1 <= len(candidates) <= MAX_JOINT_RECORDS:
        return _refuse("JOINT_CANDIDATE_BUDGET_OR_EMPTY")
    try:
        state = snapshot.state
        if not isinstance(state, dict):
            return _refuse("INVALID_OBSERVED_STATE")
        preview_six = state.get("opponent_six")
        if preview_six is not None:
            if (not isinstance(preview_six, list)
                    or any(not _valid_nonblank(x) for x in preview_six)
                    or len(preview_six) not in (0, 6)
                    or len(set(preview_six)) != len(preview_six)):
                return _refuse("INVALID_OPPONENT_PREVIEW")
        seen: set[str] = set()
        hypotheses: list[Hypothesis] = []
        for row in candidates:
            if not isinstance(row, SourcedJointSetCandidate):
                return _refuse("INVALID_JOINT_CANDIDATE_INPUT")
            candidate_id = row.candidate_id
            if not _valid_nonblank(candidate_id) or candidate_id in seen:
                return _refuse("DUPLICATE_OR_INVALID_HYPOTHESIS_ID",
                               str(candidate_id))
            seen.add(candidate_id)
            if (row.source_kind not in _ALLOWED_SOURCES
                    or not _valid_nonblank(row.source_ref)
                    or not _valid_source_datetime(row.retrieved_at)
                    or not _valid_nonblank(row.regulation)
                    or row.regulation != snapshot.window.regulation):
                return _refuse("JOINT_SOURCE_OR_REGULATION_UNVERIFIED",
                               candidate_id)
            names = row.selected_three
            if (not isinstance(names, tuple) or len(names) != 3
                    or any(not _valid_nonblank(n) for n in names)
                    or len(set(names)) != 3):
                return _refuse("INVALID_JOINT_SELECTED_THREE", candidate_id)
            if preview_six and not set(names).issubset(preview_six):
                return _refuse("JOINT_PARTY_CONTRADICTS_PREVIEW", candidate_id)
            try:
                payload = loads(row.opponent_sets_json)
            except (TypeError, ValueError):
                return _refuse("INVALID_JOINT_SET_JSON", candidate_id)
            if (not isinstance(payload, dict)
                    or set(payload) != {"opponent_sets"}
                    or not isinstance(payload["opponent_sets"], dict)
                    or set(payload["opponent_sets"]) != set(names)):
                return _refuse("INVALID_JOINT_SET_MEMBERS", candidate_id)
            for name in names:
                member = payload["opponent_sets"][name]
                if (not isinstance(member, dict)
                        or not _REQUIRED_FIELDS.issubset(member)
                        or not set(member).issubset(_ALLOWED_FIELDS)):
                    return _refuse("INCOMPLETE_OR_INFERRED_JOINT_MEMBER",
                                   candidate_id)
                moves = member["moves"]
                if (not isinstance(moves, list)
                        or not 1 <= len(moves) <= 4
                        or any(not _valid_nonblank(move) for move in moves)
                        or len(set(moves)) != len(moves)):
                    return _refuse("INVALID_EXPLICIT_JOINT_MOVES", candidate_id)
                for field in ("item", "ability", "form"):
                    if not _valid_nonblank(member[field]):
                        return _refuse("INCOMPLETE_OR_INFERRED_JOINT_MEMBER",
                                       candidate_id)
                for field in ("current_type", "status", "nature"):
                    if field in member and not _valid_nonblank(member[field]):
                        return _refuse("INVALID_JOINT_MEMBER_FIELD",
                                       candidate_id)
                # No guesses about AP totals/EV rules in another Pokémon game:
                # store only a complete literal mapping if supplied.
                if "ability_points" in member:
                    points = member["ability_points"]
                    if (not isinstance(points, dict)
                            or not points
                            or any(not _valid_nonblank(k)
                                   or not isinstance(v, int)
                                   or isinstance(v, bool) or v < 0
                                   for k, v in points.items())):
                        return _refuse("INVALID_JOINT_MEMBER_FIELD",
                                       candidate_id)
            provenance = (
                "SOURCE_KIND:" + row.source_kind,
                "SOURCE_REF:" + row.source_ref,
                "RETRIEVED_AT:" + row.retrieved_at,
                "REGULATION:" + row.regulation,
                "SCOPE:COOCCURRING_THREE_NOT_MARGINAL",
            )
            hypotheses.append(Hypothesis.from_assumptions(
                candidate_id, payload, evidence_refs=provenance,
                likelihood=None))
        # Existing evidence guard is authoritative. It checks every recorded
        # candidate at once and never drops an inconvenient contradiction.
        staged = replace(snapshot, hypotheses=tuple(hypotheses))
        gate = check_observed_opponent_hypotheses(staged)
        if gate.status != "CLEAR":
            return _refuse(gate.reason, gate.offending_hypothesis_id)
        return JointCandidateIntake(
            "PROVISIONAL_JOINT_CANDIDATES", "EXPLICIT_SOURCE_SET_STAGED",
            tuple(hypotheses),
        )
    except (ValueError, TypeError, AttributeError):
        return _refuse("INVALID_JOINT_CANDIDATE_INPUT")
