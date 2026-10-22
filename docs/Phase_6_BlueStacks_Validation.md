# Phase 6 — BlueStacks 5 validation procedure

This procedure validates a local, observer-only capture path. It does not automate any game input, start or stop BlueStacks, send screenshots/logs externally, call OpenAI, or establish that third-party terms allow the intended use. Check the applicable game and platform terms before using the harness.

## Evidence boundary

Docker tests and synthetic fixtures verify plumbing only. A report is not a Champions result merely because it has frames. The tools use three evidence labels:

- `SYNTHETIC`: test data only; never a real-screen claim.
- `RECORDED_CHAMPIONS`: a locally supplied, labelled recording; supports offline recognition checks but not live capture latency.
- `LIVE_WINDOWS_CHAMPIONS`: a Windows operator has retained a source hash and attested to the local target; it still requires human Phase 7 review.

No source screenshot, video, ADB serial, API key, or battle history is uploaded by these commands. Store recordings only in a user-controlled local directory, not in Git or CI artifacts.

## Phase 6R operator-reported reference environment — 2026-10-22

The operator has confirmed that Pokémon Champions launches on the following local configuration:

| Item | Operator-reported value | Evidence status |
|---|---|---|
| BlueStacks | 5.22.285.1013 | Application launch confirmed; capture not yet measured |
| Android image | Android 13 (beta) | Application launch confirmed; capture not yet measured |
| Orientation / configured resolution | Landscape, 1920 × 1080 | Emulator configuration; not a captured-image measurement |
| Android density | 240 dpi | Emulator configuration; not Win32 window DPI |

These are the Phase 6R reference conditions, not a successful Windows capture result. In particular, Android's 240 dpi must not be substituted for `GetDpiForWindow`: monitor scaling, the BlueStacks client rectangle, and Pillow's grabbed pixel dimensions must be measured from the probe report. No Champions ROI is fixed from this table.

## Portable Windows harness without installing development Python

The manually triggered GitHub workflow [windows-validation-artifact.yml](../.github/workflows/windows-validation-artifact.yml) creates a **portable PyInstaller `onedir` validation harness** on a Windows runner. It runs the Phase 6 tests, freezes a Windows executable, confirms `--version`, and confirms that a deliberately nonexistent target window is safely reported unavailable. It is not an MSI, a release build, or proof that BlueStacks capture works. The downloaded artifact contains the payload `PokemonChampionsValidation-windows-x64.zip`; after extracting that payload, the executable is exactly `PokemonChampionsValidation\PokemonChampionsValidation.exe`.

The workflow includes `PIL.ImageGrab`, required only by the native read-only Windows capture path, and does not bundle ADB. Do not upload private captures to that workflow. Formal PyInstaller dependency validation on a user's PC, installer, upgrade and uninstall tests remain Phase 7.

## First-frame procedure — Windows operator

This is the only procedure to run for the present Phase 6R checkpoint. It captures no more than one frame, makes no API request, and has no game-control capability.

1. In GitHub Actions, manually run **Windows validation harness artifact** for the intended revision. Download the resulting `PokemonChampionsValidation-windows-x64` artifact. Extract the browser-downloaded artifact once, then extract the contained `PokemonChampionsValidation-windows-x64.zip` to a private local folder.
2. In PowerShell, use the following commands. They place the executable, JSON report, and image under the current Windows user's Pictures folder; no development Python is needed.

