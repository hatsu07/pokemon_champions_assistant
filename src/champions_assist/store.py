"""SQLite append-only storage for observer evidence and immutable API snapshots."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from .reducer import StateReducer, canonical_json


EVENT_TYPES = frozenset(
    {
        "EVIDENCE",
        "PENDING_EFFECT",
        "RESOLVED_EFFECT",
        "FAILED_EFFECT",
        "NEGATED_EFFECT",
        "CONFIRMED_EFFECT",
        "DURATION_STARTED",
        "DURATION_CLEARED",
        "TURN_END_CONFIRMED",
        "NOTE",
        "ACTUAL_ACTION_CONFIRMED",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _payload_hash(payload: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class BattleStore:
    """Transactional persistence with no update/delete paths for audit records.

    The store accepts semantic events from the observer layer. It intentionally has
    no strategic-selection methods and does not communicate with an API.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        if self.database_path != Path(":memory:"):
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.database_path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        self._connection.close()

    def checkpoint(self) -> None:
        """Flush WAL pages before a user-approved next-match data migration."""
        if self.database_path == Path(":memory:"):
            return
        self._connection.execute("PRAGMA wal_checkpoint(FULL)")

    def _migrate(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS matches (
                    match_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS raw_observations (
                    observation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_id TEXT NOT NULL REFERENCES matches(match_id),
                    observation_id TEXT NOT NULL,
                    capture_seq INTEGER,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(match_id, observation_id)
                );

                CREATE TABLE IF NOT EXISTS battle_events (
                    event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_id TEXT NOT NULL REFERENCES matches(match_id),
                    event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(match_id, event_id)
                );

                CREATE TABLE IF NOT EXISTS confirmed_effects (
                    match_id TEXT NOT NULL REFERENCES matches(match_id),
                    effect_instance_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    PRIMARY KEY(match_id, effect_instance_id),
                    UNIQUE(match_id, event_id)
                );

                CREATE TABLE IF NOT EXISTS turn_ends (
                    match_id TEXT NOT NULL REFERENCES matches(match_id),
                    turn_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    PRIMARY KEY(match_id, turn_id),
                    UNIQUE(match_id, event_id)
                );

                CREATE TABLE IF NOT EXISTS api_snapshots (
                    match_id TEXT NOT NULL REFERENCES matches(match_id),
                    snapshot_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    PRIMARY KEY(match_id, snapshot_id)
                );

                CREATE TABLE IF NOT EXISTS match_assets (
                    match_id TEXT NOT NULL REFERENCES matches(match_id),
                    filename TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    contents TEXT NOT NULL,
                    PRIMARY KEY(match_id, filename)
                );

                CREATE TABLE IF NOT EXISTS decision_windows (
                    match_id TEXT NOT NULL REFERENCES matches(match_id),
                    decision_window_id TEXT NOT NULL,
                    game_turn_seq TEXT,
                    window_kind TEXT NOT NULL,
                    cause_event_id TEXT,
                    tactical_state_version INTEGER NOT NULL,
                    allowed_action_digest TEXT NOT NULL,
                    allowed_actions_json TEXT NOT NULL,
                    deadline_at_ms INTEGER,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(match_id, decision_window_id)
                );

                CREATE TABLE IF NOT EXISTS api_calls (
                    api_call_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_id TEXT NOT NULL REFERENCES matches(match_id),
                    decision_window_id TEXT NOT NULL,
                    tactical_state_version INTEGER NOT NULL,
                    allowed_action_digest TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    request_snapshot_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(match_id, decision_window_id, tactical_state_version, allowed_action_digest)
                );

                CREATE TABLE IF NOT EXISTS api_responses (
                    match_id TEXT NOT NULL REFERENCES matches(match_id),
                    request_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    transport TEXT NOT NULL,
                    provider_response_id TEXT,
                    disposition TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    response_json TEXT,
                    PRIMARY KEY(match_id, request_id)
                );

                CREATE TABLE IF NOT EXISTS api_usage (
                    match_id TEXT NOT NULL REFERENCES matches(match_id),
                    request_id TEXT NOT NULL,
                    day_utc TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    transport TEXT NOT NULL,
                    model TEXT NOT NULL,
                    external INTEGER NOT NULL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    estimated_cost_microusd INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    PRIMARY KEY(match_id, request_id)
                );

                CREATE TABLE IF NOT EXISTS state_snapshots (
                    match_id TEXT NOT NULL REFERENCES matches(match_id),
                    snapshot_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    projection_json TEXT NOT NULL,
                    source_event_seq INTEGER,
                    PRIMARY KEY(match_id, snapshot_id)
                );

                CREATE TRIGGER IF NOT EXISTS no_update_battle_events
                BEFORE UPDATE ON battle_events BEGIN SELECT RAISE(ABORT, 'battle_events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS no_delete_battle_events
                BEFORE DELETE ON battle_events BEGIN SELECT RAISE(ABORT, 'battle_events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS no_update_api_snapshots
                BEFORE UPDATE ON api_snapshots BEGIN SELECT RAISE(ABORT, 'api_snapshots are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS no_delete_api_snapshots
                BEFORE DELETE ON api_snapshots BEGIN SELECT RAISE(ABORT, 'api_snapshots are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS no_update_match_assets
                BEFORE UPDATE ON match_assets BEGIN SELECT RAISE(ABORT, 'match assets are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS no_delete_match_assets
                BEFORE DELETE ON match_assets BEGIN SELECT RAISE(ABORT, 'match assets are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS no_update_decision_windows
                BEFORE UPDATE ON decision_windows BEGIN SELECT RAISE(ABORT, 'decision windows are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS no_delete_decision_windows
                BEFORE DELETE ON decision_windows BEGIN SELECT RAISE(ABORT, 'decision windows are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS no_update_api_responses
                BEFORE UPDATE ON api_responses BEGIN SELECT RAISE(ABORT, 'api responses are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS no_delete_api_responses
                BEFORE DELETE ON api_responses BEGIN SELECT RAISE(ABORT, 'api responses are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS no_update_api_usage
                BEFORE UPDATE ON api_usage BEGIN SELECT RAISE(ABORT, 'api usage is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS no_delete_api_usage
                BEFORE DELETE ON api_usage BEGIN SELECT RAISE(ABORT, 'api usage is append-only'); END;
                """
            )
            self._connection.execute("PRAGMA user_version = 2")

    def _ensure_match(self, match_id: str) -> None:
        self._connection.execute(
            "INSERT OR IGNORE INTO matches(match_id, created_at) VALUES (?, ?)",
            (match_id, _utc_now()),
        )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._connection:
            yield

    def append_raw_observation(
        self,
        match_id: str,
        observation_id: str,
        payload: dict[str, Any],
        *,
        capture_seq: int | None = None,
    ) -> bool:
        """Append raw observer output. It is not interpreted as semantic state."""
        with self._transaction():
            self._ensure_match(match_id)
            result = self._connection.execute(
                """INSERT OR IGNORE INTO raw_observations
                   (match_id, observation_id, capture_seq, observed_at, payload_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (match_id, observation_id, capture_seq, _utc_now(), canonical_json(payload)),
            )
        return result.rowcount == 1

    def freeze_match_assets(
        self,
        match_id: str,
        *,
        contents: dict[str, str] | Any,
        sha256: dict[str, str] | Any,
    ) -> None:
        """Persist the four actual master reads as immutable match evidence."""
        if set(contents) != set(sha256):
            raise ValueError("asset contents and hashes must have identical file sets")
        with self._transaction():
            self._ensure_match(match_id)
            existing = self._connection.execute(
                "SELECT COUNT(*) FROM match_assets WHERE match_id = ?", (match_id,)
            ).fetchone()[0]
            if existing:
                raise ValueError("match assets already frozen for this match")
            self._connection.executemany(
                """INSERT INTO match_assets(match_id, filename, sha256, contents)
                   VALUES (?, ?, ?, ?)""",
                [(match_id, name, sha256[name], contents[name]) for name in sorted(contents)],
            )

    def record_decision_window(
        self,
        match_id: str,
        *,
        decision_window_id: str,
        game_turn_seq: str | None,
        window_kind: str,
        cause_event_id: str | None,
        tactical_state_version: int,
        allowed_action_digest: str,
        allowed_actions: dict[str, Any],
        deadline_at_ms: int | None,
    ) -> None:
        """Append one independently identified player choice window."""
        with self._transaction():
            self._ensure_match(match_id)
            self._connection.execute(
                """INSERT INTO decision_windows
                   (match_id, decision_window_id, game_turn_seq, window_kind, cause_event_id,
                    tactical_state_version, allowed_action_digest, allowed_actions_json,
                    deadline_at_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    match_id,
                    decision_window_id,
                    game_turn_seq,
                    window_kind,
                    cause_event_id,
                    tactical_state_version,
                    allowed_action_digest,
                    canonical_json(allowed_actions),
                    deadline_at_ms,
                    _utc_now(),
                ),
            )

    def append_event(
        self,
        match_id: str,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> bool:
        """Append one event and its idempotency marker atomically when needed."""
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unsupported event type: {event_type}")
        with self._transaction():
            self._ensure_match(match_id)
            existing = self._connection.execute(
                "SELECT 1 FROM battle_events WHERE match_id = ? AND event_id = ?",
                (match_id, event_id),
            ).fetchone()
            if existing is not None:
                return False
            if event_type == "CONFIRMED_EFFECT":
                effect_id = payload.get("effect_instance_id")
                if not isinstance(effect_id, str) or not effect_id:
                    raise ValueError("CONFIRMED_EFFECT requires effect_instance_id")
                marker = self._connection.execute(
                    """INSERT OR IGNORE INTO confirmed_effects(match_id, effect_instance_id, event_id)
                       VALUES (?, ?, ?)""",
                    (match_id, effect_id, event_id),
                )
                if marker.rowcount != 1:
                    return False
            if event_type == "TURN_END_CONFIRMED":
                turn_id = payload.get("turn_id")
                if not isinstance(turn_id, str) or not turn_id:
                    raise ValueError("TURN_END_CONFIRMED requires turn_id")
                marker = self._connection.execute(
                    "INSERT OR IGNORE INTO turn_ends(match_id, turn_id, event_id) VALUES (?, ?, ?)",
                    (match_id, turn_id, event_id),
                )
                if marker.rowcount != 1:
                    return False
            self._connection.execute(
                """INSERT INTO battle_events(match_id, event_id, event_type, occurred_at, payload_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (match_id, event_id, event_type, _utc_now(), canonical_json(payload)),
            )
        return True

    def append_evidence(
        self,
        match_id: str,
        evidence_id: str,
        *,
        field: str,
        value: Any,
        source_kind: str,
        quality: str,
        supersedes: tuple[str, ...] = (),
    ) -> bool:
        return self.append_event(
            match_id,
            evidence_id,
            "EVIDENCE",
            {
                "evidence_id": evidence_id,
                "field": field,
                "value": value,
                "source_kind": source_kind,
                "quality": quality,
                "supersedes": list(supersedes),
            },
        )

    def freeze_api_snapshot(self, match_id: str, snapshot_id: str, payload: dict[str, Any]) -> str:
        """Persist the exact local request payload before any future API transport."""
        encoded = canonical_json(payload)
        digest = _payload_hash(payload)
        with self._transaction():
            self._ensure_match(match_id)
            self._connection.execute(
                """INSERT INTO api_snapshots
                   (match_id, snapshot_id, created_at, payload_json, payload_sha256)
                   VALUES (?, ?, ?, ?, ?)""",
                (match_id, snapshot_id, _utc_now(), encoded, digest),
            )
        return digest

    def reserve_api_call(
        self,
        match_id: str,
        *,
        decision_window_id: str,
        tactical_state_version: int,
        allowed_action_digest: str,
        phase: str,
        request_snapshot_id: str,
    ) -> bool:
        """Reserve the one permitted API call key; no model call is made here."""
        with self._transaction():
            self._ensure_match(match_id)
            snapshot = self._connection.execute(
                "SELECT 1 FROM api_snapshots WHERE match_id = ? AND snapshot_id = ?",
                (match_id, request_snapshot_id),
            ).fetchone()
            if snapshot is None:
                raise ValueError("API call must reference an immutable snapshot")
            result = self._connection.execute(
                """INSERT OR IGNORE INTO api_calls
                   (match_id, decision_window_id, tactical_state_version, allowed_action_digest,
                    phase, request_snapshot_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    match_id,
                    decision_window_id,
                    tactical_state_version,
                    allowed_action_digest,
                    phase,
                    request_snapshot_id,
                    _utc_now(),
                ),
            )
        return result.rowcount == 1

    def reserve_model_request(
        self,
        match_id: str,
        *,
        decision_window_id: str,
        tactical_state_version: int,
        allowed_action_digest: str,
        phase: str,
        request_snapshot_id: str,
        request_payload: dict[str, Any],
    ) -> bool:
        """Atomically freeze one request and reserve its de-duplication key."""
        encoded = canonical_json(request_payload)
        payload_hash = _payload_hash(request_payload)
        with self._transaction():
            self._ensure_match(match_id)
            existing_call = self._connection.execute(
                """SELECT 1 FROM api_calls WHERE match_id = ? AND decision_window_id = ?
                   AND tactical_state_version = ? AND allowed_action_digest = ?""",
                (match_id, decision_window_id, tactical_state_version, allowed_action_digest),
            ).fetchone()
            if existing_call is not None:
                return False
            existing_snapshot = self._connection.execute(
                "SELECT payload_sha256 FROM api_snapshots WHERE match_id = ? AND snapshot_id = ?",
                (match_id, request_snapshot_id),
            ).fetchone()
            if existing_snapshot is not None:
                if existing_snapshot["payload_sha256"] != payload_hash:
                    raise ValueError("snapshot id already belongs to different immutable payload")
            else:
                self._connection.execute(
                    """INSERT INTO api_snapshots
                       (match_id, snapshot_id, created_at, payload_json, payload_sha256)
                       VALUES (?, ?, ?, ?, ?)""",
                    (match_id, request_snapshot_id, _utc_now(), encoded, payload_hash),
                )
            result = self._connection.execute(
                """INSERT OR IGNORE INTO api_calls
                   (match_id, decision_window_id, tactical_state_version, allowed_action_digest,
                    phase, request_snapshot_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    match_id,
                    decision_window_id,
                    tactical_state_version,
                    allowed_action_digest,
                    phase,
                    request_snapshot_id,
                    _utc_now(),
                ),
            )
        return result.rowcount == 1

    def record_api_response(
        self,
        match_id: str,
        *,
        request_id: str,
        transport: str,
        disposition: str,
        reason: str,
        response: dict[str, Any] | None,
        provider_response_id: str | None = None,
    ) -> None:
        """Append a response audit record. API keys are never accepted here."""
        with self._transaction():
            self._ensure_match(match_id)
            self._connection.execute(
                """INSERT INTO api_responses
                   (match_id, request_id, created_at, transport, provider_response_id,
                    disposition, reason, response_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    match_id,
                    request_id,
                    _utc_now(),
                    transport,
                    provider_response_id,
                    disposition,
                    reason,
                    None if response is None else canonical_json(response),
                ),
            )

    def record_api_usage(
        self,
        match_id: str,
        *,
        request_id: str,
        transport: str,
        model: str,
        external: bool,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
        estimated_cost_microusd: int,
        outcome: str,
        day_utc: str | None = None,
    ) -> None:
        if estimated_cost_microusd < 0:
            raise ValueError("estimated API cost cannot be negative")
        for value in (input_tokens, output_tokens, total_tokens):
            if value is not None and value < 0:
                raise ValueError("token counts cannot be negative")
        if day_utc is None:
            day_utc = datetime.now(timezone.utc).date().isoformat()
        with self._transaction():
            self._ensure_match(match_id)
            self._connection.execute(
                """INSERT INTO api_usage
                   (match_id, request_id, day_utc, created_at, transport, model, external,
                    input_tokens, output_tokens, total_tokens, estimated_cost_microusd, outcome)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    match_id,
                    request_id,
                    day_utc,
                    _utc_now(),
                    transport,
                    model,
                    int(external),
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    estimated_cost_microusd,
                    outcome,
                ),
            )

    def api_budget_totals(self, match_id: str, *, day_utc: str) -> dict[str, int]:
        """Return external transport counts/costs for pre-send budget gates."""
        match = self._connection.execute(
            """SELECT COUNT(*) AS calls, COALESCE(SUM(estimated_cost_microusd), 0) AS cost
               FROM api_usage WHERE match_id = ? AND external = 1""",
            (match_id,),
        ).fetchone()
        day = self._connection.execute(
            """SELECT COUNT(*) AS calls, COALESCE(SUM(estimated_cost_microusd), 0) AS cost
               FROM api_usage WHERE day_utc = ? AND external = 1""",
            (day_utc,),
        ).fetchone()
        return {
            "match_calls": int(match["calls"]),
            "match_cost_microusd": int(match["cost"]),
            "day_calls": int(day["calls"]),
            "day_cost_microusd": int(day["cost"]),
        }

    def api_usage_summary(self, match_id: str) -> dict[str, int]:
        """Return display-only API accounting; credentials are never persisted here."""
        row = self._connection.execute(
            """SELECT COUNT(*) AS calls,
                      COALESCE(SUM(input_tokens), 0) AS input_tokens,
                      COALESCE(SUM(output_tokens), 0) AS output_tokens,
                      COALESCE(SUM(total_tokens), 0) AS total_tokens,
                      COALESCE(SUM(estimated_cost_microusd), 0) AS cost_microusd,
                      COALESCE(SUM(external), 0) AS external_calls
               FROM api_usage WHERE match_id = ?""",
            (match_id,),
        ).fetchone()
        return {
            "calls": int(row["calls"]),
            "external_calls": int(row["external_calls"]),
            "input_tokens": int(row["input_tokens"]),
            "output_tokens": int(row["output_tokens"]),
            "total_tokens": int(row["total_tokens"]),
            "cost_microusd": int(row["cost_microusd"]),
        }

    def list_matches(self) -> list[dict[str, Any]]:
        """List local match ledgers for the desktop history view.

        A hidden record remains in the SQLite ledger until the user explicitly
        invokes the separate destructive purge action.  This preserves the
        append-only audit invariant during ordinary GUI use.
        """
        rows = self._connection.execute(
            """SELECT matches.match_id, matches.created_at,
                      (SELECT COUNT(*) FROM battle_events
                       WHERE battle_events.match_id = matches.match_id) AS event_count,
                      (SELECT MAX(occurred_at) FROM battle_events
                       WHERE battle_events.match_id = matches.match_id) AS last_event_at,
                      EXISTS(
                        SELECT 1 FROM battle_events AS visibility
                        WHERE visibility.match_id = matches.match_id
                          AND visibility.event_type = 'NOTE'
                          AND visibility.payload_json LIKE '%MATCH_HIDDEN_BY_USER%'
                      ) AS hidden
               FROM matches ORDER BY matches.created_at DESC"""
        ).fetchall()
        return [
            {
                "match_id": str(row["match_id"]),
                "created_at": str(row["created_at"]),
                "event_count": int(row["event_count"]),
                "last_event_at": row["last_event_at"],
                "hidden": bool(row["hidden"]),
            }
            for row in rows
        ]

    def api_responses(self, match_id: str) -> list[dict[str, Any]]:
        """Return decoded, credential-free response history for local review."""
        rows = self._connection.execute(
            """SELECT request_id, created_at, transport, provider_response_id,
                      disposition, reason, response_json
               FROM api_responses WHERE match_id = ? ORDER BY created_at""",
            (match_id,),
        ).fetchall()
        return [
            {
                "request_id": str(row["request_id"]),
                "created_at": str(row["created_at"]),
                "transport": str(row["transport"]),
                "provider_response_id": row["provider_response_id"],
                "disposition": str(row["disposition"]),
                "reason": str(row["reason"]),
                "response": None if row["response_json"] is None else json.loads(row["response_json"]),
            }
            for row in rows
        ]

    def mark_match_hidden(self, match_id: str, *, event_id: str) -> bool:
        """Hide a history row through a new audit event, without mutating history."""
        return self.append_event(
            match_id,
            event_id,
            "NOTE",
            {"kind": "MATCH_HIDDEN_BY_USER", "retention": "LEDGER_RETAINED"},
        )

    def export_match(self, match_id: str) -> dict[str, Any]:
        """Build an explicit local export without API credentials or master contents."""
        known = self._connection.execute("SELECT 1 FROM matches WHERE match_id = ?", (match_id,)).fetchone()
        if known is None:
            raise KeyError(f"unknown match: {match_id}")
        assets = self._connection.execute(
            """SELECT filename, sha256 FROM match_assets
               WHERE match_id = ? ORDER BY filename""",
            (match_id,),
        ).fetchall()
        raw_rows = self._connection.execute(
            """SELECT observation_id, capture_seq, observed_at, payload_json
               FROM raw_observations WHERE match_id = ? ORDER BY observation_seq""",
            (match_id,),
        ).fetchall()
        return {
            "match_id": match_id,
            "source_manifest": [
                {"filename": str(row["filename"]), "sha256": str(row["sha256"])} for row in assets
            ],
            "events": self.events(match_id),
            "raw_observations": [
                {
                    "observation_id": str(row["observation_id"]),
                    "capture_seq": row["capture_seq"],
                    "observed_at": str(row["observed_at"]),
                    "payload": json.loads(row["payload_json"]),
                }
                for row in raw_rows
            ],
            "api_responses": self.api_responses(match_id),
            "api_usage": self.api_usage_summary(match_id),
        }

    def purge_match_permanently(self, match_id: str, *, confirmation_match_id: str) -> None:
        """Permanently remove one local ledger after an exact explicit confirmation.

        Normal match processing never calls this method: append-only records stay
        protected.  This is a user-directed privacy/storage operation and cannot
        run for an ambiguous identifier.  Callers should offer export first.
        """
        if confirmation_match_id != match_id:
            raise ValueError("exact match_id confirmation is required for permanent deletion")
        known = self._connection.execute("SELECT 1 FROM matches WHERE match_id = ?", (match_id,)).fetchone()
        if known is None:
            raise KeyError(f"unknown match: {match_id}")
        protected_tables = (
            "battle_events",
            "api_snapshots",
            "match_assets",
            "decision_windows",
            "api_responses",
            "api_usage",
        )
        delete_order = (
            "confirmed_effects",
            "turn_ends",
            "api_calls",
            "api_responses",
            "api_usage",
            "state_snapshots",
            "decision_windows",
            "match_assets",
            "raw_observations",
            "battle_events",
        )
        try:
            with self._transaction():
                for table in protected_tables:
                    self._connection.execute(f"DROP TRIGGER IF EXISTS no_delete_{table}")
                for table in delete_order:
                    self._connection.execute(f"DELETE FROM {table} WHERE match_id = ?", (match_id,))
                self._connection.execute("DELETE FROM matches WHERE match_id = ?", (match_id,))
                for table in protected_tables:
                    self._connection.execute(
                        f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} "
                        f"BEGIN SELECT RAISE(ABORT, '{table} are immutable'); END"
                    )
        except Exception:
            # SQLite DDL is transactional; the context manager restores both
            # ledger rows and the original trigger set on failure.
            raise

    def load_api_snapshot(self, match_id: str, snapshot_id: str) -> dict[str, Any]:
        """Return an immutable payload copy for audit or local validation."""
        row = self._connection.execute(
            "SELECT payload_json FROM api_snapshots WHERE match_id = ? AND snapshot_id = ?",
            (match_id, snapshot_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown snapshot: {match_id}/{snapshot_id}")
        return json.loads(row["payload_json"])

    def replay(self, match_id: str) -> dict[str, Any]:
        rows = self._connection.execute(
            """SELECT event_seq, event_id, event_type, payload_json
               FROM battle_events WHERE match_id = ? ORDER BY event_seq""",
            (match_id,),
        ).fetchall()
        events = [
            {
                "event_seq": row["event_seq"],
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]
        return StateReducer().replay(events)

    def events(self, match_id: str) -> list[dict[str, Any]]:
        """Return a decoded append-only event history for offline replay/calibration."""
        rows = self._connection.execute(
            """SELECT event_seq, event_id, event_type, occurred_at, payload_json
               FROM battle_events WHERE match_id = ? ORDER BY event_seq""",
            (match_id,),
        ).fetchall()
        return [
            {
                "event_seq": row["event_seq"],
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "occurred_at": row["occurred_at"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def save_projection_snapshot(self, match_id: str, snapshot_id: str) -> dict[str, Any]:
        """Cache a derived projection for audit; replay remains the source of truth."""
        projection = self.replay(match_id)
        source = self._connection.execute(
            "SELECT MAX(event_seq) FROM battle_events WHERE match_id = ?", (match_id,)
        ).fetchone()[0]
        with self._transaction():
            self._ensure_match(match_id)
            self._connection.execute(
                """INSERT INTO state_snapshots(match_id, snapshot_id, created_at, projection_json, source_event_seq)
                   VALUES (?, ?, ?, ?, ?)""",
                (match_id, snapshot_id, _utc_now(), canonical_json(projection), source),
            )
        return projection

    def event_count(self, match_id: str) -> int:
        return int(
            self._connection.execute(
                "SELECT COUNT(*) FROM battle_events WHERE match_id = ?", (match_id,)
            ).fetchone()[0]
        )
