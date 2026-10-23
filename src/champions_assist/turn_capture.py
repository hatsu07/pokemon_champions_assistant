"""Read-only memory-only event sampling and calibrated command-screen gate.

Templates only identify candidate command screens, never infer battle facts.
Use calibration under the actual game window/ROI. No automatic game input.
"""
from __future__ import annotations

from collections import deque
from time import monotonic
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

    def acknowledge_calibrated_current(self, kind: str, image: np.ndarray) -> None:
        """User explicitly identified the current screen as a known menu.

        A single opted-in dispatch may start immediately from this frame.
        Disarm the transition gate until two non-menu frames appear so that
        the next 300 ms polls cannot send that same menu a second time.
        """
        self.calibrate(kind, image)
        self._last=_menu_patch(image)
        self._last_full=cv2.resize(
            cv2.cvtColor(image,cv2.COLOR_RGB2GRAY),(96,54),
            interpolation=cv2.INTER_AREA).astype(np.float32)/255.0
        self._candidate=kind
        self._streak=self.stable_frames
        self._armed=False
        self._misses=0
        self.frames.append(image.copy())

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


class AutomaticVisualProbe:
    """Built-in candidate screen detection requiring NO user calibration.

    Local frame differences identify probable scene transitions; the existing
    AI observation schema, rather than this pixel heuristic, determines UI
    identity. This is a request gate, not a claim of reliable UI recognition.
    """

    def __init__(self, *, clock=monotonic, min_gap=2.0,
                 scene_delta=0.065, stable_frames=2, matchmaking_recheck=12.0):
        self.clock=clock
        self.min_gap=min_gap
        self.scene_delta=scene_delta
        self.stable_frames=stable_frames
        self.matchmaking_recheck=matchmaking_recheck
        self.reset()

    def reset(self):
        self._phase=None
        self._last_sent=None
        self._last_sent_at=None
        self._candidate=None
        self._candidate_streak=0

    @staticmethod
    def _small(image):
        # Full-viewport signature, not a per-user chosen menu patch.
        gray=cv2.cvtColor(image,cv2.COLOR_RGB2GRAY)
        return (cv2.resize(gray,(64,36),interpolation=cv2.INTER_AREA)
                .astype(np.float32)/255.0)

    def acknowledge(self, image, phase):
        """Record an explicitly registered menu to suppress an auto duplicate."""
        self._phase=phase
        self._last_sent=self._small(image)
        self._last_sent_at=self.clock()
        self._candidate=None
        self._candidate_streak=0

    def recently_submitted(self, image, *, within=4.0):
        if self._last_sent is None or self._last_sent_at is None:
            return False
        return (self.clock()-self._last_sent_at < within
                and float(np.mean(np.abs(self._small(image)-self._last_sent)))
                    < self.scene_delta)

    def observe(self, image, phase):
        """True means inspect this already captured frame with AI once.

        - Initial eligible screenshot is inspected immediately, even if the
          screen was already open before monitoring started.
        - Matchmaking rechecks at bounded intervals because its animations
          may be too subtle to clear the scene-change threshold.
        - Selection UI already recognized in the current match isn't re-sent:
          the next event is user's actual selection confirmation.
        - In battle, only stable major changes initiate new observation, not
          every flickering frame or timer animation.
        """
        if phase not in ("MATCHMAKING","PREP","BATTLE"):
            return False
        now=self.clock()
        small=self._small(image)
        if self._phase != phase:
            self._phase=phase
            self._last_sent=None
            self._last_sent_at=None
            self._candidate=None
            self._candidate_streak=0
        if self._last_sent is None:
            self.acknowledge(image,phase)
            return True
        if now-self._last_sent_at < self.min_gap:
            return False
        delta=float(np.mean(np.abs(small-self._last_sent)))
        if delta>=self.scene_delta:
            if self._candidate is None or float(
                np.mean(np.abs(small-self._candidate)))>0.025:
                self._candidate=small
                self._candidate_streak=1
            else:
                self._candidate_streak+=1
            if self._candidate_streak>=self.stable_frames:
                self.acknowledge(image,phase)
                return True
            return False
        self._candidate=None
        self._candidate_streak=0
        if phase=="MATCHMAKING" and now-self._last_sent_at>=self.matchmaking_recheck:
            self.acknowledge(image,phase)
            return True
        return False
