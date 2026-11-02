# Phase 0: Gate A/B evidence triage after PR #172

**Decision:** Gate A = `UNKNOWN`, Gate B = `UNSUPPORTED`, Phase 1 = `NO_GO` (current M-C, pinned Showdown `ccfaa20f32af363569cd21a9d501b2bc1ded26b1`). This is a *read-only, local-only evidence readiness aid*, not an authenticating parser, a replacement calculator, a complete Battle reconstructor, or permission to change LIVE.

## Gate A: burn + Reflect rounding — what a new game hit can establish

Existing executed *synthetic* Earthquake from Garchomp to Blastoise (burned attacker, defending Reflect, noncritical, no weather/items/boosts) follows the pinned Showdown sequential-stage arithmetic. Two Champions secondary descriptions differ at 4 of 16 **paired RNG positions**, but **both unknown-RNG integer-damage sets are exactly `{21,22,23,24,25}`**. The set difference is empty in **both** directions. Hence, for this fixed fixture, **one real hit with unknown RNG cannot distinguish these methods**, even if all combatant conditions and integer HP are verified. This is a mathematical limit, not a missing quantity of samples. No single-hit evidence collection or special battle for this purpose is requested. The actual game rounding remains unknown.

`phase0_targeted_evidence_triage.cjs --empty` reports `fixture_identifiability=NOT_IDENTIFIABLE_WITH_UNKNOWN_RNG_FOR_THIS_FIXTURE` without requesting an observation. `--damage local.json` is **optional archival classification only**, not a proposed discriminator. If an existing independently verified hit is already available, it accepts pre-/post-hit integer HP, same match/target/turn, increasing revision and explicit assertions: both spreads match the *reviewed pinned fixture*, abilities, burn + Reflect, noncritical, and no weather/item/boost/intermediate HP effects. It **does not authenticate** those assertions. If missing, it returns `INSUFFICIENT_INDEPENDENT_GAME_EVIDENCE`. A delta in the common set returns `NOT_IDENTIFIABLE_WITH_UNKNOWN_RNG_FOR_THIS_FIXTURE`; a delta outside both returns `OUTSIDE_BOTH_REVIEWED_METHODS_RECHECK_CONDITIONS`. The earlier `POTENTIAL_*_ONLY_UNDER_DECLARED_FIXTURE` classes are unreachable for this fixed fixture and are no longer emitted. A future alternative test would first need a **different legal, independently source-checked fixture whose unknown-RNG integer sets genuinely differ**, or an independently observed RNG position. No broader rule or GO follows merely from that possibility.

Local-only **archival example** (placeholder, **not an actual game observation and not a discriminator for this fixture**):

```json
{
  "schema": "phase0-rounding-game-candidate-v1",
  "origin": "REAL_GAME", "verification": "USER_VERIFIED",
  "scenario": "GARCHOMP_EARTHQUAKE_BLASTOISE_BURN_REFLECT_NONCRITICAL",
  "before_hp": 170, "after_hp": 149,
  "evidence": {
    "before": {"id":"local-before", "match_id":"local-match", "active_instance_id":"defender-1", "turn_epoch":4, "state_revision":41},
    "after":  {"id":"local-after",  "match_id":"local-match", "active_instance_id":"defender-1", "turn_epoch":4, "state_revision":42}
  },
  "conditions": {
    "burn": true, "reflect": true, "noncritical": true,
    "no_weather": true, "no_items": true, "no_boosts": true,
    "exact_one_hit_hp": true, "no_intermediate_hp_change": true,
    "reviewed_fixture_stats_confirmed": true,
    "reviewed_abilities_confirmed": true, "move_hit_confirmed": true
  }
}
```

All assertions must really be checked against the actual game evidence and pinned fixture; **do not set `true` merely to make a sample pass**. If no game capture meets the requirements, retain `UNKNOWN` rather than schedule dedicated battles or invent conditions. Optional local use: `node poc/showdown_phase0/phase0_targeted_evidence_triage.cjs --empty` or `--damage <local-path>`. Raw evidence never goes to CI, GitHub, output JSON, or user-facing LIVE.

## Gate B: observation coverage, not synthetic success promotion

`--partial <local-path>` reads a `phase0-partial-observation-v1` object and reports **field names and evidence class**, not values or identifiers. Present current facts require an explicitly linked same-match, same-active-instance, same-turn/revision request frame; `UNKNOWN`, inference, synthetic fixtures, stale/cross-frame and missing observations cannot be treated as current facts. **A shared evidence ID alone is insufficient.** Current hazards, including **empty** `effects:[]`, must be a scoped `{side:"p2",effects,turn_epoch,state_revision}` object from the current request frame; only zero or one `stealthrock` is within #172's modeled envelope. The supported active effects are `raindance` and `electricterrain`; active effects require exact current-frame `effect_remaining_turns` with integer durations 1–8 and matching effect keys. Screens must be a distinct subset of `reflect`/`lightscreen`, with corresponding current-frame 1–8 `screen_remaining_turns`. Conflicting values, mismatched effect keys, stale turns/revisions, wrong side, extraneous durations and values of 0 are reported as `INVALID_CURRENT_STATE_EVIDENCE` (never as ready). Missing timer facts remain `INSUFFICIENT_CURRENT_OBSERVATION`. Historical consumed/removed items, Mega and Choice lock **remain history requiring event and subject/state-transition validation even if a field carries a current evidence ID**. In all cases, unknown moves/PP/items/AP, unresolved action queue, hidden prior turns and RNG prevent declaration of full real-game restoration. `CURRENT_FACTS_LINKED_BUT_HIDDEN_BATTLE_UNRECOVERED` is deliberately **not** Gate B success.

Status is always `Gate A UNKNOWN / Gate B UNSUPPORTED / Phase 1 NO_GO`. This helper may reject incomplete/ambiguous inputs and cannot confirm an input's provenance or replace `partial_observation_probe.cjs` / `observation_conditioned_branch_probe.cjs`. No raw image, video, battle log, private case JSON or file path is stored or printed.

## PR #169–#172 scope already merged into integration

| PR | Synthetic conditional representation | Unverified despite passing code tests |
| --- | --- | --- |
| #169 | Current HP, all seven boosts, screen durations | Real-game unique restore, hidden history, RNG |
| #170 | Current no-item plus independently linked consumed Berry | Actual consumption effects, history |
| #171 | Move-bound PP with target/revision; scalar PP refuses binding | Multiple opponent moves and prior PP events |
| #172 | Current Choice lock, Knock Off removal, linked Mega Garchomp, Rain Dance/Electric Terrain remaining turns, opponent-side Stealth Rock; switch reset and stale-empty hazard reviews | General Mega/items/effects, unknown past actions and real-game restoration |

## Reassessment boundary

Update `PHASE0_ADOPTION_ASSESSMENT.md` and Issue #139 based on **independent** game evidence plus the product axes separately. Synthetic cases, this triage's input labels and a green Windows CI are not an additional verified game battle. First decide whether a bounded verified local calculator is feasible; do not force infinite synthetic branches. Windows onedir offline worker/IPC/revision tests, actual OCR→display E2E latency and distribution licenses/SBOM/NOTICE are still unproven. If risk remains high, explicitly close Phase 0 as NO_GO **without** merging Phase 1 Draft PR #144.
