# Issue #141 Phase 2 ② — frozen OCR observations → bounded diagnostic branches

## Scope

This PR only adds a **read-only, non-executable** gate between the
existing linked ObserverPipeline / `apply_local_ocr` reducer, frozen
`FrozenBattleState`, verified four-master team IDs,
`stage_observed_calculation_scenarios`, and optional source-linked
co-occurring opponent joint-three records.

`stage_safe_observed_branches(...)` performs **fresh** staging on each
call. When a linked observer is supplied, the capture matching
`match_id`, exact frozen state JSON, acknowledged tactical revision,
source-linked OCR evidence and same preloaded assets are mandatory. A
snapshot without a linked observer can be staged only as **provisional,
not capture-authenticated**. No new OCR or screenshot logic is invented.

Every source-linked scenario remains present in the original order. An
empty joint-source tuple is represented as exactly one
`opponent_joint_three_and_all_hidden_sets` UNKNOWN case; it is never a
default opponent team. Corrupt/contradictory joint candidates,
snapshot/revision mismatch, changed pre-staged envelope or request over
the explicit 1–16 branch budget cause **whole-batch rejection**, never
selective dropping of an unfavorable opponent.

Each `ObservedSafeBranch` retains:
- the **exact frozen** observation constraints JSON and source-linked
  joint assumptions/evidence, separately;
- an enumerated set of fields actually observed (own registered
  species/move IDs, explicitly exact HP/max/FAINT/stages, raw opponent
  literals);
- a conservative, explicit set of unresolved facts: missing HP and
  maxHP, PP and lock/disabled, absent boost stages, opponent name→
  Showdown species/party slots, additional hidden moves, item/status/
  ability/form/type engine-ID translation, event/mega history,
  uncaptured simulator RNG and unproven policy coverage;
- a deterministic case fingerprint and whole-batch fingerprint
  carrying match/window/revision/snapshot.

A literal opponent HUD `73%` or `0%` **is never treated as an exact
numeric HP or faint status**. An explicit same-actor FAINT observation
can independently establish fainted state. Any evidence of a known
boost stage does **not** imply unobserved boosts are zero.
No hidden-state distributions are multiplied from marginals.

The gate ALWAYS returns `ranking_status="NO_RANKING"`,
`ready_for_engine=False`, `full_state_reconstructed=False`,
`current_game_capture_authenticated=False`. Each branch always has
`battle_json=None`, `rng_state=None`, `p2_commands=()`,
`executable=False`; it cannot be sent to the pinned worker or
`one_turn.py`/LOCAL_SHADOW, and cannot replace LIVE/GPT/GUI output.
The `PROVISIONAL_OBSERVED_BRANCH_CONSTRAINTS` status **only** means
the constraints were staged conservatively. This is not a successful
simulation, a ranking or a complete Champions Battle reconstruction.

## Tests

`tests/test_issue_141_observed_safe_branch_gate.py` covers:
- all source-linked co-recorded opponent joint cases and stable source
  SHA; no source and missing actors remain unknown, no scenario omission;
- exact own ID/HP/boosts versus unknown opponent IDs, PP,
  unobserved boosts and raw Japanese mechanics;
- stale, truncated, modified and promoted pre-staged batches; over
  budget or contradictory joint sets fail closed;
- the **production local OCR reducer** with `73%` HP and
  explicit FAINT followed by `0%`;
- the **linked ObserverPipeline ingestion contract**, preserving the
  unchanged requirement that authoritative tactical revision is
  acknowledged before joining to the frozen snapshot.

Linux isolated and native Windows regressions are registered.

## Remaining Phase 2 ② work

This is a **bounded safety boundary**, not complete ② delivery. A
subsequent slice must supply independently evidenced runnable complete
Battle candidates or an explicitly supported limited calculation mode,
with per-branch compatibility guarantees and a total
`NO_RANKING` fallback whenever unknown PP, opponent IDs, RNG,
mechanics or source completeness prevents sound comparison. Do not
manufacture a full Battle to make the test pass.

Then ③ integrates the verified complete action frontier and actual
one-turn comparison; ④ completes all 18 matchups; ⑤–⑦ add same-revision
shadow logging, time/safety metrics and actual mirrored Android game
evaluation. Existing GPT/LIVE recommendations remain untouched.

Parent #138 `SHOWDOWN_ASSUMED` allows further development without
pretending that full Champions parity or partial-to-complete Battle
reconstruction has been proved. The updated #138/#99/#133 90-second
shared selection/PREP design supersedes the old 40-second hard stop;
this PR makes **no timing change**.

Merge only into `integration/issue-138-showdown-local-engine`, after
independent review and exact-head Linux, Ready Windows full workflow
and required jobs success with actual checkout SHA and final-head
check. Never merge automatically.
