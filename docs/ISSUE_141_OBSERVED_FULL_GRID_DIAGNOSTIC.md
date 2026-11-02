# Issue #141 Phase 2②→③ — bounded observed/synthetic one-turn grid

## Delivered in this PR

`audit_observed_synthetic_turn` is an **opt-in offline diagnostic**, not a
live action recommender, game-state constructor or Phase 2②/③ acceptance claim.

It connects previously isolated implementation stages without duplicating
their judgment logic:

1. Revalidates the exact frozen match/window/revision/4-master snapshot and
   **all** supplied co-recorded, source/regulation-validated opponent 3-member
   hypotheses, including the observed move/switch **name possibilities**.
2. At entry, canonicalizes each complete synthetic Battle to bounded
   **immutable UTF-8 JSON**, checking valid finite JSON and the 4MiB limit.
   These bytes and their SHA-256 are preserved for the entire request.
   Detached fresh copies are passed to #194's worker-backed readout and its
   all-case observed-state constraint audit. Every complete Battle with
   RNG and its p2 display-name-to-slot binding is independently supplied by
   the caller. **Nothing is constructed from missing OCR fields.**
3. Stages all source hypotheses into a separate private synthetic snapshot;
   it does **not** mutate the original OCR state or store hypotheses as facts.
4. For every synthetic Battle, invokes pinned `ENUMERATE_P2` on a
   **new copy of the frozen bytes**. It independently verifies the SHA of the
   #194 READOUT_REPLAY UTF-8 submission, the canonical object fingerprint in
   ENUMERATE_P2, and the original stored hash. All subsequent SIMULATE
   scenarios use the **same original immutable JSON**, not any worker-mutated
   mapping. Incomplete, unsupported or hash-mismatched cases fail the **entire
   batch**.
5. Sends the exact Cartesian product of those hypothetical engine-issued p2
   commands and the current verified p1 actions into the existing
   `run_shadow_turn` comparator. Existing budget (240 branches),
   per-case bounds, deadlines, nonce/request binding and worker aborts apply.
6. Rechecks the current observation revision before and after expensive
   operations. An optional `current_snapshot` callback must supply the
   original OCR snapshot, **not** the private derived one.

Success returns `SYNTHETIC_TURN_DIAGNOSTIC_ONLY` with input hashes,
hypothesis IDs, p2 command counts, total evaluated branches and **separate**
hypothetical-model nondominated action IDs.

Always:

- `ranking_status="NO_RANKING"`
- `game_battle_authenticated=False`
- `opponent_mapping_authenticated=False`
- `exhaustive_opponent_policy=False`
- `full_state_reconstructed=False`
- `ready_for_engine=False`

All failures return `NO_OBSERVED_TURN_DIAGNOSTIC` and disclose **no** partial
case list, action preview or branch count. There is no automatic fallback to
the best-looking subset. This module is not imported by the GUI, LIVE/GPT,
LOCAL_SHADOW, or production action-selection paths.

## What this does NOT establish

The existence of matching complete hypothetical Battles does not prove that
they are the true Pokémon Champions game state. In particular, this PR does
not authenticate:

- Actual game capture, authoritative p2 Japanese display name/species/slot
  mapping, or current opponent three from hidden team preview
- Missing HP, max HP, PP, disable/Encore/Choice lock, items, ability,
  form/mega history, effects, turn history, or simulator RNG
- Whether the sourced opponent joint-set catalogue is exhaustive
- Whether pinned Showdown commands cover all legal Champions choices,
  including game-specific transformations and special states
- Full Champions ↔ Showdown mechanical equivalence or an empirical win rate

The `ENGINE_CHECKED_SIMPLE_CHOICES` subset still has
`exhaustive_legal_choices_verified=False`. The observational frontier
still lists **conditional move and switch names**, not authenticated p2
commands. Neither proves that all real opponent policies were covered.
Even a unique synthetic model action remains **non-actionable**.

## Tests and prerequisites for actual ②/③ completion

`tests/test_issue_141_observed_full_grid_diagnostic.py` covers whole-grid
hypothetical success, no-promotion flags, mismatched readouts, missing /
duplicate / re-ordered hypotheses, unsupported and SHA-mismatched p2,
changed OCR revision, simulated worker errors and invalid requests.
Additional regression tests mutate the caller's nested Battle HP,
moves and RNG after readout and mutate the dictionary passed to the
enumerator. All later SIMULATE branches must still use the original frozen
SHA and byte representation; forged readout SHA and oversized/nonfinite
initial values fail closed. A new **real pinned Showdown worker** Python E2E
test traces READOUT_REPLAY → ENUMERATE_P2 → SIMULATE and checks one complete
synthetic Battle/RNG SHA across every branch. The isolated regressions run on
Linux/Windows, with this pinned-worker E2E in the Phase 2 real-engine job.
These tests do **not** claim a new real-game E2E.

For Phase 2② acceptance, provide independently grounded exact
opponent-species/slot provenance and a bounded reproducible game-to-engine
scenario constructor (or witnessed full replay), while continuing to refuse
unknown states. For Phase 2③ acceptance, independently validate the whole
legal opponent policy/action coverage and the current legal p1 actions over
gameplay-relevant Golden scenarios, with fail-closed unsupported mechanics,
same-window replay and timing evidence. Only after this may an isolated
LOCAL_SHADOW recommendation be considered; switching GPT/LIVE belongs to
later project milestones.

**Do not mark Issue #141's ② or ③ complete on this offline diagnostic PR
alone.** Keep #141 Open, and do not auto-merge before independent review,
same-head Linux/real-engine/Ready Windows required CI and final HEAD check.