```powershell
$Root = Join-Path $env:USERPROFILE "Pictures\PokemonChampionsValidation"
New-Item -ItemType Directory -Force $Root | Out-Null

# The browser download is the outer GitHub artifact archive. Extract it once,
# then locate and extract the portable payload archive it contains.
$ArtifactDownload = Join-Path $env:USERPROFILE "Downloads\PokemonChampionsValidation-windows-x64.zip"
$ArtifactExtract = Join-Path $Root "artifact-download"
Expand-Archive -LiteralPath $ArtifactDownload -DestinationPath $ArtifactExtract -Force
$PayloadZip = Get-ChildItem -LiteralPath $ArtifactExtract -Recurse -Filter "PokemonChampionsValidation-windows-x64.zip" |
  Select-Object -First 1 -ExpandProperty FullName
if (-not $PayloadZip) { throw "Portable payload ZIP was not found in the downloaded artifact" }
Expand-Archive -LiteralPath $PayloadZip -DestinationPath $Root -Force

$Harness = Join-Path $Root "PokemonChampionsValidation\PokemonChampionsValidation.exe"
if (-not (Test-Path -LiteralPath $Harness)) { throw "Harness was not extracted: $Harness" }
& $Harness --version
if ($LASTEXITCODE -ne 0) { throw "Harness version check failed" }
```

3. Start BlueStacks and Pokémon Champions manually. Keep the BlueStacks window visible and not minimized. Find a substring of its current title without focusing, resizing, or moving it:

```powershell
Get-Process | Where-Object { $_.MainWindowTitle -match "BlueStacks" } |
  Select-Object ProcessName, MainWindowTitle
```

4. Set `$WindowTitle` to a nonempty substring shown by the preceding command, then request exactly one native frame. The report and image paths below are the locations to inspect and retain locally.

```powershell
$WindowTitle = "BlueStacks"
$Image = Join-Path $Root "phase6r-native-first-frame.png"
$Report = Join-Path $Root "phase6r-native-first-frame-report.json"

& $Harness capture-probe `
  --backend WINDOWS_NATIVE `
  --confirm-read-only-capture `
  --window-title $WindowTitle `
  --frames 1 --duration-ms 5000 --retry-delay-ms 250 `
  --save-first-frame $Image `
  --output $Report
if ($LASTEXITCODE -ne 0) { throw "Native first-frame probe failed to run" }

$Probe = Get-Content -Raw -LiteralPath $Report | ConvertFrom-Json
$Probe.status
$Probe.frame_measurement
$Probe.capture_timing
if ($Probe.status -eq "CAPTURED_READ_ONLY_UNVERIFIED_CONTENT" -and (Test-Path -LiteralPath $Image)) {
  Invoke-Item -LiteralPath $Image
} else {
  Write-Warning "No native first frame was saved. Leave the report in place and use the ADB fallback below."
}
```

`CAPTURED_READ_ONLY_UNVERIFIED_CONTENT`, an existing PNG, one or more `image_size_px` samples, one or more `window_client_size_screen_px` and `win32_window_dpi` samples, and non-null `first_frame_acquisition_ms` mean only that one local pixel read completed. Confirm visually that the opened PNG contains Pokémon Champions, then report the status and measurements. Do not upload the PNG, report, ADB serial, or battle data to GitHub. This result remains **Phase 6R unaccepted** until the operator reports that successful capture.

If the saved PNG does not show the matched BlueStacks window, do not calibrate ROI or treat the pixel read as a success. Retain the local report, which now includes `frame_measurement.matched_window` (title and client rectangle) and its DPI-awareness status. The next harness build requests per-monitor DPI awareness before it reads geometry or uses Pillow; rerun exactly one frame with the rebuilt artifact and compare those values. Do not use guessed coordinates or capture extra frames to compensate.

## Read-only capture probes

Run the native-window route first from the portable harness on the Windows machine. Use the visible BlueStacks window title; the adapter reads its fresh client rectangle before each frame, records its reported DPI, and returns `WINDOW_NOT_FOUND` or `WINDOW_MINIMIZED` rather than reusing old coordinates. It does not resize, restore, focus, launch or close the window.

The first-frame procedure above is the native-window route. It requires a target-window title (or an explicit rectangle for a separate diagnostic) and refuses an untargeted desktop capture. The report includes `frame_measurement` with the grabbed image size, fresh client-rectangle size in screen pixels, image-to-client pixel scale, Win32 window DPI, and DPI/96 scale. `capture_timing` records total probe time and the first-frame acquisition time. `--save-first-frame` refuses to overwrite an existing image and does not send it anywhere.

