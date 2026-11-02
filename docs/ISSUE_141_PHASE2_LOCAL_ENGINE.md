# Issue #141 — Phase 2 initial local strategy slice (PR #175)

**Design basis:** #174 (fixed Showdown as engineering default, parity evidence not a development blocker). Builds on merged Phase 1 PR #144. Scope of this PR: **pure local selection and comparison of one-turn synthetic Showdown results**. It is not Phase 2 acceptance completion and does not alter existing GPT/LIVE runtime.

## Implemented

### `local_strategy/selector.py`
- Takes the match-preloaded `FrozenMatchAssets` and a confirmed, distinct opponent six; does not access disk/Web/reload the lookup master.
- Checks exact registered-six LIVE matrix compatibility. Blocks unknown or provisional matrix cells.
- Calls `assets.matrix_rows_for(opponent_six)` **once**. Reuses its six rows to rank 20 three-member combinations × three leads = 60 choices.
- Ranks **coverage by explicit processor names** before ordinal lead/whole-roster quality. `insurance_only=1` never becomes explicit coverage. `recv=U` is unsafe and never considered a safe switch. Returns any uncovered opponent species.
- Output is `MODEL_PROVISIONAL`, ordinal tuple + original matrix rows + frozen team SHA; **not a probability, exact KO, safe hard-switch guarantee or player-facing automatic recommendation**.

### `local_strategy/one_turn.py`
- Receives current `FrozenBattleState`, fixed `FrozenMatchAssets`, and a set of `PARTIAL` one-turn `SimulationResult` branches produced by the pinned Showdown worker.
- Requires all current legal actions to be evaluated against the **same joint opponent hypotheses and candidate action scenarios**, including both known and hypothesis-dependent opponent choices. No independent adoption-rate products, imagined joint probabilities, or opponent perfect information.
- Validates all IDs: current window identity, request and snapshot SHA, action, hypothesis, branch nonce, deadline, frozen roster mapping. **Review P1 fix:** Phase 1 adapter attaches the exact JSON `SIMULATE` request **after its response echo passes validation**. The Phase 2 comparator checks the submitted request envelope (window/request/nonce), recalculates SHA256 of the full submitted payload (Battle JSON including any serialized RNG state, p1/p2 actual choices, hypothesis, candidate action and mapped p1 roster) and binds the p1 slot to the registered legal action. It requires `BranchObservation.opponent_choice_id` to equal the **actual submitted `p2_choice` string** (not a generic "attack"/"switch" label); otherwise it rejects the branch. Branches with the same hypothesis/p2 command must share **identical starting Battle/RNG bytes**, and a hypothesis reuses one starting Battle state across choices. Unbound/mismatched/missing submitted requests produce `NO_RANKING`. This is in-process request provenance, not proof of physical Champions game parity or tamper-proof external attestation. Validates 3-v-3 result schema and rejects malformed/contradictory HP and inconsistent opponent roster.
- Uses three-Pokémon ordinal result vectors prioritizing terminal win/loss, survival of the stopper/preserve roles, remaining own/opponent count, and whole-team HP. Detects when a remaining own Pokémon is not actually alive rather than awarding a false victory for dealing damage.
- Only selects a candidate when it **dominates every other legal candidate for each joint opponent scenario**; tradeoffs become `AMBIGUOUS_OPPONENT_POLICY` with nondominated choices, not a fabricated win percentage.
- Output stays `MODEL_PROVISIONAL`, no `LOCAL_VERIFIED` promotion, no GUI publication or silent replacement of GPT.

### CI
- Added `tests/test_issue_141_selection.py` and `tests/test_issue_141_one_turn.py` to isolated Linux and native Windows CI.
- Test coverage: explicit processing vs insurance, six-row single batch, unreviewed/unknown matrix, scenario dominance/tradeoffs, missing scenario, lost 1-v-1, stopper survival, stale/duplicate branch and invalid HP. **Additional negatives:** same label with different submitted p2 choice, changed initial Battle/RNG, fake 64-character hash, missing request provenance, substituted branch nonce or p1 move slot; Phase 1 adapter request round-trip checks.

## Not yet delivered (next Phase 2 work)
1. Showdown-driven **opponent joint scenario construction** from observed moves/items, unknown sets, and actual legal Showdown commands. Must not claim independent item/move usage rates as a combined probability.
2. Real branch orchestration through the Phase 1 worker with fixed team ID mappings, deadline budgeting, batch evaluation, and ordering of each `candidate_action_id`.
3. Full 18-matchup PREP with concrete evaluations meeting existing `prep.validate_prep_result`, plus stopper/preserve/closer roles. Existing matrix hints **alone do not certify PREP**.
4. `LOCAL_SHADOW` comparison with existing GPT outputs for the identical snapshot: log model reason, disagreement, latency, current-state freshness, and any observed tactical errors. GPT outcome is not truth ground-truth.
5. Integration with Windows GUI and Phase 3 offline runtime packaging/feature flag, Windows end-to-end capture→OCR→local decision→display, and continued discrepancy repair.

This PR is intentionally independently testable and reviewable. It does not claim a functional 90-second full selection/PREP pipeline, 45-second fully automatic turn advice, or complete Champions mechanics parity. Existing LIVE/GPT and manual game controls remain unchanged.

**Merge controls:** latest PR HEAD must pass Windows Desktop and Linux isolated CI, review issues addressed; no automatic merge by assistant.
