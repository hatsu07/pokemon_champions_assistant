# Codex repository guidance — Pokémon Champions local assistance


## Mandatory AI development / CI policy (2026-10-23)
- **Read and follow `docs/AI_DEVELOPMENT_AND_CI_POLICY.md` and `docs/CI_MERGE_GUARD_AND_NEXT_CHAT.md` before changing code or reporting a result.** This requirement applies to Codex, ChatGPT, other development agents and human contributors.
- Use a Draft PR while implementing, testing, documenting and committing. **Freeze the final commit before triggering/accepting release CI.**
- When CI fails, add commits, or HEAD moves: ignore the old run for completion; ensure a new CI run validates the **latest PR HEAD SHA** from the start.
- A successful result is relevant only if the `workflow_run.head_sha`, actual checkout SHA, and current PR head are identical and all required jobs succeeded. Keep run ID + SHA in the completion report.
- A CI run on an old SHA is never proof that newer code works. Do not merge, publish an artifact as verified, or tell the user the work is complete without checking the final revision. Never claim game-device/real API correctness from offline tests.
- The Windows workflow reruns on Ready PR commits, cancels superseded jobs, verifies the exact PR SHA, and tests Windows builds without uploading ZIPs. Explicit manual artifact publishing is separate, can fail when the Actions quota is exhausted, and is never silently reported as complete.
- **HARD MERGE STOP:** Never merge if the *entire* latest-HEAD PR CI is anything other than `completed/success`, even if all code tests passed, an upload failure seems unrelated, or GitHub reports `mergeable: true`. Additionally, never merge while GitHub's server-side `master` protection / required Windows check and no-admin-bypass rules are absent or cannot be verified. See `docs/CI_MERGE_GUARD_AND_NEXT_CHAT.md`. This applies to this and **every later chat/agent**.
- **Confirmed blocker as of 2026-10-23:** `master.protected=false`; private-repo Rulesets returned a plan restriction, and the installed GitHub connector cannot write protection. A Markdown policy does NOT physically disable GitHub's Merge button. Document the blocker and stop; do not make a discretionary exception.



## Development host isolation (v2.6)
- **Source code is stored in WSL2 Linux**, not `/mnt/c` or a Windows filesystem. VS Code WSL + Codex IDE extension is the default editing/agent workflow.
- **All Python/ADB/Tesseract/development tools run in Docker.** The canonical commands are `docker compose run --rm dev ...` or terminal in Dev Container. Never direct Codex to install Python/Node/ADB/OCR tools on Windows or WSL host or create a host `.venv`.
- Use `.devcontainer/`, `compose.yaml` and `WSL_DOCKER_VSCODE_CODEX_SETUP.md` for environment and setup. Host retains Windows VS Code, Docker Desktop, WSL2, BlueStacks, and a limited VS Code/Codex extension installation.
- BlueStacks runs in **Windows**, not Docker. ADB network connectivity must be tested; do not bind an unauthenticated ADB port to the LAN to make it work. Never assume Win32 screen capture works inside Linux container.
- Do not mount Docker socket, SSH credentials, ChatGPT/Codex auth or Windows home to dev container without permission. Codex extension inside Dev Container is optional, can require reauthentication / adjusted sandbox, and must not be presumed working.
- Preserve app isolation across all BUILD_PLAN phases. A phase is not complete unless Docker-based unit tests pass. Real Windows/BlueStacks coverage remains separate.

## Windows-native runtime and MSI contract (v2.6)
- The authoritative **deployment and packaging** design is `docs/Pokemon_Champions_WSL_Docker_Windows_MSI_Design_v2_6.md`. It does NOT supersede API wire v2.3 or Champions strategy sources.
- Develop inside WSL2 + Docker, but the production desktop application runs **natively on Windows, without Docker/WSL**.
- Primary desktop implementation is PySide6. Keep OS-independent core separate from Win32 capture/credential adapters. No claims of working Windows capture until tested on BlueStacks.
- Build Windows executable with PyInstaller **onedir on Windows** (initially GitHub Actions Windows runners). Build per-user `.msi` with WiX Toolset. Do not try PyInstaller cross-compilation on Linux and do not install Python toolchain on user's Windows host for release.
- Keep program files separate from `%LOCALAPPDATA%\PokemonChampionsAssistant` user DB/config/log/cache. In-place upgrade and uninstall must retain user data by default; deleting user data needs explicit confirmation. Use OS credential protection for the runtime API key; never embed it in MSI, CI logs or source.
- Windows build/release gates: Windows CLI/GUI smoke, runtime DLL/OCR bundling, MSI install/upgrade/uninstall, no privilege escalation by default, security/licensing review, and separate actual BlueStacks E2E validation.
- WiX version/licensing (including revenue-related maintenance requirements) and code signing must be checked at actual release time. A packaging spec is not a completed `.msi`.