For the fallback, verify that the ADB endpoint is private/local before running it. The implementation uses only `adb -s <serial> get-state` and `adb -s <serial> exec-out screencap -p`; it contains no `adb input`, shell tap, keyevent, launch, or port-forward command.

```powershell
# Run this only if the Windows-native first-frame report is unavailable or has zero frames.
# Use a local/private ADB endpoint only. Do not run adb connect, forward, input, tap, or keyevent.
$AdbCandidates = @(
  (Join-Path $env:ProgramFiles "BlueStacks_nxt\HD-Adb.exe"),
  (Join-Path ${env:ProgramFiles(x86)} "BlueStacks_nxt\HD-Adb.exe")
)
$AdbPath = $AdbCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $AdbPath) { throw "Provide the trusted local path to HD-Adb.exe or adb.exe" }
& $AdbPath devices

# Copy a serial displayed as 'device' by the preceding read-only listing.
$AdbSerial = "<locally-verified-serial>"
$AdbImage = Join-Path $Root "phase6r-adb-first-frame.png"
$AdbReport = Join-Path $Root "phase6r-adb-first-frame-report.json"

& $Harness capture-probe `
  --backend ADB_SCREENSHOT `
  --confirm-read-only-capture `
  --adb-path $AdbPath `
  --adb-serial $AdbSerial `
  --frames 1 --duration-ms 5000 --retry-delay-ms 250 `
  --save-first-frame $AdbImage `
  --output $AdbReport
if ($LASTEXITCODE -ne 0) { throw "ADB first-frame probe failed to run" }

$AdbProbe = Get-Content -Raw -LiteralPath $AdbReport | ConvertFrom-Json
if ($AdbProbe.status -eq "CAPTURED_READ_ONLY_UNVERIFIED_CONTENT" -and (Test-Path -LiteralPath $AdbImage)) {
  Invoke-Item -LiteralPath $AdbImage
} else {
  Write-Warning "No ADB first frame was saved. Retain the local report; do not retry with game-control commands."
}
```

ADB has no Win32 window/DPI/client-rectangle measurements. The fallback uses only `get-state` and `exec-out screencap -p`; it contains no `adb input`, shell tap, keyevent, launch, or port-forward command. Proceed to ROI calibration only when the native report has `status: CAPTURED_READ_ONLY_UNVERIFIED_CONTENT`, at least one `frame_measurement.image_size_px` sample, at least one Win32 client/DPI sample, and the saved local image has been manually confirmed to show Pokémon Champions. A successful pixel read is still not a UI/OCR validation. If image size, client size, or pixel scale changes while resizing/moving monitors, retain separate reports and calibrate ROIs for the applicable measured reference size.

The comparison reports both measured capture-lag p95 values when both reports have frames, but makes no automatic backend recommendation. A report always says `real_game_validated: false`; inspect its target content, labelled replay results and terms separately. Record the following manual probes for each route: BlueStacks absent/starting, window closed, minimize/restore, resize, monitor/DPI change, and normal recovery. The capture worker recreates its source after an unavailable result so those changes can be measured as recovery time.

## ROI/template calibration and labelled replay

Keep every coordinate local and derived from an actual supplied screen. The calibration tool writes a normalized ROI JSON and optional explicit template crops. It makes no claim that a coordinate or template is correct.

```powershell
.\PokemonChampionsValidation.exe roi-calibrate `
  --image .\local-calibration\phase6r-native-first-frame.png `
  --output .\local-calibration\roi.json `
  --source-label "RECORDED_CHAMPIONS_UNREVIEWED" `
  --roi "ROI_UI_CONTROLS=0.05,0.68,0.95,0.97" `
  --template "COMMAND_MENU,ROI_UI_CONTROLS,.\local-calibration\command-menu-template.png"
```

