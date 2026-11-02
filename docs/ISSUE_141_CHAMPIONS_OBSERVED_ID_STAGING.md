# Issue #141 — Champions visible evidence and frozen ID bridge (read-only)

## Intent and verified scope

The existing `battle_evidence.py` stores explicitly visible, game-native
Japanese display literals. The Phase 2 worker needs exact engine IDs and a
**complete** serialized Showdown Battle, including hidden opponent set,
invisible event history and RNG. The merged #184 probe intentionally
accepts **synthetic** projections only. This PR does not weaken that
boundary or forge a real Battle JSON.

`src/champions_assist/local_strategy/observed_id_bridge.py` adds a
read-only `stage_observed_champions_ids(snapshot,assets)` staging API.
It operates entirely on the **current frozen in-memory** four-master set:

1. Reuse the existing `_verified_roster_binding` contract, including
   manifest SHAs, frozen source bytes, actual six-member CSV and the
   selected three / Showdown party-order binding. Only the **own** three
   have a mapped `showdown_species_id` and exact `showdown_moveN_id`
   from that current CSV; Japanese names are never transliterated.
   If these mapping columns are absent or incomplete, return
   `NO_OBSERVATION_ID_STAGE`. Current masters without mapping cannot
   be used to bypass this guard.
2. Bind the exact own active display name to the current decision window
   and its position in the **selected three**, not the registered six.
   Record `match_id`, `decision_window_id`, `state_revision` and the
   input snapshot SHA; never use a previous decision window's identity.
3. Preserve explicit HP as an integer only for strictly formatted exact
   `HP/maxHP`, or literal `0` from a confirmed own KO event. Reject
   percentages, positive bare numerals, impossible HP and ambiguous
   overlays. Absence of a value is `None`, not zero or full HP.
4. Translate **only** the known `battle_evidence.py` stat-stage labels
   `A/B/C/D/S/ACC/EVA` into the Showdown stat *field names*
   `atk/def/spa/spd/spe/accuracy/evasion`. Preserve the explicitly
   observed integer value; never infer a +0 from missing stages. Invalid
   keys, ranges and types cause all staging to fail closed.
5. Preserve revealed own moves only if they occur in the frozen own
   CSV move slots, and map them via the already frozen explicit move IDs.
   Opponent moves are kept as **raw display strings** (not Showdown
   IDs); no hidden move, ability, item, status or base form is filled in.
6. Preserve known opponent history from previous switches, including
   explicit HP, stages and revealed moves/items/abilities/forms. If the
   six-name preview is available, require every observed opponent to
   belong to it. Without a preview, only the explicitly identified
   current active opponent is accepted, not a guessed party.
   No opponent three-member selection, party slot, or Showdown species
   ID is invented.
7. Visible item, ability, form, current type and status are preserved
   as **raw strings**. No assumption is made that a Japanese status is
   exactly Showdown `par`/`brn`, that a displayed form is a specific
   Showdown ID, or that registered static items are still held after a
   switch, consumption or exchange.

Output is always `PARTIAL_OBSERVATION_ID_STAGE` when the *staging*
succeeds, with `full_state_reconstructed=false`,
`opponent_joint_set_verified=false` and `ready_for_engine=false`.
If its source checks fail it returns `NO_OBSERVATION_ID_STAGE` without
any partial member list. The output is **not** the synthetic
`SYNTHETIC_PINNED_SHOWDOWN_PROJECTION` required by #184, never a
`Battle.toJSON` object, and is not exposed over Node IPC or wired to
`run_shadow_turn`, GPT, LIVE or GUI.

## Remaining verification

- Real-game UI/OCR evidence provenance, HP scaling, live display state
  reconciliation and per-observation epoch/deduplication.
- A match-frozen, independently vetted Champions name → simulator ID
  mapping for opponents and exact known status/type/form/item/ability
  translation; these are **not** available from generic text aliasing.
- Sourced, co-occurring opponent joint sets, active order and event
  reconstruction; unknown RNG cannot become an observed engine seed.
- Controlled construction and engine-side audit of a real observed
  Showdown scenario, including exact current-window and legal actions,
  before *any* adoption by local strategy.

## Offline acceptance

`python -m unittest tests.test_issue_141_observed_id_bridge -v`
contains synthetic controls for source SHA drift, stale actor, exact own
move binding, previous opponent switch evidence, raw-only enemy moves,
HP percentage refusal, malformed status/boost/roster shapes and
read-only preservation. Added to Linux Phase 1 and native Windows test
commands. Current fixed Showdown real-engine CI remains unchanged;
it does not verify actual Champions screen input.

Merge target: `integration/issue-138-showdown-local-engine` only.
Use Ready latest-HEAD **Linux + Windows full workflow and jobs** plus
independent review. Do not merge on merely `mergeable=true`.
