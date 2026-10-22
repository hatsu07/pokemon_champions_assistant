"""Windows-native, read-only capture with an explicit BlueStacks boundary.

The module only reads pixels.  It neither starts nor stops BlueStacks and has
no tap, click, key, or ADB input operation.  A successful call proves only
that pixels were read from a requested window; validation must still label the
content and review the resulting evidence on the actual Windows machine.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from time import monotonic_ns
from typing import Callable, Iterator, Protocol

import numpy as np

from .capture import CaptureUnavailable, Frame


@dataclass(frozen=True)
class WindowGeometry:
    """Current client-area geometry in screen pixels, never guessed."""

    status: str
    title: str | None = None
    hwnd: int | None = None
    client_bbox: tuple[int, int, int, int] | None = None
    dpi: int | None = None

    @property
    def available(self) -> bool:
        return self.status == "AVAILABLE" and self.client_bbox is not None

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "title": self.title,
            "hwnd": self.hwnd,
            "client_bbox": None if self.client_bbox is None else list(self.client_bbox),
            "dpi": self.dpi,
            "coordinate_space": "CLIENT_AREA_SCREEN_PIXELS",
        }


class WindowGeometryProvider(Protocol):
    """Read the requested window's state without changing it."""

    def probe(self) -> WindowGeometry: ...


class Win32WindowGeometryProvider:
    """Find one visible window by caller-supplied title text.

    The client rectangle is refreshed before every frame so resize and DPI
    changes do not reuse stale coordinates.  Windows API availability is not
    evidence that the target is a BlueStacks/Champions window.
    """

    def __init__(self, title_substring: str) -> None:
        if not title_substring.strip():
            raise ValueError("a non-empty window title substring is required")
        self.title_substring = title_substring

    def probe(self) -> WindowGeometry:
        if os.name != "nt":
            return WindowGeometry("WINDOWS_RUNTIME_REQUIRED")
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        matches: list[tuple[int, str]] = []
        needle = self.title_substring.casefold()
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetClientRect.restype = wintypes.BOOL
        user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        user32.ClientToScreen.restype = wintypes.BOOL

        def visit(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            text = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, text, length + 1)
            if needle in text.value.casefold():
                matches.append((int(hwnd), text.value))
                return False
            return True

        user32.EnumWindows(callback_type(visit), 0)
        if not matches:
            return WindowGeometry("WINDOW_NOT_FOUND")
        hwnd, title = matches[0]
        if user32.IsIconic(hwnd):
            return WindowGeometry("WINDOW_MINIMIZED", title=title, hwnd=hwnd)
        rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return WindowGeometry("CLIENT_RECT_UNAVAILABLE", title=title, hwnd=hwnd)
        top_left = wintypes.POINT(rect.left, rect.top)
        bottom_right = wintypes.POINT(rect.right, rect.bottom)
        if not user32.ClientToScreen(hwnd, ctypes.byref(top_left)) or not user32.ClientToScreen(hwnd, ctypes.byref(bottom_right)):
            return WindowGeometry("CLIENT_COORDINATES_UNAVAILABLE", title=title, hwnd=hwnd)
        if top_left.x >= bottom_right.x or top_left.y >= bottom_right.y:
            return WindowGeometry("CLIENT_RECT_EMPTY", title=title, hwnd=hwnd)
        get_dpi = getattr(user32, "GetDpiForWindow", None)
        if get_dpi is not None:
            get_dpi.argtypes = [wintypes.HWND]
            get_dpi.restype = wintypes.UINT
        dpi = None if get_dpi is None else int(get_dpi(hwnd))
        return WindowGeometry(
            "AVAILABLE",
            title=title,
            hwnd=hwnd,
            client_bbox=(top_left.x, top_left.y, bottom_right.x, bottom_right.y),
            dpi=dpi,
        )


ImageGrabber = Callable[[tuple[int, int, int, int] | None], object]


def _monotonic_ms() -> int:
    return monotonic_ns() // 1_000_000


class WindowsNativeFrameSource:
    """Read pixels using Pillow's Windows path after explicit selection.

    ``window_title`` selects a dynamically measured client rectangle.  ``bbox``
    keeps the Phase 4 explicit-rectangle mode.  The caller may pass neither to
    deliberately capture the desktop, but this is unsuitable for an accepted
    BlueStacks measurement because it has no target-window evidence.
    """

    verification_status = "UNVERIFIED_ON_BLUESTACKS"

    def __init__(
        self,
        *,
        bbox: tuple[int, int, int, int] | None = None,
        window_title: str | None = None,
        max_frames: int | None = None,
        geometry_provider: WindowGeometryProvider | None = None,
        image_grabber: ImageGrabber | None = None,
        clock_ms: Callable[[], int] = _monotonic_ms,
        is_windows: bool | None = None,
    ) -> None:
        if bbox is not None and window_title is not None:
            raise ValueError("specify either bbox or window_title, not both")
        if bbox is not None and (len(bbox) != 4 or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]):
            raise ValueError("bbox must be a non-empty (left, top, right, bottom) rectangle")
        if max_frames is not None and max_frames < 1:
            raise ValueError("max_frames must be positive when supplied")
        self.bbox = bbox
        self.window_title = window_title
        self.max_frames = max_frames
        self._geometry_provider = geometry_provider or (None if window_title is None else Win32WindowGeometryProvider(window_title))
        self._image_grabber = image_grabber
        self._clock_ms = clock_ms
        self._is_windows = os.name == "nt" if is_windows is None else is_windows
        self.last_geometry: WindowGeometry | None = None

    def _grab(self, bbox: tuple[int, int, int, int] | None) -> np.ndarray:
        if self._image_grabber is not None:
            image = self._image_grabber(bbox)
        else:
            try:
                from PIL import ImageGrab
            except ImportError as exc:  # pragma: no cover - package/runtime dependent
                raise CaptureUnavailable("PILLOW_IMAGEGRAB_UNAVAILABLE") from exc
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
        try:
            rgb = image.convert("RGB")
        except AttributeError as exc:
            raise CaptureUnavailable("WINDOWS_CAPTURE_RETURNED_NON_IMAGE") from exc
        pixels = np.asarray(rgb).copy()
        if pixels.ndim != 3 or pixels.shape[2] != 3:
            raise CaptureUnavailable("WINDOWS_CAPTURE_RETURNED_INVALID_PIXELS")
        return pixels

    def _current_bbox(self) -> tuple[int, int, int, int] | None:
        if self._geometry_provider is None:
            return self.bbox
        geometry = self._geometry_provider.probe()
        self.last_geometry = geometry
        if not geometry.available:
            raise CaptureUnavailable(geometry.status)
        return geometry.client_bbox

    def __iter__(self) -> Iterator[Frame]:
        # Test-injected providers/grabbers make geometry lifecycle behaviour
        # unit-testable in Docker.  Normal runtime retains the Windows guard.
        if not self._is_windows and self._geometry_provider is None and self._image_grabber is None:
            raise CaptureUnavailable("WINDOWS_RUNTIME_REQUIRED")
        sequence = 0
        while self.max_frames is None or sequence < self.max_frames:
            pixels = self._grab(self._current_bbox())
            sequence += 1
            yield Frame(
                frame_id=f"windows:{sequence}",
                capture_seq=sequence,
                captured_at_ms=self._clock_ms(),
                image=pixels,
                source="WINDOWS_NATIVE_UNVERIFIED",
                reference=None if self.last_geometry is None else self.last_geometry.title,
            )
