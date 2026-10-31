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

**This PR validates the *internal consistency of a narrow independently coded interpretation of documentation*. It does not yet compare each transition with an executed Showdown turn.** Simultaneous mechanics, in-engine event order, numerical damage application, temporary ability suppression, incompatible items, game-specific mega conditions, and all undocumented exceptions are explicitly outside the contract. A future separate pinned Showdown event-probe must classify results as `WIKI_SPEC_MATCH/MISMATCH` versus `UNKNOWN`; a discrepancy is not automatic evidence of a Champions game bug.

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

## Next bounded step

Add event-level observations from the **pinned Showdown model** for the same events without deriving expectations from simulator output. Preserve source-text expected poststates separately, compare to resulting simulator state, and record only the actually checked conditions. Give priority to Protean's use limit/Mega reset, Knock Off removal despite contact retaliation, White Herb negative-stage reset, and Choice Scarf move lock and switch reset. If the simulation cannot supply a valid exact event state or a Champions-specific source is missing, return `INCONCLUSIVE` and request no dedicated match.
