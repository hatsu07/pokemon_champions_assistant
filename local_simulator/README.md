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
- As of the latest review, `assets` must be the actual match-preloaded
  `FrozenMatchAssets`. All four on-disk preload SHA hashes must match
  the frozen window and in-memory source bytes; the six registered members
  must be derived from the frozen team CSV. The selected three require
  explicit frozen `showdown_species_id` and `showdown_move1_id` etc.
  These IDs are **not** guessed from Japanese display names, and current
  team masters without those mappings will fail closed. Python sends an
  exact p1 roster/active binding; Node compares it against deserialized
  Showdown `Battle.sides[0].pokemon[].species.id`,
  `moveSlots[].id`, order and `active[0]` **before** applying choices.
  Mismatch returns `UNSUPPORTED / P1_ROSTER_OR_MOVE_SLOT_MISMATCH`.
- `simulate(...,candidate_action_id=...,assets=...)` requires frozen team
  assets and a currently LEGAL candidate. The caller's `p1_choice` must
  exactly match the move slot resolved from the team; for switch windows,
  an explicit frozen `showdown_party_order` matching the selected three is
  necessary. Missing/ambiguous mappings are rejected, never silently guessed.
- Worker `stdin.write/flush` runs on a separate bounded writer thread.
  A stalled worker that stops reading its pipe is killed and cannot block the
  decision thread past its deadline.
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
plus a local Node executable. The worker also checks the staging manifest's SHA256 fields for Node, worker,
licenses **and every file in the Showdown dist/dependency runtime tree**.
The staging input must include an independently reviewed exact-file inventory
(`--verified-build-manifest`) and a clean pinned checkout. Added, removed,
or modified dependencies prevent startup; a bare source marker is insufficient. Do not create the marker without verifying the upstream checkout. Python's LocalSimulatorWorker requires explicit paths.
This project does not download or update anything while playing.

Windows onedir bundling of Node, licenses and dependencies is **a remaining
product engineering task, not blocked by full game-parity proof**.
This branch intentionally does not ship a new unverified runtime
or add a silent developer Node dependency to production packaging.

Tests: `node --test local_simulator/test_worker.cjs` and
`python -m unittest tests.test_issue_140_foundation -v`.
