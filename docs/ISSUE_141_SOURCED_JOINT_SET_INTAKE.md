# Issue #141 — Sourced joint opponent set intake (incremental Phase 2)

## Why this exists

The current Champions single-battle environment snapshot (e.g. `Pokemon_Champions_environment_snapshot.txt`, sourced from the user's Champions Singles data policy) contains **separate Pokémon, move, item, ability, nature and spread adoption rankings**. Such marginals do **not** identify which exact moves and held item coexist on an individual opponent set, let alone three specific Pokémon together. Products of those marginal rates would fabricate a joint distribution and could cause a dangerous tactical ranking.

This PR therefore **does not manufacture opponent sets from rankings**. It introduces a strict, in-memory intake for source-identified, explicitly **co-recorded three-Pokémon bundles**. These are candidate claims, never verified game facts. It attaches metadata before any simulation and reuses PR #177's observed-evidence gate.

## Interface

`local_strategy/joint_set_intake.py` defines:

- `SourcedJointSetCandidate`: candidate ID; `CHAMPIONS_COOCCURRING_JOINT_RECORD` or `USER_SUPPLIED_COMPLETE_JOINT_RECORD`; explicit `source_ref`; timezone-aware `retrieved_at`; regulation matched to current `DecisionWindow.regulation`; names of precisely 3 distinct opponent Pokémon; and a JSON object with exactly one `opponent_sets` map for those same names.
- Each member must include explicit, unique, nonempty 1–4 moves, item, ability and form as **game-visible literal strings**. Optional current type, status, nature and literal nonnegative ability-point mapping are retained without guessing unfamiliar Champions rules. Extra fields, including ungrounded `usage_rate`, are refused.
- `stage_sourced_joint_candidates(snapshot, tuple_of_records)` consumes 1–16 records. It checks unique IDs, source metadata, 3-member integrity, optional already-observed six-Pokémon preview compatibility, and regulation. If any record is invalid, the **whole batch fails**, not just that entry.
- Every accepted `Hypothesis` has `likelihood=None`, a stable ID, and source kind/ref/retrieval time/regulation/scope in `evidence_refs`. The intake checks **all** staged hypotheses using PR #177's `check_observed_opponent_hypotheses`. Contradictions or missing representations of observed opponent values cause `NO_RANKING`.
- **Successful status is only `PROVISIONAL_JOINT_CANDIDATES` with `coverage=NOT_PROVEN_EXHAUSTIVE`.** The intake does not mutate the frozen match snapshot, does not assert source authenticity, does not return a Showdown-ready `ReplayOpponentScenario`, and does not decide that the record list covers every relevant enemy type.

## Boundaries

- No web, filesystem, statistics fetch, OCR or further master loading during a match. Sources must be curated and captured **before** the live turn; the caller can pass immutable records.
- Merely supplying `source_kind` and `source_ref` is **not verification** that the provided claims were actually co-observed. A subsequent trusted provenance stage is required; until then the data are **provisional** and must not be silently promoted to verified opponent facts or a ranked action. This is a structure/contradiction gate, not an independent authenticity check.
- Current `battle_evidence` uses literal Japanese strings. No cross-game SV/ZA mechanics, move learnset, team probability or English Showdown mapping is inferred.
- A Pokémon that has not revealed an item is still unknown on-screen even if a sourced candidate explicitly proposes one. Its provenance is hypothesis, not observation.
- The 3-mon requirement describes **three explicit members in a single co-recorded bundle**, not the assertion that the current player's opponent has already revealed all three; it is compared with the six-preview where that is available.
- Practical missing steps: curated Champions **complete joint-set** source (not current marginal statistics), independent source verification/coverage policy, actual opponent action options, full Showdown Battle/RNG reconstruction and verified mapping, then three-party tactics, PREP 18 and local/GPT Shadow logging.

## Tests

`tests/test_issue_141_joint_set_intake.py` uses synthetic fixture parties only. Covers positive multiple joint records, no fabricated likelihood, unchanged input snapshot, source timestamps and regulation, disallowed marginal/SV record kinds, selected-three/preview contradictions, member/move omissions and duplicates, extra inferred probability fields, observed item mismatch failing the **entire** batch, limits and existing hypothesis binding. Linux isolated and Windows Desktop CI both include these tests.

No real game image, WSL/VS Code/Codex, or user Windows action is required for this initial intake PR. They become relevant when building and measuring an actual game-screen→Showdown state→GUI pipeline.
