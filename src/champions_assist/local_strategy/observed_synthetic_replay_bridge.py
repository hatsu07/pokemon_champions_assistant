"""Phase 2 #141(②): all-case read-only observed↔synthetic replay worker join.

This is a strict *offline diagnostic* transition: a complete, caller-supplied
hypothetical Showdown Battle is read via the pinned local worker for EACH
sourced opponent joint-three, then every returned six-member readout is
compared to the same frozen OCR observation.

A matching synthetic Battle does not establish real Champions state, engine
RNG provenance, actual opponent species IDs or legal choices. NEVER return a
runnable Battle, rank actions, or wire this to LIVE/GPT/LOCAL_SHADOW.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from time import monotonic
from typing import Any, Mapping

from .engine_replay_readout_bridge import (
    ClaimedEngineReadoutCase, audit_claimed_engine_readouts,
)
from .joint_set_intake import MAX_JOINT_RECORDS, SourcedJointSetCandidate
from .models import FrozenBattleState, canonical_json, fingerprint
from .observed_safe_branch_gate import stage_safe_observed_branches
from .simulator_adapter import (
    LocalSimulatorWorker, SyntheticEngineReadoutReceipt, WorkerUnavailable,
    _verified_roster_binding,
)

_MAX_BATTLE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class ExplicitSyntheticBattleBranch:
    hypothesis_id: str
    # A completely external, hypothetical Battle. NEVER built from OCR.
    complete_battle: Mapping[str, Any]
    # Supplied display names bound to hypothetical p2 slot 1,2,3.
    # Their authenticity and species-ID translation are NOT established.
    opponent_display_by_slot: tuple[str, str, str]


@dataclass(frozen=True)
class SyntheticReplayComparison:
    status: str
    reason: str
    match_id: str = ""
    decision_window_id: str = ""
    state_revision: int = -1
    snapshot_sha256: str = ""
    branch_batch_sha256: str = ""
    hypothesis_ids: tuple[str, ...] = ()
    # Only complete-input hashes / checked field labels. No Battle / commands.
    battle_sha256: tuple[str, ...] = ()
    checked_fields: tuple[tuple[str, ...], ...] = ()
    opponent_mapping_authenticated: bool = False
    game_battle_authenticated: bool = False
    full_state_reconstructed: bool = False
    exhaustive_opponent_policy: bool = False
    ready_for_engine: bool = False
    ranking_status: str = "NO_RANKING"


def _no(reason: str) -> SyntheticReplayComparison:
    # No "successful" partial prefix even if some workers already ran.
    return SyntheticReplayComparison("NO_SYNTHETIC_REPLAY_COMPARISON", reason)


def _valid_receipt(
    receipt: Any, snapshot: FrozenBattleState, assets: Any,
    hypothesis_id: str, battle_text: str,
) -> bool:
    """Verify original request binding in addition to the worker's echo.

    A standalone dataclass can be forged; this check is consistency only.
    The worker method independently validates the live process IPC reply.
    """
    if not isinstance(receipt, SyntheticEngineReadoutReceipt):
        return False
    window = snapshot.window
    if (receipt.match_id != window.match_id
            or receipt.decision_window_id != window.decision_window_id
            or type(receipt.state_revision) is not int
            or receipt.state_revision != window.state_revision
            or receipt.snapshot_sha256 != snapshot.snapshot_sha256
            or receipt.hypothesis_id != hypothesis_id
            or type(receipt.engine_battle_authenticated) is not bool
            or receipt.engine_battle_authenticated
            or receipt.full_state_reconstructed
            or receipt.opponent_slot_mapping_authenticated
            or receipt.ready_for_engine
            or not isinstance(receipt.branch_request_id, str)
            or len(receipt.branch_request_id) != 32
            or any(c not in "0123456789abcdef" for c in receipt.branch_request_id)
            or not isinstance(receipt.branch_input_sha256, str)
            or len(receipt.branch_input_sha256) != 64
            or receipt.battle_sha256 !=
            sha256(battle_text.encode("utf-8")).hexdigest()):
        return False
    try:
        request = json.loads(receipt.submitted_request_json)
        readout = json.loads(receipt.readout_json)
        if (not isinstance(request, dict)
                or request.get("op") != "READOUT_REPLAY"
                or request.get("request_id") != window.request_id
                or request.get("identity") != window.identity()
                or request.get("snapshot_sha256") != snapshot.snapshot_sha256
                or request.get("branch_request_id") != receipt.branch_request_id):
            return False
        payload = request.get("payload")
        if (not isinstance(payload, dict)
                or set(payload) != {
                    "source", "battle_json", "hypothesis_id",
                    "expected_p1_roster", "expected_p1_active",
                    "branch_input_sha256"}
                or payload["source"] != "FULL_REPLAY_SYNTHETIC"
                or payload["battle_json"] != battle_text
                or payload["hypothesis_id"] != hypothesis_id
                or payload["expected_p1_active"] != window.active_pokemon
                or payload["expected_p1_roster"] !=
                   _verified_roster_binding(snapshot, assets)
                or payload["branch_input_sha256"] != receipt.branch_input_sha256
                or fingerprint({k:v for k,v in payload.items()
                                if k!="branch_input_sha256"})
                   != receipt.branch_input_sha256
                or readout.get("battle_sha256") != receipt.battle_sha256
                or canonical_json(readout) != receipt.readout_json):
            return False
        return True
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def compare_observed_to_synthetic_replays(
    snapshot: FrozenBattleState, assets: Any, *,
    joint_candidates: tuple[SourcedJointSetCandidate, ...],
    complete_branches: tuple[ExplicitSyntheticBattleBranch, ...],
    worker: LocalSimulatorWorker,
    linked_observer: Any | None = None,
) -> SyntheticReplayComparison:
    """Fail closed for the *entire* batch, always NO_RANKING.

    No synthetic Battle is constructed here. The caller must provide one
    explicit complete Battle per source-linked opponent joint hypothesis.
    Partial OCR facts are NEVER upgraded into unspecified hidden state.
    """
    if (not isinstance(snapshot, FrozenBattleState)
            or not isinstance(worker, LocalSimulatorWorker)
            or not isinstance(joint_candidates, tuple)
            or not isinstance(complete_branches, tuple)
            or not 1 <= len(complete_branches) == len(joint_candidates)
                   <= MAX_JOINT_RECORDS):
        return _no("EXPLICIT_COMPLETE_SYNTHETIC_BATCH_REQUIRED")
    staged = stage_safe_observed_branches(
        snapshot, assets, joint_candidates=joint_candidates,
        linked_observer=linked_observer)
    if (staged.status != "PROVISIONAL_OBSERVED_BRANCH_CONSTRAINTS"
            or staged.ready_for_engine or staged.ranking_status != "NO_RANKING"
            or len(staged.branches) != len(complete_branches)):
        return _no("FROZEN_OBSERVED_BRANCH_GATE_REJECTED_" + staged.reason)
    expected = tuple(row.hypothesis_id for row in staged.branches)
    actual = tuple(row.hypothesis_id
                   if isinstance(row, ExplicitSyntheticBattleBranch) else None
                   for row in complete_branches)
    if actual != expected or len(set(expected)) != len(expected):
        return _no("SYNTHETIC_HYPOTHESIS_ORDER_OR_COVERAGE_MISMATCH")
    # Preflight ALL explicit inputs before starting any workers. Do not
    # discard one inconvenient hypothesis after seeing its observations.
    battle_texts: list[str] = []
    try:
        for case in complete_branches:
            if (not isinstance(case.complete_battle, Mapping)
                    or not isinstance(case.opponent_display_by_slot, tuple)
                    or len(case.opponent_display_by_slot) != 3
                    or any(not isinstance(n, str) or not n or n != n.strip()
                           for n in case.opponent_display_by_slot)
                    or len(set(case.opponent_display_by_slot)) != 3):
                return _no("INCOMPLETE_SYNTHETIC_BATTLE_OR_P2_BINDING")
            text = canonical_json(dict(case.complete_battle))
            if len(text.encode("utf-8")) > _MAX_BATTLE_BYTES:
                return _no("SYNTHETIC_BATTLE_SIZE_LIMIT")
            # canonical_json is the existing project serialization, but
            # Python json.dumps defaults to allowing NaN/Infinity. These
            # are not valid JSON Battle values: reject before contacting
            # Node instead of relying on a downstream parse difference.
            def reject_nonfinite(value: str) -> None:
                raise ValueError("non-finite JSON number: " + value)
            if not isinstance(json.loads(text, parse_constant=reject_nonfinite), dict):
                return _no("SYNTHETIC_BATTLE_ROOT_INVALID")
            battle_texts.append(text)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return _no("MALFORMED_SYNTHETIC_BATTLE_INPUT")
    if monotonic() >= snapshot.window.deadline_monotonic:
        return _no("OBSERVED_WINDOW_DEADLINE_EXPIRED")
    readouts: list[ClaimedEngineReadoutCase] = []
    seen_nonce: set[str] = set()
    try:
        for case, text in zip(complete_branches, battle_texts):
            if monotonic() >= snapshot.window.deadline_monotonic:
                return _no("OBSERVED_WINDOW_DEADLINE_EXPIRED")
            receipt = worker.readout_synthetic_replay(
                snapshot, case.complete_battle,
                assets=assets, hypothesis_id=case.hypothesis_id,
                joint_candidates=joint_candidates)
            if (not _valid_receipt(receipt, snapshot, assets, case.hypothesis_id, text)
                    or receipt.branch_request_id in seen_nonce):
                return _no("WORKER_READOUT_RECEIPT_OR_NONCE_UNBOUND")
            seen_nonce.add(receipt.branch_request_id)
            readouts.append(ClaimedEngineReadoutCase(
                case.hypothesis_id, json.loads(receipt.readout_json),
                case.opponent_display_by_slot, receipt.match_id,
                receipt.decision_window_id, receipt.state_revision,
                receipt.snapshot_sha256, receipt.battle_sha256,
            ))
        if monotonic() >= snapshot.window.deadline_monotonic:
            return _no("OBSERVED_WINDOW_DEADLINE_EXPIRED")
        audit = audit_claimed_engine_readouts(
            snapshot, assets, joint_candidates=joint_candidates,
            readouts=tuple(readouts))
        if (audit.status != "PARTIAL_REPLAY_CONSTRAINTS_MATCH"
                or audit.ready_for_engine or audit.full_state_reconstructed
                or audit.engine_battle_authenticated
                or audit.opponent_slot_mapping_authenticated
                or len(audit.cases) != len(complete_branches)):
            return _no("READOUT_OBSERVATION_CONFLICT_" + audit.reason)
        if tuple(c.hypothesis_id for c in audit.cases) != expected:
            return _no("POST_AUDIT_HYPOTHESIS_ORDER_CHANGED")
        return SyntheticReplayComparison(
            "SYNTHETIC_REPLAYS_MATCH_OBSERVED_CONSTRAINTS_ONLY",
            "HYPOTHETICAL_FULL_BATTLES_NOT_AUTHENTICATED_OR_RUNNABLE",
            snapshot.window.match_id, snapshot.window.decision_window_id,
            snapshot.window.state_revision, snapshot.snapshot_sha256,
            staged.branch_batch_sha256, expected,
            tuple(c.battle_sha256 for c in audit.cases),
            tuple(c.checked_fields for c in audit.cases),
        )
    except (WorkerUnavailable, TypeError, KeyError, ValueError,
            AttributeError, OSError, RecursionError):
        return _no("WORKER_READOUT_OR_AUDIT_UNAVAILABLE")
