"""Issue #141⑤: isolated, same-revision GPT vs LOCAL_SHADOW SQLite evidence log.

Opt-in diagnostic only. Never import the GUI, read a source file, interpret a
model as a verified battle action, or replace GPT production recommendations.
Only hashes/labels and timing are stored; no screenshot, token or raw OCR.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import os
import re
import sqlite3
from typing import Any, Callable

from .models import FrozenBattleState, action_digest, fingerprint
from .observed_full_grid_diagnostic import ObservedFullGridDiagnostic

_SCHEMA = "issue141-shadow-same-state-v2"
_TABLE = "phase2_shadow_same_state_v2"
_ACCEPTED_TABLE = "phase2_shadow_finalized_v1"
_LOCAL_STATUSES = frozenset({"NO_RANKING", "MODEL_DIAGNOSTIC"})
_GPT_STATUSES = frozenset({"GPT_ADVICE", "GPT_ABSTAIN", "GPT_UNVERIFIED"})

# Only closed, non-sensitive labels are permitted in durable storage. Raw GPT
# reason text, OCR snippets, unverified move strings and worker error details
# remain caller-owned, in-memory data and MUST never cross the SQLite boundary.
_GPT_REASON_CODES = frozenset({
    "GPT_REGISTERED_ACTION", "GPT_UNREGISTERED_ACTION",
    "GPT_ABSTAINED", "GPT_NOT_VERIFIED",
})
_LOCAL_REASON_CODES = frozenset({
    "LOCAL_SYNTHETIC_DIAGNOSTIC", "LOCAL_NOT_RUN",
    "LOCAL_WORKER_REFUSED", "LOCAL_STALE_OBSERVATION",
    "LOCAL_DEADLINE", "LOCAL_SOURCE_REFUSED",
    "LOCAL_READOUT_REFUSED", "LOCAL_P2_REFUSED",
    "LOCAL_FULL_GRID_REFUSED", "LOCAL_INVALID_INPUT",
    "LOCAL_OTHER_REFUSAL",
})
_LOCAL_REASON_BY_PREFIX = {
    "SYNTHETIC_P2_OR_TURN_WORKER_UNAVAILABLE": "LOCAL_WORKER_REFUSED",
    "OBSERVATION_REVISION_CHANGED": "LOCAL_STALE_OBSERVATION",
    "OBSERVATION_CHANGED_OR_DEADLINE": "LOCAL_DEADLINE",
    "FROZEN_BATTLE_PREFLIGHT_DEADLINE": "LOCAL_DEADLINE",
    "PRE_REGISTERED_HYPOTHESES_OR_DEADLINE": "LOCAL_DEADLINE",
    "SOURCE_BOUND_ACTION_FRONTIER_REFUSED": "LOCAL_SOURCE_REFUSED",
    "JOINT_SOURCE_INVALID": "LOCAL_SOURCE_REFUSED",
    "OBSERVED_SYNTHETIC_READOUT_REFUSED": "LOCAL_READOUT_REFUSED",
    "INCOMPLETE_OR_UNBOUND_SYNTHETIC_P2_REQUEST": "LOCAL_P2_REFUSED",
    "FROZEN_BATTLE_READOUT_SHA_MISMATCH": "LOCAL_P2_REFUSED",
    "SYNTHETIC_FULL_GRID_REFUSED": "LOCAL_FULL_GRID_REFUSED",
    "INVALID_CURRENT_OBSERVED_GRID_REQUEST": "LOCAL_INVALID_INPUT",
    "INVALID_COMPLETE_SYNTHETIC_BATTLE": "LOCAL_INVALID_INPUT",
    "SYNTHETIC_BATTLE_SIZE_LIMIT": "LOCAL_INVALID_INPUT",
}
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:/-]{1,128}\Z")


def _local_reason_code(status: str, reason: str) -> str:
    if status == "MODEL_DIAGNOSTIC":
        return "LOCAL_SYNTHETIC_DIAGNOSTIC"
    if not reason:
        return "LOCAL_NOT_RUN"
    # Only the known machine-issued reason PREFIX is classified; arbitrary
    # suffixes and attacker-controlled strings are NEVER stored or hashed.
    prefix = reason.removeprefix("LOCAL_ABSTAIN:").split(":", 1)[0]
    return _LOCAL_REASON_BY_PREFIX.get(prefix, "LOCAL_OTHER_REFUSAL")


def _gpt_reason_code(status: str, verified: bool) -> str:
    if status == "GPT_ADVICE":
        return "GPT_REGISTERED_ACTION" if verified else "GPT_UNREGISTERED_ACTION"
    return "GPT_ABSTAINED" if status == "GPT_ABSTAIN" else "GPT_NOT_VERIFIED"


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
    # A digest is kept ONLY for a frozen-window verified action. Never store
    # GPT-provided action text, even if it resembles a valid move.
    gpt_action_sha256: str
    gpt_action_verified_in_window: bool
    gpt_reason_code: str
    gpt_elapsed_ms: int
    local_status: str
    local_reason_code: str
    local_elapsed_ms: int
    hypothetical_branch_count: int
    same_action_comparison: str
    input_deadline_met: bool
    recorded_at: str


def _valid_time(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 600_000


def _ensure_schema(db: sqlite3.Connection) -> None:
    db.execute(f"""CREATE TABLE IF NOT EXISTS {_TABLE} (
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
        gpt_action_sha256 TEXT NOT NULL,
        gpt_action_verified_in_window INTEGER NOT NULL,
        gpt_reason_code TEXT NOT NULL,
        gpt_elapsed_ms INTEGER NOT NULL,
        local_status TEXT NOT NULL,
        local_reason_code TEXT NOT NULL,
        local_elapsed_ms INTEGER NOT NULL,
        hypothetical_branch_count INTEGER NOT NULL,
        same_action_comparison TEXT NOT NULL,
        input_deadline_met INTEGER NOT NULL,
        recorded_at TEXT NOT NULL,
        PRIMARY KEY(match_id, decision_window_id, state_revision)
    )""")


def _pending_lease_path(
    database_path: str | Path, record: ShadowComparisonRecord,
) -> Path:
    """Only a hashed identity goes into this diagnostic sidecar filename."""
    if not isinstance(record, ShadowComparisonRecord):
        raise ValueError("SHADOW_LEASE_REQUIRES_RECORD")
    digest = sha256((
        record.match_id + ":" + record.decision_window_id + ":" +
        str(record.state_revision) + ":" + record.snapshot_sha256
    ).encode("utf-8")).hexdigest()
    path = Path(database_path)
    return path.with_name(path.name + ".pending-" + digest)


def begin_shadow_lease(
    database_path: str | Path, record: ShadowComparisonRecord,
) -> None:
    """Durable off-UI failure barrier before the FIRST SQLite write.

    A crash or failed revocation can leave a finalized row, but its lease
    remains and makes the entire postmatch software report fail closed.
    No OCR/GPT/action/HP values are written to this marker.
    """
    path = _pending_lease_path(database_path, record)
    if not path.parent.is_dir():
        raise ValueError("SHADOW_LEASE_DIRECTORY_MISSING")
    descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, b"PENDING\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def finish_shadow_lease(
    database_path: str | Path, record: ShadowComparisonRecord,
) -> None:
    """Off-worker only. Clear the barrier AFTER success or confirmed revoke."""
    # Both the worker and its serialized cleanup task can independently
    # finish the SAME revocation. Removing the barrier must be idempotent.
    _pending_lease_path(database_path, record).unlink(missing_ok=True)


def pending_shadow_lease_count(database_path: str | Path) -> int:
    """Fail closed on any unfinished write/revoke in the same SQLite corpus."""
    path = Path(database_path)
    return sum(1 for _ in path.parent.glob(path.name + ".pending-*"))


def _record_fingerprint(record: ShadowComparisonRecord) -> str:
    """Fingerprint all stored fields (not just a reused match/window ID)."""
    fields = tuple(ShadowComparisonRecord.__dataclass_fields__)
    return fingerprint([getattr(record, name) for name in fields])


def _ensure_finalized_schema(db: sqlite3.Connection) -> None:
    db.execute(f"""CREATE TABLE IF NOT EXISTS {_ACCEPTED_TABLE} (
        match_id TEXT NOT NULL,
        decision_window_id TEXT NOT NULL,
        state_revision INTEGER NOT NULL,
        snapshot_sha256 TEXT NOT NULL,
        record_sha256 TEXT NOT NULL,
        PRIMARY KEY(match_id, decision_window_id, state_revision)
    )""")


def finalize_shadow_comparison(
    database_path: str | Path, record: ShadowComparisonRecord,
    *, active_at_commit: Callable[[], bool] | None = None,
) -> str:
    """Durable proof that _finish accepted an *already committed* row.

    This runs off the GUI thread only AFTER the session's in-memory commit
    decision, which is serialized against OFF/new revision by the short lock.
    A row whose cleanup fails or crashes before this separate acknowledgment
    remains in raw SQLite, but cannot be counted by the software audit.
    """
    if not isinstance(record, ShadowComparisonRecord):
        raise ValueError("FINALIZED_SHADOW_RECORD_REQUIRED")
    if active_at_commit is not None:
        if not callable(active_at_commit):
            raise ValueError("INVALID_FINALIZATION_FENCE")
        if active_at_commit() is not True:
            raise ValueError("SHADOW_WINDOW_REVOKED_BEFORE_FINALIZATION")
    path = Path(database_path)
    if not path.is_file():
        raise ValueError("UNCOMMITTED_SHADOW_DATABASE")
    fields = tuple(ShadowComparisonRecord.__dataclass_fields__)
    serialized = tuple(
        int(value) if isinstance(value, bool) else value
        for value in (getattr(record, name) for name in fields))
    key = (record.match_id, record.decision_window_id, record.state_revision)
    with closing(sqlite3.connect(path, timeout=3.0)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (_TABLE,)).fetchone() is None:
            raise ValueError("SHADOW_ROW_MISSING_AT_FINALIZATION")
        row = db.execute(
            "SELECT " + ",".join(fields) + " FROM " + _TABLE +
            " WHERE match_id=? AND decision_window_id=? AND state_revision=?",
            key).fetchone()
        if row != serialized:
            raise ValueError("SHADOW_ROW_CHANGED_BEFORE_FINALIZATION")
        _ensure_finalized_schema(db)
        digest = _record_fingerprint(record)
        old = db.execute(
            "SELECT snapshot_sha256,record_sha256 FROM " + _ACCEPTED_TABLE +
            " WHERE match_id=? AND decision_window_id=? AND state_revision=?",
            key).fetchone()
        if old is not None:
            if old != (record.snapshot_sha256, digest):
                raise ValueError("CONFLICTING_SHADOW_FINALIZATION")
            if active_at_commit is not None and active_at_commit() is not True:
                raise ValueError("SHADOW_WINDOW_REVOKED_DURING_FINALIZATION")
            return "ALREADY_FINALIZED"
        db.execute(
            "INSERT INTO " + _ACCEPTED_TABLE +
            " (match_id,decision_window_id,state_revision,snapshot_sha256,"
            "record_sha256) VALUES (?,?,?,?,?)",
            (*key, record.snapshot_sha256, digest))
        # Like append, run the revocation check while still inside
        # BEGIN IMMEDIATE, so the transaction rolls back on cancellation.
        if active_at_commit is not None and active_at_commit() is not True:
            raise ValueError("SHADOW_WINDOW_REVOKED_DURING_FINALIZATION")
    return "FINALIZED"


def build_shadow_comparison(
    submitted: FrozenBattleState, current: FrozenBattleState,
    gpt: BoundGPTAdvice, local: ObservedFullGridDiagnostic | None,
    *, local_elapsed_ms: int, remaining_ms_at_capture: int,
    recorded_at: str | None = None,
    local_refusal_reason: str = "",
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
            ):
        raise ValueError("GPT_OR_TIMING_UNBOUND")
    if gpt.status != "GPT_ADVICE" and gpt.action_id:
        raise ValueError("ABSTAINED_GPT_HAS_ACTION")
    if gpt.status == "GPT_ADVICE" and not gpt.action_id:
        raise ValueError("GPT_ADVICE_MISSING_ACTION")
    if (not isinstance(local_refusal_reason, str)
            or (local is not None and local_refusal_reason)):
        raise ValueError("INVALID_LOCAL_REFUSAL_REASON")
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
    # Only a verified in-window action is hashed; all unverified action
    # text is dropped completely. Even a direct SQLite record can store
    # nothing but a 64-character hash in this column.
    action_sha = sha256(
        (submitted.snapshot_sha256 + ":" + gpt.action_id).encode("utf-8")
    ).hexdigest() if verified else ""
    gpt_reason_code = _gpt_reason_code(gpt.status, verified)
    local_reason = _local_reason_code(
        local_status,
        local.reason if local is not None else local_refusal_reason)
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
        gpt.status, action_sha, verified, gpt_reason_code,
        gpt.elapsed_ms, local_status, local_reason,
        local_elapsed_ms,
        (local.evaluated_branch_count if local_status == "MODEL_DIAGNOSTIC" else 0),
        comparison, remaining_ms_at_capture > 0, stamp)


def append_shadow_comparison(
    database_path: str | Path, record: ShadowComparisonRecord,
    *, active_at_commit: Callable[[], bool] | None = None,
) -> str:
    """Append exactly one atomic same-window record. Never overwrite evidence.

    Repeated identical inserts are idempotent. A different snapshot, verdict,
    timing or GPT result for an existing match/window/revision is rejected.
    Call only from an explicitly opted-in shadow/diagnostic task, not LIVE.

    An optional revocation fence is checked INSIDE the SQLite write transaction
    just before commit. The opt-in concurrent caller must additionally enqueue
    a serialized off-GUI exact-window purge at invalidation, because the cancel
    event can race with the micro-gap between the fence and SQLite commit.
    """
    if not isinstance(record, ShadowComparisonRecord):
        raise ValueError("INVALID_SHADOW_RECORD")
    if active_at_commit is not None and not callable(active_at_commit):
        raise ValueError("INVALID_SHADOW_REVOCATION_FENCE")
    if active_at_commit is not None and active_at_commit() is not True:
        raise ValueError("SHADOW_WINDOW_REVOKED_BEFORE_WRITE")
    path = Path(database_path)
    if not path.parent.is_dir():
        raise ValueError("DATABASE_DIRECTORY_MISSING")
    if (record.local_status not in _LOCAL_STATUSES
            or record.gpt_status not in _GPT_STATUSES
            or record.gpt_reason_code not in _GPT_REASON_CODES
            or record.local_reason_code not in _LOCAL_REASON_CODES
            or type(record.gpt_action_verified_in_window) is not bool
            or type(record.input_deadline_met) is not bool
            or (record.gpt_reason_code != _gpt_reason_code(
                record.gpt_status, record.gpt_action_verified_in_window))
            or (bool(record.gpt_action_sha256) !=
                record.gpt_action_verified_in_window)
            or (bool(record.gpt_action_sha256) and
                (not isinstance(record.gpt_action_sha256, str) or
                 _HEX64.fullmatch(record.gpt_action_sha256) is None))
            or (record.local_status == "MODEL_DIAGNOSTIC"
                and record.local_reason_code != "LOCAL_SYNTHETIC_DIAGNOSTIC")
            or (record.local_status == "NO_RANKING"
                and record.local_reason_code == "LOCAL_SYNTHETIC_DIAGNOSTIC")
            or not isinstance(record.match_id, str)
            or not isinstance(record.decision_window_id, str)
            or _SAFE_IDENTIFIER.fullmatch(record.match_id) is None
            or _SAFE_IDENTIFIER.fullmatch(record.decision_window_id) is None
            or record.same_action_comparison not in (
                "NO_VERIFIED_LOCAL_ACTION", "LOCAL_ABSTAINED")
            or record.gpt_action_verified_in_window not in (True, False)
            or record.input_deadline_met not in (True, False)
            or any(not _valid_time(v) for v in (
                record.gpt_elapsed_ms, record.local_elapsed_ms))
            or any(not isinstance(value, str)
                   or _HEX64.fullmatch(value) is None
                   for value in (
                       record.snapshot_sha256, record.team_sha256,
                       record.evidence_sha256, record.master_manifest_sha256,
                       record.allowed_action_digest))
            or record.hypothetical_branch_count < 0
            or (record.local_status == "NO_RANKING"
                and record.hypothetical_branch_count != 0)
            or (record.local_status == "MODEL_DIAGNOSTIC"
                and record.same_action_comparison != "NO_VERIFIED_LOCAL_ACTION")):
        raise ValueError("INVALID_NONACTIONABLE_SHADOW_RECORD")
    try:
        if (datetime.fromisoformat(record.recorded_at).utcoffset() is None
                or len(record.recorded_at) > 64):
            raise ValueError("INVALID_RECORDED_AT")
    except (TypeError, ValueError):
        raise ValueError("INVALID_NONACTIONABLE_SHADOW_RECORD") from None
    cols = tuple(ShadowComparisonRecord.__dataclass_fields__)
    values = tuple(getattr(record, name) for name in cols)
    # sqlite3.Connection.__exit__ commits/rolls back but DOES NOT close the
    # handle. A leaked handle locks the database on Windows (WinError 32)
    # when the owning temporary directory is removed. The outer closing()
    # must run after the inner transaction context, including on early return
    # (idempotent record) and exception (conflicting record).
    with closing(sqlite3.connect(path, timeout=3.0)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        _ensure_schema(db)
        key = (record.match_id, record.decision_window_id, record.state_revision)
        old = db.execute(
            "SELECT " + ",".join(cols) +
            " FROM " + _TABLE + " WHERE match_id=? AND decision_window_id=? AND state_revision=?",
            key).fetchone()
        if old is not None:
            if tuple(old) != tuple(int(v) if isinstance(v, bool) else v for v in values):
                raise ValueError("CONFLICTING_SAME_WINDOW_RECORD")
            if active_at_commit is not None and active_at_commit() is not True:
                raise ValueError("SHADOW_WINDOW_REVOKED_BEFORE_EXISTING_ROW")
            return "ALREADY_RECORDED"
        db.execute(
            "INSERT INTO " + _TABLE + "(schema," +
            ",".join(cols) + ") VALUES (" +
            ",".join("?" for _ in range(len(cols)+1)) + ")",
            (_SCHEMA, *(int(v) if isinstance(v, bool) else v for v in values)))
        # A cancellation while waiting for SQLite BEGIN IMMEDIATE or during
        # INSERT must roll back the entire transaction, not leave evidence.
        if active_at_commit is not None and active_at_commit() is not True:
            raise ValueError("SHADOW_WINDOW_REVOKED_BEFORE_COMMIT")
    return "RECORDED"


def revoke_shadow_comparison(
    database_path: str | Path, *,
    match_id: str, decision_window_id: str, state_revision: int,
    snapshot_sha256: str,
) -> str:
    """Remove a revoked in-flight result by *exact* identity off the UI.

    Never touch another match/window/revision/snapshot. Called as the next
    job on the SAME single-worker executor after any pending SQLite append;
    this serialization is essential even if close() happened during commit.
    Previous finished/accepted records are not revoked by session.close().
    """
    if (_SAFE_IDENTIFIER.fullmatch(match_id or "") is None
            or _SAFE_IDENTIFIER.fullmatch(decision_window_id or "") is None
            or type(state_revision) is not int or state_revision < 0
            or not isinstance(snapshot_sha256, str)
            or _HEX64.fullmatch(snapshot_sha256) is None):
        raise ValueError("INVALID_SHADOW_REVOKE_IDENTITY")
    path = Path(database_path)
    if not path.is_file():
        return "NO_ROW"
    with closing(sqlite3.connect(path, timeout=3.0)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (_TABLE,)).fetchone() is None:
            return "NO_ROW"
        identity = (match_id, decision_window_id, state_revision,
                    snapshot_sha256)
        deleted = db.execute(
            "DELETE FROM " + _TABLE +
            " WHERE match_id=? AND decision_window_id=? AND state_revision=?"
            " AND snapshot_sha256=?", identity).rowcount
        if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (_ACCEPTED_TABLE,)).fetchone() is not None:
            db.execute(
                "DELETE FROM " + _ACCEPTED_TABLE +
                " WHERE match_id=? AND decision_window_id=? AND state_revision=?"
                " AND snapshot_sha256=?", identity)
    return "REVOKED" if deleted else "NO_ROW"


def read_shadow_comparisons(
    database_path: str | Path, *, finalized_only: bool = False,
) -> tuple[ShadowComparisonRecord, ...]:
    """Read raw rows, or only separately finalized software evidence.

    Raw rows are NOT accepted-stage proof: a failed cleanup may leave a
    revoked, never-finalized window in the same SQLite file.
    """
    path = Path(database_path)
    if not path.is_file():
        return ()
    if finalized_only and pending_shadow_lease_count(path):
        # A committed finalized receipt is NOT yet auditable while the
        # finalizing future is still in progress or revocation is unresolved.
        return ()
    uri = "file:" + str(path.resolve()).replace("?", "%3F").replace("#", "%23") + "?mode=ro"
    # Same close guarantee on the read-only path. Otherwise Windows cannot
    # remove a database after a diagnostic read even when the SELECT ended.
    with closing(sqlite3.connect(uri, uri=True)) as db:
        # A revoked transaction can roll back the *first CREATE TABLE*:
        # SQLite file exists but has no committed schema. That means zero
        # recorded evidence, not a successful or corrupted shadow row.
        if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (_TABLE,)).fetchone() is None:
            return ()
        cols = tuple(ShadowComparisonRecord.__dataclass_fields__)
        query = ("SELECT " + ",".join(cols) + " FROM " + _TABLE +
                 " ORDER BY match_id,decision_window_id,state_revision")
        if finalized_only:
            if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (_ACCEPTED_TABLE,)).fetchone() is None:
                return ()
            accepted = {
                (match, window, revision): (snapshot, digest)
                for match, window, revision, snapshot, digest in db.execute(
                    "SELECT match_id,decision_window_id,state_revision,"
                    "snapshot_sha256,record_sha256 FROM " + _ACCEPTED_TABLE)
            }
        else:
            accepted = None
        records = db.execute(query).fetchall()
    results = tuple(ShadowComparisonRecord(
        *(bool(v) if name in ("gpt_action_verified_in_window", "input_deadline_met") else v
          for name, v in zip(cols, row)))
        for row in records)
    if accepted is None:
        return results
    if pending_shadow_lease_count(path):
        # Check again after the read in case an in-flight writer started
        # while opening SQLite. Avoid accepting mixed-window snapshots.
        return ()
    return tuple(
        record for record in results
        if accepted.get((record.match_id, record.decision_window_id,
                         record.state_revision)) ==
           (record.snapshot_sha256, _record_fingerprint(record)))
