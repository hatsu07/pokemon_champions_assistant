# Issue #141 Phase 2 — observed opponent action frontier (step ③)

This PR follows merged PR #188, **not** a completed runnable live Showdown
state. It turns current-window observed Champions data and wholly sourced,
co-occurring opponent three-member sets into **per-hypothesis possibilities**
that a later audited engine replay builder can constrain against.

## Supported end-to-end staging

`stage_observed_opponent_action_frontier(snapshot, frozen_assets,
joint_candidates=..., observed_batch=...)` re-runs the existing
`stage_observed_calculation_scenarios` contract over exactly the same
window, four frozen asset sources and **entire** sourced joint batch. When
a precomputed stage-② batch is supplied, it must be exactly equal to
the fresh result. A mismatched or edited stage-② batch returns
`NO_ACTION_FRONTIER`; there is no "valid subset" fallback.

For each sourced opponent three, the read-only frontier includes:

- The current opponent by its literal game-visible display name, never an
  implicitly invented Showdown species ID or party slot.
- Individual possible move **display names** from the recorded *joint*
  set, tagged whether an exact move was previously observed. A revealed
  move must occur in every accepted joint set, or the existing opponent
  evidence gate rejects the whole batch.
- The other two members of the same recorded three as conditional switch
  **display names**. A bench member with directly observed zero HP is
  excluded. Unknown bench HP does **not** become an assertion of a
  survivable/available switch; trapped/disabled/replacement mechanics stay
  unknown.
- Complete, unmodified candidate source refs, regulation and retrieved
  timestamps inherited from the accepted joint records; each case retains
  its own ID. Independent Pokémon/move/item/ability usage-rate marginals
  are prohibited. No arbitrary Cartesian product or likelihood is made.
- A hash of the entire source-bound frontier, match, window, current
  tactical revision and snapshot SHA. Repeated reads are deterministic.

Without a source-bound complete opponent joint set, the frontier has one
explicitly **unknown** case with no generated move or switch names.
Missing current opponent, invalid observed state, source contradiction or
an altered/stale precomputed batch fail closed.

## Legality, simulator and current-game limits

Every `OpponentPossibility.legal_status` is **UNKNOWN**. The output
has `p2_commands=()`, `exhaustive_legal_choices_verified=false`,
`executable_battle_reconstructed=false`, `ready_for_engine=false`.

**Move name is not a legal action**. This stage cannot know current PP,
disabled/locked moves, trap/forced-switch state, priority, opponent
Mega availability, special actions or all legal Showdown commands. It
does not infer the current p2 party slot or a Showdown move ID from
Japanese display text. A sourced joint party does not establish
completeness of the opponent policy. The output cannot be passed to
`run_shadow_turn`, `ENUMERATE_P2` or GUI recommendations.

The separate existing `LocalSimulatorWorker.enumerate_p2_from_replay`
accepts only a **complete** externally supplied
`FULL_REPLAY_SYNTHETIC` Battle with RNG and actual engine p2
request. Its engine-checked results are still provisional and not
exhaustiveness proof. Do **not** attach such a replay to these partial
observations just to fill their gaps. The subsequent audit slice needs
an independently verified Champions-to-Showdown full-state constructor
or preserved event replay, explicit p2 roster/identity and current
choice-request mapping, RNG/effect timers, and a separately reviewed
completeness policy before any opt-in strategy orchestration.

## Tests and change isolation

`tests/test_issue_141_observed_opponent_actions.py` tests two distinct
co-recorded joint opponents, full-batch rejection, no source/unknown
case, stale/edited stage-② input, observed KO bench exclusion, source
evidence and deterministic fingerprints, and absence of executable
commands. Registered in Linux isolated and Windows desktop jobs.
No raw gameplay frames, user accounts or image bytes are added.

This change does **not** modify the real observer/LM/GPT/GUI decision
path, the Windows app controls, the pinned Showdown worker, or the
existing `run_shadow_turn` engine policy.

Merge only into `integration/issue-138-showdown-local-engine` after an
independent review and successful full workflow/required jobs on the
exact current HEAD for both Linux and Ready-PR Windows, including
checkout SHA verification; never auto-merge.
