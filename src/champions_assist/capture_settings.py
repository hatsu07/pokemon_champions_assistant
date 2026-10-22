"""Explicit, read-only preview helpers used by the desktop settings dialog."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic_ns
from typing import Callable

import cv2
import numpy as np

from .adb_capture import AdbScreenshotFrameSource
from .app_config import CaptureConfiguration, ConfigurationError
from .capture import CaptureUnavailable, FrameSource
from .roi import NormalizedROI
from .windows_capture import (
    WindowCandidate,
    Win32WindowGeometryProvider,
    WindowsNativeFrameSource,
    candidate_matches_identity,
    enumerate_visible_windows,
)


def _now_ms() -> int:
    return monotonic_ns() // 1_000_000


@dataclass(frozen=True)
class CapturePreviewResult:
    status: str
    message: str
    image: np.ndarray | None = None
    capture_method: str | None = None
    image_size: tuple[int, int] | None = None
    client_bbox: tuple[int, int, int, int] | None = None
    dpi: int | None = None
    acquisition_ms: int | None = None
    target: WindowCandidate | None = None


class CapturePreviewService:
    """Perform a one-frame preview only after an explicit target selection.

    GUI callers run this service in a worker thread.  It never falls back to a
    desktop grab, launches a process, focuses a window, or sends game input.
    """

    def __init__(
        self,
        *,
        window_enumerator: Callable[[], tuple[WindowCandidate, ...]] = enumerate_visible_windows,
        native_source_factory: Callable[[WindowCandidate], FrameSource] | None = None,
        adb_source_factory: Callable[[CaptureConfiguration], FrameSource] | None = None,
        clock_ms: Callable[[], int] = _now_ms,
    ) -> None:
        self._window_enumerator = window_enumerator
        self._native_source_factory = native_source_factory or self._default_native_source
        self._adb_source_factory = adb_source_factory or self._default_adb_source
        self._clock_ms = clock_ms

    @staticmethod
    def _default_native_source(candidate: WindowCandidate) -> FrameSource:
        return WindowsNativeFrameSource(
            window_title=candidate.title,
            geometry_provider=Win32WindowGeometryProvider(
                candidate.title, expected_hwnd=candidate.hwnd, expected_pid=candidate.pid,
            ),
            max_frames=1,
        )

    @staticmethod
    def _default_adb_source(config: CaptureConfiguration) -> FrameSource:
        if not config.adb_path or not config.adb_serial:
            raise ConfigurationError("ADB preview requires an explicit executable path and serial")
        return AdbScreenshotFrameSource(config.adb_serial, adb_path=config.adb_path, max_frames=1)

    def list_windows(self) -> tuple[WindowCandidate, ...]:
        return self._window_enumerator()

    def preview_once(self, config: CaptureConfiguration) -> CapturePreviewResult:
        started = self._clock_ms()
        target: WindowCandidate | None = None
        try:
            if config.backend == "WINDOWS_NATIVE":
                target = next(
                    (candidate for candidate in self.list_windows() if candidate_matches_identity(candidate, config.selected_window_identity)),
                    None,
                )
                if target is None:
                    return CapturePreviewResult("WINDOW_IDENTITY_UNAVAILABLE", "選択済みウィンドウが消失または再生成されました")
                if target.minimized:
                    return CapturePreviewResult("WINDOW_MINIMIZED", "最小化中のウィンドウはプレビューできません", target=target)
                source = self._native_source_factory(target)
            elif config.backend == "ADB_SCREENSHOT":
                source = self._adb_source_factory(config)
            else:
                return CapturePreviewResult("REPLAY_PREVIEW_NOT_CONFIGURED", "画像/録画リプレイは既存のオフライン再生CLIで確認してください")
            frame = next(iter(source))
            elapsed = max(0, self._clock_ms() - started)
            geometry = getattr(source, "last_geometry", None)
            method = getattr(source, "last_capture_method", None)
            height, width = frame.image.shape[:2]
            return CapturePreviewResult(
                "CAPTURED_READ_ONLY_UNVERIFIED_CONTENT",
                "取得内容は手動で確認してください。OCR較正済みを意味しません。",
                frame.image.copy(), method, (width, height),
                None if geometry is None else geometry.client_bbox,
                None if geometry is None else geometry.dpi,
                elapsed, target,
            )
        except (CaptureUnavailable, ConfigurationError, StopIteration) as exc:
            return CapturePreviewResult("CAPTURE_FAILED", type(exc).__name__, target=target)

    @staticmethod
    def save_explicit_frame(result: CapturePreviewResult, destination: str | Path, *, roi: tuple[float, float, float, float] | None = None) -> Path:
        """Save only a caller-confirmed preview image; never overwrite evidence."""
        if result.image is None:
            raise ConfigurationError("there is no captured preview to save")
        image = result.image
        if roi is not None:
            try:
                image = NormalizedROI("GAME_VIEWPORT", *roi).crop(image)
            except (ValueError, TypeError) as exc:
                raise ConfigurationError("invalid game viewport ROI for saved preview") from exc
        target = Path(destination)
        if target.exists():
            raise ConfigurationError("refusing to overwrite an existing capture")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(target), cv2.cvtColor(image, cv2.COLOR_RGB2BGR)):
            raise ConfigurationError("preview image could not be saved")
        return target