Use `observer-replay` in Docker for offline videos/images and a separate local DB. Pass `--validation-dataset` for a labelled JSON dataset. The schema is intentionally small:

```json
{
  "schema_version": "phase6-validation-v1",
  "source_label": "local recording label set",
  "evidence": {
    "kind": "RECORDED_CHAMPIONS",
    "capture_backend": "WINDOWS_NATIVE",
    "source_sha256": "sha256:<recording digest>",
    "operator_attested": false,
    "terms_reviewed": false
  },
  "frames": [
    {
      "frame_id": "frame-0001",
      "visual_ui": "COMMAND_MENU",
      "event_kinds": ["MOVE_USED"],
      "ocr_text": {"ROI_BATTLE_LOG": "manually verified text"},
      "scenarios": ["NORMAL_COMMAND", "MOVE_AND_POKEMON_TEXT"]
    }
  ]
}
```

```bash
docker compose run --rm dev python -m champions_assist observer-replay \
  --video /workspace/local-captures/recording.mp4 \
  --roi-config /workspace/local-calibration/roi.json \
  --validation-dataset /workspace/local-calibration/labels.json \
  --db /workspace/local-calibration/replay.sqlite3 \
  --master-dir masters
```

The replay output reports UI/event/OCR misses and p50/p95/max processing time. The capture worker report separately reports frame rate, frame intervals, capture lag, estimated interval-based dropped frames and recovery times. A GUI integration may mark capture-to-observer and capture-to-GUI latency through `LatencyLedger`; do not report those fields when no live GUI handoff was measured.

When `source_sha256` is present, `observer-replay` calculates the local video/image/fixture digest and exposes whether it matches. A real-evidence acceptance gate remains blocked on a missing or mismatched digest. For multiple still images it uses the ordered filename-plus-file-digest manifest produced by the replay input order.

## Required labelled scenarios and acceptance

Every real dataset must label and replay these scenarios before Phase 7 is considered for human review:

1. `OPPONENT_TEAM_SELECTION`
2. `NORMAL_COMMAND`
3. `MOVE_MENU`
4. `PARTY_MENU`
5. `PIVOT_RESOLUTION`
6. `FAINT_REPLACEMENT`
7. `RANDOM_FORCED_SWITCH`
8. `MOVE_AND_POKEMON_TEXT`
9. `HP_STATUS_STAT_STAGES`
10. `WEATHER_FIELD_WALL_HAZARD`
11. `MEGA_ABILITY_ITEM`
12. `TURN_AND_RESULT`

`PARTY_MENU` remains one visual label. The replay must demonstrate, through the existing append-only events/FSM, whether it is normal navigation, a pivot target, a faint replacement or an effect replacement. A random forced switch with no selection must not open an API decision. A move activation, effect success/failure, duplicate message, turn end and manual correction must likewise be checked against the SQLite replay rather than inferred from a screenshot alone.

The dataset evaluator returns `READY_FOR_HUMAN_REVIEW` only when all twelve labels replay successfully, the recording is labelled as real Windows evidence, its supplied digest matches, the operator attests it, and terms are marked reviewed. This is deliberately not an approval. Phase 7 remains blocked until native EXE, packaging, MSI and installation gates are separately exercised.

## Current unverified items

- The BlueStacks 5.22.285.1013 / Android 13 beta / 1920 × 1080 landscape / 240 dpi launch configuration is operator-reported. No actual capture report, screenshot/video, title, Win32 DPI, client rectangle, grabbed-image size, ROI, template, Japanese OCR phrase, timer, latency or GUI handoff has been supplied to this repository.
- Windows Graphics Capture/Pillow behavior, ADB screenshot performance, native GUI behavior, OCR accuracy, actual event text and all Champions UI mechanics are unmeasured.
- No real OpenAI request, game-control operation, MSI build/install/upgrade/uninstall, credential-store test or terms review was performed.
