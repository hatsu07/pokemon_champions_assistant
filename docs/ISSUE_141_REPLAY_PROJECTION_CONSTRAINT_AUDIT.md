# Issue #141 — replay projection consistency without reconstructing RNG

After merged PRs #188/#189, the current-state path stages real-screen-derived
Champions visible facts and per-hypothesis **display-name** opponent actions.
It still does not know the full opponent 3-party order, moves/PP, RNG,
event queue, or a verified exact Champions↔Showdown translation.

## New read-only experimental gate

`audit_observed_replay_projections(snapshot, assets,
joint_candidates=(...), projections=(...))` consumes a frozen
`FrozenBattleState` and exactly one **caller-supplied**, all-six-member
`ReplayProjectionCandidate` for *each and every* previously source-checked
co-occurring opponent joint-three hypothesis. It re-runs stage ② before
comparing and rejects the entire batch if the candidate count, IDs,
order, window, revision, snapshot or engine SHA differ.

For each claimed p1 projection, it checks exact frozen own Pokémon
name/order, explicit own species and move IDs from the four frozen
masters, known exact HP/maxHP/faint and explicitly recorded stat stages.
It checks a claimed p2 display-name trio is exactly the corresponding
source-linked joint three, its claimed active side/slot corresponds to the
observed current opponent display name, and any **actually observed**
exact opponent HP/FAINT/stages matches. The p2 display-name→slot and
species-ID association is **caller asserted, not independently verified**.
No opponent Japanese display text is translated into a simulator ID.

The gate preserves raw opponent percent (`73%` or `0%`) as
unverified, **never** infers an exact HP, maximum HP or 'fainted' from
the percentage alone. An explicit same-actor FAINT may independently
establish `fainted=True`, which must be consistent with the claimed
replay (but still does not establish exact HP from the percentage).
Unknown status/type/form/item/move IDs remain unchecked instead of
being filled with guesses.

All accepted input is a **claimed projection**, not a confirmed
`Battle.toJSON` from the pinned engine, not an authenticated OCR event,
and not a Replay→Champions match guarantee. Even if every comparable
field matches, return only `PARTIAL_REPLAY_CONSTRAINTS_MATCH` with
`engine_battle_authenticated=False`,
`opponent_slot_mapping_authenticated=False`,
`full_state_reconstructed=False`,
`exhaustive_opponent_policy=False`, `ready_for_engine=False`.
**No complete Battle JSON, RNG, engine choice commands or runnable
simulations are returned.** These fields must never be promoted to
`FULL_REPLAY_SYNTHETIC` or `LOCAL_VERIFIED`. Engine-origin and
unobservable RNG evidence require a *separate* audited primitive.
Do not feed this output to `run_shadow_turn`, the Showdown worker,
LIVE or the GUI.

## Tests and next dependency

`tests/test_issue_141_replay_projection_audit.py` uses entirely synthetic
full six-member candidate projections and exact stage-② constraints.
Checks normal current p1 and p2 HP/stages and all three own species/move
IDs, KO and 0% ambiguity, a stale snapshot, swapped or missing
hypotheses, partial/duplicate teams, invalid formats and mismatching
active side/slot. One mismatch refuses the whole candidate batch.
Included in Linux isolated and native Windows desktop contract tests.
No gameplay frames or private game data are committed.

**Next**: derive projection directly and read-only from a *verified
pinned-Showdown* Battle instance with a trusted exact battle digest;
separately validate display-name→opponent engine IDs and each active
slot from actual-game evidence; carry all coexisting hidden-set/RNG
branches without assigning unsourced likelihoods. Only after both
passes can an opt-in bounded replay consistency path be considered,
still not Champions mechanics parity proof.

Merge only to `integration/issue-138-showdown-local-engine`, and only
after independent review and same-HEAD Linux/Ready-Windows full CI and
required jobs success with actual checkout SHA and no review blockers.
