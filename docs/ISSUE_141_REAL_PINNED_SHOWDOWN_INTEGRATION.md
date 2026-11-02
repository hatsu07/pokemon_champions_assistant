# Phase 2 — pinned Showdown actual-engine integration (PR #181)

## Objective and isolation

This test closes a **specific evidence gap** after PR #180: the existing P2
`activeRequest` extractor and clone validator had only been exercised with
a fake engine. CI now checks out **smogon/pokemon-showdown at the exact commit**
`ccfaa20f32af363569cd21a9d501b2bc1ded26b1`, verifies the Git SHA,
installs dependencies from its lockfile without lifecycle scripts, builds the
actual simulator and runs Node tests against the compiled Battle class.
It does not use the latest upstream HEAD or the player's game screen.

The integration is **not a claim of Champions cartridge parity**. It uses
synthetic, completely known three-vs-three teams, not Poké HOME rankings or
observations, and it does not add source mapping or production GUI connections.

## Real-engine checks

`local_simulator/test_real_pinned_showdown.cjs` constructs a real Battle
for `gen9championsbssregmc` (the pinned source's BSS Reg M-C format).
It executes actual team preview choices, checks the engine's `p2.activeRequest`,
serializes the whole Battle using `Battle.toJSON()`, restores via
`Battle.fromJSON()`, and checks turn/request/RNG consistency.

For ordinary singles decisions, `enumerateEngineP2` retrieves actual
request-derived choices and validates each on a separate copy, with no source
mutation. For a controlled **synthetically induced** faint state (not a natural
game KO), the actual Showdown `makeRequest('switch')` generates the
replacement request which is then validated. A tampered complex
Mega-choice flag must still return `NO_CHOICES`. No fixture is labeled
"actual Champions battle".

## CI and acceptance

The dedicated `.github/workflows/showdown-phase2-real-engine.yml`
runs on PRs touching `local_simulator` or its own workflow and on explicit
manual dispatch. It checks out the **exact PR HEAD** rather than the synthetic
merge commit and the **exact upstream commit** rather than a branch; refuses
missing upstream lockfile/license. The job must complete successfully at the
same HEAD as the pull request. Existing Linux fake-engine CI and Windows
Desktop CI remain independently required.

If the upstream pinned format/constructor/serializer differs from the mocks,
the job is expected to **fail** until a minimal real-engine-grounded patch
is reviewed. Do not disable assertions or replace the real `Battle` with a
fake to get a green build.

## Remaining work (not covered)

- Full coverage/completeness proof for **all** opponent legal choices
  (Mega variants, lock/Encore, unknown moves, complex switch, etc.) remains
  false even for `ENGINE_CHECKED_SIMPLE_CHOICES`.
- Reliable Champions observed state → full Showdown Battle and RNG
  reconstruction (including hidden opponent joint sets) remains unavailable.
- No auto-promotion of candidate sets to `run_shadow_turn` or LIVE/GPT,
  no Windows packaged Showdown runtime, no Android mirror/OCR/GUI E2E.
- CI is synthetic-data evidence of **upstream API integration only**, not
  evidence of Champions game compatibility or ladder win-rate gains.

Changes are isolated and should be mergeable after real-engine Linux test,
regular Linux/Windows CI and independent review succeed.
