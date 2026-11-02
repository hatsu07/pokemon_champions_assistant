# Issue #141 — bounded pinned Showdown PP / lock / Struggle choice slice

## Contract

This PR extends the existing **synthetic, fully serialized, pinned Showdown
`gen9championsbssregmc` Battle** P2 choice enumeration. It does not use game
screens, infer opponents' unobserved moves or modify live GPT recommendations.
All candidate actions still pass a fresh, independent `Battle.fromJSON()`
and `p2.choose()` on the same complete Battle/RNG state, followed by strict
command-to-action identity checks.

Ordinary move requests retain the existing simple/Mega scopes and their
slot/ID checks. A Choice item or modern Encore can cause the real engine
request to mark alternative slots `disabled` while preserving the PP-bearing
move list. Those disabled slots must not be enumerated. An individual move
with `pp=0, disabled=true` must not be offered as an attack when other
moves remain available.

The real pinned engine has **two different PP-less one-slot requests**:

1. A *hard LockMove* (e.g. a synthetically applied Outrage lock) returns
   one locked move with no PP field, disallows switching and can produce a
   `Side.choose()` action without a `moveSlot` field. Admit it only when
   `p2.active[0].getLockedMove()` returns exactly that request move,
   the move remains in the actual active Pokémon's moveSlots, and the
   engine request explicitly says `trapped: true`.
2. Full PP exhaustion returns a single `struggle` move without a PP
   field. Admit it only if `getMoves()` gives no enabled moves, **every**
   original move slot has zero PP, no Mega/form modifier is advertised,
   and the request actually contains `struggle` with `disabled:false`
   and target `randomNormal`. Bench switches are retained when allowed.

These two modes share the deliberately narrow scope
`PINNED_SHOWDOWN_HARDLOCK_OR_STRUGGLE_SINGLES_ONLY`. The Python
`enumerate_p2_from_replay()` receiver supports that scope without
rewriting it. It must see `move 1` plus zero or more ordinary `switch N`
commands, reject extra moves and all unknown modifiers, and preserve the
preexisting snapshot/branch nonce, registered hypothesis, SHA, deadline,
non-exhaustiveness and provenance checks.

## What this does not certify

- It does not prove all possible choices, hidden set coverage, or
  Pokémon Champions game/parity accuracy; `exhaustive_legal_choices_verified`
  **always stays false**.
- It does not accept PP-less requests for unmodeled semi locks, recharge,
  forced moves outside the bounded `getLockedMove()` branch, or Struggle
  caused by Encore/Disable/Taunt with positive PP. Those fail closed.
- It does not enumerate X/Y Mega, Ultra/Z/Max/Tera variants, uncertain
  `maybeLocked`/`maybeDisabled`/`maybeTrapped`, complex switch or
  combined two-turn states. It does not reconnect the local engine to LIVE.
- The Choice item, Encore, PP drain, Outrage and Struggle test fixtures
  are **synthetic Showdown engine cases**, not Champions learnset or
  real-match claims. Controlled effect insertion is clearly labeled.
- The public status string remains `ENGINE_CHECKED_SIMPLE_CHOICES` for
  current transport compatibility, with a distinct scope for the bounded
  action class. Renaming the status alone does not prove completeness.

## Verification gates

Linux Node/fake worker + Python IPC, actual pinned upstream Showdown Battle
(and round-trip clone), and Windows Desktop regression/build/smoke workflows
must succeed against the latest PR HEAD SHA. A reviewed CI HEAD and actual
checkout SHA must match; do not merge without the separate independent
review and all required CI success.

Next: additional lock subcases, realistic opponent joint hypothesis intake,
complete Battle observation reconstruction and 18-matchup PREP remain future
PRs.
