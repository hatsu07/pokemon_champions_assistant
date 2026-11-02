# Issue #141 Phase 2② — all-sourced-hypothesis synthetic replay consistency

This is an **offline diagnostic integration**, not a real-game
Battle constructor, damage recommendation or a runnable engine command.

It follows merged PRs #190 (partial observed-vs-external projection
audit), #191 (real six-member pinned Battle readout), #192 (nonce- and
window-bound complete synthetic worker RPC, exact UTF-8 Battle input SHA),
and #193 (current linked OCR→bounded all-case unknown-safe branch gate).

## What the join actually does

`compare_observed_to_synthetic_replays` takes a single frozen match/window
with the current four-master IDs, a complete batch of source-linked
co-occurring opponent joint-three records, a separate explicit
hypothetical **complete synthetic Showdown Battle per hypothesis**, its
separate **caller-claimed** p2 Japanese display-name→slot mapping, and a
`LocalSimulatorWorker` instance.

1. It freshly stages #193's observed branches, optionally requiring
   **linked real OCR reducer / capture receipt / authoritative tactical
   revision acknowledgment**. The snapshot and source joint hypotheses
   must pass existing observation/regulation and contradiction gates.
   Hypotheses MUST be complete, in original order, 1–16 cases; one
   missing/altered hypothesis rejects all.
2. It preflights **every complete synthetic input** (JSON only, max 4MiB,
   no NaN/Infinity, three distinct nonblank p2 display-name claims)
   before invoking the worker. It never derives missing HP/PP/items,
   statuses, current moves, complete event history or RNG from OCR.
3. For each case, the **actual pinned local process** must return
   the #192 synthetic readout with original nonce/branch hash and
   current match/window/revision/snapshot, original submitted
   `battle_json` and exact UTF-8 input SHA. Local readout receipts
   are rechecked for binding and unique per-request nonce. They are
   not transferable cryptographic certificates.
4. The receipt's six real Showdown members are then sent to #190's
   whole-batch comparison through the existing #191
   `audit_claimed_engine_readouts` adapter. Exact observed P1
   registered species and ordered moves, HP/max/faint, observed
   stages, observed opponent exact values and *claimed* active
   display slots must match. Raw percent/unknown Japanese
   status/type/item/move aliases **cannot become exact engine facts**.
5. A single engine refusal, invalid receipt, wrong actor/HP, stale
   revision, mismatched source, deadline, candidate omission or
   conflicting p2 slot claim returns `NO_SYNTHETIC_REPLAY_COMPARISON`
   with **zero accepted cases**. There is no last-known-good or
   best-looking subset to give a tactical ranking.

On full consistency, `SYNTHETIC_REPLAYS_MATCH_OBSERVED_CONSTRAINTS_ONLY`
returns only source-order hypothesis IDs, matching Battle hashes and
the **observed fields actually compared**. Each case still retains
the #193 UNKNOWN context; no full Battle JSON, RNG or p2 commands is
exported. Always:

- `ranking_status="NO_RANKING"`
- `game_battle_authenticated=False`
- `opponent_mapping_authenticated=False`
- `full_state_reconstructed=False`
- `exhaustive_opponent_policy=False`
- `ready_for_engine=False`

A hypothetical complete Showdown Battle can fit the visible Champions
facts, but that **does not prove** it is the actual game state. It also
does not validate the caller's hidden p2 species, exact unrevealed moves,
joint coverage, unseen PP/locked actions, effects, RNG nor game parity.
The `SHOWDOWN_ASSUMED` development policy does not change this.

## Tests / future step

`tests/test_issue_141_observed_synthetic_replay_bridge.py` exercises
whole-batch success, all-case rejection, wrong HP, flipped/duplicate
candidate IDs, malformed mapping, source/case SHA and replay nonce,
stale identity, worker refusal, raw nonfinite JSON and 4MiB limit.

The pinned-Showdown real-engine CI extends
`tests/test_issue_141_real_worker_python_e2e.py` with a **real pinned
Battle.toJSON→manifest-checked Node worker→Python batch-join** case
using separately claimed p2 labels and no inferred real-game RNG.
Linux isolated and native Windows contract suites run the pure
orchestration regression.

**Remaining #141②:** validate a bounded, gameplay-relevant subset of
actual OCR scenarios with independently grounded executable state and
legal policies before allowing opt-in one-turn comparison. If unknown,
keep `NO_RANKING`. The next #141③ must integrate the whole
sourced opponent action frontier and measure full 3v3 one-turn
outcomes *without* promoting these unverified partial hypotheses to
game facts. Later #141④–⑦ cover 18 matchups, LOCAL_SHADOW,
performance/safety stats, and mirrored Android/Windows real-game E2E.

No production LIVE, GPT, GUI, LOCAL_SHADOW, simulator
`SIMULATE` or `ENUMERATE_P2` call path changes. This PR is deliberately
not the final acceptance of #141②.

Merge to `integration/issue-138-showdown-local-engine` only after
independent review, same-head Linux and pinned-engine CI, Ready
Windows full workflow/job success and final PR head consistency.
