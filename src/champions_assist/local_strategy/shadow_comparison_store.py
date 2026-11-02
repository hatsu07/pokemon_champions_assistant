"""Issue #141⑤: isolated, same-revision GPT vs LOCAL_SHADOW SQLite evidence log.

Opt-in diagnostic only. Never import the GUI, read a source file, interpret a
model as a verified battle action, or replace GPT production recommendations.
Only hashes/labels and timing are stored; no screenshot, token or raw OCR.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any

from .models import FrozenBattleState, action_digest, fingerprint
from .observed_full_grid_diagnostic import ObservedFullGridDiagnostic

_SCHEMA = "issue141-shadow-same-state-v1"
_LOCAL_STATUSES = frozenset({"NO_RANKING", "MODEL_DIAGNOSTIC"})
_GPT_STATUSES = frozenset({"GPT_ADVICE", "GPT_ABSTAIN", "GPT_UNVERIFIED"})


@dataclass(frozen=True)
class BoundGPTAdvice:
    """Externally captured GPT output, NOT proof that a move is legal."""
    snapshot_sha256: str
    status: str
    action_id: str = ""
    reason: str = ""
    elapsed_ms: int = 0


@dataclass(frozen=True)
class ShadowComparisonRecord:
    match_id: str
    decision_window_id: str
    state_revision: int
    snapshot_sha256: str
    team_sha256: str
    master_manifest_sha256: str
    evidence_sha256: str
    allowed_action_digest: str
    gpt_status: str
    gpt_action_id: str
    gpt_action_verified_in_window: bool
    gpt_reason: str
    gpt_elapsed_ms: int
    local_status: str
    local_reason: str
    local_elapsed_ms: int
    hypothetical_branch_count: int
    same_action_comparison: str
    input_deadline_met: bool
    recorded_at: str


def _valid_time(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 600_000


def _ensure_schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS phase2_shadow_same_state (
        schema TEXT NOT NULL,
        match_id TEXT NOT NULL,
        decision_window_id TEXT NOT NULL,
        state_revision INTEGER NOT NULL,
        snapshot_sha256 TEXT NOT NULL,
        team_sha256 TEXT NOT NULL,
        master_manifest_sha256 TEXT NOT NULL,
        evidence_sha256 TEXT NOT NULL,
        allowed_action_digest TEXT NOT NULL,
        gpt_status TEXT NOT NULL,
        gpt_action_id TEXT NOT NULL,
        gpt_action_verified_in_window INTEGER NOT NULL,
        gpt_reason TEXT NOT NULL,
        gpt_elapsed_ms INTEGER NOT NULL,
        local_status TEXT NOT NULL,
        local_reason TEXT NOT NULL,
        local_elapsed_ms INTEGER NOT NULL,
        hypothetical_branch_count INTEGER NOT NULL,
        same_action_comparison TEXT NOT NULL,
        input_deadline_met INTEGER NOT NULL,
        recorded_at TEXT NOT NULL,
        PRIMARY KEY(match_id, decision_window_id, state_revision)
    )""")


