# Phase 2②〜⑥: selection→turn, opt-in offline evidence audit

**Status: isolated contract implementation, NOT real-game acceptance.** This
stage orchestrates the existing Phase 2 components for one match; it neither
provides Champions-valid local recommendations nor registers a LIVE/GUI/GPT
callback. `NO_RANKING` and `phase2_accepted=False` are mandatory.

## Timing: explicit user decision 2026-11-03

- **Remove any separate mandatory 40-second PREP hard cutoff.**
- Selection + PREP have **one shared 90-second budget**, starting at the first
  TEAM_SELECTION screen, not when the player confirms their selected three.
- After selection 10/35/70 seconds, the remaining target for PREP is
  respectively 80/55/20 seconds.
- Passing the 90-second target is a **soft overtime diagnostic**, not an
  automatic cancellation of a valid in-flight AI request. An evaluated,
  otherwise fresh 18-pair PREP may remain structurally valid but is **not**
  counted as an on-time success.
- A confirmed enemy lead/battle screen stops PREP **immediately**, even with
  time remaining. Subsequent stale PREP responses cannot be adopted.
- Every LIVE turn has its own 45-second game input budget; it does not extend
  the selection/PREP budget.
- Existing `prep_budget_before_battle` in `match_replay_dialog.py`
  already reports the shared 90-second target as an advisory timer. The
  offline receipt must independently contain the selection-screen start,
  selection confirmation, assessment completion and (when observed) opponent
  lead entry. No artificial common selection/turn `state_revision` is used.

## Cross-phase offline audit

`local_strategy.phase2_match_audit.audit_offline_phase2_match`:

1. Accepts an explicit, frozen **selection** receipt tied to the same match,
   own team SHA and four master SHA as a later frozen **turn**. These are
   different decision windows, so matching their revision numbers is neither
   required nor a guarantee. **P2 review fix:** before PREP/worker/SQLite,
   require the six selected-opponent preview names to match the frozen turn's
   `opponent_six` exactly as an unordered set of six distinct species.
   Normalize only through the packaged, approved Champions species aliases;
   no fuzzy/speculative OCR completion. A missing/partial/duplicate/unknown
   turn preview, one changed species, or a currently observed opposing active
   Pokémon outside the selected six blocks the entire audit. Display order
   alone does not invalidate a verified six. Even a matching frozen pair is
   labeled `FROZEN_SIX_MATCHED_NOT_GAME_AUTHENTICATED`, **not** independent
   proof that the capture really came from that in-game opponent team.
2. Runs the existing **②** observed partial-constraint / linked OCR source
   gate. Invalid joint source, stale state, changed team or provenance refuses
   the batch before invoking the synthetic model or SQLite writer.
3. Runs **④** matrix-first first-six + bench-twelve and optional external
   18-pair structural evaluation, preserving processor vs safe receiver,
   unresolved opponent max damage, roles and soft-overrun flags. No source
   certifies an immediate KO or safe switch.
4. Runs **③** only when an **explicit complete hypothetical Battle and
   source-linked joint set** have been supplied, without inventing a
   Champions Battle/RNG or claiming exhaustive enemy legal choices. The
   underlying readout / ENUMERATE_P2 / SIMULATE SHA and all-case guards remain
   unchanged.
5. Runs **⑤** only in an explicitly opted-in offline path. The current
   revision is rechecked before and after the synthetic task and the result
   is recorded in the privacy-limited SQLite v2 format. Missing synthetic
   inputs produce an abstention, never a forged zero-latency successful
   model decision. GPT source free text is NOT persisted.
6. Performs **⑥** SHA-bound golden audit when independently supplied
   expectations exist. Synthetic-only evidence does not prove real-game
   quality or Windows display-to-input latency.

## Acceptance blockers that MUST remain visible

- Independent true Champions state/ID, move slot, hidden set and RNG evidence
  cannot currently be established from a visible OCR frame.
- Showdown-issued P2 options for *hypothetical* state do not prove full
  Champions enemy legal action or type-policy coverage.
- Matrix hints and structurally complete 18 pairs do not prove tactical
  damage, safety or win-condition accuracy.
- Production same-revision GPT and LOCAL_SHADOW callback integration, and
  actual Android→Windows GUI display times, remain unverified.
- Real-game independent golden outcomes and safe nonzero applicability are
  missing; all-abstention can never pass Phase 2 quality acceptance.

The isolated harness is **not imported by the LIVE GUI**. Tests use synthetic
data and a mocked worker, not real Champions. Integrating a local engine into
production advice must wait until the required tests, same-head CI and
independent review, real-game evidence and Phase 2 acceptance are complete.
Leave #141 open and #142 Phase 3 inactive.
