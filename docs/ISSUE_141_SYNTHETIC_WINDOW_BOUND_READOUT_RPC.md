# Issue #141: bounded window-bound READOUT_REPLAY IPC (synthetic only)

This incremental slice follows merged PR #191.

## Supported transition

An opt-in `LocalSimulatorWorker.readout_synthetic_replay` sends a
`READOUT_REPLAY` request over the existing serialized, time-bounded,
match-scoped JSONL worker channel. It requires the current frozen
four-master p1 roster, active name, registered hypothesis ID (or a freshly
revalidated entire co-occurring joint-opponent batch when supplied),
complete independently supplied **synthetic** Battle JSON, a unique
branch nonce, original match/window/revision/snapshot identity, and a
canonical **full-Battle input SHA**. To eliminate cross-runtime
serialization mismatches, Python serializes the full Battle **exactly
once** as UTF-8 JSON text (`battle_json`) and SHA-256 hashes those
exact transmitted bytes. The worker passes that unchanged text to the
pinned extractor, which parses an independent clone for engine replay
and hashes the **same original text**, rather than using
`JSON.stringify` to generate a different representation. JSON
`3.0` and object keys `"10"`/`"2"` therefore stay
byte-bound to what Python submitted even when JavaScript semantically
normalizes them. Distinct serializations may have distinct SHA values
without being mistaken for new game observations.

The worker verifies its pinned engine, its own code and the independent
`pinned_replay_readout.cjs` helper against the offline runtime manifest
**before loading the helper**. The helper deserializes a detached full
Battle through actual `Battle.fromJSON`, requires an exact complete
round-trip, and extracts six real engine Pokémon with species IDs,
ordered moves, HP/max/faint, boosts and both active slots. The worker
checks own p1 party order, species/moves and active against the submitted
explicit frozen IDs. P2 Japanese names, hypothetical hidden sets and
RNG are not guessed by the worker.

The response includes its source classification, complete Battle digest,
original hypothesis, branch hash and the existing worker envelope.
Python's `_exchange` matches the **entire current request identity,
snapshot SHA, request ID, unique nonce, hypothesis ID and branch hash**,
within its original deadline; stale/out-of-order replies are discarded.
The Python method independently validates the strict readout schema,
the source and never-runnable flags, and hashes the **exact Battle that
it submitted**, rejecting any unrelated/modified digest. The result
`SyntheticEngineReadoutReceipt` retains the original submitted request
JSON and carries `engine_battle_authenticated=False`,
`full_state_reconstructed=False`,
`opponent_slot_mapping_authenticated=False`,
`ready_for_engine=False`.

**This is PROCESS-BOUND VALIDATION, NOT GAME AUTHENTICATION.** A
separate caller supplied the Battle; a local process verifies only the
synthetic engine execution against the supplied bytes and frozen
request. An attacker with arbitrary control of the local process,
unverified runtime, or upstream Battle source is not defeated by a
request/response echo or SHA. A Python dataclass can be forged later and
is not a transferable certificate. A genuine pinned engine projection
does not establish that Champions generated the Battle or that all
unseen RNG/PP/effect states are known. No new real-game observation,
complete Battle, legal opponent policy or real-time tactical inference
is obtained. No result is adopted by LIVE/GPT/GUI or `run_shadow_turn`.

## Test coverage and safety gates

- Local/isolated Node worker tests: real manifest hash pin for the
  additional helper, startup fail-closed on tampering, partial/unsupported
  `READOUT_REPLAY` rejection, unique branch hash/nonce transport;
  Node helper compares the exact text hash for `3.0` and numeric keys,
  and never replaces the hash with a parsed-object reserialization.
- Python fake-process tests: response payload and exact submitted
  Battle hash checks, arbitrary certification flags/source changes,
  stale echo rejection, unregistered opponent hypothesis refusal.
- Existing pinned Showdown real-engine fixture proves the underlying
  six-member extractor and hidden RNG fork handling. The real-engine
  workflow also exports a complete synthetic pinned 3v3 `Battle.toJSON`
  with exact frozen test-team move slots, builds a **test-only**
  manifest-pinned local worker runtime, and drives the full
  `LocalSimulatorWorker.readout_synthetic_replay` Python call. It
  verifies actual six-member readout, original request/nonce,
  strict non-runnable flags and exact transmitted SHA, including
  the numerically equivalent `turn: 3.0` variation. The temporary
  CI inventory is not a separately reviewed production build manifest.
  CI still pins exact upstream Showdown SHA.
- Native Windows offline app tests continue to run; no production
  worker bundling or UI changes have been made.

Next: add a reviewed **real-engine worker end-to-end** test with
independently verified runtime manifest and full multi-hypothesis
request batching, then separately validate actual Champions
opponent-name-to-slot/species mappings and incomplete hidden-state/RNG
branches before any opt-in tactical comparison.

Do not merge until independent code review and exact-head Linux,
pinned real-engine and Ready Windows **whole workflows and required
jobs** succeed with actual checkout SHA/current PR head verified.
Target `integration/issue-138-showdown-local-engine` only.
