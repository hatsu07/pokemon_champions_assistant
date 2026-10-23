# 実装ステータス（Codexが更新する）

- 引き継ぎパッケージ準備済み: 2026-10-21
- v2.3契約参照テスト: **16/16成功**（引き継ぎ環境のLinux / Python。Windows・実機では未検証）
- Phase 0: 完了（Docker開発環境、Python package、offline CLI、設定雛形、テスト）
- Phase 1: 完了（SQLite追記イベント、証拠再生、訂正、持続候補、API入力不変保存）
- Phase 2: 完了（FSM、4ファイル事前ロード、PREP、模擬判断、シミュレーションCLI）
- Phase 3: 完了（mock-first Responses adapter、v2.3再検証、予算/期限/失効制御）
- Phase 4: 完了（合成フレームでのobserver/OCR/replay校正。Windows/BlueStacks実機は未検証）
- Phase 5: 完了（Docker Qt offscreenでのPySide6デスクトップ、モックGUIデモ。Windows実機は未検証）
- Phase 6: Windowsネイティブの最初の1フレーム取得を実機で確認済み（Champions起動画面、`PrintWindow`）。ROI較正・OCR・対戦UI/FSM・GUIの実機受入は未実施・BLOCKED
- GUI設定 v2.8: 完了（Docker/offscreenの設定保存、構築CSV、モックキャプチャ選択、次戦適用）。WindowsネイティブGUI操作は未検証
- GUI設定 v2.8 Windows実機準備: ポータブル設定検証GUIと操作手順を追加。GitHub Actionsビルド・BlueStacks上のGUIプレビュー・ROI・設定復元は未検証
- BlueStacks録画認識: 未検証
- OpenAI実API通信: 未検証（明示的オプトインCLIのみ。今回の有料通信は0件）
- 実機のターン境界/交代UI: 未検証
- 戦術判断: OpenAI APIのみ。Phase 3は実APIと同じ境界のモックで検証済みであり、実通信の戦術品質・遅延は未検証。

## 実装工程の進捗（Codexが追記）

| Phase | 進捗 | 変更ファイル | テスト | ブロッカー |
|---|---|---|---|---|
| 0 Baseline / scaffold | DONE | `.devcontainer/Dockerfile`, `.dockerignore`, `compose.yaml`, `pyproject.toml`, `.env.example`, `src/champions_assist/`, `tests/`, `docs/implementation_decisions.md` | Docker build; source integrity; baseline 16/16; new 9/9 | Windows/BlueStacks are outside this phase |
| 1 SQLite / replay | DONE | `src/champions_assist/reducer.py`, `src/champions_assist/store.py`, `src/champions_assist/history_inspect.py`, `tests/test_phase_0_1.py` | correction/replay, immutable snapshot, restart, effect and turn dedupe, API-call reservation, read-only XLSX inspection | No Champions UI/effect mechanics are asserted |
| 2 FSM / preloader | DONE | `src/champions_assist/fsm.py`, `preload.py`, `prep.py`, `wire.py`, `mock_provider.py`, `simulate.py`, `tests/test_phase_2.py`; `store.py`, `__main__.py` | inherited 16/16; application 25/25 (new Phase 2: 16/16); simulated CLI smoke | Real UI classification/API transport are intentionally absent |
| 3 OpenAI adapter | DONE | `model_adapter.py`, `contract_validation.py`, `structured_schema.py`, `prompts.py`, `session_payloads.py`, `settings.py`, Phase 2 bridge/storage/CLI/tests | Docker build; baseline 16/16; app 33/33; mock SELECT→PREP→BATTLE→REVIEW CLI | 実API・実機タイミングは未検証 |
| 4 Observer / OCR / calibration | DONE (synthetic only) | `capture.py`, `windows_capture.py`, `adb_capture.py`, `roi.py`, `vision.py`, `ocr.py`, `observation_events.py`, `observer_pipeline.py`, `calibration.py`, `observer_replay.py`, Phase 2/SQLite/CLI/deps/tests | Docker baseline 16/16; app 43/43 (Phase 4: 10/10); synthetic CLI | BlueStacks Windows capture, ROI/templates, Japanese OCR accuracy, actual event wording |
| 5 Desktop UI | DONE (Docker/offscreen only) | `desktop.py`, `desktop_demo.py`, `fsm.py`, `store.py`, CLI/dependencies/tests | baseline 16/16; app 54/54 (Phase 5: 11/11); mock GUI demo | Windows native GUI/capture/OCR/API latency/MSI are unverified |
| 6 Real tests | TOOLING DONE / ON-DEVICE BLOCKED | `capture_runtime.py`, Windows/ADB adapters, labelled replay/ROI/CLI, Windows validation artifact workflow, tests/docs | baseline 16/16; app 67/67 (Phase 6/6R: 13/13); Docker synthetic replay | Windows/BlueStacks footage and both capture-route measurements; terms review; Phase 7 packaging gates |
| GUI settings v2.8 | DONE (Docker/offscreen only); Windows実機検証成果物を準備 | `app_config.py`, `settings_management.py`, `team_management.py`, `capture_settings.py`, `settings_dialog.py`, desktop/preload/FSM/store/CLI/tests/docs, Windows validation workflow | baseline 16/16; app 76/76 (settings: 9/9); portable GUI smoke（Docker） | GitHub Actionsの凍結GUIビルド、Windows GUI picker/preview/ROI/migration、BlueStacks実機結果は未検証 |

