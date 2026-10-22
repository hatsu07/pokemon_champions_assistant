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


_dpi_awareness_status: str | None = None


def ensure_per_monitor_dpi_awareness() -> str:
    """Align Win32 geometry and Pillow screen coordinates for this process.

    This changes only the validation harness process's coordinate awareness. It
    never changes a window's size, position, focus, or DPI setting.  The
    result is recorded as a diagnostic because a failed/unchanged request must
    not be treated as a confirmed capture-coordinate alignment.
    """
    global _dpi_awareness_status
    if _dpi_awareness_status is not None:
        return _dpi_awareness_status
    if os.name != "nt":
        _dpi_awareness_status = "WINDOWS_RUNTIME_REQUIRED"
        return _dpi_awareness_status
    import ctypes

    user32 = ctypes.windll.user32
    set_context = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if set_context is not None:
        try:
            set_context.argtypes = [ctypes.c_void_p]
            set_context.restype = ctypes.c_bool
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 is -4.
            if set_context(ctypes.c_void_p(-4)):
                _dpi_awareness_status = "PER_MONITOR_AWARE_V2_SET"
                return _dpi_awareness_status
        except (AttributeError, OSError):
            pass
    try:
        shcore = ctypes.windll.shcore
        set_awareness = shcore.SetProcessDpiAwareness
        set_awareness.argtypes = [ctypes.c_int]
        set_awareness.restype = ctypes.c_long
        # PROCESS_PER_MONITOR_DPI_AWARE is 2; HRESULT 0 means success.
        if int(set_awareness(2)) == 0:
            _dpi_awareness_status = "PER_MONITOR_AWARE_SET"
            return _dpi_awareness_status
    except (AttributeError, OSError):
        pass
    _dpi_awareness_status = "DPI_AWARENESS_UNCHANGED_OR_UNAVAILABLE"
    return _dpi_awareness_status


@dataclass(frozen=True)
class WindowGeometry:
    """Current client-area geometry in screen pixels, never guessed."""

    status: str
    title: str | None = None
    hwnd: int | None = None
    client_bbox: tuple[int, int, int, int] | None = None
    dpi: int | None = None
    dpi_awareness: str | None = None

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
            "dpi_awareness": self.dpi_awareness,
            "coordinate_space": "CLIENT_AREA_SCREEN_PIXELS",
        }


def _validate_rgb_pixels(pixels: np.ndarray, *, failure_reason: str) -> np.ndarray:
    if pixels.ndim != 3 or pixels.shape[2] != 3 or pixels.dtype != np.uint8:
        raise CaptureUnavailable(failure_reason)
    return pixels


