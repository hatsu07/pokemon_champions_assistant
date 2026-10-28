# PP-OCRv6 small via shared ONNX Runtime (Windows)

## Architecture

The live OCR backend is **PP-OCRv6_small_det_onnx + PP-OCRv6_small_rec_onnx**,
loaded using the same `onnxruntime-directml` package as the screen classifier.

- **No separate Python environment, PaddlePaddle, sidecar installation,
  `CHAMPIONS_OCR_PYTHON`, or runtime download.**
- One persistent pair of ONNX Runtime sessions is initialized by the local OCR
  worker; recognition returns untrusted text, confidence and frame timestamps.
- Windows prefers DirectML GPU, can try CUDA if available, and falls back to
  CPU for `auto` (including when DML attaches but a model operator cannot run).
  The selected provider is reported as `ONNX_OCR_READY_GPU` or
  `ONNX_OCR_READY_CPU`.
- The GUI's screen classifier and OCR use separate model sessions, but the
  same executable, interpreter and ONNX Runtime installation.
- The OCR processor NEVER interprets battle moves or decides Pokémon identity.
  Only AI proposals validated by the existing evidence/state verifier may
  change battle state.

## Model acquisition and reproducibility

Models are fetched **at build time**, not on the user's machine.

```powershell
python scripts/stage_ocr_onnx.py --output src/champions_assist/data/ocr-v6
```

The script downloads the official PaddlePaddle release weights at pinned HF
revisions, verifies both model SHA-256 digests, validates the 18,708-entry
official PP-OCRv6 CTC dictionary, and stages them for PyInstaller. Windows
Actions runs this step before building the portable EXE, then bundles the
files into `_internal/ocr-v6/`.

Official source:
- https://huggingface.co/PaddlePaddle/PP-OCRv6_small_det_onnx
- https://huggingface.co/PaddlePaddle/PP-OCRv6_small_rec_onnx
- https://github.com/PaddlePaddle/PaddleOCR/blob/main/ppocr/utils/dict/ppocrv6_dict.txt

Weights and dictionary retain their upstream Apache-2.0 attribution.

## Validation

Windows CI invokes real detection and CTC recognition using CPU ONNX Runtime
**and** repeats a synthetic inference smoke in the packaged EXE. This checks
that the bundled weights, dictionary and model execution are present. CI also
runs deterministic tactical-state, late OCR, GUI stale-advice and queue tests.

The smoke result says `gpu_inference_tested=false`, because a hosted Windows
runner does not establish actual DirectML runtime speed and accuracy. Confirm
on the player's GPU: active provider, p50/p95 capture-to-OCR, Japanese
Pokémon/move-name correctness, and 45-second decision readiness. Do not
describe the build as GPU-validated based only on the CPU smoke.

If OCR remains disabled, the existing policy of continuing AI support with
previously known and explicitly uncertain information is unchanged. No
silent OCR-derived battle-state updates are added.
