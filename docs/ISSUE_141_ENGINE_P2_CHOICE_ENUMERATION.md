# Issue #141 — Actual pinned Showdown P2 request extraction and clone validation

**Experimental, isolated Node worker slice; no production LIVE/GPT connection.**

## Pinned API basis

The upstream source was inspected at SHA `ccfaa20f32af363569cd21a9d501b2bc1ded26b1`. In `sim/battle.ts`, `Battle.fromJSON` deserializes a complete Battle, `sides[1].activeRequest` contains its own issued request, and `requestState` specifies move/switch. In `sim/side.ts`, `Side.choose(command)` returns a boolean and sets `choice.error` on invalid input, and `Side.isChoiceDone()` confirms that all required actions were selected. `Battle.makeChoices` is not used for legality validation because it does not check each `Side.choose` return value.

## Implementation

- New `local_simulator/p2_engine_choices.cjs`: `enumerateEngineP2(Battle, battleJson)`. Rehydrate the same complete detached Battle; obtain the P2 request **directly from the Battle**, never from client-generated/guessed JSON.
- Only accept verified `champions` mod, *singles* with exactly one active side slot, 3 P2 members, matching declared/actual HP and faint state, no unknown actor state, and only normal `move` or single `forceSwitch` requests. Extract PP-positive non-disabled move slots and eligible living bench switches. Disallow unknown request keys, `noCancel`, `update`, `maybeLocked`, `maybeTrapped`, Mega/Z/Max/Tera/Ultra, active tera, reviving/commanding and other nontrivial request modes.
- Independently restore `Battle.fromJSON` for **each candidate** with the exact same starting Battle/RNG. Require an unchanged `p2.activeRequest` digest, a successful `side.choose(command)`, no choice error/cantUndo, a completed `side.isChoiceDone()`, and an action of the expected category. **Any** mismatch fails the entire set as `NO_CHOICES`.
- New `worker.cjs` `ENUMERATE_P2` JSONL operation uses the existing pinned runtime manifest integrity and P1 frozen roster/slot validation, full synthetic Battle input, match/decision identity, per-request nonce and payload fingerprint. It never writes to an evidence store, mutates the supplied input, or issues game controls.
- The runtime staging script now includes and hashes the new P2 helper alongside `worker.cjs`; worker boot refuses an absent/changed helper. Tests are updated for staging, worker protocol, and the isolated clone checker.
- Status on positive synthetic sample: `PARTIAL` with `ENGINE_CHECKED_SIMPLE_CHOICES`, commands, request hash and Battle hash. **`exhaustive_legal_choices_verified` remains false** because validation of each candidate **is not a proof that the complete game's action grammar, historical mechanics or all alternatives have been captured**. In particular, these choices MUST NOT be silently passed to `run_shadow_turn` as an exhaustively verified opponent policy. New cases should be added only with independent real pinned-engine tests.

## Not completed in this PR

1. No independent verified built Showdown distribution or actual pinned upstream integration test is included in the repository CI. Existing Node tests use a fake engine API to verify cloned choice protocol/negative cases; they do not certify the real engine's behavior. The manual opt-in offline staging requires the actual pinned SHA, exact file inventory, licenses/NOTICE and helper hash.
2. No production Python call site or shadow branch promotion. The JSONL op is explicit and isolated. The next slice should use `LocalSimulatorWorker` with a bound `ENUMERATE_P2` exchange and compare that result with the `opponent_request.py` structural parser, then prove coverage of all options or stop.
3. No Champions observation→full Showdown Battle/RNG reconstruction, opponent joint set authenticity/coverage, 18-PREP or UI integration. Mechanics parity Gate A=UNKNOWN/B=UNSUPPORTED is **not** a stop to isolated development but it is not evidence of real-game correctness.
4. No real Windows/Android game E2E or performance measurement for this new operation. Node protocol tests and Desktop CI do not substitute for that evidence.

The helper is only called on full **`FULL_REPLAY_SYNTHETIC`** inputs; it does not infer midbattle states from incomplete OCR. No user-run WSL/VS Code/Codex or game screenshots are needed for this CI-only slice.
