"""Conservative ruby-band masking for one- and two-line battle messages.

Keep the original calibrated message ROI and mask only two ruby rows. Never
remove/reflow the main text or call OCR twice. Live use requires explicit
ruby-band calibration; the local diagnostic can preview unvalidated regions.
"""
from __future__ import annotations

import cv2
import numpy as np

from .calibrated_screen_fields import FieldRegion, ScreenFieldProfile

_REFERENCE = (960, 572)
_EXPECTED_REGION = (141, 412, 540, 505)
# Half-open reference bands: small ruby glyphs above body rows whose observed
# main text begins at approximately Y434 and Y466 on the three sample frames.
# Masking both bands is independent of the number of actual message lines.
_RUBY_BANDS = ((423, 432), (454, 464))


def _has_black_capture_bars(image: np.ndarray) -> bool:
    """Reject obvious paired black bars; this is not a general UI detector."""
    h, w = image.shape[:2]
    dh, dw = max(2, h // 80), max(2, w // 80)
    def black(block: np.ndarray) -> bool:
        return float(np.mean(block)) < 5.0 and float(np.std(block)) < 5.0
    return ((black(image[:dh]) and black(image[-dh:]))
            or (black(image[:, :dw]) and black(image[:, -dw:])))


def _current_frame_body_anchor_matches(image: np.ndarray) -> bool:
    """Fail closed on obvious UI scale or vertical-position changes.

    Uses near-neutral bright strokes from the leftmost message prefix, not
    battle semantics or an extra OCR inference. This is a narrow visual
    plausibility check, not a substitute for user-approved calibration.
    If glyphs/background cannot be distinguished, use the old full ROI.
    """
    h, w = image.shape[:2]
    x0, x1 = round(150 * w / 960), round(215 * w / 960)
    y0, y1 = round(420 * h / 572), round(520 * h / 572)
    patch = image[y0:y1, x0:x1]
    if patch.size == 0:
        return False
    # Resolve relative pixel geometry at a single reference size so 1x, 1.5x
    # and 2x follow the same conservative tolerance for each body line.
    patch = cv2.resize(patch, (65, 100), interpolation=cv2.INTER_AREA)
    lo = patch.min(axis=2).astype(np.int16)
    hi = patch.max(axis=2).astype(np.int16)
    # A fixed absolute threshold sees a uniformly light synthetic/game
    # background as "text". Use non-body rows to estimate the local neutral
    # floor, and require a meaningful brightness delta over that floor.
    # Never treat failure of this plausibility gate as a validated OCR event.
    background = float(np.median(lo[np.r_[0:9, 33:43, 67:73], :]))
    text_floor = max(155, min(230, round(background + 35)))
    active = ((lo >= text_floor) & ((hi - lo) <= 75)).sum(axis=1) >= 4
    runs = []
    start = None
    for offset, present in enumerate([*active, False]):
        if present and start is None:
            start = offset
        elif not present and start is not None:
            if offset - start >= 9:
                runs.append((420 + start, 419 + offset))
            start = None
    first = lambda pair: 432 <= pair[0] <= 438 and 447 <= pair[1] <= 452
    second = lambda pair: 465 <= pair[0] <= 470 and 478 <= pair[1] <= 484
    # The ROI itself remains intact in either case. A second line is optional:
    # when present it must have its expected baseline. An extra/moved main-text
    # run is unsafe because it may cross a fixed ruby-band mask.
    return bool(runs) and len(runs) <= 2 and first(runs[0]) and (
        len(runs) == 1 or second(runs[1])
    )


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
            "ruby_mask_rois": [], "ruby_excluded": False,
            "game_validated": False}
    if region.name != "battle_message" or region.kind != "MESSAGE":
        return original, info
    if not enabled:
        info["preprocess"] = "DISABLED"
        return original, info
    calibrated = (profile.validated_on_game_capture
                  and profile.ruby_body_rows_validated)
    if not calibrated and not preview_unvalidated:
        info["preprocess"] = ("UNVALIDATED_LIVE_DISABLED"
                              if not profile.validated_on_game_capture
                              else "RUBY_BANDS_UNVALIDATED_LIVE_DISABLED")
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
    if not _current_frame_body_anchor_matches(image):
        info["preprocess"] = "BODY_ANCHOR_MISMATCH"
        return original, info

    # Never crop or reflow either main text line. Start with the existing
    # profile-limited ROI (including its usual 2x resolution cap).
    result = np.ascontiguousarray(original.copy())
    reference_y = region.bounds_px[1]
    reference_h = region.bounds_px[3] - reference_y
    reference_x = region.bounds_px[0]
    reference_w = region.bounds_px[2] - reference_x
    mask_rois = []
    for top, bottom in _RUBY_BANDS:
        y0 = round((top - reference_y) * result.shape[0] / reference_h)
        y1 = round((bottom - reference_y) * result.shape[0] / reference_h)
        if y0 <= 0 or y1 >= result.shape[0] or y0 >= y1:
            info["preprocess"] = "INVALID_RUBY_MASK_GEOMETRY"
            return original, info
        # Linear vertical interpolation preserves the changing battle backdrop
        # better than a flat black/white rectangle, with no text detection or
        # extra OCR call. Source rows are outside the overwritten mask.
        upper = result[y0 - 1].astype(np.float32)
        lower = result[y1].astype(np.float32)
        for row in range(y0, y1):
            t = (row - y0 + 1) / (y1 - y0 + 1)
            result[row] = np.rint(upper * (1 - t) + lower * t).astype(np.uint8)
        mask_rois.append([
            round(reference_x * width / _REFERENCE[0]),
            round(top * height / _REFERENCE[1]),
            round((reference_x + reference_w) * width / _REFERENCE[0]),
            round(bottom * height / _REFERENCE[1]),
        ])
    info.update(
        preprocess=("RUBY_MASK_CALIBRATED" if calibrated
                    else "RUBY_MASK_PREVIEW_ONLY"),
        ruby_mask_rois=mask_rois,
        ruby_excluded=True,
        game_validated=bool(calibrated),
    )
    return result, info
