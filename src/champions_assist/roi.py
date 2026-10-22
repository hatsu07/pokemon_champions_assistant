"""Versioned, normalized ROI configuration with no assumed Champions layout."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np


ROI_NAMES = frozenset(
    {
        "ROI_UI_CONTROLS",
        "ROI_MOVE_CARDS",
        "ROI_PARTY_CARDS",
        "ROI_OPPONENT_TEAM",
        "ROI_BATTLE_LOG",
        "ROI_STATUS",
        "ROI_RESULT",
    }
)


@dataclass(frozen=True)
class NormalizedROI:
    name: str
    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        if not self.name or not all(0.0 <= value <= 1.0 for value in (self.left, self.top, self.right, self.bottom)):
            raise ValueError("ROI coordinates must be normalized values from 0 through 1")
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("ROI must have positive width and height")

    def pixels(self, image: np.ndarray) -> tuple[int, int, int, int]:
        height, width = image.shape[:2]
        left = int(round(self.left * width))
        top = int(round(self.top * height))
        right = int(round(self.right * width))
        bottom = int(round(self.bottom * height))
        if left >= right or top >= bottom:
            raise ValueError(f"ROI {self.name} collapses at this resolution")
        return left, top, right, bottom

    def crop(self, image: np.ndarray) -> np.ndarray:
        left, top, right, bottom = self.pixels(image)
        return image[top:bottom, left:right].copy()

    def as_dict(self) -> dict[str, float]:
        return {"left": self.left, "top": self.top, "right": self.right, "bottom": self.bottom}


@dataclass(frozen=True)
class RoiConfiguration:
    """A calibration artifact, not a source of hard-coded production coordinates."""

    version: str
    reference_resolution: tuple[int, int] | None
    rois: dict[str, NormalizedROI]
    source_label: str = "UNCALIBRATED"

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("ROI configuration needs a version")
        if set(self.rois) != {item.name for item in self.rois.values()}:
            raise ValueError("ROI map keys must equal ROI names")
        if self.reference_resolution is not None and any(value <= 0 for value in self.reference_resolution):
            raise ValueError("reference resolution must be positive")

    @classmethod
    def uncalibrated(cls) -> "RoiConfiguration":
        return cls(version="1", reference_resolution=None, rois={}, source_label="UNCALIBRATED_NO_CHAMPIONS_FOOTAGE")

    def crop(self, name: str, image: np.ndarray) -> np.ndarray | None:
        roi = self.rois.get(name)
        return None if roi is None else roi.crop(image)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "reference_resolution": None if self.reference_resolution is None else list(self.reference_resolution),
            "source_label": self.source_label,
            "rois": {name: roi.as_dict() for name, roi in sorted(self.rois.items())},
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "RoiConfiguration":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        raw_resolution = payload.get("reference_resolution")
        resolution = None if raw_resolution is None else tuple(raw_resolution)
        if resolution is not None and (len(resolution) != 2 or not all(isinstance(value, int) for value in resolution)):
            raise ValueError("reference_resolution must be [width, height] or null")
        raw_rois = payload.get("rois", {})
        if not isinstance(raw_rois, dict):
            raise ValueError("rois must be an object")
        rois = {
            name: NormalizedROI(name, float(value["left"]), float(value["top"]), float(value["right"]), float(value["bottom"]))
            for name, value in raw_rois.items()
        }
        return cls(str(payload["version"]), resolution, rois, str(payload.get("source_label", "UNCALIBRATED")))
