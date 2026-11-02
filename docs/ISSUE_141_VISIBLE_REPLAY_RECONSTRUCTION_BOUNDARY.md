# Issue #141 — visible-state / complete Battle consistency probe (synthetic only)

## Why this slice exists

The current app keeps exact Pokémon Champions screen-derived facts in
`src/champions_assist/battle_evidence.py` (notably HP, revealed moves,
held items, status and limited numeric boosts). The current Phase 2 worker,
however, requires a **complete pinned Showdown `Battle.toJSON()` snapshot**,
including its unobservable RNG state, to simulate. The observer does **not**
currently produce such a full Battle. In particular, a "known opponent name"
is not a complete revealed 3-Pokémon roster or a verified Showdown species
identifier. This PR does **not** bridge that missing information by guessing.

The experimental `local_simulator/observed_replay_audit.cjs` is a
**read-only synthetic consistency probe**:

- Input A: a complete, already constructed pinned Showdown Battle JSON.
- Input B: an explicitly normalized, **synthetically generated** visible
  projection bound to that exact Battle's SHA256, synthetic match/window ID
  and state revision. It cannot be constructed from mere OCR or a JSON
  object falsely labeled as an observed actual Champions match.
- Confirm the engine is a single active slot per side in a 3v3
  `champions` Battle, restore on an **independent** `Battle.fromJSON`
  instance and compare precisely provided active slots, members'
  `species_id`, exact integer HP/maxHP/fainted status, explicitly known
  `status` and stat stages, optional weather/terrain, and turn.
- Missing attributes are **UNKNOWN**, never auto-filled with zero,
  empty status, default weather, or an inferred item/ability/move.
  Duplicate members, unknown projection fields, inconsistent values,
  wrong SHA, wrong format, invalid identity and mismatches all fail closed.
  Never modify the supplied Battle or the observation packet.
- Even a match produces `SYNTHETIC_VISIBLE_FIELDS_MATCH` and always
  `full_state_reconstructed=false`,
  `exhaustive_observation_verified=false`. The diagnostic explicitly
  enumerates unknown provenance, enemy hidden sets, RNG/event queue,
  effect durations and Champions real-game parity.

## Demonstration of a hard boundary

The pinned real-engine test creates two fully serialized Battle states
with **different PRNG seeds**, but the same active Pokémon, HP, status,
stages, weather and other supplied visible projection. Both pass the
visible-field consistency probe *when separately bound to their own
Battle SHA*. The same visible evidence therefore **cannot uniquely
reconstruct the Battle/RNG**. A fixed SHA or a deterministic chosen seed
is a hypothetical simulation assumption, not an observed game fact.

The actual pinned engine is checked out at
`ccfaa20f32af363569cd21a9d501b2bc1ded26b1`, format
`gen9championsbssregmc`. All 3v3 teams and screen projections are
synthetic test fixtures, **not Pokémon Champions observations**.

## Explicit boundaries

- No game-screen OCR ingestion, name translation, Pokémon HOME conversion,
  opponent moveset imputation, source authenticity certificate, visible
  history-to-Battle event replay, IVs/EVs/PP inference, clock sync or
  observable-only midgame reconstruction exists in this PR.
- The normalized projection is intentionally **not** exposed over
  Python/Node IPC or wired into `run_shadow_turn`, legacy GPT, LIVE or
  GUI. It is a testable, isolated research interface for later bridging.
- `FULL_REPLAY_SYNTHETIC` remains the only source accepted by the
  current shadow runner. A matching projection **does not promote**
  `PARTIAL` to `CONFIRMED` or legal-choice exhaustiveness to true.
- Future scoped work: explicit verified Japanese-display → Showdown ID
  bindings from the frozen Champions masters, provenance of actual
  UI observations, active identity and HP reconciliation, separate
  opponent joint-set hypotheses, reconstructable event chronology,
  and handling of nonobservable RNG as **scenario branches** rather
  than pretending to observe it. Only then consider an opt-in IPC
  integration, with no accidental LIVE behavior change.

## Validation

`node --test local_simulator/test_observed_replay_audit.cjs` exercises
fake detached Battle comparison, missing and contradictory fields,
synthetic-only scope and SHA/identity binding. The pinned Showdown
integration test exercises real `Battle.toJSON/fromJSON` roundtrip,
visible projection success, active/HP/status/stage mismatch rejection
and two unseen-RNG states with identical visible facts.

Linux Phase 1, real pinned-Showdown Phase 2 and Windows desktop CI must
verify the **same latest ready PR HEAD**. Independent review is required;
these CI results do not establish real-game behavior.
