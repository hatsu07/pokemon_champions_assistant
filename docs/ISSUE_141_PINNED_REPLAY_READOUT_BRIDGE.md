# Issue #141 — pinned Showdown replay readout → observed projection audit

This slice follows merged #190. The missing piece is not "what Showdown
might do" but **the boundary between a complete, independently supplied
Showdown state and the fragment of a Champions match actually observed**.

## Two deliberately separate layers

1. **Actual pinned engine reader (Node)**:
   `local_simulator/pinned_replay_readout.cjs` accepts a COMPLETE *synthetic*
   3v3 singles `Battle.toJSON()` from the pinned
   `smogon/pokemon-showdown` at
   `ccfaa20f32af363569cd21a9d501b2bc1ded26b1`.
   It uses `Battle.fromJSON` on detached input, checks the mod,
   singles, six member slots, each side's active instance and exact
   `toJSON` roundtrip, then extracts all six actual engine member
   species IDs, move IDs in slot order, exact HP/maxHP, faint state,
   seven numeric boost stages and both active slot indices.
   The SHA-256 covers the **entire supplied serialized Battle including
   RNG and hidden state**, not just the visible projection. It neither
   invents status nor fills unknown OCR facts. An illegal/unsupported
   Battle or altered roundtrip returns `NO_ENGINE_REPLAY_READOUT`.
   Success is `SYNTHETIC_ENGINE_REPLAY_READOUT`, not an asserted
   Champions Battle.

2. **Untrusted readout adapter (Python)**:
   `audit_claimed_engine_readouts` accepts one caller-supplied readout
   per sourced, co-recorded opponent joint three, together with
   separate **unverified** opponent display-name-to-engine-slot claims.
   It checks strict readout schema, six slot uniqueness, all seven
   stage values, explicit PIN, match/window/revision through the freshly
   re-run Stage②. Critically, each `ClaimedEngineReadoutCase` must
   preserve the **original match/window/revision/snapshot and Battle SHA
   from the time it was staged**: the adapter refuses stale data rather
   than silently stamping it with a newer decision-window identity.
   These IDs are caller claims, not cryptographic evidence of source.
   The adapter then feeds PR #190's
   `audit_observed_replay_projections` with only source-bound and
   explicitly observed comparable facts. A missing, stale, altered or
   contradictory hypothesis makes the entire batch fail closed. The
   caller cannot promote a readout to a runnable Battle by setting
   `ready_for_engine=true` or `full_state_reconstructed=true`.

## Do not equate comparison with authentication

There is **no trusted Node→Python attestation channel** in this PR:
a caller can fabricate a JSON object that looks like the Node helper.
Even if it was emitted by the real pinned engine, the supplied Battle
is externally hypothetical, the opponent slot/name species mapping is
still a *claim*, and no genuine Champions history/RNG/game-parity is
proven. Both layers always return `ready_for_engine=false`.
PR #190's final audit always retains
`engine_battle_authenticated=false`,
`opponent_slot_mapping_authenticated=false`,
`full_state_reconstructed=false` and
`exhaustive_opponent_policy=false`.
No `Battle`, RNG seed, Showdown commands or inferred hidden
conditions are returned. The existing worker/LOCAL_SHADOW, LIVE,
GPT and desktop user-interface paths are unchanged.

Two fully specified synthetic Battles with identical visible state but
different RNG seeds produce **different complete-state SHAs** while
their extracted six-member projection matches. A chosen seed is a
hypothesis, never an observed real-game fact.

## CI and remaining dependencies

- `local_simulator/test_pinned_replay_readout.cjs`: detached fake-engine
  contract negatives, six slots, malformed states and hidden RNG
  ambiguity; Linux isolated Node protocol tests.
- `local_simulator/test_real_pinned_showdown.cjs`: real pinned Showdown
  fixture verifies each of six extracted values against actual Pokémon
  objects, no mutation, and different hidden RNG states with identical
  projections; Phase2 real-engine CI builds upstream at the exact pin.
- `tests/test_issue_141_engine_replay_readout_bridge.py`:
  all-hypothesis strict adapter binding, stale/forged flags, missing or
  changed fields, wrong p2 claimed slot, known HP/faint/boost, full
  no-execution safety; Linux isolated and native Windows tests.

Next dependency: audit a **trusted match/window-bound worker RPC**
that produces and cryptographically binds the actual engine readout
and its complete Battle to the SAME frozen request without accepting
self-claimed JSON; then separately validate actual opponent
display-name↔engine species/slot/round and hidden-state/RNG branches.
Complete Champions Battle reconstruction, full opponent policy
coverage, realtime orchestration, 18-matchup PREP and game-screen
Windows E2E are **not** accomplished here.

Merge only into `integration/issue-138-showdown-local-engine`, only
after exact HEAD Linux isolated, Phase2 pinned-engine real build, and
Ready-PR Windows full workflow and required jobs succeed, with
independent review cleared. No automatic merge.
