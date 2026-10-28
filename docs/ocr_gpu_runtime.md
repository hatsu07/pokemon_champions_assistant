# PP-OCRv6 small ONNX Runtime — built into the Windows desktop ZIP

The live OCR uses official PaddlePaddle **PP-OCRv6 small** ONNX detection
and recognition models with the **same ONNX Runtime** as the existing
screen classifier. The user installs **no separate Python, PaddlePaddle,
CUDA toolkit or `CHAMPIONS_OCR_PYTHON` environment variable**.
The frozen application includes both models and their official vocabulary
under `_internal/ocr-v6`. No runtime model download or network OCR.

## Build and release

1. Windows CI installs ONNX Runtime DirectML (already a core dependency)
   and PyYAML for staging metadata at build time.
2. `python scripts/stage_ppocrv6_onnx.py --output build/ocr-v6` fetches
   pinned versions of the official repositories:
   `PaddlePaddle/PP-OCRv6_small_det_onnx` and
   `PaddlePaddle/PP-OCRv6_small_rec_onnx`, their `inference.yml` metadata,
   and extracts the official recognition character dictionary.
3. It writes a SHA-256 manifest and stops on missing/invalid data.
4. `python scripts/check_onnx_ocr_bundle.py --model-dir build/ocr-v6`
   loads **both real models** and executes a synthetic crop through ONNX Runtime
   CPU, before PyInstaller builds the GUI.
5. The one-directory PyInstaller desktop ZIP embeds this exact `ocr-v6`
   folder and ONNX Runtime native libraries. The packaged GUI smoke loads
   and executes the actual CPU OCR, not a mocked call.

This is still a development distribution, not evidence of real-world
BlueStacks accuracy or GPU throughput. Validate Japanese text and GPU DirectML
on the user's Windows hardware before declaring production readiness.

## Runtime

- Model: PP-OCRv6 small detection + recognition; no other OCR model selection.
- Inference: prefer ONNX Runtime DirectML GPU, then CUDA GPU, with CPU
  fallback. Both OCR sessions are persistent in one OCR worker thread.
  The screen classifier and OCR use the same installed ONNX Runtime, though
  separate inference sessions.
- OCR remains raw untrusted text. ChatGPT interprets battle events and
  proposes state changes; the application verifies before applying them.
- The ROI capture and chronological evidence queue continue unchanged.
- Model/file/provider errors are visible as `ONNX_OCR_*` diagnostics.
  Missing OCR does **not** disable normal text-based tactical assistance.
- CPU-only CI does **not** prove GPU DirectML acceleration. The provider
  reported by the runtime is included in the OCR readiness diagnostics.

Official sources:
https://huggingface.co/PaddlePaddle/PP-OCRv6_small_det_onnx
https://huggingface.co/PaddlePaddle/PP-OCRv6_small_rec_onnx
