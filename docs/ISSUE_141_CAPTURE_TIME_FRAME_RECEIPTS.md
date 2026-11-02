# Issue #141 — capture-time frame/ROI receipt in the existing observer

## What this PR actually changes

The existing `ObserverPipeline.process_frame` already receives RGB
`Frame` pixels from the explicitly selected source (synthetic, offline file,
video, Windows or ADB adapter) and appends the OCR output to a *local*
`BattleStore.raw_observations` row. This PR adds a capture receipt **at
that exact observer-ingest boundary**, before applying any OCR event, rather
than backfilling a synthetic current-window record after the fact.

`capture_provenance.py` freezes a `FrameCaptureReceipt` with:
- `match_id`, `raw_observation_id`, `frame_id`, `capture_seq`;
- SHA256 of the actual RGB pixel bytes, width and height;
- source-claimed `captured_at_ms` and `source_claim`;
- `game_turn_seq`, `observation_seq`, `tactical_state_version`, and any
  `decision_window_id` and window-version that existed **before** handling
  that frame.

The existing SQLite raw observation now includes `capture_receipt`.
Individual OCR ROI records include `roi_rgb_sha256`, while the OCR text,
confidence, calibrated candidates and raw-observation ID remain in their
existing paths. No RGB/JPEG/PNG image bytes are added to SQLite, GitHub, CI,
a network request or a match export. A capture-time digest is not proof that
the source actually displayed a Pokémon Champions screen.

The `Frame`'s `captured_at_ms` can be an offline video offset or an
untrusted adapter clock; it is **not** a verified timezone-aware UTC capture
timestamp and is never passed to `WindowEvidenceRecord.captured_at` in
the #186 synthetic gate. The `source` label is also self-reported.
All receipt truth flags are **false**:
`capture_origin_authenticated`, `capture_timestamp_authenticated`,
`actor_identity_authenticated`, `game_observation_verified`,
`ready_for_engine`. A previous FSM decision window ID is annotated
`before_ingest` and is never a proof that a new OCR field belongs to the
same window or Pokémon. Actor identity is left unverified, rather than
translating screen text into an assumed active participant.

## Replay and duplicated frame safeguards

- `process_frame` rejects a reused match/capture-seq/frame-ID composite,
  including a new image hidden under an old raw observation ID. A refused
  frame does not apply extra interpreted state. The ROI change/episode/last
  parsed data are computed in a **staged cache** and committed only if the
  SQLite raw insert succeeds. This protects a restarted observer instance:
  a cross-instance DB duplicate must not suppress OCR on the following
  new-ID frame with the same RGB pixels.
- Buffered OCR reanalysis references the **original** frame receipt and
  original raw observation ID, rather than claiming the old pixels belong
  to the *current* window. The raw reanalysis ID includes that exact
  original frame identity, original pixel SHA256, and a local episode
  sequence. Distinct frames with the same capture sequence cannot alias.
  A repeated reanalysis ID that SQLite refuses to insert is a hard stop:
  **no semantic event is applied under an unpersisted raw source ID**.
  The later observation/tactical versions are labeled
  `reanalyzed_after_...`, not original capture metadata.
- Before reanalysis, the retained image is rehashed and checked against
  its original receipt. If the mutable numpy array was overwritten, replay
  stops without creating a new raw observation.

## Optional per-field lineage for accepted local OCR updates (same PR)

The separate `live_state_updates.apply_local_ocr()` reducer now accepts
`capture_receipts={frame_id: FrameCaptureReceipt}` **opt-in**. It does not
guess that arbitrary OCR frame IDs belong to the observer. Every record in
this explicit binding mode must have a typed receipt with the exact
`state.match_id`, `record.frame_id`, a syntactically valid pixel digest
and the originating `raw_observation_id`. When one is missing, stale or
inconsistent, that OCR record is kept as `CANDIDATE` with
`FRAME_RECEIPT_UNAVAILABLE_OR_MISMATCH`; it cannot mutate battle state
under this binding mode.

Only **after** the existing actor attribution, timestamp ordering,
confidence, switch-HUD checks and normal reducer validation accept an
event does its `battle_evidence.update_provenance` entry include the
specific actor, side, target field (`hp_by_pokemon`, `moves_by_pokemon`,
`status_by_pokemon`, `boosts`, etc.), pre/post live-state revision and an
explicit `claimed_frame_link`. The link contains the frame RGB SHA,
original raw-observation ID and pre-ingest window reference (if any).
It does **not** make the OCR text or actor authentic. The independent
ROI digest, source clock, window identity and actor remain unverified:
`roi_digest_verified=false`,
`capture_timestamp_authenticated=false`,
`current_window_binding_verified=false`,
`actor_identity_authenticated=false`, and `ready_for_engine=false`.

Without this optional map, the prior local OCR API remains behaviorally
unchanged and continues its existing non-attested provenance record;
a `frame_id` alone never gains a pixel digest.

Tests use synthetic receipt objects with the real local OCR reducer
to cover accepted HP/MOVE/STATUS attribution, mismatched/missing
origin refusing state mutation in binding mode, and legacy compatibility.
This is an **opt-in join contract**, not an automatically wired
cross-pipeline feed: runtime callers still need to carry original
frame receipts end-to-end from the capture implementation. No
`WindowEvidenceRecord` from PR #186 is produced here.

## Strict non-claims and next connection

This is a **capture-pixel and OCR-payload linkage**, not source
authentication, complete per-field provenance, capture-time actor
identification, guaranteed monotonic clock, or state reconstruction.
The separate GPT/OCR `update_battle_evidence` and
`ai_ocr_state_updates` paths remain unchanged; the local OCR reducer
only gains the explicit optional join described above. Neither is
automatically declared bound to the real observer at runtime. No `SYNTHETIC_WINDOW_EVIDENCE`
records are forged; no `SYNTHETIC_WINDOW_FACTS_BOUND` result is treated
as authentic game evidence. No Showdown Battle/RNG is created. No worker
IPC, action recommendation, legal-choice policy, game input or GUI wiring
is added.

Follow-up: source-specific immutable frame clock/sequence attestation,
runtime end-to-end receipt propagation, durable history across switches,
stale/cancelled update checks, event-to-frame reconciliation, and explicit
type-safe mapping to the #185 staged observation/ID bridge. Only after
independent checks should the integration be considered for the engine.

## Validation and privacy

`tests/test_issue_141_capture_receipts.py` exercises the real local
`ObserverPipeline` and SQLite export with synthetic pixel frames: exact
pixel/ROI hashes, pre-ingest revision, source/clock unverified flags,
duplicate raw identity, buffered-image mutation refusal, and a later
reanalysis retaining the original identity. Included in Linux Phase 1 and
Windows Desktop offline suites.

No actual game screenshots, user battle history, private keys, installed
fonts or raw PNGs are committed or uploaded. Require independent review
and latest PR HEAD Linux + full Ready Windows workflow and required
jobs `completed/success` before **manual** merge to
`integration/issue-138-showdown-local-engine`. No master release.
