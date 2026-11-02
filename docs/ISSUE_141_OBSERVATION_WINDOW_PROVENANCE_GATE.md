# Issue #141 — synthetic observation provenance / current-window binding gate

## Purpose and boundary

PR #185 stages explicitly visible Pokémon Champions literals and frozen **own**
Showdown IDs, but it does not authenticate when the UI produced a fact.
PR #184 showed that even a matching visible projection does not reconstruct
hidden Battle/RNG state.

This PR adds `audit_synthetic_window_evidence(snapshot, records)` as a
**read-only, synthetic contract test** for the next capture-ingestion slice.
It binds declared evidence records to one immutable `FrozenBattleState`.
It deliberately **does not claim real-game OCR provenance**, authenticate
an OS capture source, or reconstruct a Showdown Battle.

## Bound identity

A `WindowEvidenceRecord` must carry the exact:
- `match_id`, `decision_window_id`, `turn_epoch`, `state_revision`;
- `screen_kind` and the currently active own Pokémon name;
- SHA256 of the frozen `snapshot.observation` canonical JSON;
- nonempty unique record ID, timezone-aware capture timestamp, and explicitly
  scoped `section`/`side`/`actor`/`value_json`.

Only `source=SYNTHETIC_WINDOW_EVIDENCE` is accepted here. A caller cannot
self-label a record `REAL_GAME_CAPTURE` or `OCR_CONFIRMED` and have that label
treated as proof. Any current-active fact in the supported `battle_evidence`
sections must have **exactly one** bound record. Missing/extra records,
duplicate fields/IDs, changed values, old window/revision, actor mismatch or
malformed evidence => `NO_WINDOW_EVIDENCE_BINDING`. The original frozen
snapshot is never mutated.

**Absent keys are unknown; present-but-empty keys are corrupt.** This gate
refuses explicit `None`, `""`, `[]`, `{}`, and invalid section-specific
shapes, rather than skipping them. It validates **all present stored actor
values** before deciding whether they belong to the current active actors
or a historical entry. This includes a switched-out opponent's malformed
HP, move list, boost, or item/ability record: history cannot disappear just
because its stored value is empty. A valid, nonempty historical record is
still listed as unverified history rather than reauthenticated.

Sections are `hp_by_pokemon`, `boosts`, `status_by_pokemon`,
`current_type`, `moves_by_pokemon`, `item_by_pokemon`,
`ability_by_pokemon`, and `form_by_pokemon`. Values are compared
**verbatim as canonical JSON**; this module does not normalize Japanese
names or status IDs, invent HP percentages as integer HP, or fill missing
values. The PR #185 type/HP/ID staging boundary remains separate.

The success label `SYNTHETIC_WINDOW_FACTS_BOUND` says **only** that the
self-declared synthetic records exactly match the stored active actors'
facts and current window identity. The record timestamps are syntactically
timezone-aware, not cryptographically verified camera timestamps.

## Historical evidence is explicitly not upgraded

Past opponent/team evidence can remain in `battle_evidence` after switches.
This gate lists those entries in `historical_keys_unverified`. A current-window
stamp cannot retroactively establish where, when, or from which camera frame
a previous turn's HP, move, item or status originated. A later capture
integration needs a durable, append-only, per-observation source record,
verified actor identity and switch/turn chronology, with source hashes bound
at the moment of capture. This synthetic module is **not** that integration.

Both success and failure always carry
`capture_origin_authenticated=false`,
`historical_events_authenticated=false`,
`full_state_reconstructed=false` and `ready_for_engine=false`.
No worker IPC, `run_shadow_turn`, GPT/LIVE, SQLite evidence mutation,
GUI or current gameplay code is modified.

## Tests and follow-ups

`python -m unittest tests.test_issue_141_observed_provenance_gate -v`
covers current-window identity, changed observation SHA, stale revision,
malformed timezone, wrong/duplicate/omitted evidence, inactive actor
relabeling, real-source self-claims, retained history and snapshot
immutability. The regression suite also injects explicit empty/null active
HP, moves, boosts and scalar facts, plus corrupt switched-out historical
entries, including cases where self-generated records match the broken state.
Linux/Windows isolated test lists include these cases.

Next PR should instrument the actual capture/evidence writer to store
provenance **at capture time**, including image/event source digest and
match-window/actor epoch. Only after independent verification should an
actual capture adapter be permitted to construct a trusted typed record.
The present synthetic source is never promoted to an actual observation.

All integrations continue to require latest-HEAD Linux, Windows full run +
required job `completed/success`, independent review, and no automatic merge.
