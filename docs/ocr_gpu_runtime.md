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

## dev55 → dev56 desktop crash investigation

After dev56, a reported Windows desktop EXE exited during the first capture
cycle. Its existing match JSONL contains `LOCAL_OCR_STARTED` with `READY`
(which is **not** proof that ONNX session warm-up completed), followed by a
successful `ONNX_GPU_DML` screen classification. It contains no Python
exception or native crash dump, so the root cause is **not yet established**.

The Windows desktop entry point now enables local error evidence before
starting Qt or ONNX Runtime:

- `%LOCALAPPDATA%\\PokemonChampionsAssistant\\diagnostics\\runtime-*.jsonl`:
  sanitized unexpected Python, worker-thread and finalizer exceptions,
  selected handled OCR/DirectML/capture errors, Qt warning/critical/fatal
  categories, and stage markers around OCR session initialization, OCR
  inference probe and first screen-classifier inference.
- `%LOCALAPPDATA%\\PokemonChampionsAssistant\\diagnostics\\runtime-*-native.log`:
  CPython faulthandler stack traces for native faults **where supported**.
  A native access violation may terminate the EXE before standard Python
  cleanup. A driver crash, hard process kill or OS failure may leave no dump.
- `%LOCALAPPDATA%\\PokemonChampionsAssistant\\diagnostics\\startup-*.log`:
  the existing sanitized entry-point startup failures.
- The existing `debug-logs/capture-*.jsonl` remains a separate live-match
  chronology; normal exit is indicated by `WINDOWS_ENTRY_EXIT` in the new
  runtime log.

Diagnostic event values are allowlisted; no raw OCR text, screenshots, API
credentials, prompts, exception messages or HTTP bodies are persisted.
The native stack file may expose local module paths: review it before sharing.

For isolation on an affected dev56 machine, use **設定 → 画面分類GPU →
ONNX推論デバイス → CPUのみ** and restart the monitoring session. This applies
to both the classifier and OCR. If CPU avoids the crash, that strengthens a
DirectML/native-provider hypothesis but does not conclusively identify it.
Use the new build to capture `OCR_SESSION_INIT_BEGIN` and any native stack
trace before deciding on a GPU-specific remediation.

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