- 2026-10-21 v2.5: `.devcontainer`/Compose scaffolding prepared for WSL2 Docker VS Code Codex. Docker build and Windows BlueStacks connectivity **未実行**（作成環境にDocker CLIがない）。

## GUI設定 v2.8 — 2026-10-22

- Added a versioned, non-secret JSON settings store with atomic replacement, last-good backup recovery and Windows `%LOCALAPPDATA%\PokemonChampionsAssistant` defaults. API keys and other secret-like fields are rejected from this store.
- Added a Settings dialog to the existing PySide6 desktop: explicit Windows target enumeration/selection, one-frame read-only preview, normalized game-viewport ROI selection, ADB fields, storage roots, diagnostics and first-launch wizard. A selected HWND/PID/title must still match the current visible window; missing/minimized/recreated windows stop safely without a desktop-capture fallback.
- Added safe storage-root migration (copy, SHA-256/size manifest verification, atomic target replacement, no source deletion) and deferred storage/team application at the next-match boundary. A storage move checkpoints and blocks the old DB-bound model worker until restart; a capture-target change during an active match explicitly emergency-stops in-flight recommendation entitlement. Actual observer-worker resynchronisation is not claimed.
- Added private team registration/edit/export. UTF-8 BOM CSV data, TEAM data, MON six, unknown columns and form/ability raw text are retained. Fixed Champions master validation rejects known-invalid entries and labels AP/nature constraints `NEEDS_REVIEW` when a fixed-master basis is absent; no other-game fallback is used. The selected CSV becomes the fourth newly read/frozen preloader input only for the next session.
- Added [GUI settings instructions](docs/GUI_Settings_v2_8.md). No paid API request, web lookup, master rewrite, automatic game control, or private-media upload was added.


## v2.6 packaging handoff
- Windows-native/MSI design file added, developer instructions amended.
- **Windows executable/MSI: NOT BUILT**. Windows integration, capture, OCR, GitHub Actions and installation tests: NOT VERIFIED.
- Docker development image is validated in the Phase records below; Windows-native build, MSI and installation remain unverified.
- Real Pokémon Champions observations are not verified by this handoff.

## v2.7 packaging update
- Added reconstructed user guidance and CODEX O/X postmatch workflow; **not** a verbatim export from the ChatGPT project settings.
- Added private battle Excel snapshots as byte-for-byte copies, excluded from Git and Docker build context; schemas not yet semantically mapped.
- Added historical team, environment sourcing rules/snapshot, and prior v1.0 design; pre-existing v2.3 API and LIVE gate remain unchanged.
- Added `scripts/verify_v27_assets.py` for source-integrity checks. Only source packaging is validated; no BlueStacks/Windows MSI E2E claim.

## Phase 0/1 implementation record — 2026-10-21

### Changed files

- Development isolation: `.devcontainer/Dockerfile`, `.dockerignore`, `.gitignore`, `compose.yaml`.
- Package/configuration: `pyproject.toml`, `.env.example`, `src/champions_assist/__init__.py`, `src/champions_assist/__main__.py`.
- SQLite/replay core: `src/champions_assist/reducer.py`, `src/champions_assist/store.py`.
- Private-history inspection and tests: `src/champions_assist/history_inspect.py`, `tests/__init__.py`, `tests/test_phase_0_1.py`.
- Implementation record: `docs/implementation_decisions.md`, this status file.

### Implemented

- The Docker image now contains Python 3.12, the pinned `jsonschema` baseline dependency, Tesseract Japanese language data, and ADB. These tools are isolated to the container; no WSL/Windows host package or venv was created.
- The offline CLI (`python -m champions_assist --dry-run`) reports v2.3.0 and explicitly states that live capture, OpenAI API transport, and game control are not implemented in Phase 0/1.
- SQLite stores raw observations and append-only semantic events. Deterministic replay preserves certainty/provenance/conflicts, append-only corrections, unresolved/confirmed effect IDs, duration candidate sets, and turn IDs. Duplicate confirmed effects and duplicate turn ends are rejected transactionally.
- API snapshots are immutable and API-call keys are deduplicated by `(match_id, decision_window_id, tactical_state_version, allowed_action_digest)`. No request is transmitted in this phase.
- Both `private_history` XLSX files were structurally inspected through the read-only CLI. No rows were imported, no semantic column mapping was inferred, and no workbook data was sent externally. The proposed future provenance key is documented in `docs/implementation_decisions.md`.

### Docker verification

| Command | Result |
|---|---|
| `docker compose build` | PASS |
| `docker compose run --rm dev python scripts/verify_v27_assets.py` | PASS: all recorded source hashes, including private workbook copies |
| `docker compose run --rm dev python scripts/verify_package.py` | PASS: required files and 892 effect records |
| `docker compose run --rm dev python -m unittest discover -s baseline_v2_3 -p 'test_*.py' -v` | PASS: 16/16 |
| `docker compose run --rm dev python -m unittest discover -s tests -p 'test_*.py' -v` | PASS: 9/9 |
| `docker compose run --rm dev python -m champions_assist --dry-run` | PASS |

### Remaining uncertainty / next command

- BlueStacks capture, OCR accuracy, actual turn-end evidence, Champions-specific effect values, real OpenAI transport, response-schema validation, and Windows-native GUI/MSI are not implemented or verified. No simulated result is claimed as real-game accuracy.
- Next phase command: `docker compose run --rm dev python -m unittest discover -s tests -p 'test_*.py' -v`, then implement the Phase 3 Responses API adapter with mock-first contract tests.

