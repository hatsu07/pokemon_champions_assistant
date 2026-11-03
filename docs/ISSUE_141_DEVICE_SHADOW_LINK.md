# Issue #141④⑤⑥: Device-capture / Shadow-SQLite evidence binding

**Non-LIVE, read-only acceptance evidence intake. Not Phase 2 completion.**

Existing `real_battle_validation.py` verifies supplied capture file SHA,
five nonduplicated human-labelled matches, a 90-second **shared** selection
+ PREP clock (no separate 40-second cap), a 45-second turn display window,
18 PREP rows and three role labels. The v3 device packet is a **claim**
supported by locally readable bytes and human review; it does not
cryptographically prove that these bytes depict actual Champions play.

PR #201 adds `device_shadow_evidence_link.audit_offline_device_shadow_link`:
for each game in the device packet, require exactly one external
`CapturedTurnLink` per observed turn, and an exactly matching same-match
+ same-window + same-revision + snapshot SHA and registered team SHA
`ShadowComparisonRecord` from existing privacy-bounded SQLite v2.
The game's capture file SHA must match each annotated turn's file.
Optional `shadow_visible_ms` timestamps are compared to each captured
action-screen start (not to internal local model elapsed time), yielding
P50/P95/P99 and 45s-late counts. A missing timestamp does not count as
within deadline. Duplicate windows, swapped matches/capture files, changed
revision, missing turn, or mismatched registered team refuse the batch.

Eight required golden **labels** are bound to the same game file SHA and
window revision: immediate KO; choice lock/PP; Mega/form; priority/speed;
processor versus safe switch; setup; endgame 1v1; and stale revision.
An external `KNOWN_UNSAFE` label remains an explicit blocker. An
all-abstention local diagnostic remains a blocker even if all eight labels
are claimed PASS. Neither local `MODEL_DIAGNOSTIC` nor a GPT action-ID
hash is proof of a game-legal or strategically correct recommendation.

**P2 review safeguards (2026-11-03):** The v3 device packet
`team_id` / `team_fingerprint` and Shadow SQLite `team_sha256` are
individually checked against separately supplied command arguments, but no
trusted registered-team roster manifest binds all three values. This audit
**must not assert a same-team link** from a matching match/window/revision
and snapshot label alone. It reports `team_identity_cross_bound=false`,
`same_revision_shadow_turns=0`,
`unverified_aligned_shadow_rows=<matching-record-count>`, and the permanent
`TEAM_IDENTITY_NOT_CROSS_BOUND` blocker until an authoritative source is
implemented and independently reviewed. Even a device packet claiming team A
and SQLite rows from team B with carefully copied window SHA labels cannot
be counted as a verified same-team pairing.

The SQLite `input_deadline_met=false` for **any** matching window is
recorded separately as `shadow_input_deadline_missed_count`, with
`SHADOW_INPUT_DEADLINE_MISSED` in `missing_evidence`. Annotated video
`shadow_visible_ms` and `late_shadow_displays` never erase an existing
SQLite input-deadline failure, nor does a local elapsed-time measurement
prove the actual video display deadline. Regression tests exercise A/B team
cross-identity and ten (or one) false input-deadline flags.

**Important: audit `status=STRUCTURALLY_LINKED_UNVERIFIED` means the
supplied packet and database are *internally consistent*, not that their
origins or tactical judgments are independently authenticated.**
`phase2_accepted` is always false; game truth, local actionable coverage,
Champions mechanic parity, 18 tactical values and true live GPT/local
noninterference remain to be checked via real-game review.

## Off-match command

```bash
python scripts/report_phase2_device_shadow_link.py \
  --device-packet path/to/device-evidence-v3.json \
  --annotations path/to/reviewed-window-links.json \
  --shadow-sqlite path/to/shadow.sqlite \
  --team-id YOUR_REGISTERED_TEAM_ID \
  --team-fingerprint YOUR_REGISTERED_TEAM_FINGERPRINT \
  --frozen-team-sha256 YOUR_FROZEN_TEAM_CSV_SHA256 \
  --regulation M-C
```

`--annotations` is a JSON object with `frames` and `goldens` arrays.
Each frame contains `match_id`, `turn_index` (zero-based within one
game's v3 `timeline.turns`), `decision_window_id`, `state_revision`,
`snapshot_sha256`, `capture_sha256`, and optional integer
`shadow_visible_ms`. Each golden contains `case_id`, `kind`, same
match/window/revision/snapshot/capture SHA, `outcome` of `PASS`,
`KNOWN_UNSAFE` or `UNREVIEWED`, and source label
`EXTERNALLY_SUBMITTED_CAPTURE_REVIEW`.

No screenshots, raw OCR, unverified GPT action text, or model explanations
are printed or stored. Only source hashes, category counts and latency
summaries are emitted. Both code and CI use intentionally synthetic
fake capture bytes to verify rejection paths, not real game evidence.

This evidence-linking PR is a prerequisite to collecting real proof,
**not** final verification of ②, ③, ④, ⑤, ⑥, or ⑦. The separately
required user-controlled Android→Windows mirror/real battle capture and
independent tactical adjudication are still missing. Keep #141 OPEN and
Phase 3 #142 blocked.

## Optional real Windows GUI clock trace (non-LIVE audit)

The existing desktop **対戦テスト結果を保存** JSON export includes
`timing_observations.events` collected using an actual GUI monotonic clock.
Before the export is produced, the emitting GUI now adds the *opaque*
`match_id`, `request_id` and `decision_window_id` to
`ACTION_GUI_RENDERED`. These are source-event identifiers, not an action or
a screenshot.

A separate, pure, off-match reader checks an exact chain:
`TURN_SCREEN_OBSERVED` → `AI_REQUEST_DISPATCH` →
`AI_RESULT_DISPLAYED` → `ACTION_GUI_RENDERED`, all in the same
match, turn, request and state revision, in chronological order.
It reports actual Qt callback display delays relative to the first observed
turn screen, plus 45-second late/missing turn counts and P50/P95/P99:

```bash
python scripts/audit_phase2_gui_timing_export.py path/to/exported-match.json
```

Missing/unbound request IDs, a stale revision, a different match, an invalid
clock, a duplicate rendered advice, and `TEST_ONLY` results are refused,
rather than treated as a timely actionable move. OCR revision retries with
a new request ID are supported without treating any old reply as the result.
The exported JSON is manually saved by the user **after** a session, so
this audit runs no code during time-limited gameplay.

**Important:** `GUI_MONOTONIC_TIMING_ONLY` does **not** authenticate video
frames, game turn deadlines, correctness/legal Pokémon Champions actions,
or a LOCAL_SHADOW GUI display. Qt callback times do not replace device-video
alignment, independent Golden reviews, or live GPT/LOCAL noninterference.
The local Shadow model remains nonactionable and this diagnostic always
returns `phase2_accepted=false`. Existing older GUI exports without the
new request/window fields must be re-collected, not inferred as valid.
