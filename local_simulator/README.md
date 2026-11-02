# Issue #140 — opt-in local simulation worker (not a battle recommender)

This directory is an experimental foundation, **not wired into the app's
OpenAI tactical path**. Showdown Champions is third-party code and Phase 0
#139 still records the historical science-based NO_GO (calculation A=UNKNOWN;
partial-observation restoration B=UNSUPPORTED). Under the 2026-11-02
Showdown-first design decision (#174), this no longer blocks development.
A synthetic simulation may return PARTIAL, **never CONFIRMED**.

## Protocol

- Persistent child process, bounded UTF-8 JSONL (1 MiB/request and response).
- First message HELLO, then SIMULATE with a complete synthetic/replay Battle
  JSON serialized from the *same pinned build*.
- Echo protocol, request_id, complete identity fields, capability, reason.
  Each simulation also has its own 128-bit branch nonce, SHA256 of the exact
  branch payload (hypothesis, candidate action and both Showdown choices) and
  action ID. Node checks the payload SHA and echoes these fields; Python
  rejects stale, swapped, duplicated, malformed, late or dead-worker replies.
- PIVOT_TARGET/FAINT_REPLACEMENT/NORMAL_SWITCH are distinct decision windows.
  Input choices after an intervening turn/KO must be revalidated.
- Do not reconstruct arbitrary real partially observed turns with Showdown.
- No runtime game input, socket, master reload, SQLite write or auto-update.

## Explicit, offline runtime staging

Fixed candidate upstream SHA:
`ccfaa20f32af363569cd21a9d501b2bc1ded26b1`

This source SHA is only the pinned PoC candidate, **not proof of Champions
compatibility**. The runtime is disabled unless a prebuilt Showdown tree has
`dist/sim/index.js` and a text marker `.champions-source-sha` with that SHA,
plus a local Node executable. The worker also checks the staging manifest's
SHA256 fields for Node, worker, Showdown entry and license files; a bare marker
is not sufficient. Do not create the marker without verifying the upstream checkout. Python's LocalSimulatorWorker requires explicit paths.
This project does not download or update anything while playing.

Windows onedir bundling of Node, licenses and dependencies is **a remaining
product engineering task, not blocked by full game-parity proof**.
This branch intentionally does not ship a new unverified runtime
or add a silent developer Node dependency to production packaging.

Tests: `node --test local_simulator/test_worker.cjs` and
`python -m unittest tests.test_issue_140_foundation -v`.
