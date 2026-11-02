# Phase 2 issue #141 — Observed-opponent joint-hypothesis evidence gate

**Scope:** add a read-only preflight between already-frozen Champions UI evidence and explicitly supplied *joint opponent hypotheses*. This is an incremental guard, **not** a claim of game→Showdown full-state restoration, a set generator, or full Phase 2 acceptance.

## Trusted source and model input

The current `battle_evidence.py` updates the frozen `state["battle_evidence"]` from observed UI events, retaining per-opponent `moves_by_pokemon`, `item_by_pokemon`, `ability_by_pokemon`, `form_by_pokemon`, `current_type`, `status_by_pokemon`. These are literal game-visible strings, not automatically matched English Showdown IDs. The known active opponent is `state["opponent_active"]`.

A `Hypothesis.assumptions_json` may include an **explicit joint** schema, illustrated below with *synthetic names only*:

```json
{
  "opponent_sets": {
    "相手ポケモン名": {
      "moves": ["観測された技", "追加の仮定技"],
      "item": "観測された持ち物",
      "ability": "観測された特性",
      "form": "観測されたフォルム",
      "current_type": "観測された現在タイプ",
      "status": "観測された状態異常"
    }
  }
}
```

All values are compared **literally** with game-visible strings. The gate does not translate names using SV/ZA/older-game movesets. A name mismatch, unprovided member or missing required field is **UNBOUND**, not guessed. A contradictory explicitly declared value is **CONTRADICTORY**. A hypothesis without this schema when any opponent evidence is known cannot be promoted to provisional tactical ranking. Unknown/unobserved fields are not invented or required merely to pass this check. With no opponent evidence at all, it reports `CLEAR/NO_REVEALED_OPPONENT_DETAILS`, which is **not confirmation that any unknown item, ability, move or Battle replay is correct**.

## Integration and fail-closed boundary

- `local_strategy/opponent_evidence_gate.py::check_observed_opponent_hypotheses(snapshot)` reads only the current `FrozenBattleState`; no I/O, Web, statistics, screenshots or mutating evidence.
- `shadow_runner.run_shadow_turn` invokes the check **before any worker calls**. `one_turn.compare_shadow_turn` independently invokes it to protect direct invocation.
- The check distinguishes an absent `battle_evidence` key (no recorded details) from a present but corrupt value: `[]`, `""`, `0`, `False`, and `None` are all **INVALID_OBSERVED_OPPONENT_EVIDENCE**, never silently coerced to empty `{}`. A truly absent key with no other observed opponent details remains `CLEAR/NO_REVEALED_OPPONENT_DETAILS`; that does not certify a real set.
- The check processes **every registered joint hypothesis**. If even one set is missing evidence or contradicts any observed move/item/ability/form/type/status, the entire calculation returns `NO_RANKING` with a stable reason (`JOINT_ASSUMPTIONS_UNBOUND`, `JOINT_ASSUMPTIONS_CONTRADICT_OBSERVATION`, `INVALID_OBSERVED_OPPONENT_EVIDENCE`, etc.). Never silently drop a losing hypothesis from a comparison grid.
- The unique joint hypothesis-ID gate from PR #176 is preserved. This evidence gate does not certify the initial Battle JSON, the simulated p2 choice legality, the opponent's unknown moves, or Showdown/Champions mechanical parity.
- No change to the production LIVE/GPT path, Windows GUI, automatic input control, local file loading during a match, or the existing four-master preloader.

## Synthetic regression

`tests/test_issue_141_opponent_evidence_gate.py`: empty evidence; two consistent joint sets; conflicting revealed item, move, ability, form, current type and status; missing item/member/legacy unstructured hypothesis; an observed actor with no joint sets; malformed observed move list; independent `compare_shadow_turn` call; blocker before all worker calls, and no invented moves from marginal adoption data. Review P2 regression additionally tests all five explicit falsy corrupt `battle_evidence` values, zero worker calls, no provisional best action, and direct comparison guard; separate missing/empty-dict positive control. CI on Linux isolated and Windows desktop.

## Still required

- A Champions-specific source of **valid complete joint opponent sets**, without multiplying marginal usage stats into joint probabilities; source/date/regulation/evidence refs and hypothesis binding.
- An authenticated complete Showdown Battle replay (including exact RNG state) and an opponent legal-choice enumerator from the correct pinned Showdown implementation.
- A source-specific name/ID mapping verified against current Champions moves, items, abilities and forms rather than importing past-game data.
- Real single/three-turn policy, 18-matchup PREP completion, Shadow vs GPT analysis and Windows game-display E2E. This PR **does not** generate unseen sets, simulate a real screenshot, or certify winning odds.

No user-provided live screen, WSL, VS Code or Codex is necessary to test this isolated guard. Real game captures and the native Windows environment become necessary when we validate the OCR→battle state→worker→display integration.
