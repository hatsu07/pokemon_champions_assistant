# Issue #140 Phase 1 implementation notes — isolated, not merged

Base: `integration/issue-138-showdown-local-engine`, after PoC PR #143.
Branch: `feat/issue-140-showdown-foundation`; Draft PR #144.
Parent: #138. Precondition: #139 Phase 0 independent A/B adoption gates.

## What is actually implemented

- Immutable JSON-backed `FrozenBattleState`, sourced `ObservedFact`,
  separate `Hypothesis`, `CandidateAction`, `SimulationResult`,
  `CapabilityStatus` and `DecisionWindow` contract.
- Identity includes match, input window ID + kind, turn epoch, revision,
  screen, active Pokémon, team SHA, four master SHAs, regulation, pinned
  simulator SHA, current allowed-action digest, request ID and deadline.
- Conservative action generation calls **the same**
  `match_replay.validate_battle_choice` as the existing GPT replay path.
  Validated absence of known illegal constraints is not legality proof;
  current observed actions, confirmed constraints, positive PP and screen
  support are required to report LEGAL. Else UNKNOWN or ILLEGAL.
  NO_ACTION is not emitted as a player-pressable command.
- Future pivot target and actual post-pivot target selection are **not** the
  same input window. Any pivot-success/replacement/turn change must create a
  new `decision_window_id` and digest, then revalidate.
- `accept_result` rejects old or changed identity, revision, window,
  digest, observations, hypothesis, deadline, illegal action or non-CONFIRMED
  calculations. It is a safety gate, not a tactical scoring algorithm.
- Explicitly opt-in Python `LocalSimulatorWorker` and pinned SHA Node JSONL
  process for full **synthetic/replay-only** snapshots. 1 MiB request/response,
  read-only worker, request serialisation, no silent restart, stale replies
  dropped, crash/timeout fail-closed. Node never certifies Champions mechanics.
- Offline Python/Node contract/failure tests and isolated Linux workflow.

## Important limitations and remaining acceptance gates

This Phase 1 code has **no production entrypoint or default enable flag**.
It does not replace tactical GPT calls, alter GUI/SQLite evidence, change
four-master preload, add autonomous taps or implement win-rate exploration.

Showdown Phase 0 #139 is **NO_GO**: actual Champions computation A=UNKNOWN,
partial-observation reconstruction B=UNSUPPORTED. The pinned synthetic
runner is not game proof. The Phase 0 100 unique representative case and
real-device golden tests, Windows-bundled runtime+licenses+SBOM, Windows
offline smoke and Android mirror→PySide6 E2E remain unverified.

There is a separate open policy discrepancy: parent #138 supersedes the
independent 40-second PREP stop with a shared 90-second selection+PREP
window. The project instructions and stored LIVE gate still include the
old cap. This branch neither duplicates #133/#136 changes nor updates
a canonical gameplay rule silently.

## Test and approval policy

Run in permitted WSL2/Docker development environment and inspect CI
on the current exact PR HEAD. `phase1-isolated-foundation` success would
validate only Python/Node contracts, **not** Windows runtime packaging,
real-game compatibility or tactical correctness. Do not merge PR #144
without explicit user instruction. No automatic use of PoC engine in matches.