## Project-specific historical sources & offline improvement (v2.7)
- Before implementation, read `docs/V2_7_SOURCE_COMPLETENESS_AND_AUTHORITY.md`, `docs/Pokemon_Champions_Project_User_Guidance_Reconstructed_v2_7.md`, and `knowledge/README.md`. The user-defined project rules document is a **faithful reconstruction, not a byte-for-byte export of private project settings**. The canonical strategy source remains `masters/Pokemon_Champions_MC_live_decision_gate.txt`.
- The complete supplementary `masters/PROJECT_INSTRUCTION_live_matrix_addition.txt` is included. The previous team (`2026-09-29.csv`) is archived and MUST NOT replace the current team (`2026-10-03.csv`) unless the user explicitly changes the current lineup and associated sources are synchronized.
- `private_history/*.xlsx` contains copies of the user-provided battle workbooks (binary unchanged, extension added). This path is deliberately Git-ignored and excluded from Docker build context. Never publish the package ZIP or workbooks as public assets, never commit or upload private user match data to CI by default, and never put raw historical workbooks into live prompts. Use only for explicitly permitted offline import/analysis after inspecting their actual schema.
- `knowledge/Pokemon_Champions_environment_snapshot_2026-10-21_archive.txt` is a **historical snapshot** (archive date only) and never auto-equivalent to fresh usage stats. For off-match current meta research follow `knowledge/Pokemon_Champions_environment_data_source_policy.md` and preserve retrieval date/regulation/provenance. No web research in live matches.
- At postmatch, use `docs/Pokemon_Champions_CODEX_OX_Postmatch_Method_v2_7.md`: O (option superiority) and X (execution stability) are offline review aids only. First distinguish pre-existing LIVE gate execution errors from genuinely missing rules; candidates need evidence and tests before any gate revision. Do not add O/X computation to time-limited live decision flow.
- `knowledge/Pokemon_Champions_Realtime_AI_System_Design_v1_0.md` and schema v0.1 are archived historical sources, superseded where conflicting with versioned v2.x contracts.

## Objective and scope
Build a **Windows local desktop, observer-only** assistant for Pokémon Champions singles ranked battles running in BlueStacks 5. The user manually plays. **All strategic decisions must come from the OpenAI API**, not Python heuristics: team selection (three + lead), mandatory PREP, every new move/party choice, and post-match analysis. Python owns screen capture, local OCR, Champions-specific evidence parsing, effects, deterministic state management, legal action parsing/validation, SQLite logging, notification UI, and API scheduling.

This repository contains a design/handoff package, not a completed working system. Do NOT claim BlueStacks/Champions detection works without sample footage and tests. Game rule details not in Champions masters are **unknown**; never import Sword/Shield/SV/ZA mechanics by assumption. Do not pretend `UNMAPPED` effects are understood.

## Source of truth (read in this order)
1. `docs/Pokemon_Champions_Contract_And_State_Reconciliation_v2_3.md` and `contracts/` (wire protocol `2.3.0`).
2. `docs/Pokemon_Champions_Integrated_Architecture_v2_0.md` (overall S00–S08 flow and principles).
3. `docs/Pokemon_Champions_P0_Implementation_Spec_v2_1.md` (invariants, events, state/SQLite).
4. `docs/Pokemon_Champions_S05_Battle_FSM_Design_v1_2.md` (input-window FSM).
5. `masters/Pokemon_Champions_MC_live_decision_gate.txt` (ONE canonical strategy gate; NEVER overwrite/reinterpret).
6. Other `masters/` and supporting older `docs/` only as needed. Older wire examples are historical and superseded by v2.3.
7. `docs/Pokemon_Champions_WSL_Docker_Windows_MSI_Design_v2_6.md` governs build/runtime separation and MSI release only.
8. `baseline_v2_3/` is a tested reference contract implementation, not a working game observer; improve without discarding tests.

