# Phase 2 #141: registered-team ID readiness (real blockers)

Status: **an off-match readiness tool, NOT completion of ②〜⑥.**

[PR #197](https://github.com/hatsu07/pokemon_champions_assistant/pull/197)
merged the cross-window offline ②〜⑥ audit. It intentionally keeps
`NO_RANKING` and does not map an incomplete Champions observation into a
complete Showdown Battle or start the local engine in LIVE.

## Confirmed blocker in the checked-in October 3 real team

`masters/Pokemon_Champions_MC_team_2026-10-03.csv` contains six actual
registered `MON` rows and 24 registered moves, but **does not include the
explicit `showdown_species_id` and `showdown_move1_id` through
`showdown_move4_id` columns** that the pinned worker
`_verified_roster_binding` requires. Therefore the worker must refuse an
actual roster mapping even though the synthetic E2E fixtures use populated,
internally consistent fake mappings.

Run this **before a match / out of battle** from the repository root:

```bash
python scripts/report_phase2_roster_readiness.py --masters-dir masters
```

Optionally inspect a registered team CSV selected from elsewhere:

```bash
python scripts/report_phase2_roster_readiness.py --masters-dir masters --team-csv path/to/registered.csv
```

The report lists missing fields without generating guessed IDs. For the
unchanged October 3 team the expected missing set is **six species IDs and
24 move-slot IDs**. A zero exit status only means these *strings are present
and structurally acceptable*, not that the IDs are correct, the simulator
supports the Champions mechanics, or that the game is safe to simulate.

Any remapping must be an explicit, separately reviewed Champions-specific
provenance decision under the frozen team SHA. In particular, a mapped
species with a Mega Z form should not be silently substituted by a different
Showdown Pokémon or a past game's mechanics. Unsupported forms or moves must
remain unsupported.

## Remaining gates after the roster inventory

- **②** Independently attest observed opponent species/slot and all
  necessary state; preserve unknown HP/PP/locks/form/RNG rather than
  fabricating engine Battle JSON.
- **③** Independently validate supported Champions player/opponent moves,
  their legality and coverage, and immediate-loss counterexamples. A
  Showdown-issued candidate for a hypothetical Battle is NOT exhaustive
  real Champions policy.
- **④** Independently verify first-six+bench-twelve tactical fields plus
  preserve/stopper/closer. Only a shared **90-second** selection/PREP budget;
  no independent 40-second PREP cutoff. Opponent lead supersedes PREP.
- **⑤** Integrate opt-in same-window, noninterfering GPT/LOCAL_SHADOW
  comparison with actual Windows captured events and privacy-safe SQLite v2.
  Do not replace the existing GPT response.
- **⑥** Independently adjudicated real-game Goldens, zero
  illegal/fabricated/known-immediate-loss suggestions, nonzero actionability,
  abstention, in-time display and P50/P95/P99; all abstention is not success.

The Android→Windows actual gameplay capture and human-reviewed outcome
evidence cannot be manufactured from repository fixtures or CI. Real-game
acceptance remains open, as does the separate #141⑦ device Shadow E2E.
Phase 3 #142 stays blocked.
