"""#141②〜⑥ single OFF-MATCH evidence report, with no simulated game approval.

The auditor consumes existing typed outputs from the registered roster check,
cross-window match PREP/one-turn diagnostics, the actual-source-bound SQLite
device link, and one GUI clock export per match. It never extrapolates from
synthetic Battle to Champions legality, never treats GPT as the answer key,
and never enables/localizes live recommendations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .roster_mapping_readiness import RosterMappingReadiness
from .phase2_match_audit import Phase2MatchAudit
from .device_shadow_evidence_link import DeviceShadowLinkAudit
from .gui_timing_trace_audit import GuiTurnTraceAudit
from .shadow_comparison_store import ShadowComparisonRecord


@dataclass(frozen=True)
class ConsolidatedPhase2Report:
    status: str
    section_2: str
    section_3: str
    section_4: str
    section_5: str
    section_6: str
    match_count: int = 0
    frozen_species_id_fields: int = 0
    frozen_move_id_fields: int = 0
    synthetic_one_turn_runs: int = 0
    prep18_structural_matches: int = 0
    prep18_assessed_pairs: int = 0
    locally_bound_shadow_windows: int = 0
    unverified_shadow_windows: int = 0
    local_nonabstaining_diagnostic_windows: int = 0
    shadow_input_deadline_missed: int = 0
    gpt_gui_matched_matches: int = 0
    # v3 device turn count minus the same-match GUI recorded display count.
    # Not a video-verified same-frame match; never assert game truth.
    gpt_gui_missing_device_turns: int = 0
    gpt_gui_late_displays: int = 0
    local_shadow_late_displays: int = 0
    golden_label_categories: int = 0
    missing_evidence: tuple[str, ...] = ()
    # No combination of externally submitted labels is proof of game truth.
    game_capture_independently_authenticated: bool = False
    champions_mechanics_verified: bool = False
    real_tactical_correctness_verified: bool = False
    live_gpt_local_parallelism_verified: bool = False
    phase2_accepted: bool = False


def consolidated_phase2_report(
    *, roster: RosterMappingReadiness,
    device: DeviceShadowLinkAudit,
    expected_match_ids: Iterable[str],
    matches: Iterable[Phase2MatchAudit] = (),
    gui_clocks: Iterable[GuiTurnTraceAudit] = (),
) -> ConsolidatedPhase2Report:
    """Report each ②〜⑥ readiness separately, not as a misleading 'pass'.

    Every optional report is output by an already reviewed offline function.
    Missing/unlinked match IDs, duplicate evidence, and internally inconsistent
    input are refused as a batch. Real-world game/video authenticity is not
    inferable from immutable hashes or a matching time counter.
    """
    mids = tuple(expected_match_ids)
    reports = tuple(matches)
    clocks = tuple(gui_clocks)
    if (not isinstance(roster, RosterMappingReadiness)
            or not isinstance(device, DeviceShadowLinkAudit)
            or not 1 <= len(mids) <= 100
            or any(not isinstance(mid, str) or not 1 <= len(mid) <= 120
                   for mid in mids)
            or len(set(mids)) != len(mids)
            or len(reports) > len(mids) or len(clocks) > len(mids)
            or any(not isinstance(m, Phase2MatchAudit) for m in reports)
            or any(not isinstance(g, GuiTurnTraceAudit) for g in clocks)):
        return ConsolidatedPhase2Report(
            "CONSOLIDATED_AUDIT_REFUSED", "INVALID_INPUT", "INVALID_INPUT",
            "INVALID_INPUT", "INVALID_INPUT", "INVALID_INPUT",
            missing_evidence=("MALFORMED_OR_DUPLICATE_MATCH_INPUT",))
    match_set = set(mids)
    if (any(m.match_id not in match_set for m in reports)
            or len({m.match_id for m in reports}) != len(reports)
            or any(g.source_match_id not in match_set for g in clocks)
            or len({g.source_match_id for g in clocks}) != len(clocks)
            or device.match_count != len(mids)):
        return ConsolidatedPhase2Report(
            "CONSOLIDATED_AUDIT_REFUSED", "CROSS_MATCH_MISMATCH",
            "CROSS_MATCH_MISMATCH", "CROSS_MATCH_MISMATCH",
            "CROSS_MATCH_MISMATCH", "CROSS_MATCH_MISMATCH",
            missing_evidence=("UNRELATED_OR_DUPLICATED_MATCH_REPORT",))
    if device.status != "STRUCTURALLY_LINKED_UNVERIFIED":
        return ConsolidatedPhase2Report(
            "CONSOLIDATED_AUDIT_REFUSED", "INVALID_DEVICE_LINK",
            "INVALID_DEVICE_LINK", "INVALID_DEVICE_LINK",
            "INVALID_DEVICE_LINK", "INVALID_DEVICE_LINK",
            missing_evidence=("DEVICE_SQLITE_LINK_FAILED",))
    fields_ok = (
        roster.status == "STRUCTURAL_IDS_PRESENT_UNATTESTED"
        and roster.worker_id_fields_structurally_complete
        and roster.inspected_members == 6
        and roster.checked_move_slots == 24
        and not roster.missing_fields
        and not roster.invalid_fields)
    # Only format/string coverage of own IDs; not game-derived mapping.
    species = roster.inspected_members if fields_ok else 0
    moves = roster.checked_move_slots if fields_ok else 0
    same_source = device.team_identity_cross_bound
    # A Phase2MatchAudit by itself is only a caller-owned result. Do NOT
    # count ③/④ by shared match_id alone. Demand an existing local-source-
    # cross-bound SQLite row from *this device batch*, exact frozen state,
    # assessed/observed status and on-time PREP with observed opponent six.
    window_ids = set(device.locally_bound_window_identities)
    def qualifies(m: Phase2MatchAudit) -> bool:
        rec = m.shadow_record
        return (
            same_source
            and isinstance(rec, ShadowComparisonRecord)
            and m.status == "OFFLINE_EVIDENCE_ONLY"
            and m.reason == "NO_REAL_GAME_ACCEPTANCE_AUTHORITY"
            and m.ranking_status == "NO_RANKING"
            and m.phase2_accepted is False
            and m.observed_stage_status ==
                "PROVISIONAL_OBSERVED_BRANCH_CONSTRAINTS"
            and m.opponent_preview_binding ==
                "FROZEN_SIX_MATCHED_NOT_GAME_AUTHENTICATED"
            and m.prep_time_status ==
                "PREP_REPORTED_WITHIN_SHARED_90_SECONDS_UNVERIFIED"
            and m.prep_status == "ASSESSED_18_CONDITIONAL_ONLY"
            and m.prep_assessed_pairs == 18
            and "PREP18_TACTICAL_FIELDS_NOT_ASSESSED"
                not in m.missing_acceptance_evidence
            and "SELECTION_PREP_SHARED_90_TIME_NOT_VERIFIED"
                not in m.missing_acceptance_evidence
            and m.shadow_store_status in ("RECORDED", "ALREADY_RECORDED")
            and m.match_id == rec.match_id
            and m.decision_window_id == rec.decision_window_id
            and m.state_revision == rec.state_revision
            and m.snapshot_sha256 == rec.snapshot_sha256
            and m.synthetic_turn_status == rec.local_status
            and (
                rec.match_id, rec.decision_window_id, rec.state_revision,
                rec.snapshot_sha256, rec.team_sha256,
                rec.master_manifest_sha256, rec.evidence_sha256,
                rec.allowed_action_digest, rec.local_status,
                rec.input_deadline_met,
            ) in window_ids
        )
    valid_reports = tuple(m for m in reports if qualifies(m))
    synthetic = sum(m.synthetic_turn_status == "MODEL_DIAGNOSTIC"
                    for m in valid_reports)
    assessed = sum(m.prep_assessed_pairs for m in valid_reports)
    finished = len(valid_reports)
    # The device validator verifies *all* frame annotations against each
    # actual submitted match timeline. Read its validated per-match turn
    # counts here, not the GUI export's self-reported observed-turn count.
    expected_pairs = device.expected_turns_by_match
    try:
        expected = dict(expected_pairs)
        turns_bound = (
            len(expected_pairs) == len(mids)
            and len(expected) == len(mids)
            and set(expected) == match_set
            and all(type(count) is int and count > 0
                    for count in expected.values())
            and sum(expected.values()) == device.bound_turns
        )
    except (TypeError, ValueError):
        expected, turns_bound = {}, False
    clocks_by_match = {g.source_match_id: g for g in clocks}
    gui_complete = sum(
        g.status == "GUI_MONOTONIC_TIMING_ONLY"
        and g.observed_turns == expected[mid]
        and g.complete_gpt_display_turns == expected[mid]
        and g.missing_or_unmatched_turns == 0
        and g.late_gpt_display_turns == 0
        for mid, g in clocks_by_match.items()
        if turns_bound
    )
    missing_gui_device_turns = (
        sum(max(0, count - max(
            0, clocks_by_match[mid].complete_gpt_display_turns))
            if mid in clocks_by_match else count
            for mid, count in expected.items())
        if turns_bound else 0
    )
    reasons = [
        "CHAMPIONS_LEGAL_IDS_MOVES_FORMS_NOT_INDEPENDENTLY_ATTESTED",
        "REAL_CHAMPIONS_BATTLE_RNG_OPPONENT_POLICY_NOT_RECONSTRUCTED",
        "TACTICAL_18_AND_WIN_ROLES_NOT_INDEPENDENTLY_ADJUDICATED",
        "NO_PROVEN_SAME_WINDOW_REAL_GPT_AND_LOCAL_SHADOW",
        "NO_VIDEO_VERIFIED_WINDOWS_AND_INDEPENDENT_GOLDEN_OUTCOMES",
    ]
    if not fields_ok:
        reasons.append("OWN_SIX_AND_24_MOVE_ID_FIELDS_NOT_PRESENT")
    if len(reports) != len(mids):
        reasons.append("MISSING_SAME_MATCH_ONE_TURN_PREP_REPORTS")
    if len(valid_reports) != len(reports):
        reasons.append("REJECTED_OR_UNLINKED_PHASE2_MATCH_AUDIT")
    if finished != len(mids):
        reasons.append("PREP18_NOT_STRUCTURALLY_ASSESSED_FOR_ALL_MATCHES")
    if not same_source:
        reasons.append("REGISTERED_LOCAL_SOURCE_IDENTITY_NOT_CROSS_BOUND")
    if device.status != "STRUCTURALLY_LINKED_UNVERIFIED":
        reasons.append("DEVICE_SQLITE_EVIDENCE_INVALID")
    if device.shadow_input_deadline_missed_count:
        reasons.append("SHADOW_INPUT_DEADLINE_MISSED")
    if device.late_shadow_displays:
        reasons.append("LOCAL_SHADOW_UI_DEADLINE_MISSED")
    if not turns_bound:
        reasons.append("DEVICE_EXPECTED_TURN_COUNTS_NOT_BOUND")
    if (len(clocks) != len(mids)
            or gui_complete != len(mids)
            or missing_gui_device_turns):
        reasons.append("INCOMPLETE_GUI_TIME_EVENTS_FOR_ALL_MATCHES")
    if turns_bound and any(
            g.observed_turns != expected[mid]
            or g.complete_gpt_display_turns != expected[mid]
            for mid, g in clocks_by_match.items()):
        reasons.append("GUI_DEVICE_TURN_COUNT_MISMATCH")
    if device.missing_golden_kinds or device.submitted_unsafe_claim_count:
        reasons.append("MISSING_OR_UNSAFE_GOLDEN_REVIEW_CATEGORIES")
    if device.nonabstaining_diagnostic_turns == 0:
        reasons.append("ALL_LOCAL_SHADOW_DIAGNOSTICS_ABSTAINED")
    # Retain structural and quality errors from the underlying source-link
    # gate, not just a collapsed status.
    for cause in device.missing_evidence:
        if cause not in reasons:
            reasons.append(cause)
    return ConsolidatedPhase2Report(
        status="INCOMPLETE_REAL_GAME_ACCEPTANCE",
        section_2="SOURCE_ID_CANDIDATES_ONLY_NOT_CHAMPIONS_VERIFIED",
        section_3="SYNTHETIC_ONE_TURN_ONLY_NOT_CHAMPIONS_VERIFIED",
        section_4="PREP18_STRUCTURAL_ONLY_TACTICS_NOT_VERIFIED",
        section_5="SQLITE_WINDOWS_OFFLINE_ONLY_NOT_LIVE_PARALLEL",
        section_6="TIMING_AND_GOLDEN_LABELS_NOT_GAME_VERIFIED",
        match_count=len(mids),
        frozen_species_id_fields=species,
        frozen_move_id_fields=moves,
        synthetic_one_turn_runs=synthetic,
        prep18_structural_matches=finished,
        prep18_assessed_pairs=assessed,
        locally_bound_shadow_windows=device.same_revision_shadow_turns,
        unverified_shadow_windows=device.unverified_aligned_shadow_rows,
        local_nonabstaining_diagnostic_windows=(
            device.nonabstaining_diagnostic_turns),
        shadow_input_deadline_missed=(
            device.shadow_input_deadline_missed_count),
        gpt_gui_matched_matches=gui_complete,
        gpt_gui_missing_device_turns=missing_gui_device_turns,
        gpt_gui_late_displays=sum(g.late_gpt_display_turns for g in clocks),
        local_shadow_late_displays=device.late_shadow_displays,
        golden_label_categories=len(device.covered_golden_kinds),
        missing_evidence=tuple(reasons),
    )
