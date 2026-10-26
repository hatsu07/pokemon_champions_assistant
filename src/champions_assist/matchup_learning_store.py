"""Private, append-only learning database for v28+ team-scoped AI proposals.

Read legacy files; NEVER move, truncate or modify any v28 folder or source JSON.
Only call during setup/off-match or from the finished-match path.
The LIVE decision path continues to use frozen 4 files, never SQLite.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any

from .app_config import ConfigurationError
from .team_matchup_v2 import SCHEMA_V2, validate_v2_reply
from .team_ai_analysis import SCHEMA as SCHEMA_V1, validate_reply

_MAX_FILE = 3 * 1024 * 1024
_CATALOG_SCHEMA = "champions-matchup-evidence-v1"
_DB_FILENAME = "matchup-learning.sqlite3"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",",":"))


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class MatchupLearningStore:
    def __init__(self, data_root: str | Path):
        self.root = Path(data_root)
        self.db_path = self.root / _DB_FILENAME

    def _db(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS proposal_files(
          file_sha TEXT PRIMARY KEY, catalog_sha TEXT NOT NULL,
          team_id TEXT NOT NULL, schema TEXT NOT NULL, source_path TEXT NOT NULL,
          imported_at TEXT NOT NULL, cells INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS proposal_cells(
          file_sha TEXT NOT NULL, opponent TEXT NOT NULL, slot INTEGER NOT NULL,
          member_signature TEXT NOT NULL, evidence_sha TEXT NOT NULL,
          regulation TEXT NOT NULL, proposal_schema TEXT NOT NULL,
          payload TEXT NOT NULL,
          PRIMARY KEY(file_sha, opponent, slot, member_signature),
          FOREIGN KEY(file_sha) REFERENCES proposal_files(file_sha)
        );
        CREATE INDEX IF NOT EXISTS ix_cells_evidence
          ON proposal_cells(evidence_sha,opponent,member_signature);
        CREATE TABLE IF NOT EXISTS match_history(
          match_id TEXT PRIMARY KEY, team_id TEXT, result TEXT NOT NULL,
          observed_at TEXT NOT NULL, source TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS matchup_observations(
          match_id TEXT NOT NULL, side TEXT NOT NULL, pokemon TEXT NOT NULL,
          category TEXT NOT NULL, observed_value TEXT NOT NULL,
          source TEXT NOT NULL,
          PRIMARY KEY(match_id,side,pokemon,category),
          FOREIGN KEY(match_id) REFERENCES match_history(match_id)
        );
        CREATE TABLE IF NOT EXISTS postmatch_reviews(
          match_id TEXT PRIMARY KEY, team_id TEXT, review_schema TEXT NOT NULL,
          review_json TEXT NOT NULL, reviewed_at TEXT NOT NULL,
          status TEXT NOT NULL, FOREIGN KEY(match_id) REFERENCES match_history(match_id)
        );
        """ )
        return db

    @staticmethod
    def _request_for_rows(catalog: dict, catalog_sha: str, cells: list[dict]) -> dict:
        return {
            "catalog_sha256":catalog_sha,
            "schema":SCHEMA_V2,
            "cells":[{k:c[k] for k in ("opponent","slot","member_signature")}
                     for c in cells],
            "own_members":[{"slot":c["slot"],"moves":c["registered_moves"],
                            "traits":c["own_species"]} for c in cells],
            "opponents":[{"name":c["opponent"],"traits":c["opponent_species"]}
                         for c in cells],
            "regulation":catalog["regulation"],
            "member_signatures":catalog["member_signatures"],
        }

    def _import_file(self, db: sqlite3.Connection, file: Path,
                     catalog: dict, catalog_sha: str, team_id: str) -> int:
        if not file.is_file() or file.stat().st_size > _MAX_FILE:
            return 0
        raw = file.read_bytes()
        digest = sha256(raw).hexdigest()
        if db.execute("SELECT 1 FROM proposal_files WHERE file_sha=?",(digest,)).fetchone():
            return 0
        stored = json.loads(raw.decode("utf-8-sig"))
        schema = stored.get("schema")
        if (schema not in (SCHEMA_V1,SCHEMA_V2)
                or stored.get("catalog_sha256") != catalog_sha
                or stored.get("status") != "AI_PROPOSAL_UNVERIFIED"
                or stored.get("live_approved") is not False
                or stored.get("processor_certified") is not False
                or stored.get("safe_switch_certified") is not False):
            return 0
        rows = stored.get("proposals")
        if not isinstance(rows,list) or not rows or len(rows)>48:
            return 0
        catalog_keys={(c["opponent"],c["slot"],c["member_signature"]):c
                      for c in catalog["cells"]}
        pairs=[]
        keys=set()
        for r in rows:
            if not isinstance(r,dict):
                return 0
            key=(r.get("opponent"),r.get("slot"),r.get("member_signature"))
            if key not in catalog_keys or key in keys:
                return 0
            keys.add(key)
            pairs.append(catalog_keys[key])
        request=self._request_for_rows(catalog,catalog_sha,pairs)
        if schema==SCHEMA_V2:
            validate_v2_reply(request,{"schema":SCHEMA_V2,
                                       "catalog_sha256":catalog_sha,"proposals":rows})
        else:
            validate_reply(request,{"schema":SCHEMA_V1,
                                    "catalog_sha256":catalog_sha,"proposals":rows})
        from .team_matchup_v2 import evidence_key
        # File and all cells enter the DB together, preserving batch atomicity.
        with db:
            db.execute("""INSERT INTO proposal_files
                (file_sha,catalog_sha,team_id,schema,source_path,imported_at,cells)
                VALUES(?,?,?,?,?,?,?)""",
                (digest,catalog_sha,team_id,schema,str(file),_stamp(),len(rows)))
            for cell,row in zip(pairs,rows):
                db.execute("""INSERT INTO proposal_cells
                    (file_sha,opponent,slot,member_signature,evidence_sha,
                     regulation,proposal_schema,payload)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (digest,cell["opponent"],cell["slot"],cell["member_signature"],
                     evidence_key(cell,catalog),catalog["regulation"],schema,_json(row)))
        return len(rows)

    def import_legacy_assets(self, *, max_catalogs: int = 200,
                             max_files: int = 10000) -> dict:
        """Read all complete v28/v29 JSON batches, including former fingerprints.

        Idempotent and repeatable: useful when v28 is still writing new batches.
        Incomplete/invalid batches are skipped without marking them as imported;
        immutable valid batches reappear on the next scan.
        """
        imported=files=skipped=0
        directories = sorted((self.root/"live-team-assets").glob("*/*/matchup-evidence.json"))
        with self._db() as db:
            for catalog_path in directories[:max_catalogs]:
                try:
                    raw=catalog_path.read_bytes()
                    if len(raw)>_MAX_FILE:
                        skipped+=1
                        continue
                    catalog=json.loads(raw)
                    if (catalog.get("schema")!=_CATALOG_SCHEMA
                            or not isinstance(catalog.get("cells"),list)):
                        skipped+=1
                        continue
                    digest=sha256(raw).hexdigest()
                    team_id=catalog_path.parent.parent.name
                    for proposal_file in sorted(catalog_path.parent.glob("ai-proposal-*.json")):
                        if files>=max_files:
                            break
                        files+=1
                        try:
                            imported+=self._import_file(db,proposal_file,catalog,digest,team_id)
                        except (OSError,UnicodeError,ValueError,TypeError,KeyError,
                                sqlite3.DatabaseError,ConfigurationError):
                            skipped+=1
                except (OSError,UnicodeError,ValueError,TypeError,KeyError):
                    skipped+=1
                if files>=max_files:
                    break
            total=db.execute("SELECT COUNT(*) FROM proposal_cells").fetchone()[0]
            counted=db.execute("SELECT COUNT(*) FROM proposal_files").fetchone()[0]
        return {"imported_cells":imported,"stored_cells_all_versions":total,
                "stored_batches":counted,"files_examined":files,
                "invalid_or_incomplete":skipped,"legacy_files_preserved":True}

    def coverage(self, catalog_path: str | Path) -> dict:
        path=Path(catalog_path)
        raw=path.read_bytes()
        data=json.loads(raw)
        sha=sha256(raw).hexdigest()
        with self._db() as db:
            rows=db.execute("""SELECT pc.opponent,pc.slot,pc.proposal_schema
                FROM proposal_cells pc JOIN proposal_files pf
                ON pc.file_sha=pf.file_sha WHERE pf.catalog_sha=?""",(sha,)).fetchall()
            corrections=db.execute("""SELECT COUNT(*) FROM postmatch_reviews
                WHERE team_id=?""",(path.parent.parent.name,)).fetchone()[0]
        resolved={}
        for opponent,slot,schema in rows:
            key=(opponent,slot)
            if resolved.get(key)!=SCHEMA_V2:
                resolved[key]=schema
        blocked=sum(bool(x.get("missing_source")) and x.get("evaluation")=="UNKNOWN"
                    for x in data["cells"])
        return {
            "total_cells":len(data["cells"]),
            "ai_evaluated":len(resolved),
            "structured_v2":sum(v==SCHEMA_V2 for v in resolved.values()),
            "not_analyzed":max(0,len(data["cells"])-len(resolved)),
            "source_gaps":blocked,
            "postmatch_reviews":corrections,
        }

    def record_finished(self, *, match_id: str, team_id: str | None,
                        state: dict, source: str = "AI_VISUAL_UNVERIFIED") -> bool:
        """Append confirmed result flag and bounded *observed* state once.

        Visual AI assertions are candidates, never independently verified
        damage/S observations. No screenshot, token, or raw unbounded event dump.
        """
        if not match_id or len(match_id)>128 or state.get("phase")!="ENDED" or state.get("result") not in ("WIN","LOSS"):
            return False
        evidence=state.get("battle_evidence") or {}
        snapshot={
            "result":state["result"],
            "opponent_six":list(state.get("opponent_six") or [])[:6],
            "selected_three":list(state.get("selected_three") or [])[:3],
            "lead":state.get("lead"),
            "history":list(state.get("history") or [])[-30:],
            "battle_evidence":evidence,
            "observed_hp":state.get("observed_hp") or {},
            "observation_provenance":source,
        }
        if len(_json(snapshot))>120000:
            snapshot["history"]=snapshot["history"][-8:]
        payload=_json(snapshot)
        if len(payload)>150000:
            raise ConfigurationError("実戦観測記録がサイズ上限を超えました")
        with self._db() as db:
            if db.execute("SELECT 1 FROM match_history WHERE match_id=?",
                          (match_id,)).fetchone():
                return False
            with db:
                db.execute("""INSERT INTO match_history
                    (match_id,team_id,result,observed_at,source,payload)
                    VALUES(?,?,?,?,?,?)""",
                    (match_id,team_id,state["result"],_stamp(),source,payload))
                for category in ("hp_by_pokemon","item_by_pokemon",
                                 "ability_by_pokemon","form_by_pokemon",
                                 "status_by_pokemon","boosts"):
                    for side, entries in (evidence.get(category) or {}).items():
                        if side not in ("own","opponent") or not isinstance(entries,dict):
                            continue
                        for pokemon,value in list(entries.items())[:6]:
                            if not isinstance(pokemon,str) or not isinstance(value,str):
                                continue
                            db.execute("""INSERT OR IGNORE INTO matchup_observations
                                (match_id,side,pokemon,category,observed_value,source)
                                VALUES(?,?,?,?,?,?)""",
                                (match_id,side,pokemon[:100],category,value[:160],source))
            return True

    def pending_match(self, match_id: str) -> dict | None:
        with self._db() as db:
            row=db.execute("""SELECT team_id,payload FROM match_history
                WHERE match_id=? AND NOT EXISTS(
                  SELECT 1 FROM postmatch_reviews WHERE match_id=?)""",
                  (match_id,match_id)).fetchone()
        if not row:
            return None
        return {"match_id":match_id,"team_id":row[0],"snapshot":json.loads(row[1])}

    def save_postmatch_review(self, *, match_id: str, review: dict) -> bool:
        """Store AI review for NEXT match only. No on-field decision mutation."""
        if review.get("schema")!="champions-postmatch-review-v1":
            raise ConfigurationError("対戦後評価形式が不正です")
        if set(review)!={"schema","match_id","findings","next_match_notes"} or review["match_id"]!=match_id:
            raise ConfigurationError("対戦後評価と試合IDが一致しません")
        findings=review["findings"]
        notes=review["next_match_notes"]
        if (not isinstance(findings,list) or len(findings)>8
                or not isinstance(notes,str) or len(notes)>360):
            raise ConfigurationError("対戦後評価が上限を超えています")
        for item in findings:
            if (not isinstance(item,dict)
                    or set(item)!={"opponent","our_pokemon","observation",
                                    "issue","suggestion","certainty"}
                    or item["certainty"] not in ("OBSERVED_CANDIDATE","HYPOTHESIS")
                    or any(not isinstance(v,str) or len(v)>180 for v in item.values())):
                raise ConfigurationError("対戦後評価に未検証の断定か不正データがあります")
        with self._db() as db:
            with db:
                row=db.execute("SELECT team_id FROM match_history WHERE match_id=?",
                               (match_id,)).fetchone()
                if not row:
                    return False
                db.execute("""INSERT OR IGNORE INTO postmatch_reviews(
                    match_id,team_id,review_schema,review_json,reviewed_at,status)
                    VALUES(?,?,?,?,?,?)""",(
                    match_id,row[0],review["schema"],_json(review),_stamp(),
                    "AI_HYPOTHESIS_UNVERIFIED"))
            return True

    def recent_review_notes(self, team_id: str, *, limit: int = 10) -> list[dict]:
        with self._db() as db:
            rows=db.execute("""SELECT review_json FROM postmatch_reviews
                 WHERE team_id=? ORDER BY reviewed_at DESC LIMIT ?""",
                 (team_id,max(0,min(limit,20)))).fetchall()
        return [json.loads(x[0]) for x in rows]
