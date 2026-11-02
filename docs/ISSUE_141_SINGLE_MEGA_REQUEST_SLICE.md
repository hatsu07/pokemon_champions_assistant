# Phase 2 — single Mega request choices and exact action validation

## Scope (after PR #181)

The fixed pinned Showdown `gen9championsbssregmc` synthetic Battle is
the source of the engine-issued P2 request. This isolated Phase 2 slice adds
**ordinary Mega** (`canMegaEvo: true`) to the already-supported simple
singles move/normal-switch/forced-switch requests. Each enabled, PP-positive
move generates two separate candidate commands, `move N` and
`move N mega`. The normal choice is never silently replaced by Mega.

The candidate set is accepted only after re-deserializing a **fresh complete
Battle/RNG copy per choice**. Each chosen action must correspond to the exact
requested move slot and move ID, Mega flag, or switch target. In particular,
an engine `Side.choose()` returning `true` while an Encore/lock overrides
the selected move is **not** proof that the requested slot was legal.
An altered request, unexpectedly substituted action, rejected move or switch,
inconsistent P2 roster/HP, unsupported form flag, or incomplete reply fails
closed to `NO_CHOICES`.

## Explicit exclusions

- `canMegaEvoX`, `canMegaEvoY`, Ultra Burst, Z/Max/Tera, ambiguous
  `maybeDisabled`/`maybeLocked`/`maybeTrapped` and complex choice
  restrictions remain `NO_CHOICES`. The code does **not** infer them from
  non-Champions game versions.
- A hard-lock/Encore/choice restriction is not classified as fully covered
  simply because the engine accepts a command; command and action identity
  must agree. Full completeness for lock, Struggle, or complex switch
  behavior remains future work.
- The worker still exposes the historical status string
  `ENGINE_CHECKED_SIMPLE_CHOICES`, with a narrower `scope` identifier
  `PINNED_SHOWDOWN_SINGLE_MEGA_SINGLES_ONLY` on Mega request paths.
  `exhaustive_legal_choices_verified` remains **false** for every result.
- This is a synthetic Showdown API test, not observation from a Pokémon
  Champions match, and not proof of game-mechanics parity, complete legal
  choices, valid hidden opponent sets, or real-game Battle/RNG reconstruction.
  Do not auto-promote results to the GPT/LIVE UI or Shadow ranking.

## Verification

- Isolated Node unit tests: valid normal+Mega paired choices, unsupported
  flags, forged Mega eligibility and command-to-action mismatch.
- Real pinned Showdown test: Garchomp with Garchompite on a complete synthetic
  3v3, an actual `p2.activeRequest` enabling Mega, fresh-clone action
  verification, unchanged Battle/RNG state and the existing non-Mega and
  forced-switch regression suite.
- Linux isolated + pinned real-engine + Windows Desktop **latest-HEAD CI**
  are all required before reviewing for merge. Read PR HEAD, CI HEAD and
  checkout SHA; do not treat draft/skipped or earlier success as verified.

## Next slices

X/Y special Mega semantics and known lock/Encore/Struggle contexts require
separate pinned-engine tests and a completeness policy before expansion.
Reliable observed-state reconstruction, opponent joint-set coverage, full
18-matchup PREP, same-revision LOCAL_SHADOW/GPT evaluation and Windows
Android-mirror E2E remain outside this PR.