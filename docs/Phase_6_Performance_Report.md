# Phase 6 capture/OCR/event performance report

Date: 2026-10-22 (Asia/Tokyo)

## Evidence status

No Windows or BlueStacks evidence was available in this development workspace. Therefore no recognition accuracy, FPS, capture latency, OCR latency, event-detection rate, GUI latency, recovery time, or backend comparison value is reported as a real measurement.

The following table intentionally contains no substituted synthetic value.

| Metric | Windows native | ADB screenshot | Status |
|---|---:|---:|---|
| Capture FPS | — | — | Unmeasured on Windows/BlueStacks |
| Capture lag p50 / p95 / max | — | — | Unmeasured on Windows/BlueStacks |
| Frame interval / estimated drops | — | — | Unmeasured on Windows/BlueStacks |
| Window/ADB failure recovery | — | — | Unmeasured on Windows/BlueStacks |
| UI classification accuracy | — | — | No real labelled frames |
| Event detection misses / false positives | — | — | No real labelled frames |
| HP / status / turn / text OCR accuracy | — | — | No real labelled frames |
| Capture-to-observer p50 / p95 / max | — | — | No live worker-to-pipeline measurement |
| Capture-to-GUI p50 / p95 / max | — | — | No live GUI handoff measurement |

## Implemented measurement schema

`capture-probe` writes local JSON with:

- measured FPS, frame interval p50/p95/max, capture lag p50/p95/max;
- interval-derived estimated dropped frames only when `--expected-fps` is supplied;
- unavailable reasons (`WINDOW_NOT_FOUND`, `WINDOW_MINIMIZED`, ADB failures) and recovery p50/p95/max;
- explicit `CAPTURED_READ_ONLY_UNVERIFIED_CONTENT` status even if pixels are available.

`observer-replay --validation-dataset` emits UI/event/OCR counts and observer processing p50/p95/max. `LatencyLedger` emits capture-to-observer and capture-to-GUI p50/p95/max only when a runtime supplies those corresponding timestamp marks. Missing timing stages remain `null`; they must not be treated as zero latency.

The exact invocation, label schema and Phase 7 human-review gate are in [Phase_6_BlueStacks_Validation.md](Phase_6_BlueStacks_Validation.md).

## Required next measurements

1. Run both capture routes against a local BlueStacks 5 window and retain both reports.
2. Collect local labelled videos/screenshots covering all twelve scenarios listed in the validation procedure.
3. Compare replayed SQLite/FSM projection with labels, including duplicate messages, effect outcomes, turn ends and manual corrections.
4. Attach real ROI/template and Japanese OCR evidence only after manual review; do not add private media to Git or CI.
5. Record native GUI handoff timestamps and decide a capture backend by human review.
6. Confirm game/platform terms before operational use, then perform the separate Phase 7 Windows packaging/MSI gates.