## Phase 2 implementation record — 2026-10-21

### Changed files

- Session and wire core: `src/champions_assist/fsm.py`, `src/champions_assist/wire.py`, `src/champions_assist/mock_provider.py`.
- Match preload/PREP/simulation: `src/champions_assist/preload.py`, `src/champions_assist/prep.py`, `src/champions_assist/simulate.py`, `src/champions_assist/__main__.py`.
- SQLite audit additions: `src/champions_assist/store.py`.
- Tests and decisions: `tests/test_phase_2.py`, `docs/implementation_decisions.md`, this status file.

### Implemented

- S00–S08 lifecycle control with a required fresh four-master read on every `start_match`; four immutable bodies and their SHA-256 values are retained in memory and append-only SQLite `match_assets` records.
- S05 observation and decision-window states with separate `game_turn_seq`, `decision_seq`, and `observation_seq`. `PARTY_MENU` is one visual type; its `PIVOT_TARGET`, `FAINT_REPLACEMENT`, and `EFFECT_REPLACEMENT` meaning comes only from prior semantic events.
- Exact v2.3 legal-action digest, duplicate-safe request keys, immutable mock request snapshots, 90/40/45-second caps with actual UI time taking precedence, and expiration without a local tactical fallback.
- Cached one-batch six-row matrix lookup, actual-selection-overrides-recommendation behavior, 18-pair PREP input, and strict completion validation. PREP ends incomplete when interrupted or expired.
- Scripted mock provider and `python -m champions_assist simulate --master-dir masters`, explicitly labelled simulated with no capture, network, or game control.

### Docker verification

| Command | Result |
|---|---|
| `docker compose build` | PASS |
| `docker compose run --rm dev python scripts/verify_package.py` | PASS |
| `docker compose run --rm dev python -m unittest discover -s baseline_v2_3 -p 'test_*.py' -v` | PASS: 16/16 |
| `docker compose run --rm dev python -m unittest discover -s tests -p 'test_*.py' -v` | PASS: 25/25 (9 Phase 0/1 + 16 Phase 2) |
| `docker compose run --rm dev python -m champions_assist simulate --master-dir masters` | PASS: labelled simulated trace; 1 scripted mock request |

### Phase 3 handoff / unresolved items

- Replace `ScriptedMockDecisionProvider` with a current OpenAI Responses API adapter behind the same request boundary. Build complete v2.3 SELECT/PREP/BATTLE/REVIEW payloads, locally validate schemas and semantics, enforce spend/deadline controls, and suppress stale responses.
- Preserve the frozen manifests, actual selected three, cached matrix rows, PREP status, legal-action digest, and current state projection in every applicable Phase 3 request. Do not add Python tactical alternatives.
- UI/OCR classifiers, actual legal-candidate extraction, timer capture, and real Champions confirmation remain Phase 4/6 work; no BlueStacks claim is supported by this synthetic test suite.

## Phase 3 implementation record — 2026-10-21

### Changed files

- Model boundary and contract checks: `src/champions_assist/model_adapter.py`, `contract_validation.py`, `structured_schema.py`, `prompts.py`, `settings.py`, `session_payloads.py`.
- Phase 2 integration/audit: `src/champions_assist/fsm.py`, `preload.py`, `store.py`, `simulate.py`, `__main__.py`.
- Runtime configuration/dependencies: `requirements-baseline.txt`, `pyproject.toml`, `.env.example`.
- Tests and records: `tests/test_phase_3.py`, `docs/implementation_decisions.md`, this file.

### Implemented

- Added the OpenAI Responses API transport using the official Python SDK. It sends `text.format` strict JSON Schema, sets `max_retries=0`, passes a bounded timeout, sends no tools, and uses `store=False`. The default transport is an offline scripted mock; external transport additionally requires an explicit environment opt-in, runtime key, positive request caps, a positive spend cap, and a per-request cost estimate.
- Kept `contracts/` as the local v2.3.0 canonical source. A separate Responses-compatible strict Schema is derived for SELECT, PREP, BATTLE, and REVIEW; every returned response is then validated again against the untouched local Schema and semantic gates (identity tuple, current window/state/digest, move/choice lock/mega/switch/pivot/keep-back legality, and PREP18 + WIN3 completion).
- Added frozen-master prompts. They receive a `FrozenMatchAssets` object only, so no in-match master reread occurs. The canonical LIVE gate is the stable system prefix; frozen team, cached six matrix rows in the v2.3 payload, manifest hashes, and at most two targeted PREP lookup excerpts are supplied as data. Prompts prohibit web/tools and cross-game inference.
- Added immutable model request reservation plus append-only response and usage audit records. The unique Phase 2 API key remains the deduplication key. External request/token counts and estimated spend are queryable by match and UTC day; failures/timeouts are also counted conservatively for spend control. API keys are neither accepted by SQLite methods nor persisted/logged.
- Added response freshness/deadline handling: response arrival rereads the supplied current session identity, discards closed/stale windows and post-deadline responses, and produces no local tactical substitute. The session bridge applies only already-displayable responses. The `api-smoke --real-api` CLI can make one SELECT request only when both that flag and all runtime guards are present; without the flag it refuses before creating a client or contacting a network.
- Replaced the synthetic CLI trace with a mock-only SELECT → PREP → NORMAL_COMMAND → REVIEW run. It remains labelled `SIMULATED_ONLY`, reports `network: NOT_USED`, and performs no capture or game controls.

