# Codex implementation plan / acceptance gates

Make each phase a separate logical PR/commit, with tests passing **inside Docker** before next phase. `AGENTS.md` is binding. When BlueStacks test footage is missing, implement interfaces and synthetic fixtures, label unverified parts clearly, and do not halt unrelated offline work.

## Phase 0 — Inventory and reproducible baseline
- Inspect package and reconcile schema path; install minimal dependencies; run `baseline_v2_3/test_champions_contract_v2_3.py` without modifying expected results.
- Initialize actual Python package `src/champions_assist/`, `tests/`, Windows-friendly CLI, `.env.example` without secrets, `.gitignore` and `pyproject.toml`.
- Produce `IMPLEMENTATION_STATUS.md`, `docs/implementation_decisions.md` (clearly mark new assumptions), and tests.
- Success: existing 16 tests pass; new smoke CLI reports mode/version and explicitly states live capture not configured.

## Phase 1 — State/event/SQLite replay engine
- Define observation/event schemas; SQLite append-only events, API snapshots, unique effect_instance_id, turn_end_id, window id; migrations, transactionality, crash restore.
- Deterministic reducer: certainty/provenance/conflicts, corrections and supersedes, weather/screen duration candidates, choice lock, forms, bench, HP, boosts, and unresolved effects.
- Reproduce v2.3 `EvidenceLedger` behavior with SQLite and tests, without assuming any unmapped game effect.
- Success: restart/replay identical projection; revisions never mutate API-sent snapshot; two identical screenshots don't double count events; turn end idempotent.

## Phase 2 — Decision-window FSM and session control
- S00–S08, S05 `NORMAL_COMMAND`, `PIVOT_TARGET`, `FAINT_REPLACEMENT`, `EFFECT_REPLACEMENT`. Shared `PARTY_MENU` for all party choice types. Distinguish game turn vs decision window vs observation.
- 4-file preloader + SHA manifest, frozen match assets, one-batch six LIVE rows, extraction of 18-matchup input; no master rereads during live battles.
- Dry-run simulated opponent preview -> selection -> bounded PREP -> battle choices -> review -> next match; phase boundaries and deadlines.
- Success: 12 acceptance cases in docs v2.0 plus pivot reuse, forced random switch no call, faint+command two calls, no intra-turn extra duration ticks, actual selected Pokémon overrides AI proposed selection.

## Phase 3 — Model adapter (all strategy at OpenAI)
- Implement OpenAI Responses API using current official docs: four structured outputs for SELECT/PREP/BATTLE/REVIEW, protocol 2.3.0, schema validation, action legality, digest, timeout/expiry, abort stale responses. Use mock provider for no-key tests.
- Fixed guideline prefix from LIVE gate and Masters; dynamic immutable battle snapshot; strict no local tactical alternatives. Error UI for NO_DECISION/BLOCKED; no silent guess.
- Note Responses Structured Outputs subset may not accept full Draft2020 local schemas verbatim; **explicitly keep local canonical contract and adapt to API-supported strict schema without weakening local validation**; test both.
- Success: mock end-to-end; API network smoke via explicit opt-in (not by default); no duplicate calls or stale displays.

## Phase 4 — Observer pipeline, OCR and calibration
- Windows BlueStacks capture adapter (choose method empirically), region-of-interest registry based on user sample frames, image UI type detection, OCR event parser and message patterns. No hardcoded 'official' coordinates before calibration.
- Shared `PARTY_MENU`, battle texts, statuses, HP/remaining turns from actual HUD if available. Evidence confidence, buffering and missed-text recovery, screenshots/manual correction in UI.
- Success: offline frame replay deterministic, per-widget metrics. Real Champions UI accuracy must remain UNVERIFIED until the user supplies screenshots/video and tests pass.

## Phase 5 — Usable Windows desktop and operationalization
- Foreground/on-top readable UI or separate control panel: one-line recommended action, three+lead, preloading/PREP readiness, deadlines, current battle state, manual correction and emergency API stop. **No game input injection**.
- Persist match logs, mark likely CPU/human/unknown cautiously, error budgets, API request and token/cost reporting, privacy and retention settings, no secrets in logs, update notifications nonblocking.
- Success: Windows end-to-end dry-run with fake video/mock model; on-device acceptance/accuracy/latency still gated.

## Phase 6 — Real BlueStacks validation (requires user evidence)
- Capture real sequence: team select, move command, party menu normally, U-turn after successful pivot, faint replacement, random phaze, weather/screens/failures/mega, results.
- Measure OCR recall, UI classification, response p95, API spend, deadline misses; adjust. Ensure external assistance conforms to user's game usage rules.
- Success: each scenario passes instrumented capture + recorded expected behavior. Never assert readiness before this evidence.

## Phase 7 — Windows native release + MSI packaging (v2.6)
- Create the Windows-native PySide6 executable (Windows adapters for capture, credentials, packaging), using PyInstaller onedir on a **Windows** runner. No release tools installed in user Windows host by default.
- Author WiX Toolset per-user MSI, keep data under `%LOCALAPPDATA%\PokemonChampionsAssistant` separate from program files, and store runtime API credentials using Windows protection. Version and upgrade identifiers are stable. Default uninstall leaves user DB/config behind.
- Windows GitHub Actions job: Windows tests → PyInstaller onedir → sanity-check executable/resource bundles → WiX MSI → test install, update, uninstall, retained-data path, and artifact hashes. Code sign when a legitimate certificate is configured. Do not publish screenshots/keys or private logs.
- Install and launch MSI on a clean Windows 10/11 x64 VM without Python/WSL/Docker. Test upgrade and version rollback policy; only then mark release eligible.
- `docs/Pokemon_Champions_WSL_Docker_Windows_MSI_Design_v2_6.md` is authoritative on these gates. Building a sample MSI without working desktop/runtime functions is not a completed assistant.

## WSL + Docker-specific acceptance gate (v2.6)
- No Python venv or application packages installed on Windows/WSL host.
- `docker compose build` succeeds on a real WSL2 + Docker Desktop setup (NOT verified in package-generation Linux session).
- `docker compose run --rm dev python scripts/verify_package.py` and unit tests pass.
- Codex+VS Code WSL can edit repository and execute Docker test commands. Dev Containers UI is an optional second workflow.
- Windows BlueStacks connectivity via ADB or Win32 native capture remains a separate real-hardware gate. Production must not require Docker or WSL.

## v2.7 — Source fidelity & historical knowledge acceptance gate
- In Phase 0 verify `assets/source_copies_v2_7.json` and the immutable sha256 copies, and explicitly distinguish source files from reconstructed guidance. Do not claim a full export of ChatGPT project configuration.
- Retain current team 2026-10-03 as live source, retain old 2026-09-29 as offline history. Do not let old team data alter preloader.
- In Phase 1 or later build a read-only schema inspection for `private_history/*.xlsx`. Historical data import is optional and must require explicit opt-in, provenance, and deduplication. It must NEVER be accessed by live-match event loop or new API prompts.
- In Phase 5/review, reference `docs/Pokemon_Champions_CODEX_OX_Postmatch_Method_v2_7.md`; distinguish implementation misses of existing gate vs true proposed improvements. New live heuristics require separate validation.
- Use `knowledge/Pokemon_Champions_environment_data_source_policy.md` for any off-match online meta data; label archive snapshot with its actual date and original regulation only if documented.