## Non-negotiable requirements
- On EACH 'start match/next match' request, actually read and hash **all four** files and retain immutable contents for the match: `Pokemon_Champions_MC_live_decision_gate.txt`, `Pokemon_Champions_MC_live_selection_matrix.txt`, `Pokemon_Champions_MC_60sec_lookup_master.txt`, `Pokemon_Champions_MC_team_2026-10-03.csv` (until team config changes). Only declare 事前ロード完了 when all four really succeeded. During live battle, NO filesystem re-reads of masters and NO web searches.
- On receiving six opponents, retrieve six corresponding LIVE matrix rows in one batch; return 3 Pokémon + lead immediately, then run bounded PREP: 18 matchups (lead six + bench twelve) + preserve/stopper/closer; 40-second hard end. 90 seconds for selection, 45 seconds per turn; actual remaining UI deadline takes precedence. No forced low-confidence 'complete' label. Avoid a fresh per-opponent lookup sweep.
- Detect UI *type* from image; track input **reason/context** in FSM. Every party selection uses `PARTY_MENU` detector. Distinguish normal menu navigation, actual pivot choice, faint replacement, effect replacement from event history. Random phazing without a player choice must NOT call OpenAI.
- Move, pivot, faint replacement, and subsequent move command can be separate decision windows. Only one API request per valid (match_id, decision_window_id, tactical_state_version, allowed_action_digest). Reuse a previously API-chosen pivot target only after checking its assumptions remain valid. New meaningful decisions always OpenAI.
- Evidence is append-only; raw observations, semantic events, current deterministic projection, immutable sent snapshots. States `READY_CONFIRMED`, `READY_WITH_UNCERTAINTY`, `WAIT_FOR_SYNC`, `BLOCKED`. Unknown legal options => BLOCKED, no local strategically chosen fallback. Response JSON must be schema- and semantically validated and stale results suppressed.
- Durations decrement ONLY once on a confirmed game turn end, never by UI changes, animations, pivots, or faint selection. Unknown item durations remain a candidate set; read screen when available. Ability/move/item effects require confirmation, prevent duplicate updates.
- No automatic game controls (no tap/click/key injection or automation to play). Display recommendations; user executes. Do not imply this is compliant with all game terms without verification.
- No secrets in source or commits. Use `.env` (ignored) or Windows environment variables; no API key in logs, screenshot exports, Codex prompts, tests, or README. Have configurable API spend cap; fail safe. The user's Codex login is NOT automatically the runtime OpenAI API key.
- Long-running reviewer tasks never block live play. Do not modify Masters in-place; corrections require provenance, tests, and new version.

## Working methods
- Follow `BUILD_PLAN.md` phase by phase. Produce real code, tests, and user-facing Windows run instructions, not only documentation.
- First execute existing tests inside Docker: `docker compose build`, `docker compose run --rm dev python scripts/verify_package.py`, then `docker compose run --rm dev python -m unittest discover -s baseline_v2_3 -p "test_*.py" -v`. Do not install host-side Python packages. Report truthfully.
- Python >= 3.11, type hints, package layout, pytest or unittest, structured logging, settings via environment. Choose specific screen capture/OCR libraries based on Windows verification, not guesses; separate adapters to enable offline replay.
- Testing without BlueStacks is via synthetic frames and **explicitly labeled simulated** event traces; don't equate simulated pass with game accuracy. Provide a calibration workflow for user-provided screenshots/videos.
- Keep stage log in `IMPLEMENTATION_STATUS.md` and document blockers and next commands. All main modules must offer `--dry-run` or equivalent no-API mode.
- If requirements conflict, newer versioned **wire contract** wins on API structure, while gameplay constraints and Champions fixed masters remain authoritative; record conflicts rather than silently assuming behavior.
- Implement each phase with acceptance tests; no blanket claim of completion if phase-specific evidence is absent.

## Done means
A local app that can (1) load four masters with verifiable hashes, (2) replay and reason about observed events and turn boundaries deterministically, (3) open/close/de-duplicate decision windows, (4) call OpenAI with v2.3 structured contract and reject stale/illegal results, (5) show user-facing recommendation/correction UI, (6) log/replay sessions, (7) ingest BlueStacks screenshots after calibration, all with documented Windows setup and tests. Real-match readiness requires separate on-device evidence and approval.
