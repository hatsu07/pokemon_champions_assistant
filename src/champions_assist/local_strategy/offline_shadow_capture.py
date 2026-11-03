"""Issue #141⑤: explicit offline one-window paired capture.

The LOCAL path is an untrusted synthetic diagnostic; never promotes its
nondominated action to LIVE or treats GPT text as a verified local choice.
No hooks into GUI or production match handlers. The real measured duration
here is worker computation, NOT screen presentation latency.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Callable

from .models import FrozenBattleState
from .joint_set_intake import SourcedJointSetCandidate
from .observed_synthetic_replay_bridge import ExplicitSyntheticBattleBranch
from .observed_full_grid_diagnostic import audit_observed_synthetic_turn
from .shadow_comparison_store import (
    BoundGPTAdvice, ShadowComparisonRecord,
    append_shadow_comparison, build_shadow_comparison,
)


@dataclass(frozen=True)
class OfflineCaptureReceipt:
    store_status: str
    record: ShadowComparisonRecord


def capture_offline_same_window(
    database_path: str | Path, snapshot: FrozenBattleState, assets: Any,
    *, gpt: BoundGPTAdvice,
    joint_candidates: tuple[SourcedJointSetCandidate, ...],
    complete_branches: tuple[ExplicitSyntheticBattleBranch, ...],
    worker: Any, preserve: str, stopper: str,
    current_snapshot: Callable[[], FrozenBattleState],
    clock: Callable[[], float] = monotonic,
) -> OfflineCaptureReceipt:
    """Run one frozen synthetic model; capture genuine offline elapsed time.

    A live callback must still be supplied for revision tracking, and this
    explicit function MUST NOT be invoked from the 45-second LIVE turn path.
    Failures and unsupported branches are recorded as NO_RANKING (never as
    succeeded, omitted or advice); old OCR revisions cause no SQLite write.
    """
    if not callable(current_snapshot) or not callable(clock):
        raise ValueError("CURRENT_IN_MEMORY_OBSERVER_REQUIRED")
    if (not isinstance(snapshot, FrozenBattleState)
            or not isinstance(gpt, BoundGPTAdvice)
            or snapshot.window.identity() != current_snapshot().window.identity()
            or snapshot.snapshot_sha256 != current_snapshot().snapshot_sha256):
        raise ValueError("STALE_WINDOW_BEFORE_OFFLINE_CAPTURE")
    started = clock()
    diagnostic = audit_observed_synthetic_turn(
        snapshot, assets, joint_candidates=joint_candidates,
        complete_branches=complete_branches, worker=worker,
        preserve=preserve, stopper=stopper,
        current_snapshot=current_snapshot, clock=clock)
    completed = clock()
    current = current_snapshot()
    if (current.window.identity() != snapshot.window.identity()
            or current.snapshot_sha256 != snapshot.snapshot_sha256):
        raise ValueError("OBSERVATION_CHANGED_DURING_OFFLINE_CAPTURE")
    elapsed = max(0, round((completed - started) * 1000))
    if elapsed > 600_000:
        raise ValueError("OFFLINE_DURATION_TOO_LONG")
    remaining = max(0, round((snapshot.window.deadline_monotonic - completed) * 1000))
    if remaining > 600_000:
        raise ValueError("INVALID_OFFLINE_DEADLINE")
    usable = (diagnostic.status == "SYNTHETIC_TURN_DIAGNOSTIC_ONLY"
              and diagnostic.ranking_status == "NO_RANKING"
              and not diagnostic.ready_for_engine)
    record = build_shadow_comparison(
        snapshot, current, gpt, diagnostic if usable else None,
        local_elapsed_ms=elapsed, remaining_ms_at_capture=remaining,
        local_refusal_reason=(
            "" if usable else "LOCAL_ABSTAIN:" + diagnostic.reason[:230]))
    return OfflineCaptureReceipt(
        append_shadow_comparison(database_path, record), record)
