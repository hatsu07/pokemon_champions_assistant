# Issue #141 — Conservative Showdown P2 ChoiceRequest option boundary

This incremental PR adds a **read-only, synthetic-request parser**, not a legal-action oracle or a screen-to-Showdown converter.

## Motivation

PR #176 requires an explicit list of opponent Showdown commands (such as `move 1` and `switch 2`) for every full replay/joint hypothesis. Using human-made hypothetical commands as if the Showdown engine had certified them can omit a significant opponent choice. Pinned Showdown `Battle.fromJSON` exposes each side's `activeRequest` (a `ChoiceRequest` with `active[].moves`, `side.pokemon[]`, `forceSwitch` and mechanic flags). That actual engine-side request **must be captured and attested separately in a future PR**; this module deals only with its conservative structure.

## What the module does

`local_strategy/opponent_request.py::enumerate_simple_p2_request(snapshot, envelope)` receives an explicitly detached canonical synthetic Battle JSON, a canonical P2 ChoiceRequest JSON, and the exact Battle SHA, input snapshot SHA, and pinned engine SHA. It checks the hashes and provenance type, requires exactly three P2 members, one active Pokémon, valid HP/active flags, and the side ID `p2`.

- For an ordinary singles move request, emit only `move <slot>` for distinct, well-formed, PP-positive, explicitly non-disabled moves. Optionally add `switch <party-slot>` for living reserves if the request does not mark the actor trapped.
- For exactly one forced switch request (`forceSwitch: [true]`), emit only viable reserve switch slots.
- Reject waiting/preview/multi-actor requests, missing PP, hidden disability, uncertainty about locks/trapping, or any nontrivial `canMegaEvo*`, Z-Move, Max Move, Ultra Burst or Terastallize choice flags. Unknown/complex formats return **`NO_CHOICES`**, never a guessed command.
- Reject malformed roster, HP/active flags, duplicate/invalid IDs, disabled+zero-PP inconsistencies, stale source hash, snapshot hash, engine hash and noncanonical JSON.
- Output `PROVISIONAL_REQUEST_OPTIONS` / `exhaustive_legal_choices_verified=False` **always**, even for the simplest move list. This is a deliberately **non-authoritative candidate set**. There is no implicit adapter to `run_shadow_turn` or `ReplayOpponentScenario`.

## Why provisional, and next integration

The `P2RequestEnvelope.provenance` string `EXPLICIT_SYNTHETIC_REQUEST_UNATTESTED` is descriptive, **not a cryptographic proof** that the raw P2 request came from the same Showdown Battle. Matching the request's attached Battle SHA only proves that the claimant supplied matching bytes; a malicious or simply inaccurate caller could still fabricate an unrelated request.

Therefore the generated strings **must not be used as a complete set of legal actions**. The next development slice must have the existing pinned `worker.cjs` load the full `Battle.fromJSON`, extract the actual `b.sides[1].activeRequest` for the same Battle/RNG and decision window, and independently test each candidate through the engine's choice validation on identical detached copies. It must also verify that *every* nontrivial choice branch (Mega, forced replacement, lock/encore, multi-input if any) was either covered or blocked. Only then can a complete set be considered for `run_shadow_turn`.

The synthetic Battle JSON is **not reconstructed from current in-game screenshots** in this PR. A real Champions↔Showdown ID mapping, reliable 3v3 state reconstruction, exact p2 request generation, and timing/GUI validation remain future work. No change to LIVE/GPT, no game-control actions, no filesystem/Web reads during an active match.

## CI

`tests/test_issue_141_opponent_request.py` tests simple move and switch extraction, forced replacement, trapping, stale/hash mismatch, wrong side/roster/HP, incorrect PP, duplicate moves and complex mechanic flags. Linux and Windows isolated tests are updated. These are **synthetic parser tests**, not a pinned real-Showdown integration test.

No real game screen or user-run WSL/VS Code/Codex is required for this scoped PR. A real pinned Showdown distribution and Windows/Android mirrored UI captures are necessary for the future end-to-end stages.
