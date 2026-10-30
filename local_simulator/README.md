# Issue #140 — opt-in local simulation worker (not a battle recommender)

This directory is an experimental foundation, **not wired into the app's
OpenAI tactical path**. Showdown Champions is third-party code and Phase 0
#139 is still NO_GO (calculation A=UNKNOWN; partial-observation restoration
B=UNSUPPORTED). A synthetic simulation may return PARTIAL, **never CONFIRMED**.

## Protocol

- Persistent child process, bounded UTF-8 JSONL (1 MiB/request and response).
- First message HELLO, then SIMULATE with a complete synthetic/replay Battle
  JSON serialized from the *same pinned build*.
- Echo protocol, request_id, complete identity fields, capability, reason.
  Python rejects stale/mismatched, late, invalid or dead-worker responses.
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

Windows onedir bundling of Node, licenses, dependencies and validated
Champions engine is **blocked on Phase 0 adoption and Windows distribution
tests**. This branch intentionally does not ship a new unverified runtime
or add a silent developer Node dependency to production packaging.

Tests: `node --test local_simulator/test_worker.cjs` and
`python -m unittest tests.test_issue_140_foundation -v`.
