"""Offline Windows CI gate: official bundled model must decode real text."""
from __future__ import annotations
import argparse
from champions_assist.ocr_smoke import verify_real_cpu_ocr


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    args = parser.parse_args()
    result = verify_real_cpu_ocr(model_dir=args.model_dir)
    print("ONNX_OCR_CPU_TEXT_VERIFIED", result["ocr_cpu_provider"],
          result["ocr_text_verified"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
