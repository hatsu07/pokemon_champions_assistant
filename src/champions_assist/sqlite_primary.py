"""SQLite-primary persistent Champions assets and lossless migration boundary.

All operations run BETWEEN matches. Legacy CSV/JSON are retained as immutable
sources or compatibility exports. Never import a debug trace into strategy data.
"""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
from pathlib import Path
from datetime import datetime, timezone
import json
import sqlite3
import shutil

from .app_config import ConfigurationError
from .matchup_learning_store import MatchupLearningStore
from .team_matchup_v2 import SCHEMA_V2, validate_v2_reply, evidence_key
from .team_ai_analysis import SCHEMA as SCHEMA_V1, validate_reply

def _hash(raw: bytes) -> str:
    return sha256(raw).hexdigest()

def _json(value) -> str:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))

def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()

class ChampionsPrimaryData:
    def __init__(self, root: str | Path):
        self.root=Path(root)
        self.learning=MatchupLearningStore(self.root)

    def _db(self):
        db=self.learning._db()
        db.executescript("""
          CREATE TABLE IF NOT EXISTS source_archives(
            source_kind TEXT NOT NULL, source_key TEXT NOT NULL,
            sha256 TEXT NOT NULL, content BLOB NOT NULL,
            imported_at TEXT NOT NULL,
            PRIMARY KEY(source_kind, source_key, sha256)
          );
          CREATE TABLE IF NOT EXISTS current_sources(
            source_kind TEXT NOT NULL, source_key TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            PRIMARY KEY(source_kind, source_key),
            FOREIGN KEY(source_kind,source_key,sha256)
             REFERENCES source_archives(source_kind,source_key,sha256)
          );
          CREATE TABLE IF NOT EXISTS migration_audit(
            id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
            input_manifest TEXT NOT NULL, complete INTEGER NOT NULL DEFAULT 0,
            expected_sources INTEGER NOT NULL, verified_sources INTEGER NOT NULL DEFAULT 0
          );
        """)
        return db

    def archive_source(self, kind: str, key: str, path: str | Path) -> str:
        if kind not in ("team_csv","team_index","master","usage","provenance","review"):
            raise ConfigurationError("SQLite取込み対象外のファイルです")
        file=Path(path)
        raw=file.read_bytes()
        if len(raw)>12_000_000:
            raise ConfigurationError("保存元が検査上限を超えています")
        digest=_hash(raw)
        # Preserve bytes and source provenance; never alter source itself.
        with closing(self._db()) as db, db:
            db.execute("""INSERT OR IGNORE INTO source_archives
                (source_kind,source_key,sha256,content,imported_at)
                VALUES (?,?,?,?,?)""",(kind,key,digest,raw,_utc()))
            db.execute("""INSERT INTO current_sources(source_kind,source_key,sha256)
                VALUES (?,?,?) ON CONFLICT(source_kind,source_key)
                DO UPDATE SET sha256=excluded.sha256""",(kind,key,digest))
        return digest

    def verified_export(self, kind: str, key: str) -> bytes | None:
        with closing(self._db()) as db:
            row=db.execute("""SELECT a.content,a.sha256 FROM current_sources c
                JOIN source_archives a ON a.source_kind=c.source_kind
                AND a.source_key=c.source_key AND a.sha256=c.sha256
                WHERE c.source_kind=? AND c.source_key=?""",(kind,key)).fetchone()
        if row and _hash(row[0]) != row[1]:
            raise ConfigurationError("SQLite原本のSHAが不一致です")
        return row[0] if row else None

    def archive_all_sources(self, *, master_dir: str | Path | None = None) -> dict:
        counts={}
        paths=[]
        teams=self.root/"teams"
        for team in sorted(teams.glob("*.csv"))[:128]:
            paths.append(("team_csv",team.stem,team))
        index=teams/"index.json"
        if index.exists():
            paths.append(("team_index","registered",index))
        evidence=self.root/"battle-evidence"
        for kind,filename in (("usage","singles-usage.json"),
                              ("provenance","singles-provenance.json")):
            p=evidence/filename
            if p.exists():
                paths.append((kind,"singles",p))
        for file in sorted((self.root/"matchup-reviews").glob("*.json"))[:128]:
            paths.append(("review",file.stem,file))
        if master_dir:
            for file in sorted(Path(master_dir).glob("Pokemon_Champions_MC_*")):
                if file.suffix in (".csv",".txt") and file.is_file():
                    paths.append(("master",file.name,file))
        for kind,key,path in paths:
            self.archive_source(kind,key,path)
            counts[kind]=counts.get(kind,0)+1
        return {"source_archived":len(paths),"kinds":counts}

    def migration_manifest(self, *, master_dir: str | Path | None = None) -> dict:
        """Create a byte-level audit of source inputs plus a *real SQLite*
        online backup before writing a new schema. Legacy files remain intact.
        """
        files=[]
        for dirname in ("teams","live-team-assets","battle-evidence","matchup-reviews"):
            folder=self.root/dirname
            if not folder.exists():
                continue
            for file in sorted(folder.rglob("*")):
                if file.is_symlink() or not file.is_file():
                    continue
                if file.suffix not in (".json",".jsonl",".csv",".txt"):
                    continue
                # Skip debug logs by design (not in the eligible roots).
                files.append((file.relative_to(self.root).as_posix(),_hash(file.read_bytes())))
                if len(files)>12500:
                    raise ConfigurationError("監査ファイル上限を超えています")
        if master_dir:
            for file in sorted(Path(master_dir).glob("Pokemon_Champions_MC_*")):
                if file.suffix in (".csv",".txt") and file.is_file():
                    files.append(("masters/"+file.name,_hash(file.read_bytes())))
        result={"schema":"champions-sqlite-migration-audit-v1","sources":files,
                "generated_at":_utc()}
        result["input_sha256"]=_hash(_json(files).encode("utf-8"))
        return result

    def migrate(self, *, master_dir: str | Path | None = None) -> dict:
        """Idempotent. Never deletes/rewrites a v28 or v29 file."""
        audit=self.migration_manifest(master_dir=master_dir)
        digest=audit["input_sha256"]
        backups=self.root/"migration-backups"
        backups.mkdir(parents=True,exist_ok=True)
        report=backups/("migration-"+digest[:20]+".json")
        if not report.exists():
            # File-level audit is durable even if process terminates later.
            from .app_config import atomic_write_text
            atomic_write_text(report,json.dumps(audit,ensure_ascii=False,indent=2))
        # sqlite3 online backup before any migration: existing SQLite is never
        # copied by naive file read while a WAL writer may be active.
        dbfile=self.learning.db_path
        snap=backups/("learning-before-"+digest[:20]+".sqlite3")
        if dbfile.exists() and not snap.exists():
            with closing(sqlite3.connect(dbfile)) as origin:
                with closing(sqlite3.connect(snap)) as backup:
                    origin.backup(backup)
        counts=self.archive_all_sources(master_dir=master_dir)
        imported=self.learning.import_legacy_assets()
        verify=self.verify_manifest(audit)
        with closing(self._db()) as db, db:
            db.execute("""INSERT OR REPLACE INTO migration_audit(
                id,started_at,input_manifest,complete,expected_sources,verified_sources)
                VALUES(?,?,?,?,?,?)""",(
                digest,audit["generated_at"],_json(audit),int(verify["matched"]),
                len(audit["sources"]),verify["verified_files"]))
        return {"status":"COMPLETE" if verify["matched"] else "SOURCE_CHANGED_DURING_MIGRATION",
                "audit_path":str(report),"db_backup":str(snap) if snap.exists() else None,
                **counts,**imported,**verify}

    def verify_manifest(self, audit: dict) -> dict:
        checked=0
        missing=[]
        for path,digest in audit["sources"]:
            # Only inspect user-data roots, never arbitrary paths.
            if path.startswith("masters/"):
                continue
            file=self.root/path
            if not file.is_file() or _hash(file.read_bytes())!=digest:
                missing.append(path)
            else:
                checked+=1
        expected=sum(not p.startswith("masters/") for p,_ in audit["sources"])
        return {"matched":not missing,"verified_files":checked,
                "expected_files":expected,"changed_files":missing[:10]}

    @staticmethod
    def _materialize_bytes(path: Path, raw: bytes) -> Path:
        # Derived files only; old team/source files are never overwritten.
        if path.is_file():
            if _hash(path.read_bytes()) != _hash(raw):
                raise ConfigurationError("SQLite生成キャッシュが外部変更されました")
            return path
        path.parent.mkdir(parents=True,exist_ok=True)
        import os
        import tempfile
        fd,temporary=tempfile.mkstemp(prefix=".sqlite-live-",dir=str(path.parent))
        try:
            with os.fdopen(fd,"wb") as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary,path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return path

    def materialize_for_live(self, *, team_id: str,
                             master_dir: str | Path,
                             source_path: str | Path,
                             environment_snapshot: str | Path | None) -> tuple[Path,Path,Path | None]:
        """Materialize immutable byte-exact pre-match inputs FROM SQLite.

        All filesystem/SQLite work occurs before the four-file preload.
        Existing editable CSV and bundled master files remain unmodified.
        """
        original=Path(source_path)
        self.archive_source("team_csv",team_id,original)
        team_bytes=self.verified_export("team_csv",team_id)
        if team_bytes is None or team_bytes!=original.read_bytes():
            raise ConfigurationError("SQLiteと登録構築のSHAが不一致です")
        base=self.root/".sqlite-live-cache"
        digest=_hash(team_bytes)
        staged_team=self._materialize_bytes(
            base/"teams"/digest/(team_id+".csv"),team_bytes)
        with closing(self._db()) as db:
            keys=[x[0] for x in db.execute(
                """SELECT source_key FROM current_sources
                   WHERE source_kind='master' ORDER BY source_key""")]
        required={
            "Pokemon_Champions_MC_usable_pokemon.csv",
            "Pokemon_Champions_MC_move_master.csv",
            "Pokemon_Champions_MC_pokemon_learnsets_linked.csv",
            "Pokemon_Champions_MC_live_selection_matrix.txt",
            "Pokemon_Champions_MC_60sec_lookup_master.txt",
            "Pokemon_Champions_MC_team_2026-10-03.csv",
        }
        if not required.issubset(keys):
            raise ConfigurationError("SQLiteのLIVE固定マスターが不足しています")
        # The cache fingerprint changes whenever any source master changes.
        master_digests=[]
        data={}
        for key in keys:
            if not key.startswith("Pokemon_Champions_MC_") or Path(key).name!=key:
                raise ConfigurationError("不正なマスターキーです")
            raw=self.verified_export("master",key)
            if raw is None:
                raise ConfigurationError("SQLite固定マスターの原本がありません")
            # Check archived version against the live bundled master before
            # accepting it; bundled updates must trigger a new archive version.
            installed=Path(master_dir)/key
            if installed.exists() and _hash(installed.read_bytes())!=_hash(raw):
                self.archive_source("master",key,installed)
                raw=self.verified_export("master",key)
            master_digests.append(key+":"+_hash(raw))
            data[key]=raw
        master_fingerprint=_hash("|".join(master_digests).encode("utf-8"))
        staged_master=base/"masters"/master_fingerprint
        for key,raw in data.items():
            self._materialize_bytes(staged_master/key,raw)
        env_path=Path(environment_snapshot) if environment_snapshot else None
        if env_path and env_path.is_file():
            self.archive_source("usage","singles",env_path)
            receipt=env_path.with_name("singles-provenance.json")
            if receipt.is_file():
                self.archive_source("provenance","singles",receipt)
            env_bytes=self.verified_export("usage","singles")
            receipt_bytes=self.verified_export("provenance","singles")
            if env_bytes is None:
                raise ConfigurationError("SQLiteの環境統計が不正です")
            env_dir=base/"environment"/_hash(env_bytes+(receipt_bytes or b""))
            env_path=self._materialize_bytes(env_dir/"singles-usage.json",env_bytes)
            if receipt_bytes:
                self._materialize_bytes(
                    env_dir/"singles-provenance.json",receipt_bytes)
        return staged_master,staged_team,env_path

    @staticmethod
    def _row_request(catalog: dict, digest: str, cells: list[dict]) -> dict:
        return MatchupLearningStore._request_for_rows(catalog,digest,cells)

    def store_proposals(self, catalog_path: str | Path, validated: dict) -> int:
        """Atomic SQLite write first. JSON is optional backward-compatible
        mirror, not needed to decide which v2 cells are already evaluated."""
        path=Path(catalog_path)
        raw=path.read_bytes()
        digest=_hash(raw)
        if digest!=validated.get("catalog_sha256"):
            raise ConfigurationError("保存中にChampions対面根拠が変更されました")
        catalog=json.loads(raw)
        rows=validated.get("proposals",[])
        if not rows:
            return 0
        cells={(c["opponent"],c["slot"],c["member_signature"]):c
               for c in catalog["cells"]}
        selected=[]
        for row in rows:
            key=(row["opponent"],row["slot"],row["member_signature"])
            if key not in cells or cells[key] in selected:
                raise ConfigurationError("対面キーが重複または不一致です")
            selected.append(cells[key])
        req=self._row_request(catalog,digest,selected)
        schema=validated.get("schema")
        if schema==SCHEMA_V2:
            validate_v2_reply(req,{"schema":schema,"catalog_sha256":digest,
                                   "proposals":rows})
        elif schema==SCHEMA_V1:
            validate_reply(req,{"schema":schema,"catalog_sha256":digest,
                                "proposals":rows})
        else:
            raise ConfigurationError("未対応の対面評価形式です")
        if (validated.get("status")!="AI_PROPOSAL_UNVERIFIED"
                or validated.get("live_approved") is not False
                or validated.get("processor_certified") is not False
                or validated.get("safe_switch_certified") is not False):
            raise ConfigurationError("AI評価を確定情報として格納できません")
        encoded=_json(validated).encode("utf-8")
        sha=_hash(encoded)
        team_id=path.parent.parent.name
        with closing(self._db()) as db, db:
            db.execute("""INSERT OR IGNORE INTO proposal_files
                (file_sha,catalog_sha,team_id,schema,source_path,imported_at,cells)
                VALUES(?,?,?,?,?,?,?)""",(sha,digest,team_id,schema,
                    "SQLITE_PRIMARY",_utc(),len(rows)))
            for cell,row in zip(selected,rows):
                db.execute("""INSERT OR IGNORE INTO proposal_cells
                    (file_sha,opponent,slot,member_signature,evidence_sha,
                    regulation,proposal_schema,payload)
                    VALUES(?,?,?,?,?,?,?,?)""",(sha,cell["opponent"],
                    cell["slot"],cell["member_signature"],evidence_key(cell,catalog),
                    catalog["regulation"],schema,_json(row)))
        return len(rows)

    def proposal_rows(self, catalog_path: str | Path, *, only_v2: bool = False) -> list[dict]:
        path=Path(catalog_path)
        digest=_hash(path.read_bytes())
        with closing(self._db()) as db:
            rows=db.execute("""SELECT pc.payload,pc.proposal_schema FROM
                proposal_cells pc JOIN proposal_files pf ON pc.file_sha=pf.file_sha
                WHERE pf.catalog_sha=? ORDER BY
                CASE pc.proposal_schema WHEN ? THEN 0 ELSE 1 END,
                pf.imported_at DESC""",(digest,SCHEMA_V2)).fetchall()
        result={}
        for value,schema in rows:
            if only_v2 and schema!=SCHEMA_V2:
                continue
            row=json.loads(value)
            key=(row["opponent"],row["slot"],row["member_signature"])
            result.setdefault(key,row)
        return list(result.values())

    def hydrate_unchanged(self, catalog_path: str | Path) -> dict:
        """Reuse only identical member and opponent evidence, across versions;
        never use stale mismatched form data from v28.
        """
        path=Path(catalog_path)
        catalog=json.loads(path.read_bytes())
        digest=_hash(path.read_bytes())
        from .team_matchup_v2 import evidence_key
        allowed={(c["opponent"],c["member_signature"],evidence_key(c,catalog)):c
                 for c in catalog["cells"] if not c.get("missing_source")}
        existing={(r["opponent"],r["slot"],r["member_signature"])
                  for r in self.proposal_rows(path,only_v2=True)}
        accepted=[]
        with closing(self._db()) as db:
            for opp,signature,proof,payload in db.execute(
                    """SELECT opponent,member_signature,evidence_sha,payload
                    FROM proposal_cells WHERE proposal_schema=?""",(SCHEMA_V2,)):
                cell=allowed.get((opp,signature,proof))
                if cell is None:
                    continue
                key=(cell["opponent"],cell["slot"],cell["member_signature"])
                if key in existing:
                    continue
                row=json.loads(payload)
                accepted.append({**row,"slot":cell["slot"]})
                existing.add(key)
        for offset in range(0,len(accepted),36):
            rows=accepted[offset:offset+36]
            self.store_proposals(path,{
                "schema":SCHEMA_V2,"catalog_sha256":digest,
                "status":"AI_PROPOSAL_UNVERIFIED",
                "live_approved":False,"processor_certified":False,
                "safe_switch_certified":False,
                "proposals":rows,
            })
        return {"reused_from_sqlite":len(accepted)}
