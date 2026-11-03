# Phase 2 #141② — Champions master keys as OFFLINE mapping CANDIDATES

Status: **source-key inventory only; Phase 2②〜⑥ incomplete**.

## The actual registered October 3 team

PR #198 established that the registered six-player team CSV contains no
`showdown_species_id` or `showdown_move1_id` through
`showdown_move4_id`, although the pinned worker requires them.

The **Champions-specific** project masters additionally contain:
- `Pokemon_Champions_MC_move_master.csv`: Japanese move names → `MoveKey`.
- `Pokemon_Champions_MC_pokemon_learnsets_linked.csv`: base species and
  form rows → `LearnsetKey` and allowed `習得MoveKey一覧`.
- `Pokemon_Champions_MC_usable_pokemon.csv`: eligible listed species/form.

`champions_master_key_candidates.audit_champions_master_key_candidates`
joins those three source files with the *already validated frozen four-master
team*, using only exact names and an explicit fullwidth-to-ASCII decimal
digit equivalence. The result contains per-member and per-move candidates,
membership in the **Champions linked learnset**, source byte SHA-256, and
unmapped fields. It never writes into the registered team CSV.

**Source-key integrity gate (PR #199 P2 review fix):** Across the *entire*
Champions move master, require one-to-one mapping in both directions:
digit-width-normalized Japanese move name → `MoveKey`, and
`MoveKey` → normalized Japanese move name. A collision between two different
move names with one key invalidates the complete source inventory before any
6-member/24-move candidate coverage can be reported. For example, changing
トリックフラワー from `flowertrick` to `uturn` must be rejected even
when the registered マスカーニャ learnset contains both keys. A conflict in
a move *outside* the current team is also rejected. The actual master
source remains unchanged; a new test injects the bad mapping in memory.

The present source files supply **six base-species LearnsetKey candidates**
and **24 move MoveKey candidates**. Of particular interest:
- `10まんボルト` in the registered CSV equals `１０まんボルト` in the
  Champions move master under **decimal digit width only**, giving
  `MoveKey=thunderbolt`; the learnset lists that key.
- `ルカリオ → メガルカリオZ` has the base `LearnsetKey=lucario` and
  a distinct listed target form. The report explicitly marks the target
  `FORM_NOT_ENGINE_ATTESTED`; **do not map it to an old Mega Lucario**.

These are candidates extracted from a **secondary Champions data master**,
NOT authenticated Showdown-engine IDs for the running format, not an
independent legality proof, and not proof of identical move effects,
priority, target form, stats, held items, abilities or RNG. The original
master sources themselves are not automatically verified current official
Champions data. Even perfect source-key coverage ALWAYS returns
`showdown_mapping_verified=False`, `champions_mechanics_verified=False`
and `usable_in_live=False`.

## Usage (before battle / off-match)

```bash
python scripts/report_phase2_champions_key_candidates.py --masters-dir masters
```

Optionally add `--team-csv path/to/registered.csv` to audit another
registered team via the canonical preloader.

This command makes **no web requests** and is not called from LIVE selection,
PREP, battle, GPT, Windows GUI or the local worker. A zero exit code means
only that the source-key inventory is structurally complete; it is **never
permission for a game recommendation**. The script does not write a mapping
patch or modify any master.

### Next actual acceptance work

1. Independently review each of the 30 candidate key bindings **against the
   pinned Showdown implementation and current Champions source evidence**;
   retain source URL/date/regulation/approval. Clearly mark unsupported
   forms or effects. No automation should promote a candidate by name alone.
2. Produce a separately reviewed, SHA-bound mapping for the actual registered
   team. Then run the real worker's exact roster binding gate with those
   approved fields, not a synthetic fabricated Battle.
3. ②: verify the actual observed opponent identity/slot and partial game
   state without inventing hidden HP/PP/RNG. ③: establish a limited,
   independent Champions legal-action and opponent-policy coverage gate.
4. ④: independently adjudicate all 18 tactical PREP rows and three roles.
   Selection plus PREP use a shared 90-second clock with **no separate
   40-second cutoff**. Once the enemy lead appears, PREP is no longer valid.
5. ⑤: wire a privacy-safe, opt-in, non-interfering live GPT/LOCAL_SHADOW
   comparison; the existing offline SQLite record does not do this.
6. ⑥: collect independent real-game Goldens, actionability, abstention,
   safety and actual Windows display timing P50/P95/P99. Synthetic fixtures
   and the existing post-match battle log cannot establish these.

Keep issue #141 Open and Phase 3 #142 disabled until real-game evidence and
required CI/review acceptance are complete.