def build_shadow_comparison(
    submitted: FrozenBattleState, current: FrozenBattleState,
    gpt: BoundGPTAdvice, local: ObservedFullGridDiagnostic | None,
    *, local_elapsed_ms: int, remaining_ms_at_capture: int,
    recorded_at: str | None = None,
) -> ShadowComparisonRecord:
    """Reject stale ABA state, fabricated verified local advice and missing SHA.

    The submitted and current windows must be identical, including request,
    four master SHA manifest, team SHA and deadline. LOCAL_SHADOW diagnostics
    are NEVER promoted to a game action or an artificial agreement score.
    """
    if not isinstance(submitted, FrozenBattleState) or not isinstance(current, FrozenBattleState):
        raise ValueError("FROZEN_PAIR_REQUIRED")
    if (submitted.window.identity() != current.window.identity()
            or submitted.snapshot_sha256 != current.snapshot_sha256
            or action_digest(current.verified_action_ids) !=
               current.window.allowed_action_digest):
        raise ValueError("STALE_OR_UNBOUND_OBSERVATION_WINDOW")
    if not isinstance(gpt, BoundGPTAdvice):
        raise ValueError("BOUND_GPT_RESULT_REQUIRED")
    if (gpt.snapshot_sha256 != submitted.snapshot_sha256
            or gpt.status not in _GPT_STATUSES
            or not _valid_time(gpt.elapsed_ms)
            or not _valid_time(local_elapsed_ms)
            or not _valid_time(remaining_ms_at_capture)
            or not isinstance(gpt.action_id, str)
            or not isinstance(gpt.reason, str)
            or len(gpt.reason) > 256):
        raise ValueError("GPT_OR_TIMING_UNBOUND")
    if gpt.status != "GPT_ADVICE" and gpt.action_id:
        raise ValueError("ABSTAINED_GPT_HAS_ACTION")
    if gpt.status == "GPT_ADVICE" and not gpt.action_id:
        raise ValueError("GPT_ADVICE_MISSING_ACTION")
    if local is not None and (
            not isinstance(local, ObservedFullGridDiagnostic)
            or local.ranking_status != "NO_RANKING"
            or local.ready_for_engine
            or local.game_battle_authenticated
            or local.opponent_mapping_authenticated
            or local.exhaustive_opponent_policy
            or local.full_state_reconstructed
            or local.match_id != submitted.window.match_id
            or local.decision_window_id != submitted.window.decision_window_id
            or local.state_revision != submitted.window.state_revision
            or local.observed_snapshot_sha256 != submitted.snapshot_sha256):
        raise ValueError("LOCAL_DIAGNOSTIC_NOT_SAME_WINDOW_OR_PROMOTED")
    local_status = (
        "MODEL_DIAGNOSTIC" if local is not None and
        local.status == "SYNTHETIC_TURN_DIAGNOSTIC_ONLY"
        else "NO_RANKING")
    if local_status not in _LOCAL_STATUSES:
        raise ValueError("INVALID_LOCAL_STATUS")
    verified = (gpt.status == "GPT_ADVICE"
                and gpt.action_id in submitted.verified_action_ids)
    # This indicates membership in the frozen allowed-action *list*, not
    # proof that the GPT move was executed or strategically correct.
    comparison = ("NO_VERIFIED_LOCAL_ACTION"
                  if local_status == "MODEL_DIAGNOSTIC"
                  else "LOCAL_ABSTAINED")
    stamp = recorded_at or datetime.now(timezone.utc).isoformat()
    try:
        parsed = datetime.fromisoformat(stamp)
        if parsed.utcoffset() is None:
            raise ValueError("timezone missing")
    except (ValueError, TypeError):
        raise ValueError("RECORDED_AT_MUST_HAVE_TIMEZONE") from None
    return ShadowComparisonRecord(
        submitted.window.match_id, submitted.window.decision_window_id,
        submitted.window.state_revision, submitted.snapshot_sha256,
        submitted.window.team_sha256,
        fingerprint(list(map(list, submitted.window.four_master_sha256))),
        fingerprint(submitted.observed_state_json),
        submitted.window.allowed_action_digest,
        gpt.status, gpt.action_id[:128], verified, gpt.reason,
        gpt.elapsed_ms, local_status,
        ((local.reason if local else "NO_LOCAL_DIAGNOSTIC")[:256]),
        local_elapsed_ms,
        (local.evaluated_branch_count if local_status == "MODEL_DIAGNOSTIC" else 0),
        comparison, remaining_ms_at_capture > 0, stamp)


def append_shadow_comparison(
    database_path: str | Path, record: ShadowComparisonRecord,
) -> str:
    """Append exactly one atomic same-window record. Never overwrite evidence.

    Repeated identical inserts are idempotent. A different snapshot, verdict,
    timing or GPT result for an existing match/window/revision is rejected.
    Call only from an explicitly opted-in shadow/diagnostic task, not LIVE.
    """
    if not isinstance(record, ShadowComparisonRecord):
        raise ValueError("INVALID_SHADOW_RECORD")
    path = Path(database_path)
    if not path.parent.is_dir():
        raise ValueError("DATABASE_DIRECTORY_MISSING")
    cols = tuple(ShadowComparisonRecord.__dataclass_fields__)
    values = tuple(getattr(record, name) for name in cols)
    with sqlite3.connect(path, timeout=3.0) as db:
        db.execute("BEGIN IMMEDIATE")
        _ensure_schema(db)
        key = (record.match_id, record.decision_window_id, record.state_revision)
        old = db.execute(
            "SELECT " + ",".join(cols) +
            " FROM phase2_shadow_same_state WHERE match_id=? AND decision_window_id=? AND state_revision=?",
            key).fetchone()
        if old is not None:
            if tuple(old) != tuple(int(v) if isinstance(v, bool) else v for v in values):
                raise ValueError("CONFLICTING_SAME_WINDOW_RECORD")
            return "ALREADY_RECORDED"
        db.execute(
            "INSERT INTO phase2_shadow_same_state(schema," +
            ",".join(cols) + ") VALUES (" +
            ",".join("?" for _ in range(len(cols)+1)) + ")",
            (_SCHEMA, *(int(v) if isinstance(v, bool) else v for v in values)))
    return "RECORDED"


def read_shadow_comparisons(database_path: str | Path) -> tuple[ShadowComparisonRecord, ...]:
    """Read recorded aggregate facts only. No updates or action adoption."""
    path = Path(database_path)
    if not path.is_file():
        return ()
    uri = "file:" + str(path.resolve()).replace("?", "%3F").replace("#", "%23") + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        cols = tuple(ShadowComparisonRecord.__dataclass_fields__)
        records = db.execute(
            "SELECT " + ",".join(cols) +
            " FROM phase2_shadow_same_state ORDER BY match_id,decision_window_id,state_revision"
        ).fetchall()
    return tuple(ShadowComparisonRecord(
        *(bool(v) if name in ("gpt_action_verified_in_window", "input_deadline_met") else v
          for name, v in zip(cols, row)))
        for row in records)
