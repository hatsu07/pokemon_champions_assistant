"""Calibration-driven local pixel observations; no Pokémon tactics or guessing.

All positions, HSV ranges and icon exemplars require user-provided screenshots.
Observed HP intervals and icon identities remain candidates until independently
confirmed. This module never returns a fabricated exact HP percentage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import cv2
import numpy as np

from .roi import NormalizedROI


@dataclass(frozen=True)
class HpBarCalibration:
    side: str
    roi: NormalizedROI
    # HSV colors in the observed fill, calibrated per theme/health color.
    hsv_lower: tuple[int, int, int]
    hsv_upper: tuple[int, int, int]
    minimum_saturation: int = 35
    minimum_row_coverage: float = 0.55
    uncertainty_percent: int = 4


@dataclass(frozen=True)
class VisualCandidate:
    field: str
    value: object
    certainty: str
    source: str
    evidence_ids: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if self.certainty != "CANDIDATE":
            raise ValueError("pixel recognition may only produce CANDIDATE facts")
        if not self.evidence_ids:
            raise ValueError("visual candidate requires evidence provenance")


def estimate_hp_bar(image: np.ndarray, calibration: HpBarCalibration, *, frame_id: str) -> VisualCandidate | None:
    """Return a conservative percentage interval after detecting a solid fill.

    Fail closed on missing fill, conflicting color spans or visible animation.
    ROI must contain ONLY the inner bar, not its border/HP text.
    """
    if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
        raise ValueError("expected RGB uint8 pixels")
    if not 0 <= calibration.uncertainty_percent <= 25:
        raise ValueError("invalid uncertainty bound")
    crop = calibration.roi.crop(image)
    if crop.shape[0] < 3 or crop.shape[1] < 12:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    lower = np.array(calibration.hsv_lower, dtype=np.uint8)
    upper = np.array(calibration.hsv_upper, dtype=np.uint8)
    match = cv2.inRange(hsv, lower, upper) > 0
    match &= hsv[:, :, 1] >= calibration.minimum_saturation
    # Use only rows carrying the bar; ignore antialiasing at top/bottom.
    row_strength = match.mean(axis=1)
    rows = match[row_strength >= calibration.minimum_row_coverage]
    if len(rows) < max(2, crop.shape[0] // 4):
        return None
    occupancy = rows.mean(axis=0)
    strong = occupancy >= 0.75
    indices = np.flatnonzero(strong)
    if len(indices) < 2:
        return None  # 0% cannot be certified from "no fill" alone
    end = int(indices[-1])
    # A bar should be filled from its left edge with no significant gaps.
    if indices[0] > max(2, crop.shape[1] // 30):
        return None
    if strong[:end + 1].mean() < 0.95:
        return None
    # Variable theme/compression/frame effects make exact percentages unsafe.
    ratio = (end + 1) / crop.shape[1]
    percentage = round(100 * ratio)
    error = calibration.uncertainty_percent + 1
    return VisualCandidate(
        field=f"hp_status.{calibration.side}",
        value={"percent_range": [max(0, percentage-error), min(100, percentage+error)]},
        certainty="CANDIDATE", source="HP_BAR", evidence_ids=(frame_id,),
        reason="UNCALIBRATED_REAL_GAME_ACCURACY",
    )


@dataclass(frozen=True)
class IconCalibration:
    slot: str
    roi: NormalizedROI
    exemplars: Mapping[str, np.ndarray]
    min_similarity: float = 0.92
    min_margin: float = 0.07


def recognize_icon(image: np.ndarray, calibration: IconCalibration, *, frame_id: str) -> VisualCandidate | None:
    """Compare calibrated exemplars; ties and unknown forms remain unknown."""
    crop = calibration.roi.crop(image)
    if not calibration.exemplars:
        return None
    target = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    scores: list[tuple[str, float]] = []
    for name, exemplar in calibration.exemplars.items():
        if not name or exemplar.ndim != 3 or exemplar.shape[2] != 3 or exemplar.dtype != np.uint8:
            raise ValueError("icon exemplar must be named RGB uint8 image")
        candidate = cv2.cvtColor(exemplar, cv2.COLOR_RGB2GRAY)
        candidate = cv2.resize(candidate, (target.shape[1], target.shape[0]), interpolation=cv2.INTER_AREA)
        # Untextured/flat exemplars cannot be reliably matched.
        if target.std() < 8 or candidate.std() < 8:
            continue
        score = float(cv2.matchTemplate(target, candidate, cv2.TM_CCOEFF_NORMED)[0, 0])
        if np.isfinite(score):
            scores.append((name, score))
    scores.sort(key=lambda item: item[1], reverse=True)
    if not scores or scores[0][1] < calibration.min_similarity:
        return None
    if len(scores) >= 2 and scores[0][1] - scores[1][1] < calibration.min_margin:
        return None
    return VisualCandidate(
        field=f"visible_icon.{calibration.slot}",
        value=scores[0][0], certainty="CANDIDATE", source="ICON_TEMPLATE",
        evidence_ids=(frame_id,), reason="ICON_REQUIRES_TEXT_OR_MANUAL_CONFIRMATION",
    )


class VisualCandidateStabilizer:
    """Repeatable frames add independent evidence, not automatic certainty.

    A second modality or user confirmation is needed to mark a game fact confirmed.
    """
    def __init__(self, required_frames: int = 2) -> None:
        if required_frames < 2:
            raise ValueError("require at least two frames")
        self.required_frames = required_frames
        self._seen: dict[str, tuple[str, list[str]]] = {}

    def observe(self, item: VisualCandidate) -> VisualCandidate | None:
        from .reducer import canonical_json
        key = canonical_json(item.value)
        prior_key, ids = self._seen.get(item.field, ("", []))
        if key != prior_key:
            ids = []
        if item.evidence_ids[0] not in ids:
            ids.append(item.evidence_ids[0])
        self._seen[item.field] = (key, ids[-self.required_frames:])
        if len(ids) < self.required_frames:
            return None
        return VisualCandidate(
            field=item.field, value=item.value, certainty="CANDIDATE",
            source=item.source, evidence_ids=tuple(ids[-self.required_frames:]),
            reason="REPEATED_FRAMES_CANDIDATE_NOT_CONFIRMED",
        )
