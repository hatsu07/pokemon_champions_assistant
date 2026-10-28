# Windows GPU stress diagnostic — development debug prerelease

This offline diagnostic compares PP-OCRv6 recognition and the five-class
screen classifier on the affected NVIDIA DirectML machine. It is not a
battle recognition accuracy test or a permanent workaround.

## Run from the dedicated Windows debug ZIP

1. Download PokemonChampionsGPUDiagnostics-v0.1.0-dev.N-windows-x64.zip
   from the Windows DEVELOPMENT prerelease created after the feature is
   merged and all Windows CI checks pass. Old dev57 does NOT include it.
2. Extract the entire ZIP to a new folder. Check BUILD_REVISION.txt.
3. Save other work and stop the live match monitor. Native GPU failures
   can terminate the diagnostic process or disturb the driver.
4. Double-click Run-GPU-Diagnostics.cmd. This Phase-1 launcher defaults to
   **E only**, avoiding repeated known crashes from condition D. It runs
   3 independent trials
   per condition, 20 inferences per trial on DirectML GPU 0.
5. Inspect the automatically opened folder:
   %LOCALAPPDATA%\PokemonChampionsAssistant\diagnostics\gpu-stress-*
   Share summary.json and the case JSONL files. If a crash occurs,
   also inspect runtime-*-native.log and Windows Application Error
   event 1000. Review local stack paths for privacy before sharing.

The test does not send screenshots, prompts or API calls. Inputs are
deterministic synthetic arrays. No screen capture is performed.

## The five conditions

A: rec — PP-OCRv6 small recognizer model alone, one DirectML session.
B: classifier — selected five-class ONNX screen classifier alone.
C: serial — both sessions initialized; recognizer then classifier with
   no overlapping Run() calls.
D: parallel — both distinct sessions initialized, two dedicated threads,
   synchronized per iteration; parallel Run() calls on DIFFERENT sessions.
E: multiprocess — two independent EXE processes, OCR recognizer in one and
   screen classifier in the other. Both use DirectML GPU 0, each with its OWN
   ONNX Runtime process state and session. A filesystem barrier aligns every
   inference round; both workers log monotonic run start/end. The report
   includes measured_iterations, overlap_iterations and overlap_ms.
   NO_MEASURED_OVERLAP is NOT a successful concurrent test.

The OCR recognizer uses the production resize/preprocessing on a synthetic
64x128 white BGR image. The screen classifier uses its production predict()
method on a synthetic 240x400 image. The detector is not used in these four
cases; follow-up testing may include det and the full OCR pipeline.

Each test is an independent child EXE. The parent records the exit code
even if native code crashes before Python can write an exception. An exit
code of 0xC0000005 is labeled ACCESS_VIOLATION. Unfinished iterations,
timeouts and other errors are never counted as successes. Every test
explicitly requests DirectML; CPU fallback is not enabled.

The screen classifier model is read from the same selected model choice
as the normal GUI, including the recorded trial opt-in. Missing screen model weights are SKIPPED_NO_SCREEN_MODEL; unverified
or otherwise incompatible weights are NOT fabricated or silently replaced. These cases are SKIPPED_NO_SCREEN_MODEL, not GPU passes.
The --screen-model override still requires validation.

Advanced example (from the extracted folder):
Run-GPU-Diagnostics.cmd --gpu-id 0 --iterations 100 --trials 5

To explicitly rerun A through E in a single set of conditions (warning: D is
known to crash on the affected NVIDIA PC):
Run-GPU-Diagnostics.cmd --gpu-diagnostic-modes rec,classifier,serial,parallel,multiprocess

Keep the same GPU, driver, model, and input in all five cases. Do not
immediately raise the trial count if the system repeatedly crashes.
This is opt-in stress testing and can expose driver instability.

Windows CI validates offline logic, CLI availability and the frozen EXE
without performing GPU inference. CI success is NOT evidence of stable
DirectML execution on the affected hardware. An all-pass result indicates
no reproduction within the measured attempts, not absence of a bug.
