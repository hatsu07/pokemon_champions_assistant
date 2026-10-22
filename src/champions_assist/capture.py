"""Frame-source interfaces for offline analysis and explicit capture adapters.

All sources are read-only.  They return pixels only and contain no input/tap/key
operations.  The Windows and ADB implementations live in their own modules so
the Docker image can exercise image analysis without claiming a working capture
path to BlueStacks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Iterator, Protocol

import cv2
import numpy as np


class CaptureUnavailable(RuntimeError):
    """Raised only when an explicitly selected capture backend is unavailable."""


@dataclass(frozen=True)
class Frame:
    """One immutable-by-convention RGB capture supplied to the observer."""

    frame_id: str
    capture_seq: int
    captured_at_ms: int
    image: np.ndarray = field(repr=False, compare=False)
    source: str = "UNKNOWN"
    reference: str | None = None

    def __post_init__(self) -> None:
        image = self.image
        if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("frame image must be an HxWx3 RGB numpy array")
        if image.dtype != np.uint8:
            raise ValueError("frame image must use uint8 pixels")
        if self.capture_seq < 0 or self.captured_at_ms < 0:
            raise ValueError("capture sequence and timestamp must be non-negative")


class FrameSource(Protocol):
    """A pull-based, read-only source of RGB frames."""

    def __iter__(self) -> Iterator[Frame]: ...


class LiveCaptureBackend(str, Enum):
    WINDOWS_NATIVE = "WINDOWS_NATIVE"
    ADB_SCREENSHOT = "ADB_SCREENSHOT"


def create_live_frame_source(
    backend: LiveCaptureBackend = LiveCaptureBackend.WINDOWS_NATIVE,
    *,
    windows_bbox: tuple[int, int, int, int] | None = None,
    windows_title: str | None = None,
    adb_serial: str | None = None,
    max_frames: int | None = None,
) -> FrameSource:
    """Select an explicit live capture adapter; Windows native is the default.

    There is no silent fallback.  An ADB route can be selected after secure
    local connectivity has been verified, but neither route performs input.
    """
    if backend is LiveCaptureBackend.WINDOWS_NATIVE:
        from .windows_capture import WindowsNativeFrameSource

        return WindowsNativeFrameSource(bbox=windows_bbox, window_title=windows_title, max_frames=max_frames)
    if backend is LiveCaptureBackend.ADB_SCREENSHOT:
        if adb_serial is None:
            raise ValueError("ADB fallback requires an explicit serial")
        from .adb_capture import AdbScreenshotFrameSource

        return AdbScreenshotFrameSource(adb_serial, max_frames=max_frames)
    raise ValueError(f"unsupported live capture backend: {backend}")


class SyntheticFrameSource:
    """Fixture-only source.  Its use is explicit in replay output and tests."""

    def __init__(self, frames: Iterable[Frame]) -> None:
        self._frames = tuple(frames)

    def __iter__(self) -> Iterator[Frame]:
        yield from self._frames


class ImageFileFrameSource:
    """Read still images in the supplied order; no image is modified."""

    def __init__(self, paths: Iterable[str | Path], *, start_at_ms: int = 0, step_ms: int = 100) -> None:
        self._paths = tuple(Path(item) for item in paths)
        self._start_at_ms = start_at_ms
        self._step_ms = step_ms

    def __iter__(self) -> Iterator[Frame]:
        for sequence, path in enumerate(self._paths, start=1):
            bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if bgr is None:
                raise CaptureUnavailable(f"cannot read image frame: {path}")
            yield Frame(
                frame_id=f"image:{sequence}:{path.name}",
                capture_seq=sequence,
                captured_at_ms=self._start_at_ms + (sequence - 1) * self._step_ms,
                image=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB),
                source="IMAGE_FILE",
                reference=str(path),
            )


class VideoFrameSource:
    """Offline video decoder.  Codec compatibility is a host/runtime concern."""

    def __init__(self, path: str | Path, *, max_frames: int | None = None) -> None:
        self.path = Path(path)
        self.max_frames = max_frames

    def __iter__(self) -> Iterator[Frame]:
        capture = cv2.VideoCapture(str(self.path))
        if not capture.isOpened():
            raise CaptureUnavailable(f"cannot open video: {self.path}")
        try:
            sequence = 0
            while self.max_frames is None or sequence < self.max_frames:
                ok, bgr = capture.read()
                if not ok:
                    break
                sequence += 1
                timestamp = capture.get(cv2.CAP_PROP_POS_MSEC)
                yield Frame(
                    frame_id=f"video:{sequence}",
                    capture_seq=sequence,
                    captured_at_ms=max(0, int(timestamp)),
                    image=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB),
                    source="VIDEO_FILE",
                    reference=str(self.path),
                )
        finally:
            capture.release()
