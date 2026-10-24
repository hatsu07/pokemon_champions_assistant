"""Calibrated ROI OCR of real 960x572 Pokémon Champions *sample* screenshots.

This is deliberately a non-authoritative observation adapter.  A screenshot
does not establish a move, active form, ability or legal action by itself.
Calibration reflects eight user-shared frames; it must be rechecked on the
actual capture viewport before enabling in a ranked match.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Mapping

import numpy as np

from .battle_visual_fields import VisualCandidate
from .ocr import OcrEngine
from .roi import NormalizedROI


@dataclass(frozen=True)
class FieldRegion:
    name: str
    kind: str
    bounds_px: tuple[int, int, int, int]
    applicable_labels: tuple[str, ...]


# Pixel coordinates are derived from the *provided* 960x572 samples, not
# inferred from other Pokémon games. They are intentionally preliminary.
_SAMPLE_960 = (
    FieldRegion("own_hp_fraction", "HP_FRACTION", (126, 526, 218, 562), ("TURN_ACTION", "BATTLE_EVENT")),
    FieldRegion("opponent_hp_percent", "HP_PERCENT", (864, 53, 938, 93), ("TURN_ACTION", "BATTLE_EVENT")),
    FieldRegion("own_active_name", "TEXT", (75, 489, 184, 526), ("TURN_ACTION", "BATTLE_EVENT")),
    FieldRegion("opponent_active_name", "TEXT", (777, 14, 877, 49), ("TURN_ACTION", "BATTLE_EVENT")),
    FieldRegion("battle_message", "MESSAGE", (141, 412, 540, 505), ("BATTLE_EVENT",)),
    FieldRegion("opponent_effect_popup", "MESSAGE", (754, 195, 941, 284), ("BATTLE_EVENT",)),
    FieldRegion("own_item_popup", "MESSAGE", (58, 194, 267, 279), ("BATTLE_EVENT",)),
    FieldRegion("system_message", "MESSAGE", (317, 47, 667, 116), ("BATTLE_EVENT", "TURN_ACTION")),
    FieldRegion("selection_own_list", "TEXT", (49, 89, 310, 474), ("TEAM_SELECTION",)),
    FieldRegion("selection_opponent_list", "TEXT", (766, 88, 941, 477), ("TEAM_SELECTION",)),
    FieldRegion("switch_party_list", "TEXT", (26, 123, 307, 500), ("SWITCH_SELECTION",)),
    FieldRegion("switch_detail_panel", "TEXT", (320, 58, 729, 508), ("SWITCH_SELECTION",)),
    FieldRegion("turn_command_timer", "TIMER", (790, 123, 946, 166), ("TURN_ACTION",)),
)


@dataclass(frozen=True)
class ScreenFieldProfile:
    reference_resolution: tuple[int, int]
    regions: tuple[FieldRegion, ...]
    source: str
    validated_on_game_capture: bool = False

    @classmethod
    def from_user_samples_960x572(cls) -> "ScreenFieldProfile":
        return cls((960, 572), _SAMPLE_960, "USER_SCREENSHOTS_2026_10_24", False)

    @classmethod
    def from_json(cls, path: str | Path) -> "ScreenFieldProfile":
        """Load explicit private local calibration; refuse malformed geometry."""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            raise ValueError("invalid screen field calibration schema")
        dims = payload.get("reference_resolution")
        if not isinstance(dims, list) or len(dims) != 2 or not all(type(v) is int and v > 0 for v in dims):
            raise ValueError("reference_resolution must be positive pixel dimensions")
        width, height = dims
        raw_regions = payload.get("regions")
        if not isinstance(raw_regions, list) or not raw_regions:
            raise ValueError("calibration requires regions")
        regions = []
        seen = set()
        valid_kinds = {"HP_FRACTION", "HP_PERCENT", "TIMER", "MESSAGE", "TEXT"}
        valid_labels = {"TEAM_SELECTION", "TURN_ACTION", "SWITCH_SELECTION", "BATTLE_EVENT"}
        for raw in raw_regions:
            name = raw.get("name")
            kind = raw.get("kind")
            bounds = raw.get("bounds_px")
            labels = raw.get("applicable_labels")
            if not isinstance(name, str) or not name.isidentifier() or name in seen:
                raise ValueError("region needs a unique identifier")
            if kind not in valid_kinds or not isinstance(labels, list) or not labels or not set(labels) <= valid_labels:
                raise ValueError("invalid region kind/labels")
            if not isinstance(bounds, list) or len(bounds) != 4 or any(type(v) is not int for v in bounds):
                raise ValueError("invalid region pixel bounds")
            x0, y0, x1, y1 = bounds
            if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
                raise ValueError("region is outside calibrated capture")
            seen.add(name)
            regions.append(FieldRegion(name, kind, tuple(bounds), tuple(labels)))
        return cls(
            (width, height), tuple(regions),
            str(payload.get("source", "PRIVATE_LOCAL_SCREEN_CALIBRATION")),
            bool(payload.get("validated_on_game_capture", False)),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "reference_resolution": list(self.reference_resolution),
            "source": self.source,
            "validated_on_game_capture": self.validated_on_game_capture,
            "regions": [
                {"name": item.name, "kind": item.kind,
                 "bounds_px": list(item.bounds_px),
                 "applicable_labels": list(item.applicable_labels)}
                for item in self.regions
            ],
        }

    def compatible(self, image: np.ndarray) -> bool:
        if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
            return False
        width, height = self.reference_resolution
        # Never apply an unverified ROI map to stretched, resized or cropped
        # screenshots. Manual viewport recalibration is required.
        return (image.shape[1], image.shape[0]) == (width, height)

    def crop(self, image: np.ndarray, region: FieldRegion) -> np.ndarray:
        width, height = self.reference_resolution
        x0, y0, x1, y1 = region.bounds_px
        return NormalizedROI(region.name, x0 / width, y0 / height, x1 / width, y1 / height).crop(image)


_FRACTION_RE = re.compile(r"(?<![0-9])([0-9]{1,4})\s*/\s*([0-9]{1,4})(?![0-9])")
_PERCENT_RE = re.compile(r"(?<![0-9])([0-9]{1,3})\s*[%％](?![0-9])")
_TIMER_RE = re.compile(r"(?i)COMMAND\s*([0-9]{1,2})(?![0-9])")


def parse_field_text(region: FieldRegion, raw_text: str, *, frame_id: str) -> VisualCandidate | None:
    """Only extract the literal displayed text/numbers; do not infer mechanics."""
    text = " ".join(str(raw_text).strip().split())
    if not text:
        return None
    if region.kind == "HP_FRACTION":
        hits = _FRACTION_RE.findall(text)
        if len(hits) != 1:
            return None
        current, total = map(int, hits[0])
        if not 0 < total <= 9999 or not 0 <= current <= total:
            return None
        value: object = {"current": current, "maximum": total}
    elif region.kind == "HP_PERCENT":
        hits = _PERCENT_RE.findall(text)
        if len(hits) != 1:
            return None
        percentage = int(hits[0])
        if percentage > 100:
            return None
        value = {"percent_displayed": percentage}
    elif region.kind == "TIMER":
        match = _TIMER_RE.search(text)
        if match is None:
            return None
        value = int(match.group(1))
    elif region.kind in ("MESSAGE", "TEXT"):
        if len(text) < 2:
            return None
        # Preserve the observed string; names, typing, weather and effects
        # require a separate verified game-specific parser.
        value = text[:160]
    else:
        raise ValueError(f"unknown ROI kind: {region.kind}")
    return VisualCandidate(
        field=f"screen_text.{region.name}", value=value,
        certainty="CANDIDATE", source="ROI_OCR",
        evidence_ids=(frame_id,),
        reason="OCR_SAMPLE_CALIBRATION_UNVERIFIED",
    )


def observe_screen_fields(
    image: np.ndarray, *, label: str, frame_id: str,
    ocr_engine: OcrEngine, profile: ScreenFieldProfile,
    max_rois: int = 7,
) -> tuple[VisualCandidate, ...]:
    """Extract bounded candidate observations; no network/API/strategy work.

    Game scene must already have an *independently* validated 5-class label.
    UNKNOWN/OTHER do not invoke OCR.  For low-confidence classification the
    normal vision fallback is preserved.
    """
    if label not in {"TEAM_SELECTION", "TURN_ACTION", "SWITCH_SELECTION", "BATTLE_EVENT", "HUD_UNCLASSIFIED"}:
        return ()
    if not profile.compatible(image):
        return ()
    candidates = []
    attempted = 0
    for region in profile.regions:
        if label == "HUD_UNCLASSIFIED":
            # No validated screen classifier: safely OCR *numbers only* in
            # stable HUD coordinates. Never infer a UI, legal action or
            # event message from a scene label we do not have.
            if region.kind not in {"HP_FRACTION", "HP_PERCENT"}:
                continue
        elif label not in region.applicable_labels:
            continue
        if attempted >= max_rois:
            break
        attempted += 1
        crop = profile.crop(image, region)
        reading = ocr_engine.read(crop, roi_name=region.name)
        candidate = parse_field_text(region, reading.text, frame_id=frame_id)
        if candidate is not None:
            candidates.append(candidate)
    return tuple(candidates)


def candidates_for_prompt(candidates: tuple[VisualCandidate, ...]) -> list[dict[str, object]]:
    """A small JSON-only payload; never includes the original image pixels."""
    return [
        {"field": item.field, "candidate": item.value,
         "certainty": item.certainty, "source": item.source,
         "evidence_ids": list(item.evidence_ids)}
        for item in candidates
    ]