def _print_window_client(geometry: WindowGeometry) -> np.ndarray:
    """Read a visible target window's client pixels without desktop compositing.

    ``PrintWindow`` can render a covered window into an off-screen DIB.  It is
    a read-only Windows API and deliberately has no desktop-bbox fallback: a
    fallback could silently save an overlapping application instead.  GPU
    applications may decline to render or return blank content; that outcome
    remains an unverified capture and the explicit ADB fallback is then the
    appropriate next diagnostic.
    """
    if os.name != "nt":
        raise CaptureUnavailable("WINDOWS_RUNTIME_REQUIRED")
    if geometry.hwnd is None or geometry.client_bbox is None:
        raise CaptureUnavailable("WINDOW_PRINT_TARGET_UNAVAILABLE")
    import ctypes
    from ctypes import wintypes

    left, top, right, bottom = geometry.client_bbox
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise CaptureUnavailable("WINDOW_PRINT_CLIENT_RECT_EMPTY")

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class RGBQUAD(ctypes.Structure):
        _fields_ = [
            ("rgbBlue", wintypes.BYTE),
            ("rgbGreen", wintypes.BYTE),
            ("rgbRed", wintypes.BYTE),
            ("rgbReserved", wintypes.BYTE),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(BITMAPINFO),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL

    desktop_dc = user32.GetDC(None)
    if not desktop_dc:
        raise CaptureUnavailable("WINDOW_PRINT_DESKTOP_DC_UNAVAILABLE")
    memory_dc = None
    bitmap = None
    previous = None
    try:
        memory_dc = gdi32.CreateCompatibleDC(desktop_dc)
        if not memory_dc:
            raise CaptureUnavailable("WINDOW_PRINT_MEMORY_DC_UNAVAILABLE")
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # top-down rows
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(memory_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            raise CaptureUnavailable("WINDOW_PRINT_DIB_UNAVAILABLE")
        previous = gdi32.SelectObject(memory_dc, bitmap)
        if not previous:
            raise CaptureUnavailable("WINDOW_PRINT_SELECT_FAILED")
        # PW_CLIENTONLY | PW_RENDERFULLCONTENT.  This requests the target's
        # own client rendering rather than reading the composited desktop.
        if not user32.PrintWindow(geometry.hwnd, memory_dc, 0x00000001 | 0x00000002):
            raise CaptureUnavailable("WINDOW_PRINT_FAILED")
        raw = (ctypes.c_ubyte * (width * height * 4)).from_address(bits.value)
        bgra = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 4))
        return _validate_rgb_pixels(np.ascontiguousarray(bgra[:, :, 2::-1]), failure_reason="WINDOW_PRINT_INVALID_PIXELS")
    finally:
        if previous and memory_dc:
            gdi32.SelectObject(memory_dc, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, desktop_dc)


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

        dpi_awareness = ensure_per_monitor_dpi_awareness()
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
            dpi_awareness=dpi_awareness,
        )


ImageGrabber = Callable[[tuple[int, int, int, int] | None], object]
WindowClientGrabber = Callable[[WindowGeometry], np.ndarray]


def _monotonic_ms() -> int:
    return monotonic_ns() // 1_000_000


class WindowsNativeFrameSource:
    """Read pixels from an explicitly selected Windows target.

    ``window_title`` uses Win32 ``PrintWindow`` on its dynamically measured
    client area, avoiding desktop compositing when another application covers
    the target. ``bbox`` keeps the Phase 4 explicit-desktop-rectangle mode.
    The caller may pass neither to deliberately capture the desktop, but this
    is unsuitable for an accepted BlueStacks measurement because it has no
    target-window evidence.
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
        window_client_grabber: WindowClientGrabber | None = None,
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
        self._window_client_grabber = window_client_grabber
        self._clock_ms = clock_ms
        self._is_windows = os.name == "nt" if is_windows is None else is_windows
        self.last_geometry: WindowGeometry | None = None
        self.last_capture_method: str | None = None

    def _grab(self, bbox: tuple[int, int, int, int] | None) -> np.ndarray:
        if self._image_grabber is not None:
            image = self._image_grabber(bbox)
            self.last_capture_method = "INJECTED_IMAGE_GRABBER"
        elif self.window_title is not None and self.last_geometry is not None:
            grabber = self._window_client_grabber or _print_window_client
            self.last_capture_method = "WIN32_PRINTWINDOW_CLIENT"
            return _validate_rgb_pixels(grabber(self.last_geometry), failure_reason="WINDOW_PRINT_INVALID_PIXELS")
        else:
            try:
                from PIL import ImageGrab
            except ImportError as exc:  # pragma: no cover - package/runtime dependent
                raise CaptureUnavailable("PILLOW_IMAGEGRAB_UNAVAILABLE") from exc
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
            self.last_capture_method = "PILLOW_DESKTOP_BBOX"
        try:
            rgb = image.convert("RGB")
        except AttributeError as exc:
            raise CaptureUnavailable("WINDOWS_CAPTURE_RETURNED_NON_IMAGE") from exc
        pixels = np.asarray(rgb).copy()
        return _validate_rgb_pixels(pixels, failure_reason="WINDOWS_CAPTURE_RETURNED_INVALID_PIXELS")

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
                source=f"WINDOWS_{self.last_capture_method or 'NATIVE'}_UNVERIFIED",
                reference=None if self.last_geometry is None else self.last_geometry.title,
            )
