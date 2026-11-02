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
- `ENUMERATE_P2` uses the same complete synthetic Battle and an
  engine-issued `p2.activeRequest`. The fixed Showdown engine may return
  both `move N` and `move N mega` for ordinary single Mega requests,
  each tested on its own detached Battle/RNG clone. The Python
  `enumerate_p2_from_replay` receiver explicitly admits two scopes:
  `PINNED_SHOWDOWN_SIMPLE_SINGLES_ONLY` (no Mega) and
  `PINNED_SHOWDOWN_SINGLE_MEGA_SINGLES_ONLY` (normal/Mega move pairs).
  It requires the command-to-scope grammar, same branch nonce and hashes,
  known hypothesis, and `exhaustive_legal_choices_verified=false`.
  Unknown modifiers and inconsistent pairs fail closed. This is an opt-in
  synthetic-replay API, **not** a live-game recommendation or completeness
  guarantee; see `docs/ISSUE_141_SINGLE_MEGA_REQUEST_SLICE.md`.
- The bounded pinned-engine PP/lock extension additionally supports
  `PINNED_SHOWDOWN_HARDLOCK_OR_STRUGGLE_SINGLES_ONLY` for a **verified
  PP-less one-slot** hard `getLockedMove()` or all-PP-exhausted
  `struggle` request. Its command grammar is `move 1`, optionally
  followed by regular bench switches; it does not claim arbitrary locks,
  depleted-PP-but-not-Struggle, or other ways of disabling every move.
  Standard Choice-item/Encore disabled slots and single PP-depleted slots
  remain subject to exact per-command checks. See
  `docs/ISSUE_141_LOCK_PP_STRUGGLE_SLICE.md`.
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

Tests: `node --test local_simulator/test_worker.cjs local_simulator/test_p2_engine_choices.cjs`,
`python -m unittest tests.test_issue_140_foundation tests.test_issue_141_engine_p2_transport -v`,
and (with a built, exact-SHA upstream simulator) `node --test local_simulator/test_real_pinned_showdown.cjs`.
