# Issue #141 — Stage 1 implementation checkpoint (#204)

> Status: staged code only. This is **not** a completed Phase 2 acceptance or a verified real Champions local advisor. Real-game independent evidence is tracked separately in #203.

## ② Source-bound static mechanics inventory

- `static_move_compatibility.py` reads the unchanged registered 6×4 team, matching Champions learnsets and move master; a byte hash binds fixed source. Unknown/ambiguous inputs are rejected.
- `report_phase2_static_move_compat.py` exports the 24 fields. The real pinned `champions` Dex workflow compares type, category, base power, accuracy, PP, priority and shows differing fields rather than claiming gameplay equivalence.
- A matching entry or static field is **not** an independent verification of actual move effects, Mega Lucario Z, hidden PP/RNG, legal moves, or HP. The original registered team still lacks explicit reviewed engine-ID fields. They must not be injected or inferred from foreign games.

## ③ 1-turn conditional engine

- Existing `observed_full_grid_diagnostic.py` / `shadow_runner.py` enumerate joint source-linked opponent hypotheses, engine P2 candidates and self-actions in fully *external synthetic* Battle states. If any candidate, branch, SHA, deadline, legal constraint or current revision fails, the result is `NO_RANKING`.
- Stage 1 has **not** invented a game Battle or RNG from OCR. Real-match tactical scoring remains blocked until independently supported.

## ④ PREP

- `conditional_preview_pipeline.py` returns three + lead using the frozen LIVE selector; the new `cached_matrix_rows_six` contract reuses the **same exact** six matrix rows in `stage_prep18`.
- `conditional_prep_builder.py` resolves only actual registered 4 moves, provides exactly 6 lead and 12 bench conditional rows plus *provisional* preserve/stopper/closer labels. It explicitly has `prep_status=DRAFT`, never `COMPLETE`. It does not turn matrix attack or processor hints into guaranteed KO or safe switch.
- 18 fully tactically **assessed** cells and verified maximum-hit/landing evidence are still separate; a conditional draft is not sufficient to pass #141④.

## ⑤ Opt-in same-window concurrent diagnostic

- `parallel_shadow_session.py` starts a bounded read-only local future without waiting for GPT. Once the independently produced GPT output arrives, only exact match/window/revision/frozen evidence is paired, using existing SQLite v2 schema with bounded reason codes.
- No branch evidence -> local `NO_RANKING`. Stale revision, invalid capture or session stop does not create a matched result. No raw GPT text, credentials or screenshots are persisted.
- **Default disabled**. The API accepts thread-safe frozen snapshots, and is not yet bound to an authenticated Qt/Windows production capture callback. A real GPT+LOCAL parallel-run record is therefore not claimed; real device evidence is #203.

## ⑥ Quality / CI

- Reuse existing Golden-eight, abstention, deadline, P50/P95/P99 and GUI audit code from #196/#201/#202. New tests cover real Champions fixed-master 24 fields, pinned Dex static comparison, 18 frozen-row selection/PREP reuse, same-window concurrent local/GPT pairing, and fail-closed stale inputs.
- Linux isolated Python/Node and separately pinned Showdown real-engine workflow are required; native Windows validation / independent reviewer checks remain outstanding on Draft heads.
- Neither a successful synthetic test, nor a static metadata match, nor a `NO_RANKING` log authorizes Phase 2 acceptance or Phase 3 rollout.

## Completion boundaries

Stage 1 code remains **in progress** until (a) the authenticated, limited Champions input/action mapping can be used without guessing, (b) supported 1-turn cases are evaluated by the actual coordinator, (c) all 18 tactical fields can be validly evaluated instead of only drafted, (d) the opt-in read-only dispatcher is connected to actual GPT+Qt inputs, (e) software quality contracts/Windows CI pass and review finds no P1/P2.

Full game acceptance, including user device evidence and independent adjudication, is **Stage 2 #203**. Maintain Issue #141 Open and #142 disabled until both gates are met.