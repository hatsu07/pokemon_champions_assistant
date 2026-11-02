# Phase 0: Gate A/B evidence triage after PR #172

**Decision:** Gate A = `UNKNOWN`, Gate B = `UNSUPPORTED`, Phase 1 = `NO_GO` (current M-C, pinned Showdown `ccfaa20f32af363569cd21a9d501b2bc1ded26b1`). This is a *read-only, local-only evidence readiness aid*, not an authenticating parser, a replacement calculator, a complete Battle reconstructor, or permission to change LIVE.

## Gate A: burn + Reflect rounding — what a new game hit can establish

Existing executed *synthetic* Earthquake from Garchomp to Blastoise (burned attacker, defending Reflect, noncritical, no weather/items/boosts) follows the pinned Showdown sequential-stage arithmetic. Two Champions secondary descriptions differ for 4 of 16 modeled RNG rolls; actual game rounding is unknown. The secondary-reference code independently enumerates all 16 values for both methods; a real hit has an **unobserved RNG roll**. Comparing the observed integer HP loss against the *sets* of 16 possibilities avoids pretending to know which roll occurred.

`phase0_targeted_evidence_triage.cjs --damage local.json` accepts an OPTIONAL **user-verified and independently attributed** hit with pre-/post-hit integer HP, same match/target/turn, increasing state revision and explicit positive assertions: both combatant spreads match the *reviewed pinned fixture*, required abilities, burn + Reflect, noncritical hit, no weather/item/boost/intermediate HP effects. It **does not authenticate those assertions**. If any assertion is missing, the result is `INSUFFICIENT_INDEPENDENT_GAME_EVIDENCE`. A delta in both sets is `INCONCLUSIVE_INTEGER_OVERLAP`; a delta belonging to only one set is `POTENTIAL_*_ONLY_UNDER_DECLARED_FIXTURE`; a delta outside both requires rechecking conditions. A potential discriminator is **not proof of the full Champions damage formula**, not proof of independent RNG, not a Gate A GO or guaranteed KO.

Local-only optional candidate shape (placeholder, **not an actual game observation**):

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

`--partial <local-path>` reads a `phase0-partial-observation-v1` object and reports **field names and evidence class**, not values or identifiers. Present current facts require an explicitly linked same-match, same-active-instance, same-turn/revision request frame; `UNKNOWN`, inference, synthetic fixtures, stale/cross-frame and missing observations cannot be treated as current facts. If active screens/weather/terrain require timers, their observations must also share the current frame. Historical consumed/removed items, Mega and Choice lock **remain history requiring event and subject/state-transition validation even if a field carries a current evidence ID**. In all cases, unknown moves/PP/items/AP, unresolved action queue, hidden prior turns and RNG prevent declaration of full real-game restoration. `CURRENT_FACTS_LINKED_BUT_HIDDEN_BATTLE_UNRECOVERED` is deliberately **not** Gate B success.

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
