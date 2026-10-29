"""P1 (issue #114): bounded capture-time evidence, persisted ONLY after MATCH_END.

Nothing in this module calls AI, fetches files, updates LIVE assets, or stores
screenshots/OCR text. The live-side API only touches in-memory, bounded data.
Captured candidates never become confirmed battle facts by being persisted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from contextlib import closing
from typing import Any

from .app_config import ConfigurationError
from .matchup_learning_store import MatchupLearningStore

SCHEMA = "champions-time-frozen-evidence-v1"
MAX_EVENTS = 256
MAX_EVENT_BYTES = 3072
MAX_ARCHIVE_BYTES = 1024 * 1024
MAX_LABEL = 120
_ACTION_KEYS = ("kind", "move", "target", "switch_target", "mega", "planned_pivot_target")
_RESULT_STATUSES = frozenset(("MATCH_END", "RESULT_UNCONFIRMED"))
_ADVICE_STATUSES = frozenset(("OK", "PARTIAL_ADVICE", "ADVICE", "PROVISIONAL_ADVICE", "TEST_ONLY"))
_ALLOWED_CONFIRMATION = frozenset(("USER_CONFIRMED", "GAME_EVENT_CONFIRMED"))
_ALLOWED_CERTAINTY = frozenset(("UNKNOWN", "AI_VISUAL_CANDIDATE", "OCR_CANDIDATE",
                                "USER_CONFIRMED", "GAME_EVENT_CONFIRMED"))


def _text(value: Any, maximum: int = MAX_LABEL) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join("".join(c for c in value if c.isprintable()
                              and c not in "<>|").split())[:maximum]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _time() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_fingerprint(event: dict) -> str:
    """Deduplicate the same screen/recommendation independently of UI clock.

    The FIRST display time remains in the stored event. A later, otherwise
    identical Qt redraw must not manufacture an independent observation.
    """
    content = {key: value for key, value in event.items()
               if key not in ("_fingerprint", "captured_at")}
    proposal = content.get("proposal")
    if isinstance(proposal, dict):
        content["proposal"] = {key:value for key,value in proposal.items()
                               if key != "displayed_at"}
    return _hash(content)


def _names(value: Any, limit: int = 6) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [_text(item) for item in value[:limit] if isinstance(item, str) and _text(item)]


def _action(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    result: dict = {}
    for field in _ACTION_KEYS:
        item = value.get(field)
        if field == "mega":
            if type(item) is bool:
                result[field] = item
        elif isinstance(item, str) and item:
            result[field] = _text(item)
    return result


def _bounded_mapping(value: Any, *, limit: int = 12) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, item in list(value.items())[:limit]:
        if not isinstance(key, str):
            continue
        if isinstance(item, (int, float, bool, str)) or item is None:
            result[_text(key, 60)] = _text(item, 80) if isinstance(item, str) else item
        elif isinstance(item, dict):
            result[_text(key, 60)] = _bounded_mapping(item, limit=6)
    return result


def freeze_team_context(*, team_id: str, team_sha: str, model: str,
                        regulation: str = "", regulation_source: str = "",
                        regulation_observed_at: str = "",
                        environment_source: str = "",
                        environment_observed_at: str = "",
                        tactical_version: str = "",
                        frozen_assets_sha: dict[str, str] | None = None,
                        own_members: list[dict] | None = None) -> dict:
    """Called at PRE-MATCH setup; never reads a file or recomputes during play."""
    members = []
    for entry in (own_members or [])[:6]:
        if not isinstance(entry, dict):
            continue
        members.append({"name": _text(entry.get("name")),
                        "moves": [_text(entry.get(f"move{i}")) for i in range(1, 5)],
                        "item": _text(entry.get("item")),
                        "ability": _text(entry.get("ability")),
                        "nature": _text(entry.get("nature")),
                        "ap": _text(entry.get("ap_h_a_b_c_d_s")),
                        "form_change": _text(entry.get("form_change"))})
    return {
        "team_id": _text(team_id), "team_sha": _text(team_sha),
        "tactical_version": _text(tactical_version), "model": _text(model),
        "regulation": _text(regulation),
        "regulation_source": _text(regulation_source),
        "regulation_observed_at": _text(regulation_observed_at),
        "environment_source": _text(environment_source),
        "environment_observed_at": _text(environment_observed_at),
        "frozen_assets_sha": {
            _text(k, 90): _text(v, 90)
            for k, v in (frozen_assets_sha or {}).items()
            if isinstance(k, str) and isinstance(v, str)
        },
        "own_members": members, "frozen_at": _time(),
    }


class MatchEvidenceBuffer:
    """A small in-memory capture ledger. No filesystem/SQLite/API operations."""

    def __init__(self, *, match_id: str, context: dict | None = None):
        if not isinstance(match_id, str) or not 0 < len(match_id) <= 128:
            raise ConfigurationError("invalid evidence match id")
        self.match_id = match_id
        self.context = dict(context or {})
        self.events: list[dict] = []
        self._keys: set[str] = set()
        self.dropped_events = 0
        self.last_error = ""
        self.ended = False

    def capture(self, state: dict, report: dict, *, captured_at: str | None = None) -> bool:
        """Immutable capture-time observation/recommendation snapshot.

        The observed action is UNKNOWN until an explicit confirmation source is
        supplied. Neither a model recommendation nor an AI image reading is
        proof of execution.
        """
        if self.ended or not isinstance(state, dict) or not isinstance(report, dict):
            return False
        phase = _text(state.get("phase")) or "UNKNOWN"
        status = _text(report.get("status")) or "UNKNOWN"
        step = state.get("step_number")
        observation = report.get("observation") if isinstance(report.get("observation"), dict) else {}
        latest = (state.get("image_observations_unverified") or [])
        latest = latest[-1] if isinstance(latest, list) and latest and isinstance(latest[-1], dict) else {}
        own = _text(state.get("our_active")) or _text(observation.get("our_active"))
        opponent = _text(state.get("opponent_active")) or _text(observation.get("opponent_active"))
        members = self.context.get("own_members") or []
        current = next((m for m in members if m.get("name") == own), None)
        selected = _names(state.get("selected_three"), 3)
        registered_moves = list(current["moves"]) if current else []
        switch_candidates = [x for x in selected if x != own]
        evidence_source = _text(report.get("observation_source")) or "AI_VISUAL_CANDIDATE"
        if evidence_source not in _ALLOWED_CERTAINTY:
            evidence_source = "AI_VISUAL_CANDIDATE"
        confirmed = report.get("actual_action") if isinstance(report.get("actual_action"), dict) else {}
        confirmation = _text(report.get("actual_action_source"))
        if confirmation not in _ALLOWED_CONFIRMATION:
            confirmed = {}
            confirmation = "UNKNOWN"
        # Never invent a manual action from a recommendation, even on success.
        recommended = _action(report.get("action")) if status in _ADVICE_STATUSES else {}
        timestamp = captured_at or _time()
        hp = state.get("observed_hp") if isinstance(state.get("observed_hp"), dict) else {}
        event = {
            "captured_at": timestamp, "phase": phase,
            "step": step if type(step) is int and 0 <= step < 1000000 else None,
            "report_status": status,
            "turn_id": _text(state.get("turn_id") or state.get("turn") or state.get("game_turn_seq")) or "UNKNOWN",
            "frame_id": _text(str(report.get("frame_id") or report.get("capture_frame_id") or report.get("capture_seq") or "")) or "UNKNOWN",
            "report_source": _text(report.get("result_source")) or "UNKNOWN",
            "opponent_control": _text(state.get("opponent_control_classification"))
            if state.get("opponent_control_classification") in
            ("HUMAN", "CPU_SUSPECTED", "UNKNOWN") else "UNKNOWN",
            "observation": {
                "certainty": evidence_source, "ui": _text(latest.get("ui") or observation.get("visual_ui")),
                "own_active": own, "opponent_active": opponent,
                "own_hp": _text(latest.get("our_hp") or hp.get("own")),
                "opponent_hp": _text(latest.get("opponent_hp") or hp.get("opponent")),
                "last_event_candidate": _text(state.get("last_event")),
            },
            "selection": {"our_three": selected, "lead": _text(state.get("lead")),
                          "certainty": "AI_VISUAL_CANDIDATE" if state.get("selection_assumed")
                          else "UNKNOWN" if not selected else "USER_CONFIRMED"},
            "selection_proposal": {
                "our_three": _names(report.get("selected_three"), 3),
                "lead": _text(report.get("lead")),
                "certainty": "AI_HYPOTHESIS_UNVERIFIED"
                if status in _ADVICE_STATUSES else "UNKNOWN",
            },
            "legal_options": {
                "registered_moves": registered_moves,
                "switch_candidates": switch_candidates,
                "certainty": "REGISTERED_NOT_CURRENTLY_LEGALITY_VERIFIED",
                "visible_moves": _names(observation.get("visible_moves"), 4),
                "visible_party": _names(observation.get("visible_party"), 6),
                "observed_moves_candidate": _names(state.get("observed_moves"), 12),
                "choice_lock": _text(state.get("choice_lock")),
            },
            "proposal": {"action": recommended,
                         "certainty": "AI_HYPOTHESIS_UNVERIFIED" if recommended else "UNKNOWN",
                         "displayed_at": timestamp if recommended else None},
            "execution": {"action": _action(confirmed) if confirmed else {},
                          "certainty": confirmation},
            "field_candidate": {
                key: _text(state["battlefield"].get(key))
                for key in ("weather", "terrain", "effects")
            } if isinstance(state.get("battlefield"), dict) else {},
            "result": _text(state.get("result")) if status == "MATCH_END" else "UNKNOWN",
        }
        # No OCR raw text, image bytes, bearer credentials, arbitrary history,
        # remote request payload or model reasoning can enter this allowlist.
        encoded = _json(event)
        if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
            self.last_error = "EVENT_TOO_LARGE"
            self.dropped_events += 1
            return False
        # Identical display events are deduplicated, independent of timestamp.
        fingerprint = _event_fingerprint(event)
        if fingerprint in self._keys:
            return False
        if len(self.events) == MAX_EVENTS:
            oldest = self.events.pop(0)
            self._keys.discard(oldest["_fingerprint"])
            self.dropped_events += 1
        event["_fingerprint"] = fingerprint
        self.events.append(event)
        self._keys.add(fingerprint)
        return True

    def finish(self) -> dict:
        """Snapshot at ended match only. Safe to pass to off-match storage."""
        self.ended = True
        return {
            "schema": SCHEMA, "match_id": self.match_id,
            "context": json.loads(_json(self.context)),
            "events": json.loads(_json(self.events)),
            "dropped_events": self.dropped_events,
            "incomplete": self.dropped_events > 0,
            "status": "PARTIAL_CAPTURE" if self.dropped_events else "CAPTURED_UNVERIFIED",
        }


class MatchEvidenceArchive:
    """Non-destructive, atomic SQLite archive for AFTER-MATCH review only."""

    def __init__(self, data_root: str | Path):
        self.learning = MatchupLearningStore(data_root)
        self.backup_path = self.learning.root / "matchup-learning.pre-p1-backup.sqlite3"

    def _db(self) -> sqlite3.Connection:
        db = self.learning._db()
        has_table = db.execute("""SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='p1_evidence_match'""").fetchone()
        if not has_table:
            # One-time private backup BEFORE migration, only for a preexisting
            # database holding real matches. Never overwrite an older backup.
            present = db.execute("SELECT 1 FROM match_history LIMIT 1").fetchone()
            if present and not self.backup_path.exists():
                with closing(sqlite3.connect(self.backup_path)) as backup:
                    db.backup(backup)
        db.executescript("""
        CREATE TABLE IF NOT EXISTS p1_evidence_match(
          match_id TEXT PRIMARY KEY,
          team_id TEXT NOT NULL,
          team_sha TEXT NOT NULL,
          tactical_version TEXT NOT NULL,
          metadata_json TEXT NOT NULL,
          metadata_sha TEXT NOT NULL,
          captured_count INTEGER NOT NULL,
          dropped_count INTEGER NOT NULL,
          status TEXT NOT NULL,
          archived_at TEXT NOT NULL,
          FOREIGN KEY(match_id) REFERENCES match_history(match_id)
        );
        CREATE TABLE IF NOT EXISTS p1_evidence_events(
          match_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
          event_sha TEXT NOT NULL, event_json TEXT NOT NULL,
          PRIMARY KEY(match_id, ordinal),
          FOREIGN KEY(match_id) REFERENCES p1_evidence_match(match_id)
        );
        CREATE TRIGGER IF NOT EXISTS no_p1_match_update
          BEFORE UPDATE ON p1_evidence_match
          BEGIN SELECT RAISE(ABORT, 'P1 match archive immutable'); END;
        CREATE TRIGGER IF NOT EXISTS no_p1_match_delete
          BEFORE DELETE ON p1_evidence_match
          BEGIN SELECT RAISE(ABORT, 'P1 match archive immutable'); END;
        CREATE TRIGGER IF NOT EXISTS no_p1_event_update
          BEFORE UPDATE ON p1_evidence_events
          BEGIN SELECT RAISE(ABORT, 'P1 evidence immutable'); END;
        CREATE TRIGGER IF NOT EXISTS no_p1_event_delete
          BEFORE DELETE ON p1_evidence_events
          BEGIN SELECT RAISE(ABORT, 'P1 evidence immutable'); END;
        """)
        return db

    def save_ended(self, archive: dict) -> dict:
        """All-or-nothing insert. Identical retry is a no-op; mismatch fails."""
        if not isinstance(archive, dict) or archive.get("schema") != SCHEMA:
            raise ConfigurationError("P1 archive schema mismatch")
        match_id = archive.get("match_id")
        events = archive.get("events")
        context = archive.get("context")
        if (not isinstance(match_id, str) or not 0 < len(match_id) <= 128
                or not isinstance(events, list) or len(events) > MAX_EVENTS
                or not isinstance(context, dict)
                or archive.get("status") not in ("PARTIAL_CAPTURE", "CAPTURED_UNVERIFIED")
                or not isinstance(archive.get("dropped_events"), int)
                or archive["dropped_events"] < 0):
            raise ConfigurationError("P1 archive is malformed")
        if archive.get("incomplete") is not (archive["dropped_events"] > 0):
            raise ConfigurationError("P1 completeness mismatched")
        # Explicit contract: accept only snapshots produced by the bounded buffer.
        for event in events:
            if (not isinstance(event, dict) or "_fingerprint" not in event
                    or len(_json(event).encode("utf-8")) > MAX_EVENT_BYTES + 128):
                raise ConfigurationError("P1 event validation failed")
            fingerprint = event["_fingerprint"]
            if fingerprint != _event_fingerprint(event):
                raise ConfigurationError("P1 event fingerprint mismatched")
        payload = _json(archive)
        if len(payload.encode("utf-8")) > MAX_ARCHIVE_BYTES:
            raise ConfigurationError("P1 archive size limit exceeded")
        archive_sha = _hash(archive)
        with closing(self._db()) as db:
            row = db.execute(
                "SELECT team_id,payload FROM match_history WHERE match_id=?",
                (match_id,)).fetchone()
            if not row:
                return {"status":"NOT_FINISHED","match_id":match_id}
            team_id, history_payload = row
            # Cannot mix evidence across versions or opponent research teams.
            saved_sig = json.loads(history_payload).get("team_signature", "")
            if team_id != context.get("team_id") or saved_sig != context.get("team_sha"):
                return {"status":"VERSION_MISMATCH","match_id":match_id}
            current = db.execute("""SELECT metadata_sha FROM p1_evidence_match
                                    WHERE match_id=?""", (match_id,)).fetchone()
            if current:
                if current[0] != archive_sha:
                    raise ConfigurationError("P1 archive already exists with different evidence")
                return {"status":"ALREADY_STORED","match_id":match_id}
            with db:
                db.execute("""INSERT INTO p1_evidence_match(
                    match_id,team_id,team_sha,tactical_version,metadata_json,
                    metadata_sha,captured_count,dropped_count,status,archived_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",(
                    match_id,team_id,context.get("team_sha",""),
                    context.get("tactical_version",""),
                    _json(context),archive_sha,len(events),archive["dropped_events"],
                    archive["status"],_time()))
                for ordinal, event in enumerate(events):
                    db.execute("""INSERT INTO p1_evidence_events(
                        match_id,ordinal,event_sha,event_json) VALUES(?,?,?,?)""",
                        (match_id,ordinal,event["_fingerprint"],_json(event)))
        return {"status":"STORED_UNVERIFIED","match_id":match_id,
                "events":len(events),"incomplete":archive["incomplete"]}

    def read(self, match_id: str) -> dict | None:
        with closing(self._db()) as db:
            row = db.execute("""SELECT metadata_json,metadata_sha,captured_count,
                                       dropped_count,status FROM p1_evidence_match
                                WHERE match_id=?""",(match_id,)).fetchone()
            if not row:
                return None
            events = db.execute("""SELECT event_json FROM p1_evidence_events
                                   WHERE match_id=? ORDER BY ordinal""",
                                (match_id,)).fetchall()
            return {"match_id":match_id,"context":json.loads(row[0]),
                    "archive_sha":row[1],"captured_count":row[2],
                    "dropped_count":row[3],"status":row[4],
                    "events":[json.loads(e[0]) for e in events]}

    def verify(self, match_id: str) -> dict:
        """Read-only integrity check; no after-the-fact event rewrites."""
        record = self.read(match_id)
        if record is None:
            return {"status":"MISSING"}
        ok = len(record["events"]) == record["captured_count"]
        ok = ok and all(e.get("_fingerprint") == _event_fingerprint(e)
                        for e in record["events"])
        return {"status":"VALID" if ok else "CORRUPT",
                "count":len(record["events"])}
