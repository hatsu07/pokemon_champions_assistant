# #141 Phase 2②〜⑥: one consolidated implementation/acceptance path

**Single PR, single after-match report.** This work combines existing stages;
it does not replace them, fabricate real Champions states, or turn a synthetic
Showdown result into a game recommendation.

## Source identity (② + ⑤)

The previous v3 device packet checked `team_id` and an asset `fingerprint`
against caller-supplied strings, while the SQLite Shadow record compared its
`team_sha256` against another supplied string. Matching an attacker-supplied
window SHA does not establish common registered team origin.

`registered_team_source_binding.py` now reads and independently compares
the existing app's current `data_root/teams/index.json`,
`teams/{team_id}.csv`, the canonical `TeamDocument` digest, raw CSV
SHA256, registered team regulation, and **current** team-scoped generated
LIVE manifest/matrix/lookup against the original fixed Champions masters.
It uses the existing `team_readiness` freshness checker, including source
master/review/environment version requirements. An index redirected to another
team CSV, changed file bytes, a stale fingerprint or mismatched regulation
fails closed.

**Integrity gate hardening (PR #202 review P2):** A matrix/lookup file
SHA match is not enough to certify the current LIVE assets: the existing
`team_readiness` helper sets those per-file flags before checking the
generated catalog's total matchup count and AI cell totals. Source binding
therefore accepts **only** final `team_readiness.status` values
`評価検証済み`, `条件付き準備`, or `対面データ未評価`, while still
requiring exact team fingerprint, all source hashes and no stale reasons.
`生成物不整合`, `生成物確認不可`, `未準備` and any unknown status
fail closed even when early `team_match/matrix/lookup` flags are true.
Regression tests mutate only the generated manifest's opponent row count
(negative, malformed, absent) or AI v1 cell count. All cases must refuse
registered source identity and contribute **zero** matched device/SQLite
windows. This remains local source integrity, never game truth.

Only with this **optional, source-revalidated context** does the offline
SQLite/device link set `team_identity_cross_bound=true`, transfer its exact
same-window/revision records into `same_revision_shadow_turns`, and clear
`TEAM_IDENTITY_NOT_CROSS_BOUND`. Without context it retains the previous
nonverified status; if context is supplied but invalid, the whole batch is
refused. No source hash or local manifest is independent evidence that a
given video shows the claimed game/team. `phase2_accepted=false` always.

The real baseline CSV still lacks **explicit Showdown species IDs for 6
members and move IDs for 24 slots**. Existing #198–#200 auditors report those
as missing despite all 30 candidate ID strings existing in the pinned engine.
Never silently rewrite or infer field bindings, Mega Lucario Z mechanics,
real game PP/HP, RNG or enemy legal moves.

## One combined ②〜⑥ off-match report

`scripts/report_phase2_2to6.py` composes:

- **②** the registered roster and its frozen six/24 ID-field check;
- **③** existing `Phase2MatchAudit` / source-linked single-turn diagnostics
  (if separately provided in Python; a user-submitted JSON cannot prove
  these values, so the CLI deliberately does not invent them);
- **④** per-match 18 evaluated PREP fields and three win roles when provided
  by the existing source-aware PREP audit, otherwise explicit coverage gaps;
- **⑤** bounded, exact same-window match/revision/snapshot/team SQLite
  linkage and registered-team source validation; local MODEL_DIAGNOSTIC is
  still nonactionable and a live parallel workflow is not inferred;
- **⑥** device review labels and eight golden categories; all supplied
  `--gui-export` event traces must match a distinct packet `match_id`,
  and 45-second presentation plus Shadow input deadline failures are
  distinguished.

Example (after, **never during** ranked play):

```bash
python scripts/report_phase2_2to6.py \
  --data-root path/to/your/app-data \
  --masters-dir path/to/pokemon_champions_assistant/masters \
  --team-id YOUR_REAL_REGISTERED_TEAM_ID \
  --team-fingerprint EXACT_CURRENT_LIVE_ASSET_FINGERPRINT \
  --frozen-team-sha256 RAW_REGISTERED_CSV_SHA256 \
  --regulation M-C \
  --device-packet path/to/device-v3.json \
  --annotations path/to/capture-review-links.json \
  --shadow-sqlite path/to/shadow.sqlite \
  --gui-export path/to/gui-match-A-export.json \
  --gui-export path/to/gui-match-B-export.json
```

If the generated LIVE asset manifest references a Singles environment
snapshot, supply the **exact archived input bytes** as
`--environment-snapshot path/to/snapshot.json`. Without that source, the
current-source fingerprint verification will fail closed rather than
pretend the matrix/lookup are fresh. Registered CSV/index changes during
the audit are rechecked before reporting a local source match.

Repeat `--gui-export` for all captured human matches. For real evidence,
at least five observed human matches and independent review are required
by the existing v3 device gate. The GUI timing exporter provides local
Qt callback times, **not video paint completion**; `shadow_visible_ms`
requires external video observation, not model CPU time.

The consolidated report retains separate results for ②〜⑥; it **never
returns `phase2_accepted=true`** based on source hashes, human labels,
synthesized battles or GUI clocks alone. This is deliberate: there is not
yet a verified Champions↔Showdown battle/RNG translation, independently
scored real-game 18 tactics, true same-revision GPT+LOCAL parallel capture,
or real device video and adjudicated Goldens.

## PR #202 review P2: disallow structural overcounts

The source-bound device/SQLite auditor now carries **all** accepted v3
`match_id → expected timeline.turns count` and (only when registered source
identity is cross-bound) each exact SQLite match/window/revision/snapshot SHA,
team CSV SHA, frozen-master manifest SHA, evidence digest, action digest,
local diagnostic status and input-deadline flag. The consolidated report can
count a separately supplied `Phase2MatchAudit` for ③/④ only if:

- `status=OFFLINE_EVIDENCE_ONLY`, nonactionable ranking, source-linked
  observation, exact selected-opponent-six boundary and valid **shared
  selection+PREP 90-second** status;
- `ASSESSED_18_CONDITIONAL_ONLY` and exactly 18 fields with no invalid
  assessment/timing blockers; and
- the match, decision window, revision and snapshot SHA match both that
  audit's `shadow_record` and one of the *same batch's registered-source-
  bound* SQLite records. Its team/master/evidence/action digests, local
  status and input deadline flag must all match.

Rejected, stale, unrelated, different-registered-team or malformed
`Phase2MatchAudit` objects count **zero** ③/④ runs/pairs, with
`REJECTED_OR_UNLINKED_PHASE2_MATCH_AUDIT` recorded as the missing source
constraint. A match ID alone cannot satisfy this linkage. These remain
structural, not actual Champions battle certification. The public CLI
still **does not** fabricate/import `Phase2MatchAudit` objects from untrusted
JSON, so ③/④ require separately validated off-match Python evidence.

For ⑥ the report now requires each GUI export's total observed and
displayed GPT turns to equal the **verified v3 packet's** turn count for
that exact match. A one-turn GUI export alongside a two-turn device packet
is incomplete, even if all five GUI exports internally report 100% coverage.
The report includes `gpt_gui_missing_device_turns` and flags
`GUI_DEVICE_TURN_COUNT_MISMATCH` and
`INCOMPLETE_GUI_TIME_EVENTS_FOR_ALL_MATCHES`. These are locally submitted
times/labels, **not** independent frame-aligned video evidence.

## Acceptance blockers that remain external to code

The team ID source mismatch can now be closed for local files, but **not**
for the physical game session. Actual ②〜⑥ completion requires collected,
independently examined real Champions gameplay evidence, the 6+24
Champions ID/action/mega compatibility check, tactical 18 evaluations
and win roles, the same-window LOCAL/GPT measurement with action
constraints, and video-verified presentation/safety/abstention.
Synthetic fixtures in CI must never be counted as that evidence.
Keep #141 open and Phase3 #142 disabled until those explicit requirements
are demonstrated. No automatic game action, GUI recommendation, timers,
or LIVE preloaded master behavior is changed by this PR.
