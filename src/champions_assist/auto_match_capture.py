"""Opt-in read-only capture -> in-memory ChatGPT vision input.

No screenshot file is created. The selected Win32 identity or selected ADB
serial and configured game viewport are mandatory. No desktop fallback, no
mouse/keyboard input. The caller owns the AI session and explicit start/stop.
"""
from __future__ import annotations

import hashlib
from time import monotonic
from typing import Callable

import cv2
import numpy as np

from .app_config import CaptureConfiguration
from .capture_settings import CapturePreviewService
from .roi import NormalizedROI


class AutoCaptureUnavailable(RuntimeError):
    pass


def capture_for_chatgpt(config: CaptureConfiguration,
                        *, preview: CapturePreviewService | None = None) -> np.ndarray:
    """Acquire one explicitly scoped viewport in memory, RGB uint8."""
    if config.backend == "WINDOWS_NATIVE":
        if not config.selected_window_identity:
            raise AutoCaptureUnavailable("WINDOW_NOT_SELECTED")
    elif config.backend == "ADB_SCREENSHOT":
        if not config.adb_serial or not config.adb_path:
            raise AutoCaptureUnavailable("ADB_NOT_CONFIGURED")
    else:
        raise AutoCaptureUnavailable("LIVE_CAPTURE_BACKEND_REQUIRED")
    if config.game_viewport_normalized is None:
        raise AutoCaptureUnavailable("GAME_VIEWPORT_ROI_REQUIRED")
    result = (preview or CapturePreviewService()).preview_once(config)
    if result.image is None or result.status != "CAPTURED_READ_ONLY_UNVERIFIED_CONTENT":
        raise AutoCaptureUnavailable("CAPTURE_UNAVAILABLE")
    try:
        image = NormalizedROI("GAME_VIEWPORT", *config.game_viewport_normalized).crop(result.image)
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("invalid RGB capture")
        return image
    except ValueError as exc:
        raise AutoCaptureUnavailable("CAPTURE_ROI_INVALID") from exc


def _signature(image: np.ndarray) -> bytes:
    """Ignore small timer/animation changes instead of re-billing every frame."""
    small = cv2.resize(image, (48, 30), interpolation=cv2.INTER_AREA)
    grey = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    return (grey // 16).astype("uint8").tobytes()


class FrameChangeGate:
    def __init__(self, *, clock: Callable[[], float] = monotonic,
                 minimum_interval: float = 2.0, different_fraction: float = 0.10):
        self.clock = clock
        self.minimum_interval = minimum_interval
        self.different_fraction = different_fraction
        self._previous: bytes | None = None
        self._last_at: float | None = None

    def reset(self) -> None:
        self._previous = None
        self._last_at = None

    def accept(self, image: np.ndarray) -> bool:
        now = self.clock()
        signature = _signature(image)
        if self._last_at is not None and now - self._last_at < self.minimum_interval:
            return False
        if self._previous is not None:
            diff = sum(a != b for a, b in zip(signature, self._previous)) / len(signature)
            if diff < self.different_fraction:
                return False
        self._last_at = now
        self._previous = signature
        return True