### Docker verification

| Command | Result |
|---|---|
| `docker compose build` | PASS; official `openai` SDK 2.54.0 installed in Docker only |
| `docker compose run --rm dev python scripts/verify_package.py` | PASS: required files + 892 effect records |
| `docker compose run --rm dev python -m unittest discover -s baseline_v2_3 -p 'test_*.py' -v` | PASS: 16/16 |
| `docker compose run --rm dev python -m unittest discover -s tests -p 'test_*.py' -v` | PASS: 33/33 (existing 25 + new Phase 3: 8) |
| `docker compose run --rm dev python -m champions_assist simulate --master-dir masters` | PASS: four mock jobs, `network: NOT_USED` |
| `docker compose run --rm dev python -m champions_assist api-smoke` | PASS: refused without `--real-api`; no external request |

### Remaining uncertainty / Phase 4 handoff

- No paid OpenAI request was made. Actual model compatibility, account limits, latency, token cost, refusal behavior, and real deadline headroom remain unverified; run `api-smoke --real-api` only after explicitly setting the documented runtime environment and accepting its cost.
- The current state/identity callback permits the observer pipeline to continue while a request is outstanding, but real concurrent capture/OCR, UI timers, and cancellation wiring require Phase 4 evidence and implementation.
- BlueStacks capture/OCR, true legal-candidate extraction, Windows GUI presentation, and on-device Champions mechanics remain unimplemented/unverified. Synthetic PREP and mock responses are not game accuracy claims.

## Phase 4 implementation record — 2026-10-21

### Changed files

- Capture and vision boundary: `src/champions_assist/capture.py`, `windows_capture.py`, `adb_capture.py`, `roi.py`, `vision.py`, `ocr.py`.
- Evidence normalization and replay bridge: `effect_catalog.py`, `observation_events.py`, `observer_pipeline.py`, `calibration.py`, `observer_replay.py`.
- Phase integration/runtime: `fsm.py`, `store.py`, `__main__.py`, `requirements-baseline.txt`, `pyproject.toml`.
- Tests and records: `tests/test_phase_4.py`, `docs/implementation_decisions.md`, this file.

### Implemented

- Added a read-only `FrameSource` boundary for still images, videos, synthetic fixtures, explicit Windows-native capture, and explicit ADB screenshots. `WINDOWS_NATIVE` is the default selected live backend, with ADB as an opt-in fallback; neither adapter contains tap/click/key input. Both live paths remain `UNVERIFIED_ON_BLUESTACKS`.
- Added versioned normalized ROI load/save, OpenCV ROI frame difference and template classification for all v1.4 visual categories. No Champions ROI/template/coordinate is bundled. `PARTY_MENU` remains one visual label; Phase 2 event history determines normal navigation, pivot, faint replacement, and effect replacement.
- Added pluggable Japanese OCR (`TesseractJapaneseOcr`) and deterministic mapping OCR for calibration tests. OCR only runs for changed configured ROIs. Parsed raw evidence records names/moves/status tokens and optional configured HP/remaining-turn patterns; phrase-to-event rules are calibration input, not asserted Champions text.
- Added structured event kinds for moves/effect outcomes, stat/status, weather/terrain/screens/hazards, Mega/form/type, HP/faint/switch, turn end and match finish. A seen activation is stored as pending; it becomes `CONFIRMED_EFFECT` only when an explicit configured confirmation event with the same effect ID is observed. Effect catalog records are frozen in memory when supplied and annotate provenance only; unverified mechanics are never calculated locally.
- Added event episode deduplication, retained-frame OCR reanalysis, and append-only manual corrections. Raw observations, observer semantic notes, existing effect/duration events, and evidence reuse the existing SQLite/reducer path. Confirmed turn ends still decrement duration exactly once.
- Added observer watermark accounting and state/FSM integration. A material observation or complete legal-candidate change updates `tactical_state_version`, so Phase 3’s current-identity check suppresses an outstanding stale model result. The observer does not import or invoke a model adapter and continues independently of API work.
- Added `champions-assist observer-replay` for image lists, video, or labelled synthetic fixtures, ROI settings, optional template/pattern calibration inputs, labels comparison, missed/false-positive counts, and processing-time metrics. It is offline-only, uses no API, and reports `real_game_validated: false`.
- Docker runtime record: Tesseract 5.5.0, Apache-2.0 engine, distro `jpn.traineddata` 2,471,260 bytes, not vendored. A Windows MSI must separately verify binary/data size and package notices/licenses.

### Docker verification

| Command | Result |
|---|---|
| `docker compose build` | PASS; OpenCV headless, Pillow and pytesseract added in Docker only |
| `docker compose run --rm dev python -m unittest tests.test_phase_4 -v` | PASS: 10/10 synthetic Phase 4 tests |
| `docker compose run --rm dev python scripts/verify_package.py` | PASS: required files + 892 effect records |
| `docker compose run --rm dev python -m unittest discover -s baseline_v2_3 -p 'test_*.py' -v` | PASS: 16/16 |
| `docker compose run --rm dev python -m unittest discover -s tests -p 'test_*.py' -v` | PASS: 43/43 (existing 33 + Phase 4: 10) |
| `docker compose run --rm dev python -m champions_assist simulate --master-dir masters` | PASS: mock SELECT → PREP → BATTLE → REVIEW, `network: NOT_USED` |

