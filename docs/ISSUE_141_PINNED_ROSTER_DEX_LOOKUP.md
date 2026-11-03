# Phase 2 #141② — Actual registered team against pinned Showdown Dex (metadata only)

**Status: pre-LIVE metadata diagnostic; NOT ②〜⑥ acceptance.**

PR #199 provides six base-species `LearnsetKey` candidates and 24 `MoveKey`
candidates for the actual 2026-10-03 registered Champions team. The source
masters include fullwidth digit variants and multiple real form rows; they
have no independent engine or Champions-game approval authority.

This PR adds `local_simulator/pinned_champions_roster_lookup.cjs`, an isolated
SHA-bound read-only metadata inspector. When the pinned Showdown test workflow
checks out **exact SHA `ccfaa20f32af363569cd21a9d501b2bc1ded26b1`**
and builds the actual simulator, the test generates fresh source candidates
using the existing Python pre-match CLI, then verifies these source bytes
against the original team, moves, learnsets and usable Pokémon masters.

**P2 review hardening:** Source SHA alone does not bind a supplied JSON
member/move key to those file contents. Before consulting the Dex, the audit
**re-runs the canonical Champions Python candidate extractor against the
exact checked-out four-master source bytes** and compares the entire six
members, four move slots, names, form labels and keys to the supplied JSON.
Conflicting or fabricated values (even with all source SHA unchanged) are
rejected before any Dex read; duplicate base-species IDs are also refused.
All four source master paths plus the Python preload, candidate and roster
binding implementation are in the real pinned workflow's PR trigger list.

Only after the source SHA, canonical source re-derivation and complete 6 × 4
roster slots pass does the
inspector query the **actual pinned Champions Dex** for existence and exact
IDs of each base species and move. Missing entries are reported; the pinned
test fails if all thirty source candidates do not exist. It does *not*
silently substitute an SV/ZA/vanilla-gen9 form or key. Synthetic fake-Dex
unit tests exercise rejection of partial rosters, bad source SHA, forged
approval flags, duplicated slots and missing engine entries.

**Mandatory limits:**

- `PINNED_ENGINE_KEYS_EXIST_UNATTESTED` indicates metadata ID existence
  **only**, not current Pokémon Champions legality or mechanics.
- `ルカリオ → メガルカリオZ` remains explicitly unsupported as a verified
  transformation. A base `lucario` Dex entry does not authorize a Mega-Z
  mechanic or reuse older Mega Lucario type, ability, speed or stats.
- An engine Dex entry existing does not attest move power, type, priority,
  contact, targeting, HP/PP, item/ability effects, RNG, game roster or
  a safe switch. A real Champions screenshot does not expose full Battle.
- The lookup report always has `mapping_independently_attested=false`,
  `champions_mechanics_verified=false`, `executable_in_live=false`,
  and `ranking_status=NO_RANKING`. It never mutates the registered
  four-master team CSV and cannot be imported by the LIVE worker.
- Required Linux/Windows synthetic tests and real pinned engine tests are
  separate. A successful synthetic unit test cannot replace the pinned
  workflow or independent real-game capture.

**After metadata checks:** Independently adjudicate the six Pokémon /
24 moves in Champions current regulation, then explicitly review the
frozen-roster ID binding by SHA and verify the supported subset via real
captures. Build ③ safe one-turn choices, ④ tactical 18 PREP judgments,
⑤ same-revision real GPT/LOCAL_SHADOW parallel observations, and ⑥
independent golden, safe/late/abstain metrics with real Windows timings.
Passing source metadata alone may not mark any of ②〜⑥ complete.

User-approved timer remains selection + PREP **shared 90 seconds** with
no independent 40-second PREP cutoff. An observed opponent lead immediately
supersedes PREP.

Keep [Issue #141](https://github.com/hatsu07/pokemon_champions_assistant/issues/141)
Open and Phase 3 #142 disabled until all real-game acceptance evidence exists.
