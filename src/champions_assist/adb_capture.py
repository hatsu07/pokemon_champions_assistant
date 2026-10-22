"""Explicit ADB screenshot fallback.  This module has no game-control calls."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
from time import monotonic_ns
from typing import Iterator

import cv2
import numpy as np

from .capture import CaptureUnavailable, Frame


@dataclass(frozen=True)
class AdbConnectionStatus:
    """Read-only ADB availability result, not a BlueStacks-content assertion."""

    status: str
    detail: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"status": self.status, "detail": self.detail}


class AdbScreenshotFrameSource:
    """Capture `adb exec-out screencap -p` only when a caller selects it."""

    verification_status = "UNVERIFIED_ON_BLUESTACKS"

    def __init__(self, serial: str, *, adb_path: str = "adb", max_frames: int | None = None, timeout_seconds: float = 3.0) -> None:
        if not serial:
            raise ValueError("an explicit ADB serial is required")
        self.serial = serial
        self.adb_path = adb_path
        self.max_frames = max_frames
        self.timeout_seconds = timeout_seconds

    def connection_status(self) -> AdbConnectionStatus:
        """Use only ``get-state``; this method never starts a server or sends input."""
        try:
            result = subprocess.run(
                [self.adb_path, "-s", self.serial, "get-state"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return AdbConnectionStatus("ADB_UNAVAILABLE", type(exc).__name__)
        state = result.stdout.decode("utf-8", errors="replace").strip()
        if result.returncode == 0 and state == "device":
            return AdbConnectionStatus("ADB_DEVICE_READY")
        detail = result.stderr.decode("utf-8", errors="replace").strip() or state or f"exit:{result.returncode}"
        return AdbConnectionStatus("ADB_DEVICE_NOT_READY", detail[:200])

    def __iter__(self) -> Iterator[Frame]:
        sequence = 0
        while self.max_frames is None or sequence < self.max_frames:
            try:
                result = subprocess.run(
                    [self.adb_path, "-s", self.serial, "exec-out", "screencap", "-p"],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout_seconds,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise CaptureUnavailable("ADB screenshot capture failed") from exc
            bgr = cv2.imdecode(np.frombuffer(result.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
            if bgr is None:
                raise CaptureUnavailable("ADB returned a non-PNG screenshot")
            sequence += 1
            yield Frame(
                frame_id=f"adb:{sequence}",
                capture_seq=sequence,
                captured_at_ms=monotonic_ns() // 1_000_000,
                image=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB),
                source="ADB_UNVERIFIED",
            )
