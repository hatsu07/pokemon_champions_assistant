"""#141 Phase 2(②-⑥): opt-in, nonactionable cross-window acceptance audit.

A confirmed selection/PREP and a later MOVE_COMMAND have DIFFERENT windows.
The correct join is match + 4 frozen masters/team, not a fabricated common
revision. This harness composes already reviewed contracts without opening
files during a battle, accessing Web, or replacing LIVE/GPT recommendations.

Crucially: synthetic Showdown + OCR readout cannot certify Champions legality,
coverage, safe switches, damage, policy or real-time presentation. No output of
this module is actionable. No production/Qt callbacks are registered.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from time import monotonic
from typing import Any, Callable

from ..preload import FrozenMatchAssets, REQUIRED_MASTER_FILES
from ..prep import PrepResult
from ..species_name_aliases import selection_base_name
from ..terminology import mapped_name
from .joint_set_intake import SourcedJointSetCandidate
from .models import FrozenBattleState, action_digest
from .observed_safe_branch_gate import stage_safe_observed_branches
from .observed_synthetic_replay_bridge import ExplicitSyntheticBattleBranch
from .offline_shadow_capture import capture_offline_same_window
from .prep18_audit import Prep18Audit, stage_prep18
from .shadow_comparison_store import (
    BoundGPTAdvice, ShadowComparisonRecord, append_shadow_comparison,
    build_shadow_comparison,
)
from .shadow_quality_gate import (
    GoldenExpectation, ShadowQualityReport, audit_shadow_quality,
)

# Selection + PREP share ONE 90-second window measured from preview start.
# This is an on-time KPI, not an asynchronous AI kill switch. PREP is
# superseded when the opponent's lead/battle screen is confirmed regardless
# of remaining time. There is no independent 40-second PREP cap.
SELECTION_PREP_SHARED_SECONDS = 90.0


@dataclass(frozen=True)
class SelectionPrepReceipt:
    match_id: str
    team_sha256: str
    four_master_sha256: tuple[tuple[str, str], ...]
    selected_three: tuple[str, str, str]
    lead: str
    opponent_preview_six: tuple[str, str, str, str, str, str]
    selection_started_at: float
    selection_committed_at: float
    assessment_finished_at: float | None = None
    # If the opponent entered battle before PREP finished, the PREP is stale.
    opponent_lead_seen_at: float | None = None
    source: str = "OFFLINE_EXPLICIT_SELECTION_RECEIPT"


@dataclass(frozen=True)
class Phase2MatchAudit:
    status: str
    reason: str
    match_id: str = ""
    decision_window_id: str = ""
    state_revision: int = -1
    snapshot_sha256: str = ""
    observed_stage_status: str = "NOT_RUN"
    observed_case_count: int = 0
    synthetic_turn_status: str = "NOT_RUN"
    prep_status: str = "NOT_RUN"
    prep_assessed_pairs: int = 0
    prep_time_status: str = "NOT_MEASURED"
    shadow_store_status: str = "NOT_WRITTEN"
    shadow_record: ShadowComparisonRecord | None = None
    quality_report: ShadowQualityReport | None = None
    missing_acceptance_evidence: tuple[str, ...] = ()
    ranking_status: str = "NO_RANKING"
    phase2_accepted: bool = False
    # Frozen state equality is NOT independent provenance of Champions images.
    opponent_preview_binding: str = "NOT_VERIFIED"


_ALWAYS_BLOCKERS = (
    "REAL_CHAMPIONS_FULL_BATTLE_AND_RNG_NOT_ATTESTED",
    "OPPONENT_POLICY_AND_LEGAL_COVERAGE_NOT_PROVEN",
    "REAL_GAME_TACTICAL_18_NOT_INDEPENDENTLY_VERIFIED",
    "LIVE_GPT_VS_LOCAL_SHADOW_NOT_CONNECTED",
    "REAL_GAME_SAFETY_AND_WINDOWS_UI_DEADLINE_NOT_MEASURED",
)


def _stop(reason: str) -> Phase2MatchAudit:
    return Phase2MatchAudit("BLOCKED", reason,
                            missing_acceptance_evidence=_ALWAYS_BLOCKERS)


def _unchanged(snapshot: FrozenBattleState,
               supplier: Callable[[], FrozenBattleState]) -> bool:
    try:
        live = supplier()
        return (isinstance(live, FrozenBattleState)
                and live.window.identity() == snapshot.window.identity()
                and live.snapshot_sha256 == snapshot.snapshot_sha256)
    except Exception:
        return False


def _preview_six_binding(selection: SelectionPrepReceipt,
                         state: dict[str, Any]) -> str:
    """Conservatively bind PREP preview to a later frozen turn's opponents.

    An exact six-member cross-window match prevents unrelated 18-matchup
    evidence from being logged as a matched match. No OCR truth, opponent
    set completeness or in-game legal policy is authenticated here.

    An absent/partial turn preview is NOT a compatible match; failing
    closed is preferable to assigning a complete PREP to an unknown team.
    Name normalization uses ONLY our established Champions species alias
    boundary, never fuzzy matching or inferred unseen party members.
    """
    expected = selection.opponent_preview_six
    actual = state.get("opponent_six")
    if actual is None:
        return "TURN_OPPONENT_PREVIEW_NOT_OBSERVED"
    if (not isinstance(expected, tuple)
            or not isinstance(actual, (list, tuple))
            or len(expected) != 6 or len(actual) != 6):
        return "INCOMPLETE_OR_INVALID_OPPONENT_PREVIEW_SIX"

    def names(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        normalized = []
        for name in values:
            if (not isinstance(name, str) or not name
                    or name != name.strip() or len(name) > 96
                    or name.upper() in ("UNKNOWN", "NONE", "?")):
                raise ValueError("INVALID_OPPONENT_NAME")
            # The stable Champions alias catalogue is the ONLY accepted
            # normalizer. Unknown OCR text/nicknames are not inferred as a
            # real species merely because the same spelling occurs twice.
            base = name.split("→", 1)[0]
            if not mapped_name("pokemon", base):
                raise ValueError("UNREGISTERED_OPPONENT_SPECIES")
            canonical = selection_base_name(name)
            if (not canonical or len(canonical) > 96
                    or canonical.upper() in ("UNKNOWN", "NONE", "?")):
                raise ValueError("UNVERIFIED_OPPONENT_NAME")
            normalized.append(canonical)
        if len(set(normalized)) != 6:
            raise ValueError("DUPLICATE_OPPONENT_SPECIES")
        return tuple(normalized)

    try:
        selected = names(expected)
        observed = names(actual)
        if set(selected) != set(observed):
            return "CROSS_PHASE_OPPONENT_SIX_MISMATCH"
        active = state.get("opponent_active")
        if active is not None and active != "":
            if (not isinstance(active, str)
                    or not mapped_name("pokemon", active.split("→", 1)[0])):
                return "MALFORMED_OPPONENT_ACTIVE"
            # A recognized Mega/form label resolves only to the same base
            # species; no full form or battle type is inferred.
            active_base = selection_base_name(active)
            if active_base not in selected:
                return "OPPONENT_ACTIVE_NOT_IN_SELECTION_SIX"
    except (ValueError, TypeError, AttributeError):
        return "MALFORMED_OR_DUPLICATED_OPPONENT_PREVIEW"
    return "FROZEN_SIX_MATCHED_NOT_GAME_AUTHENTICATED"


def _prep_receipt_status(receipt: SelectionPrepReceipt,
                         assessed: PrepResult | None) -> str:
    if (receipt.source != "OFFLINE_EXPLICIT_SELECTION_RECEIPT"
            or type(receipt.selection_started_at) not in (float, int)
            or not isfinite(receipt.selection_started_at)
            or type(receipt.selection_committed_at) not in (float, int)
            or not isfinite(receipt.selection_committed_at)):
        return "UNATTESTED_SELECTION_TIMELINE"
    end = receipt.assessment_finished_at
    if end is None or type(end) not in (int, float) or not isfinite(end):
        return "PREP_END_NOT_OBSERVED"
    if (receipt.selection_committed_at < receipt.selection_started_at
            or end < receipt.selection_committed_at):
        return "SELECTION_OR_PREP_TIME_REVERSED"
    # A confirmed opponent lead supersedes PREP even when it was ALSO
    # overtime: screen freshness outranks the advisory 90-second clock.
    # Never accept a stale 18-pair result solely because it is marked late.
    if (receipt.opponent_lead_seen_at is not None and
            (type(receipt.opponent_lead_seen_at) not in (int, float)
             or not isfinite(receipt.opponent_lead_seen_at)
             or receipt.opponent_lead_seen_at <= end)):
        return "PREP_AFTER_OPPONENT_LEAD"
    # On-time measurement starts at TEAM_SELECTION, not when the player
    # confirms three Pokémon. A 70-second selection leaves only 20 seconds.
    # Overtime is not an asynchronous AI kill switch while PREP stays active.
    if end - receipt.selection_started_at > SELECTION_PREP_SHARED_SECONDS:
        return "PREP_OVER_SHARED_90_SECONDS"
    return ("PREP_REPORTED_WITHIN_SHARED_90_SECONDS_UNVERIFIED"
            if assessed is not None else "NO_EVALUATED_PREP")


def audit_offline_phase2_match(
    selection: SelectionPrepReceipt, turn: FrozenBattleState,
    assets: FrozenMatchAssets, *,
    gpt: BoundGPTAdvice,
    current_snapshot: Callable[[], FrozenBattleState],
    database_path: str | Path | None = None,
    joint_candidates: tuple[SourcedJointSetCandidate, ...] = (),
    complete_branches: tuple[ExplicitSyntheticBattleBranch, ...] = (),
    worker: Any | None = None,
    linked_observer: Any | None = None,
    prep_assessment: PrepResult | None = None,
    preserve: str | None = None,
    stopper: str | None = None,
    goldens: tuple[GoldenExpectation, ...] = (),
    clock: Callable[[], float] = monotonic,
) -> Phase2MatchAudit:
    """One opt-in offline audit across selection and current turn windows.

    The caller must explicitly supply source-linked opponent joint sets and
    complete *synthetic* Battles; empty means no game calculation, not a
    fabricated default opponent set. On any invalid input this routine
    refuses before a worker or SQLite call. All game acceptance remains
    BLOCKED until separately independently evidenced in future versions.
    """
    if (not isinstance(selection, SelectionPrepReceipt)
            or not isinstance(turn, FrozenBattleState)
            or not isinstance(assets, FrozenMatchAssets)
            or not isinstance(gpt, BoundGPTAdvice)
            or not callable(current_snapshot) or not callable(clock)
            or not isinstance(joint_candidates, tuple)
            or not isinstance(complete_branches, tuple)
            or not isinstance(goldens, tuple)):
        return _stop("INVALID_CROSS_PHASE_INPUT")
    if not _unchanged(turn, current_snapshot):
        return _stop("STALE_TURN_BEFORE_OFFLINE_AUDIT")
    # Hashes/IDs must refer to the same frozen preloaded team; the selection
    # revision and the turn revision MUST NOT be forcibly made equal.
    try:
        manifest = tuple((name, assets.sha256[name])
                         for name in REQUIRED_MASTER_FILES)
        state = turn.state
        selected = tuple(state.get("selected_three", ()))
        if (selection.match_id != turn.window.match_id
                or selection.team_sha256 != turn.window.team_sha256
                or selection.team_sha256 != assets.sha256[REQUIRED_MASTER_FILES[3]]
                or selection.four_master_sha256 != manifest
                or turn.window.four_master_sha256 != manifest
                or selected != selection.selected_three
                or selection.lead not in selected
                or action_digest(turn.verified_action_ids)
                   != turn.window.allowed_action_digest):
            return _stop("CROSS_PHASE_MATCH_OR_FROZEN_TEAM_MISMATCH")
    except (KeyError, TypeError, ValueError, AttributeError):
        return _stop("CROSS_PHASE_MATCH_OR_FROZEN_TEAM_MISMATCH")
    preview_binding = _preview_six_binding(selection, state)
    if preview_binding != "FROZEN_SIX_MATCHED_NOT_GAME_AUTHENTICATED":
        # Before PREP, synthetic worker or SQLite: no successful prefix
        # and no inferred opponent roster when the observed six are absent.
        return _stop(preview_binding)
    # Stage ② uses the independently linked observation source, if present.
    # Failing an OCR provenance check forbids worker execution and DB writes.
    observed = stage_safe_observed_branches(
        turn, assets, joint_candidates=joint_candidates,
        linked_observer=linked_observer)
    if (observed.status != "PROVISIONAL_OBSERVED_BRANCH_CONSTRAINTS"
            or observed.ranking_status != "NO_RANKING"
            or observed.ready_for_engine):
        return _stop("OBSERVED_EVIDENCE_REJECTED:" + observed.reason)
    if not _unchanged(turn, current_snapshot):
        return _stop("STALE_AFTER_OBSERVED_STAGE")

    # Stage ④: structural assessment is explicitly separate from temporal
    # claims; all selected three x six must be in the frozen matrix.
    prep_time = _prep_receipt_status(selection, prep_assessment)
    tactical = stage_prep18(
        assets, selection.selected_three, selection.lead,
        selection.opponent_preview_six,
        # The 90-second countdown is advisory. A late but otherwise
        # fresh 18-pair assessment remains a valid *structural* result,
        # marked overtime instead of being destroyed. A battle screen
        # appearing before completion does supersede PREP immediately.
        reviewed_assessment=prep_assessment if prep_time in (
            "PREP_REPORTED_WITHIN_SHARED_90_SECONDS_UNVERIFIED",
            "PREP_OVER_SHARED_90_SECONDS") else None)
    prep_status = tactical.status

    # Stage ③/⑤: only an explicitly provided complete synthetic batch can
    # cause worker work. A partial OCR observation never gets an invented
    # Showdown Battle, p2 slot, RNG or synthetic policy.
    local_status = "NOT_RUN_NO_COMPLETE_SYNTHETIC_BATTLE"
    record: ShadowComparisonRecord | None = None
    store_status = "NOT_WRITTEN"
    if not _unchanged(turn, current_snapshot):
        return _stop("STALE_AFTER_PREP_STAGE")
    if database_path is not None:
        if (worker is not None and complete_branches and joint_candidates):
            # Never silently guess preserve/stopper from party position.
            # Explicit, selected roles (or externally assessed PREP roles)
            # are required even for a synthetic one-turn diagnostic.
            actual_preserve = preserve or tactical.preserve
            actual_stopper = stopper or tactical.stopper
            if (actual_preserve not in selection.selected_three
                    or actual_stopper not in selection.selected_three):
                return _stop("EXPLICIT_SELECTED_WIN_ROLES_REQUIRED")
            try:
                receipt = capture_offline_same_window(
                    database_path, turn, assets,
                    gpt=gpt, joint_candidates=joint_candidates,
                    complete_branches=complete_branches, worker=worker,
                    preserve=actual_preserve,
                    stopper=actual_stopper,
                    current_snapshot=current_snapshot, clock=clock)
                record, store_status = receipt.record, receipt.store_status
                local_status = record.local_status
            except (ValueError, TypeError, OSError):
                return _stop("SYNTHETIC_CAPTURE_REFUSED")
        elif complete_branches or worker is not None:
            return _stop("INCOMPLETE_SYNTHETIC_WORKER_OR_SOURCE_BATCH")
        else:
            try:
                # 0ms means no worker was executed; never count this as an
                # in-deadline or effective local tactical recommendation.
                record = build_shadow_comparison(
                    turn, current_snapshot(), gpt, None,
                    local_elapsed_ms=0, remaining_ms_at_capture=0,
                    local_refusal_reason="")
                store_status = append_shadow_comparison(database_path, record)
                local_status = record.local_status
            except (ValueError, TypeError, OSError):
                return _stop("OFFLINE_ABSTENTION_RECORD_REFUSED")
    elif complete_branches or worker is not None:
        return _stop("EXPLICIT_OPT_IN_SQLITE_PATH_REQUIRED")

    if not _unchanged(turn, current_snapshot):
        return _stop("STALE_AFTER_CAPTURE")
    quality = (audit_shadow_quality((record,), goldens)
               if record is not None and goldens else None)
    missing = list(_ALWAYS_BLOCKERS)
    if prep_status != "ASSESSED_18_CONDITIONAL_ONLY":
        missing.append("PREP18_TACTICAL_FIELDS_NOT_ASSESSED")
    if prep_time != "PREP_REPORTED_WITHIN_SHARED_90_SECONDS_UNVERIFIED":
        missing.append("SELECTION_PREP_SHARED_90_TIME_NOT_VERIFIED")
    if not joint_candidates:
        missing.append("SOURCE_LINKED_JOINT_SET_MISSING")
    if local_status != "MODEL_DIAGNOSTIC":
        missing.append("ONE_TURN_DIAGNOSTIC_NOT_AVAILABLE")
    if quality is None or quality.status != "SYNTHETIC_DIAGNOSTICS_AUDITED":
        missing.append("EIGHT_GOLDEN_CATEGORIES_NOT_COVERED")
    return Phase2MatchAudit(
        "OFFLINE_EVIDENCE_ONLY", "NO_REAL_GAME_ACCEPTANCE_AUTHORITY",
        turn.window.match_id, turn.window.decision_window_id,
        turn.window.state_revision, turn.snapshot_sha256,
        observed.status, len(observed.branches), local_status,
        prep_status, tactical.assessed_pairs, prep_time, store_status, record,
        quality, tuple(missing),
        opponent_preview_binding=preview_binding)
