"""Issue #141⑥: independently labelled offline shadow quality/timing audit.

No pass-through of synthetic-model predictions as verified Pokémon Champions
decisions. In particular, 100% NO_RANKING must never pass an applicability or
deadline gate; missing real-game quality labels remain UNKNOWN.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Iterable

from .shadow_comparison_store import ShadowComparisonRecord

_REQUIRED_GOLDENS = frozenset({
    "immediate_ko", "choice_lock_pp", "mega_form", "priority_speed",
    "processor_vs_safe_switch", "setup", "endgame_1v1", "stale_revision",
})
_STATUSES = frozenset({"MODEL_DIAGNOSTIC", "NO_RANKING"})


@dataclass(frozen=True)
class GoldenExpectation:
    """Curated independent test oracle, not inferred from a model output."""
    case_id: str
    kind: str
    match_id: str
    decision_window_id: str
    state_revision: int
    snapshot_sha256: str
    expected_local_status: str
    provenance: str = "SYNTHETIC_FIXTURE"


@dataclass(frozen=True)
class ShadowQualityReport:
    status: str
    reason: str
    sample_count: int = 0
    golden_covered: tuple[str, ...] = ()
    missing_goldens: tuple[str, ...] = ()
    golden_mismatches: tuple[str, ...] = ()
    abstain_count: int = 0
    model_diagnostic_count: int = 0
    actionable_count: int = 0
    actionable_applicability_rate: float = 0.0
    model_diagnostic_coverage_rate: float = 0.0
    abstention_rate: float = 0.0
    deadline_met_rate: float = 0.0
    p50_ms: int = 0
    p95_ms: int = 0
    p99_ms: int = 0
    # These require actual game outcomes / independently observed timestamps.
    game_safety_result: str = "UNVERIFIED"
    real_game_latency_result: str = "UNVERIFIED"
    pass_phase2_quality_gate: bool = False


def _percentile(sorted_values: list[int], percentile: float) -> int:
    if not sorted_values:
        return 0
    return sorted_values[max(0, ceil(percentile * len(sorted_values)) - 1)]


def audit_shadow_quality(
    rows: Iterable[ShadowComparisonRecord],
    goldens: Iterable[GoldenExpectation],
    *,
    required_goldens: frozenset[str] = _REQUIRED_GOLDENS,
) -> ShadowQualityReport:
    """Audit exact SHA-bound expectations; NO production acceptance implied.

    Measures model-diagnostic coverage separately from actionable application.
    A local synthetic diagnostic with candidate action IDs has *zero* verified
    actionable decisions. All timing values are stored-measurement samples,
    not a demonstrated Android-to-Windows UI arrival timestamp.
    """
    rows = tuple(rows)
    goldens = tuple(goldens)
    if (not rows or not isinstance(required_goldens, frozenset)
            or not required_goldens
            or any(not isinstance(x, str) or not x for x in required_goldens)
            or any(not isinstance(r, ShadowComparisonRecord) for r in rows)
            or any(not isinstance(g, GoldenExpectation) for g in goldens)):
        return ShadowQualityReport("INVALID_EVIDENCE", "NO_VALID_SAMPLES_OR_ORACLES")
    lookup: dict[tuple[str, str, int], ShadowComparisonRecord] = {}
    for row in rows:
        key = (row.match_id, row.decision_window_id, row.state_revision)
        if (key in lookup or row.local_status not in _STATUSES
                or len(row.snapshot_sha256) != 64
                or type(row.local_elapsed_ms) is not int
                or row.local_elapsed_ms < 0
                or type(row.input_deadline_met) is not bool):
            return ShadowQualityReport("INVALID_EVIDENCE", "DUPLICATE_OR_INVALID_WINDOW")
        lookup[key] = row
    seen: set[str] = set()
    covered: set[str] = set()
    mismatch: list[str] = []
    for item in goldens:
        key = (item.match_id, item.decision_window_id, item.state_revision)
        if (not item.case_id or item.case_id in seen
                or item.kind not in _REQUIRED_GOLDENS
                or item.expected_local_status not in _STATUSES
                or item.provenance not in ("SYNTHETIC_FIXTURE", "REPLAY_ANNOTATION")):
            return ShadowQualityReport("INVALID_EVIDENCE", "INVALID_GOLDEN_ORACLE")
        seen.add(item.case_id)
        row = lookup.get(key)
        if (row is None or row.snapshot_sha256 != item.snapshot_sha256):
            mismatch.append(item.case_id + ":MISSING_OR_DIFFERENT_REVISION")
        elif row.local_status != item.expected_local_status:
            mismatch.append(item.case_id + ":LOCAL_STATUS_MISMATCH")
        else:
            covered.add(item.kind)
    missing = tuple(sorted(required_goldens - covered))
    samples = len(rows)
    abstained = sum(r.local_status == "NO_RANKING" for r in rows)
    diagnostics = sum(r.local_status == "MODEL_DIAGNOSTIC" for r in rows)
    time_pass = sum(r.input_deadline_met for r in rows)
    ordered = sorted(r.local_elapsed_ms for r in rows)
    if mismatch:
        status, reason = "GOLDEN_MISMATCH", "SHA_BOUND_EXPECTATION_FAILED"
    elif missing:
        status, reason = "INCOMPLETE_GOLDEN_COVERAGE", "REQUIRED_GOLDENS_NOT_COVERED"
    else:
        status, reason = "SYNTHETIC_DIAGNOSTICS_AUDITED", (
            "NO_VERIFIED_ACTIONABILITY_OR_REAL_GAME_SAFETY_EVIDENCE")
    return ShadowQualityReport(
        status, reason, samples, tuple(sorted(covered)), missing,
        tuple(sorted(mismatch)), abstained, diagnostics,
        # NO_RANKING and MODEL_DIAGNOSTIC are both NONACTIONABLE.
        0, 0.0, diagnostics / samples, abstained / samples,
        time_pass / samples,
        _percentile(ordered, 0.50), _percentile(ordered, 0.95),
        _percentile(ordered, 0.99))
