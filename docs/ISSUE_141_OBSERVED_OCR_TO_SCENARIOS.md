# Issue #141 — combined stage ① and ②: observer OCR and scenario constraints

## In-process integration (①)

An explicitly enabled `LinkedObserverState` can now be passed to the real
`ObserverPipeline` constructor. The observer continues to ingest actual
RGB frames and persist its frame/ROI receipt through the existing local
SQLite ledger before invoking this bridge. The bridge receives **only**
that just-ingested, matching original frame and ROI text with its SHA
receipt. It checks the exact original pixels, each configured ROI hash,
match ID, frame ID and strictly rising capture sequence. No source label
is accepted as an independent authenticity certificate.

The bridge supports only explicit ROI→known field mappings:
battle_message, own/opponent name, own exact HP fraction, opponent HP
percentage, or one of the four own PP slots. The existing sample OCR
field parser parses numbers, and existing `apply_local_ocr` handles
actor evidence, stale switch HUD, ambiguity, confidence, and updates.
No independent battle-text mechanics parser is introduced. Unknown OCR,
unvalidated ROI, unsupported field names and duplicate/old captures do
not produce a confirmed game observation.

The bridge's `state` is **detached** from the production GPT/LIVE state.
Its optional mode is disabled by default. If a local OCR candidate
actually changes the detached state, the observer writes only its source
receipt ID and local OCR revision as a CANDIDATE into the authoritative FSM
evidence ledger, **incrementing the tactical revision and staling any
in-flight result**, rather than writing unverified HP/actor fields as
confirmed FSM facts. Observed revisions and evidence
are recorded with the actual frame receipt; source/time/actor authenticity
and current-window verification flags remain **false**. In particular,
this feature **does not** equate an unrelated FSM window version with
the OCR reducer's `_live_state_revision`. The authoritative state owner
must create a fresh `FrozenBattleState` from the same match, actor, screen
and current revision before using stage ②. The in-process connection
neither sends game input nor invokes the Showdown worker or GPT.

## Observed Champions → scenario constraints (②)

`stage_observed_calculation_scenarios(snapshot, assets, joint_candidates=())`
is a pure, read-only and deterministic boundary. It requires the existing
four-master SHA / current selected-own-three and explicit Showdown ID
verification via `stage_observed_champions_ids`. It keeps exact visible
HP, boosts, registered own moves, raw status/types/forms/items and
previously revealed opponents in a snapshot-sha-bound JSON constraint
envelope. Enemy species and move ID are **not** inferred from a Japanese
display name. A percentage HP cannot become exact Showdown HP.

With no opponent joint-set data, the stage creates exactly one *unknown*
scenario. When complete, co-recorded three-member candidate bundles are
provided, the existing `stage_sourced_joint_candidates` enforces
source/regulation/preview/observed evidence consistency for the **entire**
batch (never silently filtering inconvenient hypotheses). Per-scenario
source refs remain intact; adoption-rate marginals never become a joint
probability.

All output scenarios have `battle_json=None`, `rng_state=None`, no
p2 commands and `runnable=False`. The batch always has
`full_state_reconstructed=False`,
`exhaustive_opponent_policy=False`, `ready_for_engine=False`.
This is actual **scenario-input preparation**, not a complete
`Battle.toJSON`, a complete current 3v3 action history, proof of random
state or a permissible input to `run_shadow_turn`. A true runnable
Showdown midbattle scenario builder remains a separately auditable
dependency: observed event chronology, unobserved opponent set/slots,
effect timers, unseen RNG and move ID translation need explicit
bounded assumptions and real-game reconciliation. Do not rename
`PARTIAL_SCENARIOS_NOT_RUNNABLE` as `FULL_REPLAY_SYNTHETIC`.

## Explicit ① → ② join

`stage_linked_observer_calculation_scenarios` joins those two halves
only when the caller supplies a `FrozenBattleState` whose complete
observed-state JSON is **identical** to the current detached OCR reducer
state. It additionally requires the same match, the same frozen asset
object, the same active actor, **an acknowledged current authoritative FSM
tactical revision** (which prevents treating a new OCR update as an old
decision window), and at least one accepted OCR provenance
record tied to an in-process receipt recorded by the linked observer.
A stale or unlinked snapshot returns `NO_SCENARIOS`; no new decision
window is invented and no timestamp is treated as authoritative.
The game/window owner must supply a real decision window using its normal
snapshot-generation and legal-choice contract.

## Tests / release boundaries

`tests/test_issue_141_observed_ocr_scenario_bridge.py` tests the
actual ObserverPipeline→SQLite receipt→local OCR reducer connection
using synthetic frames; unchanged next-frame handling, same-match
binding, ROI digest poisoning and no default activation. It also tests
exact own-ID and visible HP staging, separate sourced joint scenarios,
unknown RNG/no fabricated Battle, invalid IDs, percentage HP rejection
and all-or-nothing failed sources. Linux Phase 1 and native Windows
offline test commands include these cases.

No game screenshots, user files or raw frames are committed. Current
real-game capture/OCR accuracy, current-window authority, complete
Champions parity, Windows end-to-end timing, and real game strategy
are **not** established by synthetic CI.

Merge only to `integration/issue-138-showdown-local-engine`, after
independent review and exact HEAD Linux/Windows workflow plus mandatory
job `completed/success` and actual checkout-SHA match. Never auto merge.