### Remaining uncertainty / Phase 5 handoff

- No BlueStacks frame, video, UI template, ROI coordinate, Japanese battle phrase, timer readout, ADB connectivity, or Windows native capture has been supplied or verified. Synthetic template/OCR results prove only offline plumbing, not game recognition accuracy.
- Actual legal-candidate OCR profiles require real calibrated screen material. Until then the extractor returns no candidates (or `UNKNOWN`) and cannot open an actionable window from real frames.
- The Windows adapter captures whole screen or an explicitly supplied rectangle only; BlueStacks window discovery and its actual bounds are deliberately not guessed. ADB remains a local, explicit screenshot fallback and was not connected.
- No paid OpenAI call, web lookup, master reread during a match, game control, or local tactical fallback was added. Windows GUI presentation and real concurrent capture runtime belong to Phase 5/6 validation.

## Phase 5 implementation record — 2026-10-21

### Changed files

- Desktop presentation/runtime: `src/champions_assist/desktop.py`, `desktop_demo.py`, `__main__.py`.
- Local history and accounting views: `src/champions_assist/store.py`.
- Docker/runtime dependencies: `.devcontainer/Dockerfile`, `requirements-baseline.txt`, `pyproject.toml`.
- Tests and records: `tests/test_phase_5.py`, `docs/implementation_decisions.md`, this file.

### Implemented

- Added a PySide6 Essentials desktop shell that shows the current phase, decision-window identity/status, one-line displayable recommendation, selection/lead, frozen four-file preload status, PREP completion status, deadline, state projection, certainty/conflict status, counterparty classification as non-definitive, and API usage. The display never converts an unavailable, stale, timeout, or rejected response into a recommendation.
- Added normal/compact mode, always-on-top control, geometry persistence through non-secret `QSettings`, next-match start, and an explicit S02 start path that delegates to the existing `MatchController`; that path reloads and hashes the four masters for each new match.
- Added generic JSON manual corrections for active/remaining Pokémon, HP/status, stat stages, revealed moves/abilities/items, Mega/forms, field effects, locks, unknowns, and cautious control classification. Actual three/lead can also be corrected after selection: the correction appends evidence, rebuilds only the frozen PREP input, marks PREP incomplete until rerun, invalidates any current recommendation, and removes a cached review; it does not invent a new selection. No correction overwrites history or API snapshots.
- Added explicit API start/stop and emergency-stop controls. A `DesktopModelJob` is an immutable payload/identity handoff, and `DesktopModelDispatcher` runs a caller-supplied adapter executor in a Qt worker thread with an independent SQLite connection. The GUI rechecks the current identity and its API epoch before applying a returned result. Rendering only reads a presentation model and cannot send an API request. The ordinary `desktop` CLI uses an empty mock transport and starts API-disabled; `--real-api` merely selects the already guarded Responses transport and still requires all Phase 3 runtime opt-ins.
- Added local match/event/API-response browsing, explicit JSON export without master bodies or credentials, append-only hide, and permanent deletion only after the exact `match_id` is re-entered. The active match cannot be purged. This is the sole intentional exception to normal append-only retention and is a user-directed privacy/storage operation.
- Added `champions-assist desktop-demo --master-dir masters --offscreen`: a labelled no-network fixture runs SELECT → PREP → battle → REVIEW, enumerates one explicitly blank synthetic frame, and renders the final desktop state. It reports `SIMULATED_ONLY`; it does not claim OCR/capture/game accuracy.
- Docker now includes PySide6 Essentials and the minimal Qt shared libraries needed for offscreen widget tests. No Windows Python/Qt package, executable, installer, credential-store integration, or MSI was created.

### Docker verification

| Command | Result |
|---|---|
| `docker build --quiet -f .devcontainer/Dockerfile -t pokemon_champions_assistant-dev .` | PASS: Python Docker development image with PySide6 Essentials / Qt runtime libraries |
| `docker compose run --rm dev python scripts/verify_package.py` | PASS: required files + 892 effect records |
| `docker compose run --rm dev python -m unittest discover -s baseline_v2_3 -p 'test_*.py' -v` | PASS: 16/16 |
| `docker compose run --rm -e QT_QPA_PLATFORM=offscreen dev python -m unittest discover -s tests -p 'test_*.py' -v` | PASS: 54/54 (existing 43 + Phase 5: 11) |
| `docker compose run --rm -e QT_QPA_PLATFORM=offscreen dev python -m champions_assist desktop-demo --master-dir masters --offscreen` | PASS: simulated SELECT → PREP → battle → review, 4 mock jobs, no network/control |

### GUI demo / Phase 6 handoff

- Docker demo: `docker compose run --rm -e QT_QPA_PLATFORM=offscreen dev python -m champions_assist desktop-demo --master-dir masters --offscreen`.
- Windows shell after native dependency packaging: `champions-assist desktop --master-dir masters --db <user-data-db>`. It is an observer-only shell; live capture must be attached separately and API remains stopped by default.
- Required real-machine evidence: BlueStacks screenshots/video for every UI category and decision-window transition; calibrated window bounds/ROI/templates; Japanese message/timer samples and ground-truth events; legal-option OCR samples; actual Windows-native/secure local ADB capture results; side-by-side window/layout behavior; and, only with explicit cost approval, real API latency/budget behavior.
- Still unverified: all BlueStacks screen recognition/OCR accuracy, Windows capture, Windows GUI rendering, Windows credential storage, PyInstaller/WiX/MSI production packaging, actual Responses API calls, and game terms/compliance. No game input injection was added.

