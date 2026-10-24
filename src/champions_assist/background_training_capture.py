"""Optional local training-frame collection from *existing* live capture frames.

No second screen capture, model call or inference, and no filesystem work on the
Qt/live-decision thread. All JPEG writes happen on one bounded, lower-priority
daemon worker. Saved images remain UNLABELLED; human review is mandatory.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import monotonic
from typing import Callable

import cv2
import numpy as np

from .screen_dataset import StagingLimitReached, UnlabelledScreenStaging, validate_session


@dataclass(frozen=True)
class CaptureStats:
    saved: int
    dropped: int
    duplicates: int
    error: str | None
    running: bool


class BackgroundTrainingCapture:
    """Offer already captured RGB frames without doing any disk IO in offer().

    Default interval limits disk traffic. At capacity, silently drop training
    candidates, never an AI decision. A stop prevents queued frames from being
    written, though the one in-flight fsync may finish.
    """

    def __init__(
        self, root: Path | str, session_id: str, *,
        interval_seconds: float = 0.5, max_queue: int = 3,
        quota_bytes: int = 2 * 1024**3,
        reserve_bytes: int = 512 * 1024**2,
        clock: Callable[[], float] = monotonic,
        sink: UnlabelledScreenStaging | None = None,
    ) -> None:
        if interval_seconds < 0.2 or max_queue < 1 or max_queue > 10:
            raise ValueError("INVALID_BACKGROUND_CAPTURE_BOUNDS")
        self.session_id = validate_session(session_id)
        self.root = Path(root)
        self._clock = clock
        self._interval = interval_seconds
        self._sink = sink or UnlabelledScreenStaging(
            self.root, quota_bytes=quota_bytes, reserve_bytes=reserve_bytes,
        )
        self._queue: Queue[np.ndarray] = Queue(maxsize=max_queue)
        self._stop_event = Event()
        self._lock = Lock()
        self._last_offer: float | None = None
        self._saved = 0
        self._dropped = 0
        self._duplicates = 0
        self._error: str | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or self._stop_event.is_set():
                raise RuntimeError("BACKGROUND_CAPTURE_ALREADY_STARTED_OR_STOPPED")
            self._thread = Thread(
                target=self._run, name="ChampionsTrainingCapture", daemon=True,
            )
            self._thread.start()

    def offer(self, image: np.ndarray) -> bool:
        """Constant-time offer; never encode, resize, or write on live UI."""
        if self._stop_event.is_set():
            return False
        if (not isinstance(image, np.ndarray) or image.ndim != 3
                or image.shape[2] != 3 or image.dtype != np.uint8 or not image.size):
            return False
        now = self._clock()
        with self._lock:
            if self._thread is None or self._error or self._stop_event.is_set():
                return False
            if self._last_offer is not None and now - self._last_offer < self._interval:
                return False
            self._last_offer = now
            try:
                # Capture yields independent RGB arrays. The worker holds a
                # reference, not a duplicate, to avoid an extra image copy.
                self._queue.put_nowait(image)
            except Full:
                self._dropped += 1
                return False
            return True

    def stop(self, *, wait: bool = False) -> None:
        self._stop_event.set()
        # Always discard pending training candidates, not live decision frames.
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                break
        if wait and self._thread is not None:
            self._thread.join(timeout=5.0)

    def stats(self) -> CaptureStats:
        with self._lock:
            return CaptureStats(
                saved=self._saved, dropped=self._dropped,
                duplicates=self._duplicates, error=self._error,
                running=(self._thread is not None and
                         not self._stop_event.is_set() and self._error is None),
            )

    def _run(self) -> None:
        last_signature: bytes | None = None
        while not self._stop_event.is_set():
            try:
                image = self._queue.get(timeout=0.1)
            except Empty:
                continue
            if self._stop_event.is_set():
                break
            try:
                # Only remove truly identical small signatures. Distinct text,
                # menu highlights and transition frames should be retained.
                signature = cv2.resize(
                    image, (96, 60), interpolation=cv2.INTER_AREA
                ).tobytes()
                if signature == last_signature:
                    with self._lock:
                        self._duplicates += 1
                    continue
                self._sink.save_unlabelled(image, self.session_id)
                last_signature = signature
                with self._lock:
                    self._saved += 1
            except (StagingLimitReached, OSError, ValueError, cv2.error) as exc:
                with self._lock:
                    self._error = str(exc) if isinstance(exc, StagingLimitReached) else type(exc).__name__
                self._stop_event.set()
                break
