"""Opt-in read-only concurrent LOCAL_SHADOW worker alongside an existing GPT request.

Stage 1 implements the operational same-window *joining mechanism*. It never
starts GPT, changes its answer, accesses Qt, or infers hidden Champions Battle
values. The caller must already possess a frozen match-scoped observation,
verified source IDs, a thread-safe current_snapshot supplier and, when
available, independently supplied FULL SYNTHETIC replay branches.
Unverified branches produce NO_RANKING rather than synthetic gameplay advice.
Physical-game proof and Windows Qt wiring remain tracked in #203/#141.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from typing import Any, Callable

from .models import FrozenBattleState
from .joint_set_intake import SourcedJointSetCandidate
from .observed_synthetic_replay_bridge import ExplicitSyntheticBattleBranch
from .observed_full_grid_diagnostic import (
    ObservedFullGridDiagnostic, audit_observed_synthetic_turn,
)
from .shadow_comparison_store import (
    BoundGPTAdvice, ShadowComparisonRecord, append_shadow_comparison,
    build_shadow_comparison, revoke_shadow_comparison,
)


@dataclass(frozen=True)
class ParallelShadowResult:
    status: str
    reason: str
    record: ShadowComparisonRecord | None = None
    # Runtime status never certifies actual gameplay or recommends a move.
    ready_for_live: bool = False


class ParallelShadowSession:
    """One opt-in match/window; GPT proceeds independently of local computation.

    This is not a process-global daemon. Explicit close() cancels queued work.
    Callers must NOT call current_snapshot by manipulating Qt objects from
    this pool: instead supply an immutable snapshot copied under a lock.
    """

    def __init__(
        self, snapshot: FrozenBattleState, assets: Any, *, database_path: str | Path,
        current_snapshot: Callable[[], FrozenBattleState], preserve: str,
        stopper: str, joint_candidates: tuple[SourcedJointSetCandidate, ...] = (),
        complete_branches: tuple[ExplicitSyntheticBattleBranch, ...] = (),
        worker: Any | None = None,
        enabled: bool = False,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if (not isinstance(snapshot, FrozenBattleState)
                or not callable(current_snapshot) or not callable(clock)
                or not isinstance(enabled, bool)
                or not isinstance(joint_candidates, tuple)
                or not isinstance(complete_branches, tuple)
                or not isinstance(preserve, str) or not preserve
                or not isinstance(stopper, str) or not stopper):
            raise ValueError("INVALID_PARALLEL_SHADOW_INPUT")
        self.snapshot = snapshot
        self.assets = assets
        self.database_path = database_path
        self.current_snapshot = current_snapshot
        self.preserve = preserve
        self.stopper = stopper
        self.joint_candidates = joint_candidates
        self.complete_branches = complete_branches
        self.worker = worker
        self.enabled = enabled
        self.clock = clock
        self._lock = Lock()
        self._pool: ThreadPoolExecutor | None = None
        self._local_future: Future | None = None
        self._joined = False
        self._closed = False
        # Nonblocking UI invalidation is linearized against a successful
        # finished receipt by _lock; disk activity never holds that lock.
        self._revoked = Event()
        self._persisted = False
        self._cleanup_future: Future | None = None

    def _current(self) -> FrozenBattleState:
        if self._revoked.is_set():
            raise ValueError("REVOKED_SHADOW_WINDOW")
        current = self.current_snapshot()
        if (self._revoked.is_set()
                or not isinstance(current, FrozenBattleState)
                or current.window.identity() != self.snapshot.window.identity()
                or current.snapshot_sha256 != self.snapshot.snapshot_sha256):
            raise ValueError("STALE_PARALLEL_SHADOW_WINDOW")
        return current

    def start(self) -> str:
        """Start local calculation BEFORE GPT finishes; never block GPT."""
        if not self.enabled:
            return "DISABLED"
        with self._lock:
            if self._closed or self._local_future is not None:
                return "ALREADY_STARTED_OR_CLOSED"
            try:
                self._current()
            except (TypeError, ValueError, AttributeError):
                return "STALE_WINDOW"
            self._pool = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="phase2-shadow")
            self._local_future = self._pool.submit(self._evaluate)
        return "RUNNING"

    def _evaluate(self) -> tuple[ObservedFullGridDiagnostic | None, int, str]:
        started = self.clock()
        if not self.complete_branches or not self.joint_candidates or self.worker is None:
            # Never synthesize Battle/RNG or an opponent set merely to get a
            # non-abstaining score. Preserve the observed GPT response.
            return None, max(0, round((self.clock()-started)*1000)), (
                "LOCAL_ABSTAIN:SOURCE_BOUND_ACTION_FRONTIER_REFUSED")
        try:
            result = audit_observed_synthetic_turn(
                self.snapshot, self.assets,
                joint_candidates=self.joint_candidates,
                complete_branches=self.complete_branches,
                worker=self.worker, preserve=self.preserve,
                stopper=self.stopper, current_snapshot=self._current,
                clock=self.clock)
            elapsed = max(0, round((self.clock()-started)*1000))
            if (result.status == "SYNTHETIC_TURN_DIAGNOSTIC_ONLY"
                    and result.ranking_status == "NO_RANKING"
                    and not result.ready_for_engine):
                return result, elapsed, ""
            return None, elapsed, "LOCAL_ABSTAIN:" + result.reason[:140]
        except Exception:
            # A local failure must never abort the user's independent GPT
            # request; the SQLite record carries ONLY a fixed reason code.
            return None, max(0, round((self.clock()-started)*1000)), (
                "LOCAL_ABSTAIN:SYNTHETIC_P2_OR_TURN_WORKER_UNAVAILABLE")

    def attach_gpt(self, gpt: BoundGPTAdvice) -> Future | None:
        """Pair GPT and LOCAL on same frozen state without blocking the UI.

        Returns a future for the off-path SQLite receipt, never a new game
        recommendation. No unverified GPT action text is persisted.
        """
        if not self.enabled:
            return None
        with self._lock:
            if (self._closed or self._joined or self._pool is None
                    or self._local_future is None):
                return None
            self._joined = True
            local = self._local_future
            return self._pool.submit(self._finish, local, gpt)

    def _active_at_commit(self) -> bool:
        """Transactional fence. A cancellation after the fence is purged by
        close()'s serialized off-UI cleanup, never accepted as evidence.
        """
        if self._revoked.is_set():
            return False
        try:
            self._current()
        except (ValueError, TypeError, AttributeError):
            return False
        return not self._revoked.is_set()

    def _revoke_unfinished(self) -> str:
        """Runs AFTER an in-flight _finish on the same one-worker executor."""
        try:
            return revoke_shadow_comparison(
                self.database_path,
                match_id=self.snapshot.window.match_id,
                decision_window_id=self.snapshot.window.decision_window_id,
                state_revision=self.snapshot.window.state_revision,
                snapshot_sha256=self.snapshot.snapshot_sha256)
        except (ValueError, OSError, TypeError):
            return "REVOKE_STORAGE_FAILED"

    @property
    def cleanup_future(self) -> Future | None:
        """Off-path cleanup receipt, useful to postmatch tests and diagnostics."""
        return self._cleanup_future

    def _finish(self, local: Future, gpt: BoundGPTAdvice) -> ParallelShadowResult:
        written = False
        try:
            self._current()
            if not isinstance(gpt, BoundGPTAdvice):
                return ParallelShadowResult("REFUSED", "INVALID_GPT_CAPTURE")
            diagnostic, elapsed, refusal = local.result()
            if elapsed > 600_000:
                return ParallelShadowResult("REFUSED", "LOCAL_DURATION_INVALID")
            completed = self.clock()
            remaining = max(0, round((
                self.snapshot.window.deadline_monotonic - completed)*1000))
            if remaining > 600_000:
                return ParallelShadowResult("REFUSED", "DEADLINE_INVALID")
            current = self._current()
            record = build_shadow_comparison(
                self.snapshot, current, gpt, diagnostic,
                local_elapsed_ms=elapsed, remaining_ms_at_capture=remaining,
                local_refusal_reason=refusal)
            self._current()
            status = append_shadow_comparison(
                self.database_path, record,
                active_at_commit=self._active_at_commit)
            written = status in ("RECORDED", "ALREADY_RECORDED")
            # Only this fast in-memory lock establishes whether the record
            # finished *before* user OFF / a new revision. Never hold it
            # across the SQLite transaction or block the Qt thread for IO.
            self._current()
            with self._lock:
                revoked = self._closed or self._revoked.is_set()
                if not revoked:
                    self._persisted = True
            if revoked:
                # A future may complete BEFORE the queued cleanup job;
                # proactively remove any row so future.result() cannot be
                # interpreted as successful old-window evidence.
                self._revoke_unfinished()
                return ParallelShadowResult("REFUSED", "REVOKED_DURING_WRITE")
            return ParallelShadowResult(status, "NONACTIONABLE_DIAGNOSTIC", record)
        except (ValueError, TypeError, AttributeError, OSError):
            if written:
                self._revoke_unfinished()
            return ParallelShadowResult("REFUSED", "STALE_OR_UNVERIFIED_WINDOW")
        except Exception:
            if written:
                self._revoke_unfinished()
            return ParallelShadowResult("REFUSED", "OFFPATH_STORAGE_FAILURE")

    def close(self) -> None:
        # The only lock shared with _finish is IN-MEMORY and never covers
        # SQLite IO. It linearizes a successful receipt vs user OFF/revision;
        # the Qt thread never waits for a disk transaction (up to 3 seconds).
        with self._lock:
            if self._closed:
                return
            self._revoked.set()
            self._closed = True
            if self._pool is not None:
                if self._joined and not self._persisted:
                    # Even if close races *after* the last precommit check,
                    # this task is enqueued behind the running _finish and
                    # removes that exact stale row before cleanup completes.
                    self._cleanup_future = self._pool.submit(
                        self._revoke_unfinished)
                # Do not cancel the queued cleanup. _finish sees revoked,
                # skips/rolls back, and the serialized revocation finishes.
                self._pool.shutdown(wait=False, cancel_futures=False)

