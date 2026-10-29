# Windows GPU stress diagnostic — optional assistant-side diagnostic

This offline diagnostic compares PP-OCRv6 recognition and the six-class
screen classifier on the affected NVIDIA DirectML machine. It is not a
battle recognition accuracy test or a permanent workaround.

## Developer-only execution from the Assistant ZIP (no separate GPU ZIP)

**This is not a normal user workflow.** New Windows DEVELOPMENT Assistant ZIPs
deliberately **exclude `Run-GPU-Diagnostics.cmd`**. The explicit diagnostic
CLI in `PokemonChampionsAssistant.exe` remains available to developers.
Previously published releases are not changed.

1. Download `PokemonChampionsAssistant-v0.1.0-dev.N-windows-x64.zip` from
   the Windows DEVELOPMENT prerelease and extract it to a new folder.
   Check `BUILD_REVISION.txt`.
2. Save other work and **stop the live match monitor**. Native DirectML/NVIDIA
   failures can terminate the diagnostic process or disturb the GPU driver.
3. From PowerShell, change to the extracted folder containing
   `PokemonChampionsAssistant.exe`, then explicitly run:

   ```powershell
   .\PokemonChampionsAssistant.exe --gpu-diagnostics --gpu-id 0 --gpu-diagnostic-modes multiprocess --iterations 20 --trials 3
   ```

   This runs condition E (two independent EXE processes on DirectML GPU 0)
   in three trials of 20 inferences each. The GUI-subsystem EXE reports
   completion in a Windows dialog; do not expect terminal output.
   Developers who prefer the old CMD wrapper can use the checked-in
   `scripts/Run-GPU-Diagnostics.cmd` from the **source repository**, copied
   manually next to the EXE. It is not shipped in the ZIP.
4. Examine the files in:
   `%LOCALAPPDATA%\PokemonChampionsAssistant\diagnostics\gpu-stress-*`.
   The CLI does not automatically open this folder; open it manually.
   Before sharing `summary.json`, case JSONL files or
   `runtime-*-native.log`, review any local stack paths for privacy.
   Windows Application Error event 1000 may provide crash evidence.

The test uses deterministic synthetic arrays. It does **not** capture game
screens, transmit prompts, or call ChatGPT APIs. It is an opt-in stress test
and not a measure of battle recognition accuracy.

## The five conditions

A: rec — PP-OCRv6 small recognizer model alone, one DirectML session.
B: classifier — selected six-class ONNX screen classifier alone.
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
method on a synthetic 240x400 image. The detector is not used in these five
cases; follow-up testing may include det and the full OCR pipeline.

A-D run one child EXE per trial; E runs two child EXEs per trial.
E's sync files are temporary and removed after both children are reaped.
The parent records the exit code
even if native code crashes before Python can write an exception. An exit
code of 0xC0000005 is labeled ACCESS_VIOLATION. Unfinished iterations,
timeouts and other errors are never counted as successes. Every test
explicitly requests DirectML; CPU fallback is not enabled.

The screen classifier model is read from the same selected model choice
as the normal GUI, including the recorded trial opt-in. Missing screen model weights are SKIPPED_NO_SCREEN_MODEL; unverified
or otherwise incompatible weights are NOT fabricated or silently replaced. These cases are SKIPPED_NO_SCREEN_MODEL, not GPU passes.
The --screen-model override still requires validation.

Advanced example (PowerShell in the extracted EXE folder):

```powershell
.\PokemonChampionsAssistant.exe --gpu-diagnostics --gpu-id 0 --gpu-diagnostic-modes multiprocess --iterations 100 --trials 5
```

To explicitly rerun A through E (warning: condition D has crashed on the
affected NVIDIA machine):

```powershell
.\PokemonChampionsAssistant.exe --gpu-diagnostics --gpu-id 0 --gpu-diagnostic-modes rec,classifier,serial,parallel,multiprocess --iterations 20 --trials 3
```

Keep the same GPU, driver, model, and input in all five cases. Do not
immediately raise the trial count if the system repeatedly crashes.
This is opt-in stress testing and can expose driver instability.

Windows CI validates offline logic, CLI availability and the frozen EXE
without performing GPU inference. CI success is NOT evidence of stable
DirectML execution on the affected hardware. An all-pass result indicates
no reproduction within the measured attempts, not absence of a bug.
