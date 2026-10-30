"""Opt-in, bounded local-only OCR capture evidence for real viewport calibration.

No image is written by the normal debug JSONL path. This diagnostic is started
only by explicit user consent, and PNG encoding / ROI extraction run off the
GUI capture thread. Images and OCR text must never enter Git, CI, or exports.
"""
from __future__ import annotations

import json
from pathlib import Path
from queue import Empty, Full, Queue
import re
from threading import Thread
from time import monotonic
from typing import Any

import numpy as np

from .battle_message_ruby import prepare_battle_message
from .calibrated_screen_fields import ScreenFieldProfile, live_ocr_region_enabled

MAX_SAMPLES = 32
MAX_SESSION_BYTES = 24 * 1024 * 1024
KEEP_SESSIONS = 5
_SAFE_ID = re.compile(r"^capture-[a-f0-9]{32}$")
_SAFE_ROI = re.compile(r"[^a-zA-Z0-9_\-]")


class OcrCaptureDebugWriter:
    """Best-effort queue: never wait for PNG writing during a 45s turn."""

    def __init__(self, app_root: str | Path, *, match_id: str,
                 max_samples: int = MAX_SAMPLES,
                 max_bytes: int = MAX_SESSION_BYTES):
        if not _SAFE_ID.fullmatch(match_id):
            raise ValueError("invalid match id")
        root = Path(app_root) / "debug-logs" / "ocr-captures"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / match_id
        self.path.mkdir(exist_ok=True)
        # Only prune this explicitly opted-in diagnostic's own session folders.
        sessions = sorted((p for p in root.glob("capture-*")
                           if p.is_dir() and _SAFE_ID.fullmatch(p.name)),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        for expired in sessions[KEEP_SESSIONS:]:
            try:
                for child in expired.iterdir():
                    if child.is_file():
                        child.unlink()
                expired.rmdir()
            except OSError:
                pass
        self.max_samples = max(1, int(max_samples))
        self.max_bytes = max(1, int(max_bytes))
        self.samples = 0
        self.dropped = 0
        self._bytes = 0
        self._closed = False
        self._last_by_reason: dict[tuple[str, str], float] = {}
        self._queue: Queue[tuple] = Queue(maxsize=4)
        self._worker = Thread(target=self._run, name="champions-ocr-debug-png",
                              daemon=True)
        self._worker.start()

    def offer(self, image: np.ndarray, *, frame_id: str, label: str,
              reason: str, profile: ScreenFieldProfile | None) -> bool:
        """Bound memory and samples before taking a copy; no synchronous I/O."""
        if self._closed or self.samples >= self.max_samples:
            return False
        key = (str(label), str(reason))
        now = monotonic()
        if now - self._last_by_reason.get(key, -1e10) < 4.0:
            return False
        if self._queue.full():
            self.dropped += 1
            return False
        try:
            frame = image.copy()
            self._queue.put_nowait(("frame", frame_id, str(label),
                                    str(reason), profile, frame))
        except (Full, MemoryError):
            self.dropped += 1
            return False
        self.samples += 1
        self._last_by_reason[key] = now
        return True

    def record_ocr(self, frame_id: str, observations: tuple) -> None:
        if self._closed or not observations:
            return
        rows = [{
            "roi": str(row.roi)[:64],
            "field": str(row.field)[:80],
            "text": str(row.text)[:160],
            "confidence": row.confidence,
            "ocr_elapsed_ms": row.ocr_elapsed_ms,
        } for row in observations[:12]]
        try:
            self._queue.put_nowait(("ocr", str(frame_id)[:80], rows))
        except Full:
            self.dropped += 1

    def close(self) -> None:
        """The daemon drains outstanding bounded work; no GUI-thread join."""
        self._closed = True

    def _save_png(self, destination: Path, rgb: np.ndarray) -> bool:
        import cv2
        success, encoded = cv2.imencode(
            ".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        if not success or self._bytes + encoded.nbytes > self.max_bytes:
            return False
        destination.write_bytes(encoded.tobytes())
        self._bytes += encoded.nbytes
        return True

    def _run(self) -> None:
        while not self._closed or not self._queue.empty():
            try:
                event = self._queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                if event[0] == "frame":
                    self._write_frame(*event[1:])
                elif event[0] == "ocr":
                    self._write_ocr(*event[1:])
            except (OSError, ValueError, RuntimeError, TypeError, ImportError):
                # Diagnostic capture must not crash or block match support.
                self.dropped += 1
            finally:
                self._queue.task_done()

    def _write_ocr(self, frame_id: str, rows: list[dict[str, Any]]) -> None:
        line = json.dumps({"frame_id": frame_id, "results": rows},
                          ensure_ascii=False) + "\n"
        payload = line.encode("utf-8")
        if self._bytes + len(payload) > self.max_bytes:
            return
        with (self.path / "ocr-results.jsonl").open("ab") as out:
            out.write(payload)
        self._bytes += len(payload)

    def _write_frame(self, frame_id: str, label: str, reason: str,
                     profile: ScreenFieldProfile | None,
                     image: np.ndarray) -> None:
        prefix = f"frame-{self.samples:03d}-{_SAFE_ROI.sub('_', frame_id)[:55]}"
        info: dict[str, Any] = {
            "frame_id": frame_id, "screen_label": label,
            "offer_reason": reason,
            "image_shape": list(image.shape),
            "image_dtype": str(image.dtype),
            "profile_source": profile.source if profile else None,
            "profile_reference_resolution": (
                list(profile.reference_resolution) if profile else None),
            "profile_compatible": (profile.compatible(image) if profile else False),
            "profile_validated_on_game_capture": (
                profile.validated_on_game_capture if profile else False),
            "regions": [],
            "image_saved": False,
        }
        if (image.ndim == 3 and image.shape[2] == 3
                and image.dtype == np.uint8 and image.size):
            info["image_saved"] = self._save_png(
                self.path / (prefix + ".png"), image)
            if profile is not None:
                for region in profile.regions[:12]:
                    if (not live_ocr_region_enabled(profile, region)
                            or label not in region.applicable_labels
                            or region.kind not in ("HP_FRACTION", "HP_PERCENT",
                                                  "TEXT", "MESSAGE", "PP_FRACTION")):
                        continue
                    entry: dict[str, Any] = {
                        "name": region.name, "kind": region.kind,
                        "bounds_px": list(region.bounds_px) if region.bounds_px else None,
                        "bounds_normalized": (list(region.bounds_normalized)
                                               if region.bounds_normalized else None),
                    }
                    try:
                        crop, pre = prepare_battle_message(image, profile, region)
                        entry["preprocess"] = pre.get("preprocess", "")
                        entry["crop_shape"] = list(crop.shape)
                        entry["image_saved"] = self._save_png(
                            self.path / (prefix + "-" +
                                         _SAFE_ROI.sub("_", region.name) + ".png"),
                            crop)
                    except (ValueError, IndexError, RuntimeError):
                        entry["crop_error"] = "INVALID_CAPTURE_GEOMETRY"
                    info["regions"].append(entry)
        output = (json.dumps(info, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if self._bytes + len(output) <= self.max_bytes:
            (self.path / (prefix + ".json")).write_bytes(output)
            self._bytes += len(output)