## Phase 6 implementation record — 2026-10-22

### Changed files

- Read-only capture/runtime: `src/champions_assist/windows_capture.py`, `adb_capture.py`, `capture.py`, new `capture_runtime.py`.
- Labelled validation/replay/UI bridge: `observer_pipeline.py`, `calibration.py`, `observer_replay.py`, new `validation.py`, `desktop.py`, `__main__.py`.
- Windows-validation delivery and instructions: `scripts/windows_validation_entry.py`, `.github/workflows/windows-validation-artifact.yml`, `docs/Phase_6_BlueStacks_Validation.md`, `docs/Phase_6_Performance_Report.md`.
- Tests and records: `tests/test_phase_6.py`, `docs/implementation_decisions.md`, this status file.

### Implemented

- Added caller-selected Windows client-window capture with per-frame Win32 geometry/DPI remeasurement, explicit `WINDOW_NOT_FOUND`/`WINDOW_MINIMIZED` failure states, monotonic timestamps and no window/game control. The existing explicit rectangle and desktop modes remain available but cannot qualify as target-window evidence.
- Added ADB availability probing plus screenshot capture using only read commands. ADB has no game-control, shell input, port-forward or automatic network configuration path. Windows native and ADB are reported separately and a comparison never chooses a backend automatically.
- Added a framework-independent capture worker that retries after source loss and captures FPS, interval, capture-lag, estimated drop and recovery statistics. A Qt bridge only refreshes the desktop from an observer-worker notification; it neither runs OCR on the UI thread nor schedules OpenAI work. `LatencyLedger` supports independently measured capture-to-observer and capture-to-GUI p50/p95/max reporting.
- Added local ROI/template crop calibration, labelled replay dataset evaluation, source/evidence distinction, UI/event/OCR counts, and the twelve Phase 6 scenario gate. A synthetic, hash-mismatched or incomplete dataset is blocked; a complete attested real dataset is only `READY_FOR_HUMAN_REVIEW`, never automatically approved.
- Added a manually triggered GitHub Actions Windows PyInstaller `onedir` **validation-harness** artifact. The runner executes Phase 6 tests, bundles the dynamic Windows `PIL.ImageGrab` dependency, smoke-tests `--version` plus a deliberately absent target-window guard, and publishes a portable ZIP layout. It is explicitly not a Phase 7 MSI/release artefact and does not include private captures or ADB by default.
- Added a zero-valued performance-report template that lists actual measurements as unmeasured until the tool runs against an on-device Windows/BlueStacks source.
- Phase 6R records the operator-reported target environment: BlueStacks 5.22.285.1013, Android 13 beta, landscape 1920 × 1080 and Android density 240 dpi. The probe now records actual captured-image dimensions, Win32 client pixels, their pixel ratio and Win32 DPI/96 scale separately, and can save one non-overwriting local frame for manual ROI calibration. This configuration record is not a capture/OCR acceptance result.
- The first-frame native probe requires a target window title (or a separately supplied explicit rectangle), blocks untargeted desktop capture, and records full-probe plus first-acquisition timing. ADB fallback accepts only an explicit trusted local executable path and serial; it remains read-only and unbundled.
- The initial Windows native probe returned one frame at 1127 × 649 with Win32 DPI 192, but the saved PNG showed VS Code/PowerShell rather than Champions. It remains a failed target-content capture. The title-selected route was changed to read-only Win32 `PrintWindow` off-screen client rendering, so overlapping desktop windows cannot be silently saved. A subsequent operator-provided local report recorded `WIN32_PRINTWINDOW_CLIENT`, a 2254 × 1298 image/client area, Win32 DPI 192 (200%), per-monitor-v2 awareness, and 47 ms first-frame acquisition; its PNG visibly contained the Pokémon Champions title screen without the prior overlay. This accepts only the Phase 6R first-frame capture checkpoint. It does not establish ROI, OCR, any battle UI/event, capture stability, GUI handoff, or game readiness.

### Docker verification

| Command | Result |
|---|---|
| `docker compose build` | PASS |
| `docker compose run --rm dev python scripts/verify_package.py` | PASS: required files + 892 effect records (before Phase 6 edits; repeated in final verification) |
| `docker compose run --rm dev python -m unittest discover -s baseline_v2_3 -p 'test_*.py' -v` | PASS: 16/16 (before Phase 6 edits; repeated in final verification) |
| `docker compose run --rm -e QT_QPA_PLATFORM=offscreen dev python -m unittest tests.test_phase_6 -v` | PASS: 13/13 Phase 6/6R synthetic/unit tests |
| `docker compose run --rm -e QT_QPA_PLATFORM=offscreen dev python -m unittest discover -s tests -p 'test_*.py' -v` | PASS: 67/67 after the `PrintWindow` overlap-safe capture change |
| `docker compose run --rm dev python -m champions_assist capture-probe --backend WINDOWS_NATIVE ...` | PASS (fail-safe): Docker/WSL returned `CAPTURE_UNAVAILABLE` / `WINDOWS_RUNTIME_REQUIRED`, 0 image/DPI samples; it did not claim a Windows capture |
| `docker compose run --rm dev python -m champions_assist observer-replay ... --validation-dataset ...` | PASS via Phase 6 synthetic CLI test; SQLite/FSM path, no network/control |

