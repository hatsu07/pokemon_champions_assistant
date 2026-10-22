# Windows desktop GUI portable build (Phase 7 entry point preview)

The `Windows validation harness artifact` workflow builds **three separate artifacts**:

| Artifact | Entry point | Intended behavior |
|---|---|---|
| `PokemonChampionsValidation-windows-x64` | `windows_validation_entry.py` | Console CLI; exits after reporting state or running a probe |
| `PokemonChampionsSettingsValidation-windows-x64` | `windows_settings_validation_entry.py` | Settings-only GUI; match-start and API-send are disabled |
| `PokemonChampionsAssistant-windows-x64` | `windows_desktop_entry.py` | **Windowed PySide6 desktop GUI**, no console on launch |

The desktop package is a **portable technical preview**, **not an MSI or complete live-battle system**. Native one-frame capture was validated separately; continuous observer/OCR/FSM integration and live API are not yet accepted.

## Download and launch

1. Merge the reviewed pull request to `master`.
2. In GitHub → Actions → **Windows validation harness artifact**, choose **Run workflow**.
3. Download the artifact `PokemonChampionsAssistant-windows-x64`.
4. GitHub's downloaded ZIP contains a nested `PokemonChampionsAssistant-windows-x64.zip`. Extract both archives.
5. Open `PokemonChampionsAssistant\\PokemonChampionsAssistant.exe` by double-click. It should open the desktop directly, **without a terminal**. Allow first-launch settings to appear.
6. Keep the entire onedir directory intact, including `_internal`.

The package bundles the three LIVE text masters, the bundled fallback current team CSV, and five fixed catalogue CSVs. User-imported teams, SQLite logs, API keys, screenshots and `private_history` files are **not** packaged. The selected private team CSV is read from user storage only on new-match preload. This entry point does **not** opt in to real paid API calls.

## Failure diagnostics

If the GUI fails to appear, check:

`%LOCALAPPDATA%\\PokemonChampionsAssistant\\diagnostics\\startup-*.log`

The logger stores only exception type and Python stack *locations*, not exception values or personal content. On Windows, a fatal startup error also displays a basic message box pointing to that log file. Packaging logs are visible in the workflow run. This smoke cannot establish actual Windows display success.

## CI smoke scope

The workflow performs an **offscreen** launch of the actual desktop UI with a temporary isolated settings directory and a short event-loop run, verifies `GUI_DESKTOP_SMOKE_OK`, and does not contact API/game/capture. **The actual user's desktop still must be tested manually** for visible GUI, initial settings, window selection/preview, ROI, team import, and persistence.

Production MSI integration, Windows display compatibility, OCR accuracy, realtime capture, API transport timing, and actual ranked battle usability remain pending.
