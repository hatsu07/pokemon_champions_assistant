# Phase 2 — persistent OCR sidecar (Windows development build)

Scope: only the live ONNX OCR inference worker is isolated from the GUI
process. This is not a change to battle recommendations, screen classification,
the OCR detector/recognizer weights, the match FSM or game controls.

## Design and reason

The affected NVIDIA/DirectML desktop reproduced native 0xC0000005 crashes
with two ONNX Runtime sessions running concurrently in the same Python process
(three of three trials). Dev59's cross-process comparison successfully
completed three of three trials, with 58 of 60 intervals overlapping.

- The GUI process retains the five-class screen classifier on its selected GPU.
- The existing AsyncBattleEventReader keeps ROI change detection, candidate
  parsing, bounded/nonblocking queue behavior, monotonic frame timestamps,
  deduplication and turn-epoch stale-result protections.
- For Windows GPU/auto OCR, the reader creates an OcrSidecarClient; its
  background worker starts one child EXE once and keeps it alive until
  monitoring stops. That child creates both PP-OCRv6 ONNX det/rec sessions and
  performs the same original warmup/inference. It is not restarted per ROI.
- Explicit CPU OCR keeps its existing in-process path. Linux CI's existing
  injected OCR fixtures remain unchanged. There is **no silent fallback**
  to the crashing in-process GPU path when the sidecar fails.

## Protocol and safety

Only anonymous local stdin/stdout pipes are used. A length-prefixed UTF-8 JSON
control message carries one PNG-encoded ROI in memory (no full screen capture,
temporary image or raw text persisted). The child returns the OCR text and
confidence to the existing candidate-only parser. The protocol bounds message
and image sizes, requires request IDs and validates replies. Pipes are private
to the child; no listening port, account credentials or API traffic is involved.

Warmup and all IPC calls occur in the pre-existing OCR background thread,
never on the GUI/turn thread. Timeout watchdogs kill stuck children. stop()
terminates without joining on the GUI thread and a daemon reaper collects the
process. Unexpected sidecar failure sets an explicit OCR_SIDECAR_* reason,
stops additional queued OCR consumption and never fabricates OCR observations.
On the next explicitly started monitoring session, a new child is created.

## Verification limits

Windows CI runs synthetic real-subprocess contract tests, child-crash and
timeout tests, and a **real frozen EXE** CPU-only OCR sidecar smoke covering
both bundled ONNX models, IPC and child startup. These checks do NOT prove
NVIDIA DirectML stability or real BlueStacks accuracy.

Acceptance on the affected Windows GPU after the build:
1. Start a normal live monitoring session and check for ONNX_OCR_READY_GPU
   and the sidecar provider in diagnostic logs. Confirm screen classification
   remains GPU-backed and turn response is nonblocking.
2. Exercise OCR messages, HP, and PP over repeated frames: frame IDs and
   turn epochs must remain correct; no stale candidate may overwrite newer
   state. Confirm no API input change beyond the existing OCR candidates.
3. Monitor GPU/crash logs for an extended session and compare latency, OCR
   backlog, GPU memory, and recognition quality to dev59/dev58 baselines.
4. Stop/start monitoring repeatedly and exit the application during busy OCR;
   confirm no lingering child EXE processes.
5. Classify any crashes with the Windows event log/dump. Do not infer a
   driver fix from the earlier synthetic cross-process test.

## Rollback and diagnosis

The legacy in-process OCR constructor is still available for offline fixtures
and explicit CPU use; the Windows GUI no longer uses it for GPU OCR.
No automatic failover to CPU or to unsafe in-process GPU is introduced.
The Phase-1 GPU experiment launcher remains available and unchanged.
