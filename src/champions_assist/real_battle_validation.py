"""Offline, reproducible user-confirmed real-game capture timing evidence gate.

This module never captures a game, predicts a tactic or tests on behalf of
the user. It validates separately supplied *device evidence* after play.
Synthetic CI fixtures cannot produce LIVE_READY. The result remains an audit
input, not a substitute for independent gameplay or Champions rule checks.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re

from .app_config import ConfigurationError, atomic_write_text

SCHEMA = "champions-live-e2e-device-evidence-v3"
LEGACY_SCHEMA = "champions-live-e2e-device-evidence-v2"
MIN_MATCHES = 5
SELECTION_LIMIT_MS = 90_000
SELECTION_PREP_LIMIT_MS = 90_000  # Full 90s shared preview window (v3)
LEGACY_SELECTION_PREP_LIMIT_MS = 87_000  # Historical v2 audit only
TURN_LIMIT_MS = 45_000
MAX_IMPORT_BYTES = 2_000_000
MAX_CAPTURE_BYTES = 800_000_000
_SHA = re.compile(r"^[0-9a-f]{64}$")
_TEAM = re.compile(r"^[0-9a-f]{32}$")


def _int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ConfigurationError("実機計測時間は0以上の整数ミリ秒です")
    return value


def _file_fingerprint(source: dict, *, max_bytes: int) -> tuple[str, int]:
    if not isinstance(source, dict):
        raise ConfigurationError("実戦の根拠ファイル指定がありません")
    label = source.get("sha256")
    filename = source.get("path")
    if not isinstance(label,str) or _SHA.fullmatch(label) is None:
        raise ConfigurationError("根拠ファイルSHA256が不正です")
    if not isinstance(filename,str) or not filename:
        raise ConfigurationError("根拠ファイルを指定してください")
    path = Path(filename).expanduser()
    if not path.is_file():
        raise ConfigurationError("根拠ファイルが存在しません")
    size = path.stat().st_size
    if not 1 <= size <= max_bytes:
        raise ConfigurationError("根拠ファイルのサイズが範囲外です")
    checksum = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1_048_576),b""):
            checksum.update(block)
    if checksum.hexdigest() != label:
        raise ConfigurationError("根拠ファイルのSHA256が一致しません")
    return label,size


def _interval(record: dict, start: str, end: str, limit: int) -> dict:
    begin = _int(record.get(start))
    finish = _int(record.get(end))
    if finish < begin:
        raise ConfigurationError("時刻は順序通りである必要があります")
    elapsed=finish-begin
    return {"elapsed_ms":elapsed,"limit_ms":limit,
            "within_limit":elapsed <= limit}


def validate_real_battle_packet(packet: object, *, team_id: str,
                                team_fingerprint: str, regulation: str) -> dict:
    if not isinstance(packet,dict) or packet.get("schema") not in (SCHEMA,LEGACY_SCHEMA):
        raise ConfigurationError("実戦E2E記録のスキーマが不正です")
    if not _TEAM.fullmatch(team_id):
        raise ConfigurationError("構築IDが不正です")
    if packet.get("team_id") != team_id or packet.get("team_fingerprint") != team_fingerprint:
        raise ConfigurationError("実戦記録の構築指紋が一致しません")
    if packet.get("regulation") != regulation:
        raise ConfigurationError("実戦記録のレギュレーションが一致しません")
    # The actual game environment/capture route is user-selected, not
    # hardcoded to an emulator. Preserve historically recorded evidence.
    platform=packet.get("platform")
    if platform not in ("WINDOWS_REAL_CAPTURE","WINDOWS_BLUESTACKS"):
        raise ConfigurationError("確認済みの実ゲーム映像に由来する収集環境が必要です")
    if packet.get("human_reviewed") is not True:
        raise ConfigurationError("実戦ログの人間確認が必要です")
    records=packet.get("matches")
    if not isinstance(records,list) or not 1 <= len(records) <= 100:
        raise ConfigurationError("実戦記録がありません、または多すぎます")
    seen_ids,seen_proofs=set(),set()
    normalized=[]
    for match in records:
        if not isinstance(match,dict):
            raise ConfigurationError("実戦レコード形式が不正です")
        match_id=match.get("match_id")
        if not isinstance(match_id,str) or not 1 <= len(match_id) <= 120:
            raise ConfigurationError("試合IDが不正です")
        if match_id in seen_ids:raise ConfigurationError("試合IDが重複しています")
        seen_ids.add(match_id)
        if match.get("source_kind") != "REAL_CAPTURE":
            raise ConfigurationError("合成・リプレイ・CPU疑いを実戦ゲートには利用できません")
        if match.get("opponent_kind") != "HUMAN_CONFIRMED":
            raise ConfigurationError("人間戦と確認できない記録は実戦ゲートには利用できません")
        if match.get("outcome") not in ("WIN","LOSS"):
            raise ConfigurationError("勝敗未確定の試合は採用できません")
        if match.get("observed_opponent_count") != 6:
            raise ConfigurationError("相手6匹を観測した根拠がありません")
        proof,size=_file_fingerprint(match.get("capture"),max_bytes=MAX_CAPTURE_BYTES)
        if proof in seen_proofs:raise ConfigurationError("同じキャプチャを複数試合に計上できません")
        seen_proofs.add(proof)
        times=match.get("timeline")
        if not isinstance(times,dict):
            raise ConfigurationError("実戦計測のタイムラインがありません")
        selection=_interval(times,"selection_visible_ms","selection_displayed_ms",SELECTION_LIMIT_MS)
        # Preserve previously-issued v2 evidence under its original 87s rule.
        target=(LEGACY_SELECTION_PREP_LIMIT_MS if packet["schema"]==LEGACY_SCHEMA
                else SELECTION_PREP_LIMIT_MS)
        prep=_interval(times,"selection_visible_ms","prep_finished_ms",target)
        if _int(times["selection_confirmed_ms"]) < _int(times["selection_visible_ms"]):
            raise ConfigurationError("選出確定時刻が選出画面前です")
        lead_visible=_int(times.get("opponent_lead_visible_ms"))
        confirmed=_int(times.get("selection_confirmed_ms"))
        selected_visible=_int(times.get("selection_displayed_ms"))
        if selected_visible>confirmed or lead_visible<confirmed:
            raise ConfigurationError("選出表示→選出確定→相手初手の順序が不正です")
        if _int(times.get("prep_finished_ms")) > lead_visible:
            # A PREP interrupted by the opponent lead is not counted as
            # complete, even if the eventual 18 matchup text is populated.
            prep["within_limit"]=False
            prep["lead_interrupted"]=True
        turns=times.get("turns")
        if not isinstance(turns,list) or not turns:
            raise ConfigurationError("実戦のターンごとの締切記録がありません")
        turn_results=[]
        for t in turns:
            if not isinstance(t,dict):
                raise ConfigurationError("ターンタイムライン形式が不正です")
            turn_results.append(_interval(t,"action_screen_ms","recommendation_visible_ms",TURN_LIMIT_MS))
        if match.get("prep_matchups_count") != 18 or match.get("prep_win_roles_count") != 3:
            raise ConfigurationError("初手6＋控え12＋勝ち筋3点のPREP完了根拠がありません")
        missing=int(_int(match.get("screen_recognition_errors")))
        late=int(_int(match.get("stale_advice_errors")))
        normalized.append({
            "match_id":match_id,"capture_sha256":proof,"capture_bytes":size,
            "selection":selection,"prep":prep,"turns":turn_results,
            "recognition_errors":missing,"stale_advice_errors":late,
            "deadline_pass":selection["within_limit"] and prep["within_limit"]
                and all(t["within_limit"] for t in turn_results),
            "outcome":match["outcome"],
        })
    all_deadlines = all(m["deadline_pass"] for m in normalized)
    no_tracking_errors = all(m["recognition_errors"]==0
                             and m["stale_advice_errors"]==0 for m in normalized)
    eligible = (len(normalized)>=MIN_MATCHES
                and all_deadlines and no_tracking_errors)
    return {
        "schema":SCHEMA,"team_id":team_id,
        "team_fingerprint":team_fingerprint,"regulation":regulation,
        "capture_platform":platform,
        "matches":normalized,"match_count":len(normalized),
        "min_matches":MIN_MATCHES,"all_deadlines_met":all_deadlines,
        "no_screen_or_stale_advice_errors":no_tracking_errors,
        "independent_capture_hashes":len(seen_proofs),
        "device_evidence_passed":eligible,
        "live_ready":False,
        "blocking_requirements":[
            "INDEPENDENT_CHAMPIONS_EFFECT_AND_DAMAGE_VALIDATION",
            "REVIEWED_PROCESSOR_AND_SAFE_SWITCH_ROLE_EVIDENCE",
            "INDEPENDENT_REAL_GAME_TACTICAL_ACCURACY_REVIEW",
        ] + ([] if eligible else ["DEVICE_E2E_90_40_45_NOT_PROVEN"]),
        "note":"Audited packet structure is not automatically proof of tactical correctness.",
    }


def import_real_battle_packet(*, data_root: Path, packet_path: Path,
                              team_id: str, team_fingerprint: str,
                              regulation: str) -> tuple[Path,dict]:
    path=Path(packet_path)
    if path.stat().st_size>MAX_IMPORT_BYTES:
        raise ConfigurationError("実戦E2E記録が容量上限を超えています")
    packet=json.loads(path.read_text(encoding="utf-8-sig"))
    audit=validate_real_battle_packet(
        packet,team_id=team_id,team_fingerprint=team_fingerprint,
        regulation=regulation)
    dest=Path(data_root)/"e2e-real-battle"/team_id/"device-quality-audit.json"
    atomic_write_text(dest,json.dumps(audit,ensure_ascii=False,indent=2,sort_keys=True))
    return dest,audit
