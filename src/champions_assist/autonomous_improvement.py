"""Issue #92, stage 1: bounded, local, after-match improvement research.

The existing per-match GPT review supplies *hypotheses*, not verified facts.
This module combines independent finished matches without another API call,
modifies no active team/masters/LIVE matrix, and cannot promote an unverified
suggestion into a LIVE action.  Only call from the finished-review worker.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from .app_config import ConfigurationError
from .matchup_learning_store import MatchupLearningStore

SCHEMA = "champions-offmatch-improvement-research-v1"
MIN_INDEPENDENT_MATCHES = 3
MAX_REVIEWS = 40
MAX_CANDIDATES = 12
MAX_EVIDENCE_PER_CANDIDATE = 12
_MAX_TEXT = 180


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _hash(value: object) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _clean(value: object) -> str:
    if not isinstance(value, str):
        return ""
    filtered = "".join(ch for ch in value if ch.isprintable()
                       and ch not in "<>|")
    return " ".join(filtered.split())[:_MAX_TEXT]


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class AutonomousImprovementStore:
    """Append-only, private SQLite research journal; NO game or LIVE mutation."""

    def __init__(self, data_root: str | Path):
        self.learning = MatchupLearningStore(data_root)

    def _db(self) -> sqlite3.Connection:
        db = self.learning._db()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS autonomous_improvement_batches(
          batch_sha TEXT PRIMARY KEY,
          team_id TEXT NOT NULL,
          team_signature TEXT NOT NULL,
          created_at TEXT NOT NULL,
          schema TEXT NOT NULL,
          status TEXT NOT NULL,
          source_match_ids TEXT NOT NULL,
          source_review_sha TEXT NOT NULL,
          candidate_count INTEGER NOT NULL,
          promotion_status TEXT NOT NULL DEFAULT 'NOT_AUTHORIZED'
        );
        CREATE TABLE IF NOT EXISTS autonomous_improvement_candidates(
          candidate_sha TEXT PRIMARY KEY,
          batch_sha TEXT NOT NULL,
          candidate_kind TEXT NOT NULL,
          opponent TEXT NOT NULL,
          our_pokemon TEXT NOT NULL,
          issue TEXT NOT NULL,
          proposed_change TEXT NOT NULL,
          evidence_match_ids TEXT NOT NULL,
          certainty TEXT NOT NULL,
          legality TEXT NOT NULL,
          validation TEXT NOT NULL,
          risk TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(batch_sha) REFERENCES autonomous_improvement_batches(batch_sha)
        );
        CREATE INDEX IF NOT EXISTS ix_auto_improvement_team
          ON autonomous_improvement_batches(team_id, team_signature, created_at);
        """)
        return db

    @staticmethod
    def _review_records(db: sqlite3.Connection, team_id: str,
                        team_signature: str) -> list[dict]:
        rows = db.execute("""
            SELECT m.match_id, m.payload, m.result, r.review_json
              FROM postmatch_reviews r
              JOIN match_history m ON m.match_id=r.match_id
             WHERE m.team_id=? AND r.status='AI_HYPOTHESIS_UNVERIFIED'
               AND CASE WHEN json_valid(m.payload)
                        THEN json_extract(m.payload, '$.team_signature')
                        ELSE NULL END = ?
             ORDER BY m.observed_at DESC, m.match_id DESC LIMIT ?
            """, (team_id, team_signature, MAX_REVIEWS)).fetchall()
        records = []
        for match_id, snapshot_json, result, review_json in rows:
            try:
                snapshot = json.loads(snapshot_json)
                review = json.loads(review_json)
            except (TypeError, ValueError):
                continue
            # Never combine several team versions, even with the same team ID.
            if snapshot.get("team_signature", "") != team_signature:
                continue
            if result not in ("WIN", "LOSS") or review.get("match_id") != match_id:
                continue
            if review.get("schema") != "champions-postmatch-review-v1":
                continue
            entries = review.get("findings")
            if not isinstance(entries, list):
                continue
            records.append({"match_id": match_id, "result": result,
                            "findings": entries[:8]})
        return records

    def aggregate_after_review(self, *, match_id: str,
                               team_id: str | None = None) -> dict:
        """Create a candidate journal only after repeated independent reviews.

        No GPT transport, team CSV writes, status promotion, or speculative
        attribution of later-visible information to earlier turns.
        Replays/retries of the same review set do not duplicate candidates.
        """
        with closing(self._db()) as db:
            row = db.execute(
                "SELECT team_id,payload FROM match_history WHERE match_id=?",
                (match_id,)).fetchone()
            if row is None:
                return {"status": "NO_FINISHED_MATCH", "candidate_count": 0}
            stored_team, snapshot_json = row
            if not stored_team or (team_id is not None and stored_team != team_id):
                return {"status": "TEAM_ID_MISMATCH", "candidate_count": 0}
            if not db.execute("SELECT 1 FROM postmatch_reviews WHERE match_id=?",
                              (match_id,)).fetchone():
                return {"status": "NO_SAVED_REVIEW", "candidate_count": 0}
            try:
                snapshot = json.loads(snapshot_json)
                team_signature = snapshot.get("team_signature") if isinstance(snapshot, dict) else None
            except (TypeError, ValueError):
                team_signature = None
            # Legacy record_finished() defaults to an empty signature: it is
            # NOT evidence that several old matches used the same team version.
            if (not isinstance(team_signature, str) or
                    not team_signature.strip() or len(team_signature) > 256):
                return {"status": "TEAM_SIGNATURE_UNKNOWN", "candidate_count": 0}
            reviews = self._review_records(db, stored_team, team_signature)
            ids = {r["match_id"] for r in reviews}
            if len(ids) < MIN_INDEPENDENT_MATCHES:
                return {"status": "INSUFFICIENT_MATCHES", "reviewed_matches": len(ids),
                        "required_matches": MIN_INDEPENDENT_MATCHES, "candidate_count": 0}

            # Avoid running a new cycle for every single result. Require at
            # least three *new* distinct matches since the previous cycle.
            latest = db.execute("""
                SELECT source_match_ids FROM autonomous_improvement_batches
                 WHERE team_id=? AND team_signature=?
                 ORDER BY created_at DESC, rowid DESC LIMIT 1
                """, (stored_team, team_signature)).fetchone()
            old_ids = set(json.loads(latest[0])) if latest else set()
            if len(ids - old_ids) < MIN_INDEPENDENT_MATCHES:
                return {"status": "NO_NEW_INDEPENDENT_EVIDENCE",
                        "candidate_count": 0}

            # Review conclusions are still model hypotheses, not proof of the
            # actual turn's available information, correct move or cause.
            groups: dict[tuple[str, str, str], dict[str, dict]] = {}
            for review in reviews:
                for item in review["findings"]:
                    if not isinstance(item, dict):
                        continue
                    if item.get("certainty") not in ("HYPOTHESIS", "OBSERVED_CANDIDATE"):
                        continue
                    issue = _clean(item.get("issue"))
                    suggestion = _clean(item.get("suggestion"))
                    opponent = _clean(item.get("opponent"))
                    own = _clean(item.get("our_pokemon"))
                    if not issue or not suggestion:
                        continue
                    # Same match can never count twice for a repeated problem.
                    key = (opponent, own, issue)
                    groups.setdefault(key, {})[review["match_id"]] = {
                        "suggestion": suggestion,
                        "result": review["result"],
                    }
            candidates = []
            for (opponent, own, issue), evidence in sorted(groups.items()):
                if len(evidence) < MIN_INDEPENDENT_MATCHES:
                    continue
                matches = sorted(evidence)[:MAX_EVIDENCE_PER_CANDIDATE]
                suggestions = [evidence[m]["suggestion"] for m in matches]
                # Do not invent a composite recommendation: retain one exact
                # GPT hypothesis, and expose disagreement as an open risk.
                suggestion = max(sorted(set(suggestions)),
                                 key=lambda v: suggestions.count(v))
                candidates.append({
                    "opponent": opponent, "our_pokemon": own, "issue": issue,
                    "proposed_change": suggestion,
                    "evidence_match_ids": matches,
                    "candidate_kind": "TACTICAL_HYPOTHESIS",
                    "certainty": "GPT_HYPOTHESIS_UNVERIFIED",
                    "legality": "NOT_CHECKED",
                    "validation": "DEFERRED_UNVERIFIED",
                    "risk": ("Review suggestions differ; independent replay required"
                             if len(set(suggestions)) > 1
                             else "Postmatch review only; no turn-time proof"),
                })
            candidates.sort(key=lambda row: (
                -len(row["evidence_match_ids"]), row["issue"], row["opponent"]))
            candidates = candidates[:MAX_CANDIDATES]
            source = [{"match_id": r["match_id"],
                       "review_sha": _hash(r["findings"])}
                      for r in sorted(reviews, key=lambda r: r["match_id"])]
            batch_sha = _hash([SCHEMA, stored_team, team_signature, source])
            now = _stamp()
            with db:
                inserted = db.execute("""
                    INSERT OR IGNORE INTO autonomous_improvement_batches(
                      batch_sha,team_id,team_signature,created_at,schema,status,
                      source_match_ids,source_review_sha,candidate_count,
                      promotion_status)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                """, (batch_sha, stored_team, team_signature, now, SCHEMA,
                      "PROVISIONAL_UNVERIFIED" if candidates else "NO_REPEATED_PATTERN",
                      _json(sorted(ids)), _hash(source), len(candidates),
                      "NOT_AUTHORIZED")).rowcount
                if inserted:
                    for c in candidates:
                        key = _hash([batch_sha,c])
                        db.execute("""
                            INSERT OR IGNORE INTO autonomous_improvement_candidates(
                              candidate_sha,batch_sha,candidate_kind,opponent,
                              our_pokemon,issue,proposed_change,evidence_match_ids,
                              certainty,legality,validation,risk,created_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """, (key,batch_sha,c["candidate_kind"],c["opponent"],
                              c["our_pokemon"],c["issue"],c["proposed_change"],
                              _json(c["evidence_match_ids"]),c["certainty"],
                              c["legality"],c["validation"],c["risk"],now))
            return {"status": "STORED_PROVISIONAL_UNVERIFIED" if candidates
                    else "STORED_NO_REPEATED_PATTERN",
                    "batch_sha": batch_sha, "candidate_count": len(candidates),
                    "reviewed_matches": len(ids), "created": bool(inserted),
                    "live_applied": False}

    def history(self, *, team_id: str, team_signature: str | None = None,
                limit: int = 10) -> list[dict]:
        """Read-only audit for user-facing future research UI."""
        if not isinstance(team_id, str) or not team_id or len(team_id) > 128:
            raise ConfigurationError("invalid research team id")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ConfigurationError("invalid research history limit")
        with closing(self._db()) as db:
            sql = ("SELECT batch_sha, team_signature, status, created_at,"
                   "source_match_ids, candidate_count, promotion_status "
                   "FROM autonomous_improvement_batches WHERE team_id=?")
            args: list = [team_id]
            if team_signature is not None:
                sql += " AND team_signature=?"
                args.append(team_signature)
            sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
            args.append(limit)
            rows = db.execute(sql, args).fetchall()
        return [{"batch_sha": h,"team_signature":sig,"status":status,
                 "created_at":stamp,"match_ids":json.loads(match_ids),
                 "candidate_count":count,"promotion_status":promotion}
                for h,sig,status,stamp,match_ids,count,promotion in rows]

    def candidates(self, *, batch_sha: str) -> list[dict]:
        """Read-only candidates, never an executable LIVE change."""
        with closing(self._db()) as db:
            rows = db.execute("""
                SELECT candidate_sha,candidate_kind,opponent,our_pokemon,issue,
                       proposed_change,evidence_match_ids,certainty,legality,
                       validation,risk
                  FROM autonomous_improvement_candidates
                 WHERE batch_sha=? ORDER BY candidate_sha
            """, (batch_sha,)).fetchall()
        return [dict(candidate_sha=h,kind=kind,opponent=opp,our_pokemon=own,
                     issue=issue,proposed_change=change,evidence_match_ids=json.loads(ids),
                     certainty=certainty,legality=legality,validation=validation,
                     risk=risk)
                for h,kind,opp,own,issue,change,ids,certainty,legality,validation,risk in rows]
