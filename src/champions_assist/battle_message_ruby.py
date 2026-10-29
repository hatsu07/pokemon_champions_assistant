"""Conservative, single-input ruby exclusion for Champions battle-message OCR.

Coordinates are provisional measurements from three 960x572 screenshots, NOT
a verified UI layout. The live path requires a game-validated calibration;
unvalidated images can be previewed only in the local, no-network diagnostic.
Never OCR both the original message and the composite in the same event.
"""
from __future__ import annotations

import cv2
import numpy as np

from .calibrated_screen_fields import FieldRegion, ScreenFieldProfile

_REFERENCE = (960, 572)
_EXPECTED_REGION = (141, 412, 540, 505)
# Half-open boundaries include every measured body pixel, but neither ruby row.
_BODY_ROWS = ((430, 454), (464, 489))
# Keep the entire plausible message width rather than the old 150..510 crop.
_TEXT_X = (141, 950)


def _has_black_capture_bars(image: np.ndarray) -> bool:
    """Reject obvious paired black bars; this is not a general UI detector."""
    h, w = image.shape[:2]
    dh, dw = max(2, h // 80), max(2, w // 80)
    def black(block: np.ndarray) -> bool:
        return float(np.mean(block)) < 5.0 and float(np.std(block)) < 5.0
    return ((black(image[:dh]) and black(image[-dh:]))
            or (black(image[:, :dw]) and black(image[:, -dw:])))


def prepare_battle_message(
    image: np.ndarray, profile: ScreenFieldProfile, region: FieldRegion,
    *, preview_unvalidated: bool = False, enabled: bool = True,
) -> tuple[np.ndarray, dict]:
    """Return the OCR input and non-secret provenance; never invokes OCR.

    The original profile ROI is returned when the provisional coordinates
    are not safe to apply. The diagnostic may preview without enabling live.
    Geometry is relative to the already selected *game viewport*, not to a
    whole desktop capture (which may have window borders).
    """
    original = profile.crop(image, region)
    info = {"preprocess": "NOT_APPLICABLE", "body_rois": [],
            "ruby_excluded": False, "game_validated": False}
    if region.name != "battle_message" or region.kind != "MESSAGE":
        return original, info
    if not enabled:
        info["preprocess"] = "DISABLED"
        return original, info
    if not profile.validated_on_game_capture and not preview_unvalidated:
        info["preprocess"] = "UNVALIDATED_LIVE_DISABLED"
        return original, info
    if (profile.reference_resolution != _REFERENCE
            or region.bounds_px != _EXPECTED_REGION
            or region.bounds_normalized is not None):
        info["preprocess"] = "UNRECOGNIZED_UI_GEOMETRY"
        return original, info
    if not profile.compatible(image):
        info["preprocess"] = "VIEWPORT_MISMATCH"
        return original, info
    height, width = image.shape[:2]
    if abs((width / height) / (_REFERENCE[0] / _REFERENCE[1]) - 1.0) > 0.002:
        info["preprocess"] = "VIEWPORT_ASPECT_MISMATCH"
        return original, info
    if _has_black_capture_bars(image):
        info["preprocess"] = "POSSIBLE_BLACK_BARS"
        return original, info

    x0, x1 = (round(_TEXT_X[0] * width / _REFERENCE[0]),
              round(_TEXT_X[1] * width / _REFERENCE[0]))
    bands, rois = [], []
    for top, bottom in _BODY_ROWS:
        y0 = round(top * height / _REFERENCE[1])
        y1 = round(bottom * height / _REFERENCE[1])
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            info["preprocess"] = "INVALID_BODY_GEOMETRY"
            return original, info
        band = image[y0:y1, x0:x1]
        if band.size == 0:
            info["preprocess"] = "EMPTY_BODY"
            return original, info
        bands.append(band)
        rois.append([x0, y0, x1, y1])

    # The background-colored spacer keeps the two body lines separate for
    # PP-OCR detection, with a single call to the existing OCR engine.
    margin = bands[0][:, :min(12, bands[0].shape[1])]
    background = np.median(margin, axis=(0, 1)).astype(np.uint8)
    gap = max(8, round(10 * height / _REFERENCE[1]))
    spacer = np.empty((gap, x1 - x0, 3), dtype=np.uint8)
    spacer[:] = background
    composite = np.concatenate((bands[0], spacer, bands[1]), axis=0)
    # Match the existing 2x cap without shrinking native 1x/1.5x glyphs.
    max_width = 2 * (_TEXT_X[1] - _TEXT_X[0])
    if composite.shape[1] > max_width:
        scale = max_width / composite.shape[1]
        composite = cv2.resize(composite, (max_width,
                              max(1, round(composite.shape[0] * scale))),
                              interpolation=cv2.INTER_AREA)
    info.update(
        preprocess=("BODY_LINES_PREVIEW_ONLY" if not profile.validated_on_game_capture
                    else "BODY_LINES_CALIBRATED"),
        body_rois=rois,
        ruby_excluded=True,
        game_validated=bool(profile.validated_on_game_capture),
    )
    return np.ascontiguousarray(composite), info
