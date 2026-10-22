"""Read-only capture worker and timing evidence used by Phase 6 validation.

This module has no Qt dependency and no game-control surface.  It deliberately
keeps capture on a worker thread; a GUI must receive immutable notifications
from the supplied callback instead of running OCR/capture in a paint handler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from threading import Event, Thread
from time import monotonic_ns
from typing import Callable, Iterable

from .capture import CaptureUnavailable, Frame, FrameSource


def monotonic_ms() -> int:
    return monotonic_ns() // 1_000_000


def percentile(values: Iterable[float], percentage: float) -> float | None:
    """Linear percentile, with a visible ``None`` for no measured samples."""
    ordered = sorted(values)
    if not ordered:
        return None
    if not 0.0 <= percentage <= 100.0:
        raise ValueError("percentage must be between 0 and 100")
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * percentage / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def latency_summary(values: Iterable[float]) -> dict[str, float | int | None]:
    measured = tuple(float(value) for value in values)
    return {
        "samples": len(measured),
        "p50_ms": percentile(measured, 50.0),
        "p95_ms": percentile(measured, 95.0),
        "max_ms": None if not measured else max(measured),
    }


@dataclass(frozen=True)
class CaptureFailure:
    at_ms: int
    reason: str


@dataclass
class CaptureMetrics:
    """Capture-side facts.  Dropped-frame count is an interval estimate only."""

    expected_fps: float | None = None
    frames: int = 0
    capture_arrivals_ms: list[int] = field(default_factory=list)
    capture_lag_ms: list[float] = field(default_factory=list)
    failures: list[CaptureFailure] = field(default_factory=list)
    recoveries_ms: list[float] = field(default_factory=list)
    estimated_dropped_frames: int = 0
    _last_failure_at_ms: int | None = None

    def record_frame(self, frame: Frame, *, arrived_at_ms: int) -> None:
        if self.capture_arrivals_ms:
            interval = arrived_at_ms - self.capture_arrivals_ms[-1]
            if interval < 0:
                raise ValueError("capture arrival clock cannot go backwards")
            if self.expected_fps and self.expected_fps > 0:
                expected_interval = 1000.0 / self.expected_fps
                if interval > expected_interval * 1.5:
                    self.estimated_dropped_frames += max(0, int(interval // expected_interval) - 1)
        if arrived_at_ms >= frame.captured_at_ms:
            self.capture_lag_ms.append(float(arrived_at_ms - frame.captured_at_ms))
        self.capture_arrivals_ms.append(arrived_at_ms)
        self.frames += 1
        if self._last_failure_at_ms is not None:
            self.recoveries_ms.append(float(arrived_at_ms - self._last_failure_at_ms))
            self._last_failure_at_ms = None

    def record_failure(self, reason: str, *, at_ms: int) -> None:
        normalized = reason[:120] if reason else "CAPTURE_UNAVAILABLE"
        self.failures.append(CaptureFailure(at_ms, normalized))
        if self._last_failure_at_ms is None:
            self._last_failure_at_ms = at_ms

    def as_dict(self) -> dict[str, object]:
        intervals = [
            float(current - previous)
            for previous, current in zip(self.capture_arrivals_ms, self.capture_arrivals_ms[1:])
        ]
        elapsed = 0 if len(self.capture_arrivals_ms) < 2 else self.capture_arrivals_ms[-1] - self.capture_arrivals_ms[0]
        fps = None if elapsed <= 0 else (len(self.capture_arrivals_ms) - 1) * 1000.0 / elapsed
        return {
            "frames": self.frames,
            "measured_fps": fps,
            "frame_interval": latency_summary(intervals),
            "capture_lag": latency_summary(self.capture_lag_ms),
            "estimated_dropped_frames": self.estimated_dropped_frames if self.expected_fps else None,
            "failure_count": len(self.failures),
            "failure_reasons": [failure.reason for failure in self.failures],
            "recovery": latency_summary(self.recoveries_ms),
        }


@dataclass
class LatencyLedger:
    """Correlate one frame ID across capture, observer and GUI notification."""

    _captured_at: dict[str, int] = field(default_factory=dict)
    _observer_done_at: dict[str, int] = field(default_factory=dict)
    _gui_done_at: dict[str, int] = field(default_factory=dict)

    def captured(self, frame_id: str, at_ms: int) -> None:
        self._captured_at.setdefault(frame_id, at_ms)

    def observer_completed(self, frame_id: str, at_ms: int) -> None:
        if frame_id in self._captured_at and at_ms >= self._captured_at[frame_id]:
            self._observer_done_at.setdefault(frame_id, at_ms)

    def gui_presented(self, frame_id: str, at_ms: int) -> None:
        if frame_id in self._captured_at and at_ms >= self._captured_at[frame_id]:
            self._gui_done_at.setdefault(frame_id, at_ms)

    def as_dict(self) -> dict[str, object]:
        capture_to_observer = [
            float(completed - self._captured_at[frame_id])
            for frame_id, completed in self._observer_done_at.items()
            if frame_id in self._captured_at
        ]
        capture_to_gui = [
            float(completed - self._captured_at[frame_id])
            for frame_id, completed in self._gui_done_at.items()
            if frame_id in self._captured_at
        ]
        return {
            "capture_to_observer": latency_summary(capture_to_observer),
            "capture_to_gui": latency_summary(capture_to_gui),
            "unpresented_captured_frames": len(set(self._captured_at) - set(self._gui_done_at)),
        }


FrameSourceFactory = Callable[[], FrameSource]
FrameCallback = Callable[[Frame], None]


class CaptureWorker:
    """Restart a read-only source after window/ADB loss without blocking a GUI.

    The source factory is intentionally called again after failure.  A native
    source therefore reprobes a newly started, resized, restored or reconnected
    target.  The worker publishes frames through a callback only; it does not
    share a SQLite connection or a mutable GUI model across threads.
    """

    def __init__(
        self,
        source_factory: FrameSourceFactory,
        on_frame: FrameCallback,
        *,
        expected_fps: float | None = None,
        retry_delay_ms: int = 250,
        clock_ms: Callable[[], int] = monotonic_ms,
    ) -> None:
        if retry_delay_ms < 0:
            raise ValueError("retry_delay_ms cannot be negative")
        self._source_factory = source_factory
        self._on_frame = on_frame
        self._retry_delay_ms = retry_delay_ms
        self._clock_ms = clock_ms
        self.metrics = CaptureMetrics(expected_fps=expected_fps)
        self.latency = LatencyLedger()
        self._stop = Event()
        self._thread: Thread | None = None

    def run(self, *, frame_limit: int | None = None, duration_ms: int | None = None) -> CaptureMetrics:
        if frame_limit is not None and frame_limit < 1:
            raise ValueError("frame_limit must be positive when supplied")
        if duration_ms is not None and duration_ms < 1:
            raise ValueError("duration_ms must be positive when supplied")
        started = self._clock_ms()
        while not self._stop.is_set():
            if frame_limit is not None and self.metrics.frames >= frame_limit:
                break
            if duration_ms is not None and self._clock_ms() - started >= duration_ms:
                break
            delivered = False
            try:
                for frame in self._source_factory():
                    arrived_at = self._clock_ms()
                    self.metrics.record_frame(frame, arrived_at_ms=arrived_at)
                    self.latency.captured(frame.frame_id, frame.captured_at_ms)
                    self._on_frame(frame)
                    delivered = True
                    if self._stop.is_set() or (frame_limit is not None and self.metrics.frames >= frame_limit):
                        break
            except CaptureUnavailable as exc:
                self.metrics.record_failure(str(exc), at_ms=self._clock_ms())
            except Exception as exc:  # keep a live observer alive, without leaking data in status text
                self.metrics.record_failure(f"CAPTURE_EXCEPTION:{type(exc).__name__}", at_ms=self._clock_ms())
            if self._stop.is_set() or (frame_limit is not None and self.metrics.frames >= frame_limit):
                break
            if not delivered:
                self._stop.wait(self._retry_delay_ms / 1000.0)
        return self.metrics

    def start(self, *, frame_limit: int | None = None, duration_ms: int | None = None) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("capture worker is already running")
        self._stop.clear()
        self._thread = Thread(
            target=lambda: self.run(frame_limit=frame_limit, duration_ms=duration_ms),
            name="champions-assist-capture",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, join_timeout_seconds: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(join_timeout_seconds)
            if self._thread.is_alive():
                raise RuntimeError("capture worker did not stop within the requested timeout")


def compare_capture_reports(reports: dict[str, dict[str, object]]) -> dict[str, object]:
    """Compare already measured reports without selecting a production backend.

    A Linux/synthetic report, a failed probe, or a single backend is explicitly
    inconclusive.  Backend selection remains a human validation decision.
    """
    required = {"WINDOWS_NATIVE", "ADB_SCREENSHOT"}
    missing = sorted(required - set(reports))
    usable: dict[str, float] = {}
    for backend, report in reports.items():
        metrics = report.get("metrics")
        if not isinstance(metrics, dict) or int(metrics.get("frames", 0)) < 1:
            continue
        latency = metrics.get("capture_lag")
        if isinstance(latency, dict) and isinstance(latency.get("p95_ms"), (int, float)):
            usable[backend] = float(latency["p95_ms"])
    return {
        "comparison_status": "INCONCLUSIVE" if missing or set(usable) != required else "MEASURED_REVIEW_REQUIRED",
        "missing_backends": missing,
        "capture_lag_p95_ms": usable,
        "automatic_recommendation": None,
        "reason": "Human review must confirm target content, OCR quality, and terms before a backend is chosen.",
        "runtime_platform": os.name,
    }
