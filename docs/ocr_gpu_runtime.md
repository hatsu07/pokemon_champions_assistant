# Pokémon Champions: PP-OCRv6 small + GPU OCR

The live OCR backend is **PP-OCRv6_small_det + PP-OCRv6_small_rec**,
using NVIDIA GPU device `gpu:0`. It runs locally; OCR content never
leaves the device until the normal user-authorized tactical AI request.
The app never derives game mechanics from OCR text.

## Windows packaged EXE

A GPU-free Windows Actions runner can validate the GUI, transport, and
mocked OCR, but **cannot verify live GPU inference**. The EXE does not
bundle multi-GB Paddle/CUDA libraries. Configure a persistent local
Python GPU worker once before playing:

1. Install Python 3.12, an appropriate NVIDIA GPU driver, and verify
   `nvidia-smi` works.
2. From the repository checkout, run
   `powershell -ExecutionPolicy Bypass -File scripts/setup_windows_gpu_ocr.ps1`
   (default CUDA 12.6 wheel, driver >= 550.54.14).
   If an older supported driver is used, pass `-CudaIndex cu118`.
3. The setup installs the GPU wheel and `paddleocr>=3.7` into an isolated
   user-local venv and **preloads both small models**. It configures the
   `CHAMPIONS_OCR_PYTHON` user environment variable to its Python binary.
4. Restart the Windows GUI. The match reader starts a persistent child
   process (`python -m champions_assist.ocr_gpu_worker`). The child only
   exchanges PNG ROI crops and OCR candidate text over local stdio.
   No HTTP/server port and no ChatGPT call from the child.
5. The GUI reports `GPU_OCR_PYTHON_NOT_CONFIGURED`,
   `GPU_OCR_CUDA_UNAVAILABLE`, `GPU_OCR_INITIALIZATION_FAILED`, or
   `GPU_OCR_SIDECAR_TIMEOUT` explicitly; it **never silently falls
   back to CPU or Tesseract**. Without GPU, OCR cannot run.

When launched from source, install the same GPU runtime and package in
the current Python environment (the `[gpu-ocr]` extra installs
PaddleOCR; install PaddlePaddle GPU from its dedicated CUDA index
separately). The OCR engine initializes on the OCR worker thread.

**Note:** Official PaddlePaddle documentation has special Windows
wheels and limitations for NVIDIA RTX 50-series GPUs. Check their
version-specific installation guidance rather than assuming the
standard CUDA wheel works for every card.

## Timing, safety and validation

- The GPU model is loaded once, not once per frame or ROI.
- Every changed source frame is queued by default without a fixed
  4-frame eviction limit; message ROIs are read before HUD ROIs.
- The action dispatcher may wait up to 1.5 seconds for outstanding OCR
  if the local 32-second AI deadline retains at least five seconds.
- It then sends ALL processed but unacknowledged OCR candidates to a
  single tactical AI request. If OCR is not ready it sends the known
  facts + explicit uncertainty, not a guessed opponent.
- Rejected/unverified AI state changes are recorded; only verified
  changes mutate the state. In particular an own actor disagreement
  blocks an illegal command, but an unknown opponent alone does not.
- Automated Windows CI tests with mocked GPU output are not evidence of
  in-game 45s viability. Confirm hardware startup time, OCR p50/p95,
  capture-to-OCR delay and real Pokémon-name correctness on the user's
  actual GPU before claiming production readiness.

References: https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md
and https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/paddlepaddle_installation.en.md
