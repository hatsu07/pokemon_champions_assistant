"""Read-only memory-only event sampling and calibrated command-screen gate.

Templates only identify candidate command screens, never infer battle facts.
Use calibration under the actual game window/ROI. No automatic game input.
"""
from __future__ import annotations

from collections import deque
import cv2
import numpy as np


def _menu_patch(image: np.ndarray) -> np.ndarray:
    h,w=image.shape[:2]
    patch=image[int(h*0.47):,int(w*0.48):]
    if not patch.size:
        raise ValueError("empty game viewport")
    gray=cv2.cvtColor(patch,cv2.COLOR_RGB2GRAY)
    return cv2.resize(gray,(64,48),interpolation=cv2.INTER_AREA).astype(np.float32)/255.0


class TurnCaptureBuffer:
    """Bounded snapshots; one trigger per transition into a calibrated menu."""
    def __init__(self, *, max_events=8, similarity_threshold=0.12,
                 event_change_threshold=0.06, stable_frames=2):
        self.frames=deque(maxlen=max_events)
        self.templates={}
        self.threshold=similarity_threshold
        self.change_threshold=event_change_threshold
        self.stable_frames=stable_frames
        self._last=None
        self._last_full=None
        self._candidate=None
        self._streak=0
        self._armed=True
        self._misses=0

    def reset_turn(self):
        self.frames.clear()

    def calibrate(self, kind: str, image: np.ndarray) -> None:
        if kind not in ("TEAM_SELECTION","MOVE_MENU","PARTY_MENU"):
            raise ValueError("unsupported menu kind")
        self.templates[kind]=_menu_patch(image).copy()
        self._candidate=None
        self._streak=0

    def _classify(self, patch: np.ndarray) -> str | None:
        candidates=[(float(np.mean(np.abs(patch-v))),k) for k,v in self.templates.items()]
        if not candidates:
            return None
        delta,kind=min(candidates)
        return kind if delta <= self.threshold else None

    def observe(self,image: np.ndarray) -> str | None:
        patch=_menu_patch(image)
        kind=self._classify(patch)
        # Whole-viewport change records battle messages/HP outside the menu patch.
        small=cv2.resize(cv2.cvtColor(image,cv2.COLOR_RGB2GRAY),(96,54),
                         interpolation=cv2.INTER_AREA).astype(np.float32)/255.0
        if self._last_full is None or float(np.mean(np.abs(small-self._last_full)))>=self.change_threshold:
            self.frames.append(image.copy())
        self._last_full=small
        self._last=patch.copy()
        if kind is None:
            self._candidate=None
            self._streak=0
            self._misses+=1
            if self._misses>=2:
                self._armed=True
            return None
        self._misses=0
        if kind!=self._candidate:
            self._candidate=kind
            self._streak=1
        else:
            self._streak+=1
        if self._armed and self._streak>=self.stable_frames:
            self._armed=False
            self.frames.append(image.copy())
            return kind
        return None

    def batch(self, current: np.ndarray, limit=4) -> list[np.ndarray]:
        # Preserve chronological order; always include the latest command screen.
        previous=list(self.frames)[-(limit-1):]
        return previous+[current.copy()]
