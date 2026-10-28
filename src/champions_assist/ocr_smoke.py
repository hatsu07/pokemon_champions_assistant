"""Deterministic, self-authored synthetic OCR crop for release verification.

This checks actual detector + recognizer output, not just session creation.
Japanese game captures and GPU timing still require separate user-hardware gates.
"""
from __future__ import annotations

import cv2
import numpy as np

from .ocr_onnx import ENGINE_NAME, PPocrV6SmallOnnx


def synthetic_ocr_sample() -> np.ndarray:
    sample = np.full((96, 500, 3), 255, dtype=np.uint8)
    cv2.putText(sample, "97%", (15, 68), cv2.FONT_HERSHEY_SIMPLEX,
                1.9, (0, 0, 0), 3, cv2.LINE_AA)
    return sample


def verify_real_cpu_ocr(*, model_dir=None) -> dict[str, object]:
    reader = PPocrV6SmallOnnx(model_dir=model_dir, device="cpu")
    result = reader.read(synthetic_ocr_sample(), roi_name="opponent_hp_percent")
    if reader.provider != "CPUExecutionProvider":
        raise RuntimeError("ONNX_OCR_CPU_PROVIDER_NOT_ACTIVE")
    if result.engine != ENGINE_NAME:
        raise RuntimeError("ONNX_OCR_ENGINE_MISMATCH")
    if result.confidence is not None and not 0 <= result.confidence <= 1:
        raise RuntimeError("ONNX_OCR_CONFIDENCE_INVALID")
    # The fixture contains "97%". These digits must survive actual text
    # detection AND CTC decoding; empty OCR output never passes this gate.
    if "97" not in "".join(result.text.split()):
        raise RuntimeError("ONNX_OCR_EXPECTED_TEXT_MISSING: " + repr(result.text))
    return {"ocr_cpu_provider": reader.provider,
            "ocr_cpu_inference_tested": True,
            "ocr_text_verified": True}
