"""Pure UI-side control plane for opt-in, noninterfering LOCAL_SHADOW.

Never imports Qt, reads frozen master files or changes the GPT response.
Current screen/PP/ability unknown => NO_RANKING, not a fabricated Battle.
The real source of a paired GPT dispatch is the caller's actual dispatch ID.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .live_shadow_dispatch import FrozenRevisionLatch, capture_gpt_dispatch_window
from .parallel_shadow_session import ParallelShadowSession
from .shadow_comparison_store import BoundGPTAdvice


class UiShadowDispatcher:
    """All public methods are invoked on the GUI thread; only the immutable
    FrozenRevisionLatch.current is called from the background pool.
    """

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.session: ParallelShadowSession | None = None
        self.latch: FrozenRevisionLatch | None = None
        self.request_id: str | None = None
        self.turn_epoch: int | None = None
        self.revision: int | None = None
        self.last_future: Any | None = None

    def prepare_storage(self) -> str:
        """Only on explicit user opt-in, before dispatch; fail closed on IO."""
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            return "READY"
        except (OSError, ValueError):
            return "STORAGE_UNAVAILABLE"

    def invalidate(self) -> None:
        latch, session = self.latch, self.session
        self.latch = None
        self.session = None
        self.request_id = None
        self.turn_epoch = None
        self.revision = None
        if latch is not None:
            latch.invalidate()
        if session is not None:
            session.close()

    def start(
        self, assets: Any, *,
        enabled: bool, match_id: str, request_id: str,
        turn_epoch: int, state_revision: int, screen_label: str,
        observed_state: Mapping[str, Any],
        deadline_monotonic: float | None,
    ) -> str:
        """Call at the SAME moment as the actual outgoing GPT dispatch."""
        self.invalidate()
        if not enabled:
            return "DISABLED"
        if not self.database_path.parent.is_dir():
            return "REFUSED:STORAGE_NOT_PREPARED"
        if deadline_monotonic is None:
            return "NON_ACTION_WINDOW"
        captured = capture_gpt_dispatch_window(
            assets, match_id=match_id, request_id=request_id,
            turn_epoch=turn_epoch, state_revision=state_revision,
            screen_label=screen_label, observed_state=observed_state,
            deadline_monotonic=deadline_monotonic)
        if captured.snapshot is None:
            return "REFUSED:" + captured.reason
        latch = FrozenRevisionLatch(captured.snapshot)
        own = observed_state.get("our_active") or "UNKNOWN"
        session = ParallelShadowSession(
            captured.snapshot, assets, database_path=self.database_path,
            current_snapshot=latch.current,
            preserve=own, stopper=own, enabled=True)
        started = session.start()
        if started != "RUNNING":
            latch.invalidate()
            session.close()
            return "REFUSED:" + started
        self.latch, self.session = latch, session
        self.request_id, self.turn_epoch, self.revision = (
            request_id, turn_epoch, state_revision)
        return "RUNNING_UNVERIFIED"

    def invalidate_if_changed(self, *, turn_epoch: int,
                              state_revision: int, phase: str) -> bool:
        if self.session is None:
            return False
        if (self.turn_epoch != turn_epoch or
                self.revision != state_revision or phase == "ENDED"):
            self.invalidate()
            return True
        return False

    def attach_gpt(self, *, request_id: str, turn_epoch: int,
                   state_revision: int, status: str,
                   advice_present: bool, elapsed_ms: int,
                   fresh: bool) -> str:
        """Nonblocking: returns immediately and never examines GPT free text."""
        if self.session is None:
            return "NOT_RUNNING"
        if (not fresh or self.request_id != request_id
                or self.turn_epoch != turn_epoch or
                self.revision != state_revision):
            self.invalidate()
            return "STALE_REFUSED"
        if (type(elapsed_ms) is not int or elapsed_ms < 0
                or elapsed_ms > 600000):
            self.invalidate()
            return "INVALID_GPT_TIMING"
        if status in {"CANCELLED", "ERROR", "SUPERSEDED",
                      "DEADLINE_EXCEEDED", "MATCH_END"}:
            self.invalidate()
            return "GPT_NOT_CURRENT"
        try:
            # The GPT output occurred but no real Champions legal-command
            # attestation was supplied. Do not manufacture a hashed action.
            gpt = BoundGPTAdvice(
                self.session.snapshot.snapshot_sha256,
                "GPT_UNVERIFIED" if advice_present else "GPT_ABSTAIN",
                elapsed_ms=elapsed_ms)
            future = self.session.attach_gpt(gpt)
            if future is None:
                return "DUPLICATE_OR_CLOSED"
            self.last_future = future
            return "JOIN_SUBMITTED_NONACTIONABLE"
        except (ValueError, TypeError, AttributeError):
            self.invalidate()
            return "GPT_CAPTURE_REFUSED"
