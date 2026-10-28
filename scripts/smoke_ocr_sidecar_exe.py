"""GPU-free end-to-end Windows frozen EXE OCR sidecar smoke (CPU provider).

This checks parent -> EXE -> native bundled detector+recognizer -> pipe reply
without performing a game capture, network request or AI call. It is NOT
evidence of DirectML stability on the user's NVIDIA system.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np

from champions_assist.ocr_sidecar import OcrSidecarClient


def main(args: list[str]) -> int:
    if len(args) != 2:
        return 2
    exe, report = args
    client = OcrSidecarClient(
        device="cpu", gpu_id=0, ready_timeout=120, read_timeout=45,
        command=(exe, "--ocr-sidecar", "--ocr-device", "cpu", "--ocr-gpu-id", "0"))
    try:
        client.warmup()
        if client.provider != "CPUExecutionProvider":
            raise RuntimeError("FROZEN_OCR_SIDECAR_CPU_PROVIDER_MISMATCH")
        test_frame = np.full((64,128,3),255,dtype=np.uint8)
        result = client.read(test_frame,roi_name="synthetic_smoke")
        if not isinstance(result.text,str):
            raise RuntimeError("FROZEN_OCR_SIDECAR_RESULT_MISSING")
        info = {"status":"FROZEN_OCR_SIDECAR_SMOKE_OK",
                "provider": client.provider,
                "det_provider": client.det_provider,
                "rec_provider": client.rec_provider,
                "gpu_inference_tested":False,
                "game_capture":"NOT_USED","network":"NOT_USED"}
        Path(report).write_text(json.dumps(info,indent=2),encoding="utf-8")
        return 0
    finally:
        client.stop()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
