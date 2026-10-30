"""Off-match, read-only team readiness snapshots and durable PREP workflow stages.

The UI must not regenerate LIVE assets to display a team's status. All
unverified AI hints remain conditional; no readiness metric certifies a KO,
safe switch, processor, or a completed in-battle 18-matchup PREP.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .app_config import atomic_write_text
from .matchup_evidence import member_signature, SOURCE_FILES
from .team_live_assets import SET_FIELDS
from .team_management import TeamDocument

STAGES = (
    ("team", "構築情報・整合性確認"),
    ("masters", "Champions固定マスター確認"),
    ("catalog", "対面カタログの準備"),
    ("reuse", "既存評価の差分再利用"),
    ("analysis", "不足対面のAI解析"),
    ("prep", "PREP評価素材の検査"),
    ("publish", "マトリクス・ルックアップ更新"),
    ("cache", "高速索引・最終整合性確認"),
)
STAGE_IDS = frozenset(key for key, _ in STAGES)
TERMINAL = frozenset({"完了", "一部完了", "停止", "失敗", "再準備が必要"})
STATE_VALUES = frozenset({"未実行", "実行中", *TERMINAL})
SCHEMA = "champions-prep-readiness-v1"


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def team_signatures(team_path: str | Path) -> list[str]:
    doc = TeamDocument.load(team_path)
    return [member_signature(member, SET_FIELDS) for member in doc.members]


def progress_path(data_root: str | Path, team_id: str) -> Path:
    return Path(data_root) / "live-team-assets" / team_id / "prep-progress.json"


class PrepWorkflowProgress:
    """Durable stage log scoped to one registered team + exact CSV bytes."""

    def __init__(self, data_root: str | Path, team_id: str, team_path: str | Path):
        self.path = progress_path(data_root, team_id)
        self.team_id = team_id
        self.team_sha = _digest(Path(team_path))
        self.state: dict[str, Any] = {}

    def start(self) -> dict:
        now = _stamp()
        self.state = {
            "schema": SCHEMA, "team_id": self.team_id,
            "team_sha256": self.team_sha, "fingerprint": None,
            "run_status": "実行中", "started_at": now, "updated_at": now,
            "finished_at": None, "counters": {},
            "stages": {key: {"status": "未実行", "detail": "",
                             "started_at": None, "finished_at": None}
                       for key, _ in STAGES},
        }
        self._save()
        return self.state

    def stage(self, key: str, status: str, detail: str = "", **counts: int) -> dict:
        if key not in STAGE_IDS or status not in STATE_VALUES:
            raise ValueError("invalid PREP workflow stage/state")
        now = _stamp()
        item = self.state["stages"][key]
        if status == "実行中" and not item["started_at"]:
            item["started_at"] = now
        if status in TERMINAL:
            item["finished_at"] = now
        item.update(status=status, detail=str(detail)[:500])
        self.state["counters"].update(counts)
        self.state["updated_at"] = now
        self._save()
        return self.state

    def fingerprint(self, value: str) -> None:
        self.state["fingerprint"] = value
        self._save()

    def finish(self, status: str) -> dict:
        if status not in TERMINAL:
            raise ValueError("invalid terminal PREP workflow state")
        now = _stamp()
        self.state.update(run_status=status, updated_at=now, finished_at=now)
        if status in ("停止", "失敗"):
            for item in self.state["stages"].values():
                if item["status"] == "実行中":
                    item.update(status=status, finished_at=now)
        self._save()
        return self.state

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(
            self.state, ensure_ascii=False, indent=2, sort_keys=True))


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid JSON object")
    return value


def team_readiness(data_root: str | Path, team_id: str,
                   team_path: str | Path,
                   *, master_dir: str | Path | None = None) -> dict:
    """Return current saved facts only. Never generate assets or call a model.

    Old or changed set signatures are not treated as a ready current team.
    No SQLite writes or file migration occurs in this accessor.
    """
    team_path = Path(team_path)
    result: dict[str, Any] = {
        "team_id": team_id, "status": "未準備", "run_status": "未実行",
        "team_match": False, "matrix": False, "lookup": False,
        "total": None, "legacy_cells": None, "ai_v2": None,
        "source_gaps": None, "unknown": None,
        "prep_lead": None, "prep_bench": None,
        "quality_ready": False, "fingerprint": None,
        "updated_at": None, "stages": {key: {"status": "未実行", "detail": ""}
                                      for key, _ in STAGES},
    }
    try:
        current_sha = _digest(team_path)
        signatures = team_signatures(team_path)
        if len(signatures) != 6:
            result["status"] = "構築未完成"
            return result
    except (OSError, ValueError, KeyError):
        result["status"] = "構築確認不可"
        return result

    ledger = progress_path(data_root, team_id)
    if ledger.is_file():
        try:
            info = _read_json(ledger)
            if info.get("schema") == SCHEMA and info.get("team_id") == team_id:
                if info.get("team_sha256") == current_sha:
                    result["stages"] = info.get("stages", result["stages"])
                    result["run_status"] = info.get("run_status", "未実行")
                    result["updated_at"] = info.get("updated_at")
                    result["counters"] = info.get("counters", {})
                    if result["run_status"] == "実行中":
                        # A live in-process worker is overlaid by GUI; on
                        # application restart old RUNNING is not a success.
                        result["run_status"] = "停止（再開可能）"
                else:
                    result["status"] = "再準備が必要"
                    result["run_status"] = "再準備が必要"
                    for key in result["stages"]:
                        result["stages"][key] = {"status": "再準備が必要",
                                                  "detail": "構築CSVが変更されました"}
        except (OSError, ValueError, TypeError, KeyError):
            result["run_status"] = "進捗確認不可"

    root = Path(data_root) / "live-team-assets" / team_id
    if not root.is_dir():
        return result
    found_stale = False
    candidates = []
    for manifest_path in root.glob("*/manifest.json"):
        try:
            manifest = _read_json(manifest_path)
            catalog_path = manifest_path.with_name("matchup-evidence.json")
            if (manifest.get("team_id") != team_id
                    or not catalog_path.is_file()):
                continue
            catalog = _read_json(catalog_path)
            if catalog.get("member_signatures") != signatures:
                found_stale = True
                continue
            candidates.append((manifest_path.stat().st_mtime, manifest_path,
                               manifest, catalog_path, catalog))
        except (OSError, ValueError, TypeError):
            continue
    if not candidates:
        if found_stale:
            result["status"] = "再準備が必要"
        return result
    _, manifest_path, manifest, catalog_path, catalog = max(
        candidates, key=lambda row: row[0])
    result["fingerprint"] = manifest.get("fingerprint")
    try:
        matrix_path = manifest_path.with_name("matrix.txt")
        lookup_path = manifest_path.with_name("lookup.txt")
        valid_catalog = (_digest(catalog_path) == manifest.get("candidate_sha256"))
        matrix_ok = matrix_path.is_file() and (
            _digest(matrix_path) == manifest.get("matrix_sha256"))
        lookup_ok = lookup_path.is_file() and (
            _digest(lookup_path) == manifest.get("lookup_sha256"))
        result["matrix"], result["lookup"] = matrix_ok, lookup_ok
        result["team_match"] = bool(valid_catalog and matrix_ok and lookup_ok)
        if not result["team_match"]:
            result["status"] = "生成物不整合"
            return result
        counts = catalog.get("counts", {})
        total = int(catalog["total_cells"])
        if total != int(manifest["opponent_rows"]) * 6:
            result["status"] = "生成物不整合"
            return result
        result["total"] = total
        result["legacy_cells"] = int(counts.get("CONDITIONAL", 0))
        result["unknown"] = int(counts.get("UNKNOWN", 0))
        result["source_gaps"] = sum(bool(x.get("missing_source"))
                                    for x in catalog.get("cells", []))
        result["ai_v2"] = int(manifest.get("ai_proposal_cells", 0))
        result["quality_ready"] = bool(manifest.get("quality_gate", {})
                                       .get("live_ready", False))
        # Do not treat a v2 proposal as an 11/6-field completed PREP result.
        # Those counts are only available when independently assessed.
        if master_dir is not None:
            masters = Path(master_dir)
            recorded = catalog.get("master_sha256", {})
            if any(not (masters / name).is_file()
                   or _digest(masters / name) != recorded.get(name)
                   for name in SOURCE_FILES):
                result["status"] = "再準備が必要"
                return result
        result["status"] = (
            "評価検証済み" if result["quality_ready"] else
            "条件付き準備" if result["ai_v2"] or result["legacy_cells"] else
            "対面データ未評価")
    except (OSError, TypeError, ValueError, KeyError):
        result["status"] = "生成物確認不可"
    return result