### On-device acceptance / Phase 7 handoff

- **First-frame checkpoint accepted on device:** the operator supplied and visually confirmed a local native `PrintWindow` PNG containing the Champions title screen, with its report values (2254 × 1298 client/image pixels, Win32 DPI 192, 47 ms first acquisition). This workspace still has no direct Windows/BlueStacks control. OCR accuracy, UI coordinates, battle event phrases, timers, capture stability, GUI handoff and gameplay readiness remain unverified and blocked.
- Required before Phase 7 human review: run and retain both read-only capture reports; label local footage for every listed scenario; replay it through the existing SQLite/FSM path; compare false/missed OCR/events and GUI latency; verify random forced switch, pivot, faint replacement, actual correction and stale response suppression; decide a backend manually; confirm game/platform terms.
- Still separate Phase 7 work: run the Windows artifact on a real PC, validate PyInstaller DLL/Qt/OCR inclusion, then build/test the per-user WiX MSI including install/upgrade/uninstall/data retention and Windows credential protection. No paid OpenAI request was made, and no automatic gameplay operation was added.

## Local screen-classifier training Docker support — 2026-10-23

### Changed files

- Optional trainer image and dependencies: `.devcontainer/Dockerfile`, `requirements-training.txt`, `compose.yaml`.
- Dataset preflight and safe WSL entrypoint: `scripts/train_screen_classifier.py`, `scripts/train_screen_classifier_docker.sh`.
- Regression and synthetic-pipeline coverage: `tests/test_screen_classifier.py`, `tests/training_pipeline_integration.py`, `.github/workflows/screen-classifier-training.yml`.
- GUI lifecycle cleanup discovered by full-suite termination: `src/champions_assist/desktop.py`, `tests/test_screen_dataset_collector.py`, `tests/test_vision_preferences.py`.
- Operator instructions: `docs/screen_dataset_collection.md`, `README.md`.

### Implemented

