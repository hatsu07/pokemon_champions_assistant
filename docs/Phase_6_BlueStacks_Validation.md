# Phase 6 — BlueStacks 5 validation procedure

This procedure validates a local, observer-only capture path. It does not automate any game input, start or stop BlueStacks, send screenshots/logs externally, call OpenAI, or establish that third-party terms allow the intended use. Check the applicable game and platform terms before using the harness.

## Evidence boundary

Docker tests and synthetic fixtures verify plumbing only. A report is not a Champions result merely because it has frames. The tools use three evidence labels:

- `SYNTHETIC`: test data only; never a real-screen claim.
- `RECORDED_CHAMPIONS`: a locally supplied, labelled recording; supports offline recognition checks but not live capture latency.
- `LIVE_WINDOWS_CHAMPIONS`: a Windows operator has retained a source hash and attested to the local target; it still requires human Phase 7 review.

No source screenshot, video, ADB serial, API key, or battle history is uploaded by these commands. Store recordings only in a user-controlled local directory, not in Git or CI artifacts.

## Portable Windows harness without installing development Python

The manually triggered GitHub workflow [windows-validation-artifact.yml](../.github/workflows/windows-validation-artifact.yml) creates a **portable PyInstaller `onedir` validation harness** on a Windows runner. It is not an MSI, a release build, or proof that capture works. Download and unzip the `PokemonChampionsValidation-windows-x64` artifact to a local Windows directory, then run its `.exe` in PowerShell.

The workflow smoke test only invokes `--version`; it has no BlueStacks, capture, API or gameplay test. Do not upload private captures to that workflow. Formal PyInstaller dependency validation, installer, upgrade and uninstall tests remain Phase 7.

## Read-only capture probes

Run the native-window route first from the portable harness on the Windows machine. Use the visible BlueStacks window title; the adapter reads its fresh client rectangle before each frame, records its reported DPI, and returns `WINDOW_NOT_FOUND` or `WINDOW_MINIMIZED` rather than reusing old coordinates. It does not resize, restore, focus, launch or close the window.

```powershell
.\PokemonChampionsValidation.exe capture-probe `
  --backend WINDOWS_NATIVE `
  --confirm-read-only-capture `
  --window-title "BlueStacks" `
  --frames 300 --duration-ms 30000 --expected-fps 10 `
  --output .\windows-native-report.json
```

For the fallback, verify that the ADB endpoint is private/local before running it. The implementation uses only `adb -s <serial> get-state` and `adb -s <serial> exec-out screencap -p`; it contains no `adb input`, shell tap, keyevent, launch, or port-forward command.

```powershell
.\PokemonChampionsValidation.exe capture-probe `
  --backend ADB_SCREENSHOT `
  --confirm-read-only-capture `
  --adb-serial "<locally-verified-serial>" `
  --frames 300 --duration-ms 30000 --expected-fps 5 `
  --output .\adb-report.json

.\PokemonChampionsValidation.exe capture-compare `
  --report .\windows-native-report.json --report .\adb-report.json
```

The comparison reports both measured capture-lag p95 values when both reports have frames, but makes no automatic backend recommendation. A report always says `real_game_validated: false`; inspect its target content, labelled replay results and terms separately. Record the following manual probes for each route: BlueStacks absent/starting, window closed, minimize/restore, resize, monitor/DPI change, and normal recovery. The capture worker recreates its source after an unavailable result so those changes can be measured as recovery time.

## ROI/template calibration and labelled replay

Keep every coordinate local and derived from an actual supplied screen. The calibration tool writes a normalized ROI JSON and optional explicit template crops. It makes no claim that a coordinate or template is correct.

```powershell
.\PokemonChampionsValidation.exe roi-calibrate `
  --image .\local-captures\command-menu.png `
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

- No actual Windows, BlueStacks 5, Pokémon Champions screenshot/video, ADB endpoint, title, DPI, ROI, template, Japanese OCR phrase, timer, latency or GUI handoff has been supplied to this repository.
- Windows Graphics Capture/Pillow behavior, ADB screenshot performance, native GUI behavior, OCR accuracy, actual event text and all Champions UI mechanics are unmeasured.
- No real OpenAI request, game-control operation, MSI build/install/upgrade/uninstall, credential-store test or terms review was performed.
