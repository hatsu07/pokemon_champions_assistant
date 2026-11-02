# Phase 2④〜⑥: isolated PREP18 / same-window SQLite / quality audit

This change is an **offline diagnostic contract**. It is not a finished
game-valid tactical engine, real-time PREP controller, or LIVE/LOCAL_SHADOW
routing. In particular the still-open acceptance conditions of #141②/③
prevent claiming Phase 2④〜⑥ completion for actual ranked play.

## ④ Conditional 18 matchup evidence extraction

`local_strategy.prep18_audit.stage_prep18`:

- Requires a truly frozen `FrozenMatchAssets`, a current compatible
  six-member registered team/matrix, three selected Pokémon, a selected lead
  and six exact known opponent display names.
- Reads **exactly six** rows from the in-memory LIVE matrix once, and
  evaluates all **lead-six + bench-twelve** pairs in deterministic order.
  Uses the matrix quality/speed/receiving/insurance flags, and only the
  explicit processor column; insurance and receiving do not establish a
  safe switch. Never guesses damage, priority, a move or an opponent set.
- Output `MATRIX_18_CONDITIONAL_ONLY` indicates eighteen frozen conditional
  evidence cells. This does **not** mean the lead's 11 mandatory tactical
  fields, the bench's 6 required fields or win-condition roles were assessed.
- With an independently supplied `PrepResult`, verifies all 18 rows using
  the existing `validate_prep_result`, concrete registered moves/first
  actions, selected preserve/stopper/closer, and refuses unproven
  `SAFE=safe` or `KO=confirmed` and unsupported processor promotion.
  A structural pass is `ASSESSED_18_CONDITIONAL_ONLY`, **not** proof of
  damage, safe switch or 18 game-verified matchups.

**Timing conflict requiring separate decision:** #141 and existing
#99/#133 code use a 90 s shared selection+PREP clock with no independent
40 s hard stop, whereas current project user instructions specify 40 s
mandatory post-selection PREP. This change introduces no new timer and
does not silently choose between the incompatible authorities. A single
approved production timing contract is required before runtime wiring.

## ⑤ Same-revision GPT / local diagnostic SQLite record

`local_strategy.shadow_comparison_store`:

- `build_shadow_comparison` requires two identical frozen windows and
  snapshot SHA, plus an externally supplied GPT answer bound to the exact
  same state. Team SHA, four-master SHA manifest digest, allowed-action SHA,
  evidence snapshot SHA, request/window/revision and source timestamp are
  recorded.
- A local result may be only a non-actionable
  `ObservedFullGridDiagnostic`: verified current match/window/revision
  and `NO_RANKING` with all game authentication/coverage flags false. It
  cannot become a certified local action or an artificial GPT agreement.
- `append_shadow_comparison` writes an append-only one-record-per-window
  SQLite table. Same data repeats idempotently; any changed evidence/timing
  or GPT result for the same match/window/revision **fails rather than
  overwriting**. Read-only retrieval is separate.
- No secrets, screenshots, raw OCR, full opponent sets, private model
  reasoning or production GPT action changes are stored.
- `offline_shadow_capture.capture_offline_same_window` is an explicit,
  opt-in demonstration of the whole trace: it invokes the synthetic
  diagnostic, measures elapsed milliseconds using the local monotonic
  clock, rechecks the original snapshot revision and writes one SQLite
  record only after the full calculation. If the worker abstains, the
  bound failure reason is logged as `NO_RANKING`; no successful prefix is
  promoted. Direct, forged `LOCAL_VERIFIED` records are refused by the
  SQLite write boundary itself.
- This is not yet a running LOCAL_SHADOW observer registered with the
  Windows GUI or production event callbacks. Offline computation time is
  not actual presentation latency in the GUI.

## ⑥ Quality and deadline metrics without false certification

`local_strategy.shadow_quality_gate`:

- Tracks independent SHA-bound synthetic/replay golden expectations for
  immediate KO, choice lock/PP, mega/form, priority/speed, processor vs safe
  switch, setup, final 1v1 and stale revision.
- Reports missing/mismatched goldens, number of examples, model-diagnostic
  coverage, true *actionable* coverage, abstentions, timely capture fraction
  and nearest-rank P50/P95/P99 local elapsed milliseconds.
- **Both** `MODEL_DIAGNOSTIC` and `NO_RANKING` mean no actionable
  Champions recommendation. Therefore the actionable count remains zero,
  even if every synthetic branch produces a unique nondominated action.
  All-abstention can never pass a quality gate.
- Game safety and true Android-to-Windows presentation latency explicitly
  remain `UNVERIFIED`; `pass_phase2_quality_gate=False` until independent
  game-valid goldens and actual UI timing exist. This prevents a synthetic
  "0 serious errors" from being advertised as production quality.

## Scope / next acceptance gates

- Tests in `tests/test_issue_141_phase2_456_audits.py` run under Linux
  and Windows contract workflows, plus unchanged full application CI.
- No out-of-match disk/web reads occur in PREP18, no live file reload,
  no GPT prompt changes, and no automated input. Existing Phase 2
  synthetic Showdown test and safety contracts remain unchanged.
- Required before claiming ④ complete: actual independent 18-pair tactical
  evaluations with grounded first actions, own/opponent maximum attacks,
  priority/speed and switch safety, verified roles, and the authoritative
  unified time budget. Required for ⑤: live observation callback hookup
  with true same-state GPT/LOCAL_SHADOW session provenance and SQLite trace.
  Required for ⑥: labelled real-game Golden scenarios, nonzero safe
  application, actual P50/P95/P99 and UI deadline distribution plus
  independent outcome/safety checks.
- Keep #141 Open; never enable Phase 3 recommendation adoption from these
  diagnostics. Review and current-head Linux/Windows CI are necessary before
  merging this offline contract PR.