- Rebased the training branch onto `c762b34` (PR #22). The only conflict was in `docs/screen_dataset_collection.md`; its 5fps default, 2/5/10fps selection, disk-backed `_UNLABELLED` JPEGs, quota handling, 100-image review pages, and post-battle human labelling flow were retained. Existing labelled and unlabelled data layouts were not changed.
- Added a dedicated Compose `trainer` profile whose Dockerfile stage contains optional CPU PyTorch, TorchVision, ONNX, and ONNX Runtime only. The ordinary `dev` target remains free of these dependencies.
- The trainer mounts the code and labelled source data read-only. Only ignored `private-training/output` and `private-training/.torch-cache` mounts are writable. The WSL wrapper resolves `%LOCALAPPDATA%`, rejects a data directory inside the workspace, handles whitespace/Japanese paths, and fixes the output to `private-training/output`.
- Added local preflight for exactly seven labels: image and independent-session counts, image readability, `_UNLABELLED` exclusion, a deterministic session-disjoint split, and clear insufficient-label/session diagnostics. A label must occur in at least two independent sessions, and every label must occur in both train and validation; evaluation frames are never reused for training.
- CPU is the only implemented training path. `auto` selects CPU even if a CUDA runtime happens to exist. No-pretrained training is the default; only explicit `--pretrained` can obtain ImageNet weights. The trainer never uploads captures, labels, results, or model files.
- Added a separate synthetic CPU workflow, triggered on Ready/synchronize for trainer changes. It explicitly checks out and compares the PR head SHA, then makes seven-class, four-session synthetic data, trains one epoch without pretrained weights, verifies the ONNX SHA-256 and unvalidated manifest, and runs ONNX Runtime inference with a `(1, 7)` output. It does not use private captures or publish artifacts.
- Full-suite investigation also fixed two independent cleanup faults: a closed legacy dashboard disconnects before its SQLite store can close, and GUI tests keep the shared `QApplication` alive while explicitly disposing benchmark dialogs. These changes remove the prior SQLite traceback, Qt warning, and exit-139 termination.

### Verification

- `bash -n scripts/train_screen_classifier_docker.sh`: PASS.
- `docker compose config --quiet` and `docker compose --profile training config --quiet`: PASS.
- `docker compose build`: PASS; ordinary development image remains separate from the trainer.
- `docker compose --profile training build trainer`: PASS; CPU-only `torch 2.5.1+cpu`, `torchvision 0.20.1+cpu`, `onnx 1.17.0`, and `onnxruntime 1.20.1` installed in the dedicated image.
- An external temporary path containing whitespace and Japanese characters: PASS. `/workspace` and `/training/screens` were non-writable; only output/cache mounts were writable. The wrapper's `--help` completed with that mount.
- `docker compose --profile training run ... python -m unittest tests.training_pipeline_integration -v`: PASS, 1/1. CPU one-epoch synthetic training exported a loadable 7-class ONNX model; the resulting manifest was intentionally `validated=false`.
- `docker compose run --rm dev python scripts/verify_package.py`: PASS; required files + 892 effect records.
- `docker compose run --rm dev python -m unittest discover -s baseline_v2_3 -p 'test_*.py' -v`: PASS; 16/16.
- The existing Windows-workflow test selection was run in Docker/offscreen: PASS, exit 0, 113/113.
- `docker compose run --rm -e QT_QPA_PLATFORM=offscreen dev python -m unittest discover -s tests -p 'test_*.py' -v`: PASS, exit 0, 175/175; no SQLite closed-database traceback or Qt cleanup warning.

### Remaining uncertainty

- No private Champions capture was read, trained, uploaded, committed, or included in CI. Therefore preflight and CPU training are exercised only with synthetic data; actual-data trainability, label distribution, ImageNet-weight use, model quality, latency, and BlueStacks recognition accuracy remain unverified.
- The dedicated trainer intentionally uses CPU-only PyTorch wheels. CUDA/NVIDIA Docker Desktop support is not implemented or verified.

## Offline auto-label proposal & GPU retrain handoff — PR #25 (2026-10-23)

- New `src/champions_assist/screen_auto_label.py`: per-session timestamp/dHash grouping, 30-frame cap, experimental ONNX raw predictions, abstention on low confidence/margin; model load remains checksum-checked.
- Dataset collector adds background analysis, progress, candidate group list and first/center/last visual previews, explicit multi-group human approval or correction to labelled PNG. Old manual review and data layout kept.
- Main runtime's validated-only model gate remains intact. Experimental `validated=false` model allowed only via explicit offline collector class option. No screen data or model uploaded.
- GUI supports explicit WSL/Docker GPU retraining from the Windows collector with progress logs and copyable command fallback. Each training run can archive its outputs with --archive-output; promotion to production is never automatic. User must retrain, compare independent heldout results and select the updated model.
- Tests cover session boundaries, grouping size, abstention, synthetic low-confidence experimental ONNX, negative human confirmation, and label batch save. Windows build collects onnxruntime for the standalone dataset collector.
- Real Pokémon Champions accuracy, Windows file copy path, grouping review time and GPU retraining quality on additional sessions remain unverified until human field tests.

## Full-frame group review improvements — 2026-10-23 (PR)

- Every group of at most 30 frames now shows all thumbnails with individual raw predicted labels, confidence, and warnings for low-confidence, UNKNOWN, or notable image differences; click to inspect full-size.
- Per-frame label overrides and non-destructive exclusion can be selected in contiguous or non-contiguous ranges. Other frames retain base labels; excluded JPEGs remain unmodified.
- Explicit group-wide confirmation required before promotion; changes invalidate the review checkbox. Original first/middle/last summary still available.
- Existing labelled PNGs remain immutable and production model validation gate unchanged.
- Tests cover frame-level corrections, excluded image preservation, review invalidation, unreviewed refusal, and per-frame AI metadata; synthetic/Windows CI must validate final PR HEAD. Real-image review usability is still unverified.

## Preserve auto-label cache across PNG promotion — 2026-10-23

- Root cause: `_reload_review_files()` previously called `_clear_auto_groups()`, discarding all model proposals, corrected frames and review acknowledgements after every PNG save.
- Separate session lifecycle reset from unlabelled file-list reload. Reconcile in-memory groups by retained staged JPEG paths; prune successfully saved/deleted frames, recompute representative thumbnails and per-group minima without re-running the model.
- Keep unaffected groups' AI predictions, individual corrections, exclusions and full-review acknowledgements. Partially modified groups must be reviewed again. Manual save and deletion follow the same reconciliation path; a new session or folder and explicit re-analysis still invalidates stale predictions.
- New Windows GUI regression cases: two independently reviewed groups with corrected frames, repeated save, refresh, manual conversion, and simulated partial-write failure. Local real-image workflow remains to be validated.
- Windows CI on first PR head showed all 121 offline core unittest cases passing, but the Python process returned an unexpected nonzero exit during interpreter/Qt finalization; reproduced on retry (runs 39928018261 attempts 1 and 2). The existing exit-safe `scripts/windows_qt_test_runner.py` was therefore extended to accept a list of suite modules while retaining separate processes for core, inline GUI and dataset collector. It calls `os._exit()` **only after** capturing the actual unittest success/failure result, preserving real test failures while avoiding the nondeterministic post-test finalizer error. Windows desktop/collector real executable startup smoke remains unchanged.

- Further diagnostic (run 39929289815, SHA 5865b5e): `UNITTEST_RUNNER_RESULT success=True tests=121 errors=0 failures=0`, but Python process exit `-1073741819` (Windows access violation `0xC0000005`) despite `os._exit(0)`. CI-only Windows runner now exits with `TerminateProcess(GetCurrentProcess(), 0|1)` **after flushing actual unittest results** to avoid Qt/native DLL detach faults; non-Windows runner uses `os._exit(0|1)`. A genuinely failed test still yields nonzero exit. All platform GUI binary smoke checks remain unchanged.

## Inline group correction label — 2026-10-23

- User feedback: the existing group thumbnails were far above the manual JPEG save label combobox, forcing repeated long scrolling between a selected frame and the label field.
- Added a dedicated `auto_label_box` in the **immediate layout row below the grouped thumbnail grid**. Both per-frame correction and group-level correction use this nearby selector; the original `label_box` stays in the manual file-save section.
- The two dropdowns are independent to prevent accidentally applying a manual-save choice to an automatically grouped frame.
- Added offscreen Windows regression tests for layout adjacency, independent selection, missing group label refusing a partial correction, and existing per-frame/batch functionality. Actual user's screen size / DPI usability still requires a native Windows check.
