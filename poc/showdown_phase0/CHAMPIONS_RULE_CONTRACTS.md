# Bulbapedia Champions mechanics source contracts — Phase 0 (#139)

**Scope: offline diagnostics only; not a live battle engine, a production damage calculator, or a proof of actual-game mechanics compatibility.** This PR adds small, dependency-free source-derived reference state transitions. The goal is to replace mandatory one-by-one device tests with reproducible documented rule contracts, while keeping the uncertainty visible.

## Source hierarchy and review date

Reviewed **2026-10-31**, regulation **M-C**. Official and Pokémon HOME data, the user's newer in-game observations and the repository's fixed Champions masters take precedence over community references. Among community documentation, use **Bulbapedia's Champions-specific flavor text and subsections** first; **never silently copy SV, Z-A, other-generation exception lists or formulas** into Champions.

| Rule ID | Primary Bulbapedia reference (Champions-specific text) | Fixed master | Included contract |
|---|---|---|---|
| `PROTEAN_FIRST_MOVE_TYPE` | [Protean](https://bulbapedia.bulbagarden.net/wiki/Protean_(Ability)) — Champions flavor text | `Pokemon_Champions_MC_ability_master.csv` | First successfully executed ordinary move after entry changes holder to move's type once; later move does not re-trigger; switching out and entering anew resets charge |
| `PROTEAN_MEGA_GRENINJA_RESET` | [Protean](https://bulbapedia.bulbagarden.net/wiki/Protean_(Ability)) — Pokémon Champions effect | Ability master + available roster | **Greninja-specific only**: after consuming Protean, Mega Evolution restores Water/Dark and grants another Protean use |
| `KNOCK_OFF_HELD_ITEM` | [Knock Off](https://bulbapedia.bulbagarden.net/wiki/Knock_Off_(move)) — Champs description and Pokémon Champions update | `Pokemon_Champions_MC_move_master.csv` | +50% power for a target holding a **known removable item**, removal on successful contact even if user faints due to defender's contact retaliation |
| `WHITE_HERB_ON_LOWERED_STATS` | [White Herb](https://bulbapedia.bulbagarden.net/wiki/White_Herb) — Champs description | `Pokemon_Champions_MC_usable_held_items.csv` | At a **specified post-stat-drop evaluation point**, restore negative stat stages to 0 and consume once, retaining positive stages |
| `CHOICE_SCARF_MOVE_LOCK` | [Choice Scarf](https://bulbapedia.bulbagarden.net/wiki/Choice_Scarf) — Champs description | Held items master | While held, Speed ×1.5; **successfully executed** first move causes fixed move lock until holder switches out; proposing a move alone does not lock |

The machine-readable source ledger is `champions_rule_contracts.json`, with `claims`, `test_scope`, `not_verified`, source URL, section and evidence type for each rule. Where Bulbapedia provides only a Champions item description without precise event timing, do not infer multi-hit, on-hit, contact, or simultaneous-effect priority.

## Implementation

- `champions_rule_reference.cjs` — five narrow pure transitions, no imports from Showdown. Every supported result explicitly returns `SOURCE_RULE_SIMULATION_ONLY`.
- `test_champions_rule_reference.cjs` — 16 isolated regression tests for first/second use, switch reset, Mega Greninja, held/no item, successful/unsuccessful hit, attacker fainting from contact effects, unknown removability rejection, White Herb single use, Choice Scarf lock, incorrect inputs, ledger provenance, and existence in the repo's fixed M-C masters.
- All inputs are fully specified by the caller. Missing state is an error; effects outside the cited Champions text are `UNSUPPORTED`. This engine is not to be called by the live battle assistant.
- The existing Phase 0 Node test command is extended to run this suite. No changes to `wiki_damage_reference.cjs`, private Golden fixtures, team construction, normal app behavior, Android flows, or live decision gates.

### Important verification boundary

The pure reference tests validate the *internal consistency of a narrow independently coded interpretation of documentation*. The separate event probe below now compares selected transitions with executed Showdown turns. Simultaneous mechanics, numerical damage distribution, temporary ability suppression, incompatible items, and undocumented exceptions remain outside the checked conditions. A discrepancy is not automatic evidence of a Champions game bug.

Examples of deliberately unsupported inputs:
- Knock Off against a target whose item removability is unknown, or where Sticky Hold/substitute/mega-item exceptions may apply.
- Protean failed moves, move-calling moves, dynamic-type moves, and any unspecified Mega Evolution other than post-Protean **Greninja**.
- White Herb timing relative to multi-strike attacks, stat-change abilities and item removal.
- Choice-item locking on an action that never executes, forced moves, item theft/removal during a turn, or exact speed rounding with other multipliers.

## Reproduction in WSL/Docker

From the repository root, with the pinned Phase 0 Docker image:

```bash
docker build -f poc/showdown_phase0/Dockerfile -t champions-phase0 .
docker run --rm --network none --read-only --entrypoint node champions-phase0 \
  --test test_champions_rule_reference.cjs
```

These are **synthetic reference-model tests**. Passing is not equivalent to Champions real-device verification. Current gates remain **Gate A=UNKNOWN, Gate B=UNSUPPORTED, adoption=NO_GO**. The output does not include user screenshots, match histories or local personal Golden JSON.

## Executed-turn event probe (Issue #139 successor)

`champions_event_probe.cjs` runs real turns in the pinned `gen9championsbssregmc` battle. It uses the pure functions in `champions_rule_reference.cjs` for expectations, then independently reads battle state, the turn log, and the next move request. Teams are checked against the fixed Champions usable roster, ability list, held items, move master, linked learnsets, pinned Dex, and format TeamValidator before play. The Mega probe wraps `runMegaEvo` only to take a snapshot immediately after that transition; the original function still executes. There is no call to `getDamage` for event classification.

With seed `[1,2,3,4]` and Showdown SHA `ccfaa20f32af363569cd21a9d501b2bc1ded26b1`, the 18 diagnostic records are **14 WIKI_SPEC_MATCH / 0 WIKI_SPEC_MISMATCH / 4 INCONCLUSIVE**. **Evidence basis is separate from status:** 13 records inspect executed turns, 1 inspects the next move request after an executed turn (`scarf-other-move`), and 4 are `SOURCE_ONLY` inconclusive declarations without an executed comparison. These are synthetic Showdown observations or source-only declarations, not actual-game observations. The matched fields are:

| Rule | Checked turn results | Remaining limits |
|---|---|---|
| Protean first move | Brick Break changes type before damage, second Knock Off does not reactivate; switching out and back restores base typing and allows reactivation | Same-type charge consumption and failed/dynamic-type move timing are `INCONCLUSIVE` |
| Mega Greninja | Initial Ice Beam changes type; after Mega, the immediate snapshot is Water/Dark and subsequent Dark Pulse reactivates Protean | Direct internal charge is inferred from the later action; other Mega timing is untested |
| Knock Off | Rocky Helmet removal, empty-item case, and removal after the attacker faints from contact retaliation | Unknown removability and the 1.5 power multiplier are `INCONCLUSIVE`; one random hit does not isolate power |
| White Herb | First Close Combat drop is cleared and item consumed; second drop persists without the item | Simultaneous effects and the intermediate activation frame remain untested |
| Choice Scarf | Speed is within integer rounding of ×1.5; used Earthquake locks, Dragon Claw is disabled in next request, and switching clears lock | Exact rounding with other modifiers and selected-but-unexecuted actions remain untested |

Each JSON record contains pre-state, requested actions, reference post-state, observed state, matched/mismatched fields, turn events, source URL/section, and unresolved conditions. `INCONCLUSIVE` records contain a reason and `UNKNOWN`/`UNSUPPORTED` auxiliary status. `WIKI_SPEC_MATCH` applies only to the fields listed in that record. The source ledger priority now explicitly includes official Pokémon HOME, and move names/IDs are resolved through the fixed Champions MoveKey master before reference locking. Unknown IDs and unlearnable fixture moves are rejected.

```bash
docker build -f poc/showdown_phase0/Dockerfile -t champions-phase0 .
docker run --rm --network none --read-only --entrypoint node champions-phase0 champions_event_probe.cjs
docker run --rm --network none --read-only --entrypoint node champions-phase0 --test test_champions_rule_reference.cjs test_champions_event_probe.cjs
```

Regression acceptance: `champions_event_baseline.json` explicitly pins each of the 18 case IDs, its rule, expected result and evidence basis. Node regression tests fail on missing/duplicate IDs, unexpected mismatch, newly inconclusive status and basis changes. Linux CI compares the diagnostic JSON against the independent checked-in baseline and emits GitHub Actions warnings for known `INCONCLUSIVE` results plus a failing error for any unreviewed change; changes to that baseline require review. The event diagnostic CLI is for discovery and is not by itself a passing acceptance gate. An observed WIKI mismatch is **not** automatically a Champions game defect.

Gate A remains `UNKNOWN`, Gate B remains `UNSUPPORTED`, and adoption remains `NO_GO`. The next separate Gate B step is to restore a synthetic **midbattle** state, clone it at the same RNG state, continue both branches, and verify divergence and unknown-state handling without treating a start-to-finish simulation as restoration proof.
