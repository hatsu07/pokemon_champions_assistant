"""OpenCV-only image differences, template UI classification, and stability."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

import cv2
import numpy as np


class DetectedUI(str, Enum):
    TEAM_SELECTION = "TEAM_SELECTION"
    COMMAND_MENU = "COMMAND_MENU"
    MOVE_MENU = "MOVE_MENU"
    PARTY_MENU = "PARTY_MENU"
    DETAIL_OVERLAY = "DETAIL_OVERLAY"
    ACTION_RESOLUTION = "ACTION_RESOLUTION"
    RESULT_SCREEN = "RESULT_SCREEN"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class UiClassification:
    visual_ui: DetectedUI
    confidence: float
    scores: dict[str, float]


def frame_difference(previous: np.ndarray, current: np.ndarray) -> float:
    """Mean absolute RGB difference, using OpenCV rather than OCR as a cheap gate."""
    if previous.shape != current.shape:
        return 255.0
    return float(np.mean(cv2.absdiff(previous, current)))


def template_similarity(image: np.ndarray, template: np.ndarray) -> float:
    """Return a conservative OpenCV template score in [0, 1]."""
    if image.shape[0] < template.shape[0] or image.shape[1] < template.shape[1]:
        return 0.0
    gray_image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    gray_template = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY)
    result = cv2.matchTemplate(gray_image, gray_template, cv2.TM_SQDIFF_NORMED)
    score = 1.0 - float(np.min(result))
    return max(0.0, min(1.0, score))


class TemplateUiClassifier:
    """Calibrated visual classifier; it never assigns a PARTY_MENU reason."""

    def __init__(self, templates: Mapping[DetectedUI, np.ndarray], *, threshold: float = 0.90) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("template threshold must be from 0 through 1")
        self.templates = {kind: image.copy() for kind, image in templates.items()}
        self.threshold = threshold

    def classify(self, image: np.ndarray) -> UiClassification:
        scores = {kind.value: template_similarity(image, template) for kind, template in self.templates.items()}
        if not scores:
            return UiClassification(DetectedUI.UNKNOWN, 0.0, scores)
        winner_name, winner_score = max(scores.items(), key=lambda item: item[1])
        kind = DetectedUI(winner_name)
        if winner_score < self.threshold:
            return UiClassification(DetectedUI.UNKNOWN, winner_score, scores)
        return UiClassification(kind, winner_score, scores)


class RoiChangeDetector:
    """Track ROI differences so OCR runs only for changed configured regions."""

    def __init__(self, *, threshold: float = 2.0) -> None:
        self.threshold = threshold
        self._previous: dict[str, np.ndarray] = {}

    def changed(self, name: str, image: np.ndarray) -> tuple[bool, float]:
        previous = self._previous.get(name)
        difference = 255.0 if previous is None else frame_difference(previous, image)
        self._previous[name] = image.copy()
        return difference >= self.threshold, difference


class UiStabilityGate:
    """Require repeated same visual results before sending them to the FSM."""

    def __init__(self, *, required_frames: int = 2) -> None:
        if required_frames < 1:
            raise ValueError("required_frames must be positive")
        self.required_frames = required_frames
        self._last: DetectedUI | None = None
        self._count = 0

    def observe(self, result: UiClassification) -> DetectedUI | None:
        if result.visual_ui is DetectedUI.UNKNOWN:
            self._last = None
            self._count = 0
            return None
        if result.visual_ui == self._last:
            self._count += 1
        else:
            self._last = result.visual_ui
            self._count = 1
        return result.visual_ui if self._count >= self.required_frames else None
