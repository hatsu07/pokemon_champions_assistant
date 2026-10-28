# PP-OCRv6 small ONNX Runtime — Windows desktop bundle

The Windows desktop ZIP embeds official PP-OCRv6 small detection/recognition
ONNX models and their **model-matched official CTC vocabulary** under
`_internal/ocr-v6`. No separate Python/Paddle/CUDA installation or runtime
OCR download is required. OCR text is untrusted evidence and does not directly
overwrite battle state; AI-proposed changes are verified by the application.

## Reproducible build and validation

1. `python scripts/stage_ocr_onnx.py --output build/ocr-v6` is the **only**
   model download/staging route. It pins full upstream Hugging Face revisions
   and verifies the detector and recognizer against known SHA-256 digests.
   Recognition characters come from the pinned recognizer's `inference.yml`.
   An Apache-2.0 license is downloaded from a fixed PaddleOCR commit.
2. A manifest records SHA-256 digests for both model weights, dictionary,
   recognition config and license. Every runtime initialization verifies
   the bundle bytes before loading either model.
3. `python scripts/check_onnx_ocr_bundle.py --model-dir build/ocr-v6`
   performs real CPU detection **and** recognition of a self-generated
   synthetic `97%` image and rejects empty/wrong text. Windows CI runs this
   before packaging.
4. Exactly one `--add-data "build\\ocr-v6;ocr-v6"` entry embeds the staged
   files in PyInstaller. The frozen GUI startup smoke re-verifies the identical
   bundled manifest and real `97` recognition; the report includes
   `ocr_cpu_provider` and `ocr_text_verified`.
5. Neither CI gate makes a claim about Japanese Pokémon Champions captures
   or GPU inference performance; real ROI fixtures and user hardware tests
   remain necessary.

## GPU and CPU behavior

OCR reads the **same configured provider policy and GPU index** as the screen
classifier: `cpu`, `auto`, `gpu`, `directml` or `cuda`, with GPU IDs 0–7.
Sessions are separate but share the same installed ONNX Runtime. `auto` can
fall back to CPU (including when an attached provider fails its warm-up
inference). Explicit `gpu`, `directml` and `cuda` requests fail with a
visible `ONNX_OCR_*` reason rather than quietly switching to CPU.

OCR startup events include the requested device/GPU index; subsequent provider
events report the effective attached provider and readiness or error reason.
A provider listed by ONNX Runtime is **not** proof that every operator
executed on the GPU. DirectML execution, CPU operator fallback and p50/p95
latency need hardware tracing and GPU tests. Input event ordering, bounded
reaction deadlines and AI verification of state changes remain unchanged.

## Local OCR diagnostic in テスト全般

The **バトルイベント読み取りテスト → ローカルOCR診断（AI送信なし）**
button uses the same bundled PP-OCRv6 ONNX engine and screen-classifier
device/GPU index preference as the live event reader. It does not call the AI
or write OCR text into match state. The report includes the actual attached
provider and individual ROI errors.

`OCR_RUNTIME_MISSING` means the **legacy Tesseract diagnostic** was run,
not a recognition-quality failure. Use an updated Windows distribution.
`ONNX_OCR_MODELS_MISSING` means the ONNX model bundle was not found;
`ONNX_OCR_BUNDLE_INTEGRITY_FAILED` means its manifest check failed.
Neither is proof that a game message was illegible. When all ROIs execute
without runtime errors but output blank text, inspect ROI calibration and
Japanese recognition accuracy separately.

## Remaining accuracy tests

Collect consented/anonymized game ROIs for `battle_message` and
`system_message`, including multiple Japanese lines, distinct Pokémon names
and messages near ROI boundaries. Record expected transcriptions and line
ordering; measure character error rate and latency before calling the
application match-ready. Do not infer match-readiness from the synthetic
CPU-only smoke.

Official models:
https://huggingface.co/PaddlePaddle/PP-OCRv6_small_det_onnx
https://huggingface.co/PaddlePaddle/PP-OCRv6_small_rec_onnx
