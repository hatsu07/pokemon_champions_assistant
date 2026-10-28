"""No-network diagnostic using the SAME bundled ONNX OCR as live events.

User images and OCR strings are returned to the in-memory GUI only.
They are never written to GitHub, logs, or promoted into trusted match state.
"""
from __future__ import annotations

from time import perf_counter
import numpy as np

from .calibrated_screen_fields import (
    ScreenFieldProfile, IGNORED_OCR_ROIS, parse_field_text,
)
from .ocr import OcrEngine
from .ocr_onnx import make_onnx_ocr


def inspect_battle_event_image(
    image: np.ndarray, profile: ScreenFieldProfile,
    *, engine: OcrEngine | None = None, device: str = "auto", gpu_id: int = 0,
) -> dict:
    if not profile.compatible(image):
        return {"status": "IMAGE_ROI_GEOMETRY_MISMATCH", "rows": []}
    reader = engine if engine is not None else make_onnx_ocr(device=device, gpu_id=gpu_id)
    rows = []
    for region in profile.regions:
        if (region.name in IGNORED_OCR_ROIS
                or "BATTLE_EVENT" not in region.applicable_labels
                or region.kind not in ("HP_FRACTION", "HP_PERCENT", "MESSAGE")):
            continue
        crop = profile.crop(image, region)
        started = perf_counter()
        try:
            reading = reader.read(crop, roi_name=region.name)
            parsed = parse_field_text(region, reading.text, frame_id="diagnostic")
            row = dict(name=region.name, kind=region.kind, crop=crop,
                       text=reading.text, confidence=reading.confidence,
                       parsed=parsed.value if parsed else None, error="")
        except Exception as exc:
            row = dict(name=region.name, kind=region.kind, crop=crop,
                       text="", confidence=None, parsed=None,
                       error=getattr(exc, "code", type(exc).__name__))
        row["elapsed_ms"] = round((perf_counter() - started) * 1000)
        rows.append(row)
    return {"status": "OCR_DIAGNOSTIC_COMPLETE", "rows": rows,
            "engine": getattr(reader, "engine_name", type(reader).__name__),
            "provider": getattr(reader, "provider", "UNKNOWN"),
            "requested_device": device, "requested_gpu_id": gpu_id}
