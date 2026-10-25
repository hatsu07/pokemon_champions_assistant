"""Offline, explicit review of AI suggestions using independent local game evidence.

Suggestions themselves are not evidence. A human must inspect actual game files,
provide a separate statement and explicitly approve the review between matches.
No role guarantee, matchup grade or damage result is inferred by this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

from .app_config import ConfigurationError
from .matchup_evidence import REVIEW_SCHEMA, validate_review_packet
from .team_ai_analysis import SCHEMA as PROPOSAL_SCHEMA

MAX_PROPOSAL_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_BYTES = 24 * 1024 * 1024
EVIDENCE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".json"})


def _load_json(path: Path, limit: int) -> dict:
    if not path.is_file() or path.stat().st_size > limit:
        raise ConfigurationError("根拠ファイルがありません、またはサイズ超過です")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ConfigurationError("根拠JSONの形式が不正です")
    return value


def _root(data_root: Path | str, team_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", team_id):
        raise ConfigurationError("不正な構築IDです")
    return Path(data_root) / "live-team-assets" / team_id


def _candidate_index(catalog: dict) -> dict[tuple[str, int], dict]:
    return {(x["opponent"], x["slot"]): x for x in catalog["cells"]}


def available_ai_reviews(*, data_root: Path | str, team_id: str,
                         catalog: dict) -> list[dict]:
    """Show only suggestions still matching THIS team/Champions master revision.

    Old proposal folders remain available across reviews, but not across changed
    builds, game-master revisions or regulation changes.
    """
    root = _root(data_root, team_id)
    current = _candidate_index(catalog)
    seen = set()
    options = []
    if not root.is_dir():
        return options
    for path in sorted(root.glob("*/ai-proposal-*.json")):
        try:
            if path.is_symlink() or path.parent.is_symlink():
                continue
            packet = _load_json(path, MAX_PROPOSAL_BYTES)
            origin = _load_json(path.parent / "matchup-evidence.json", MAX_PROPOSAL_BYTES * 3)
            if (packet.get("schema") != PROPOSAL_SCHEMA
                    or packet.get("status") != "AI_PROPOSAL_UNVERIFIED"
                    or packet.get("live_approved") is not False
                    or origin.get("schema") != "champions-matchup-evidence-v1"
                    or sha256((path.parent / "matchup-evidence.json").read_bytes()).hexdigest()
                       != packet.get("catalog_sha256")
                    or packet.get("member_signatures") != catalog.get("member_signatures")
                    or origin.get("member_signatures") != catalog.get("member_signatures")
                    or origin.get("master_sha256") != catalog.get("master_sha256")
                    or packet.get("regulation") != catalog.get("regulation")):
                continue
            entries = packet.get("proposals")
            if not isinstance(entries, list) or len(entries) > 12:
                continue
            for index, item in enumerate(entries):
                if not isinstance(item, dict):
                    continue
                opponent, slot, sig = item.get("opponent"), item.get("slot"), item.get("member_signature")
                if not isinstance(opponent, str) or type(slot) is not int:
                    continue
                cell = current.get((opponent, slot))
                key = (opponent, slot)
                if (key in seen or cell is None or cell.get("evaluation") != "UNKNOWN"
                        or cell.get("missing_source")
                        or cell.get("member_signature") != sig
                        or not isinstance(item.get("assessment"), str)
                        or len(item["assessment"]) > 1200):
                    continue
                seen.add(key)
                options.append({
                    "opponent":opponent,"slot":slot,"own":cell["own"],
                    "assessment":item["assessment"],"risks":item.get("risks", ""),
                    "unknowns":item.get("unknowns", ""),
                    "source_path":str(path),"proposal_index":index,
                    "member_signature":sig,
                })
        except (OSError, ValueError, KeyError, TypeError, UnicodeError):
            # A bad/stale optional proposal never affects existing LIVE assets.
            continue
    return options


def _observed_source(path: str | Path, when: str) -> dict:
    proof = Path(path)
    if (not proof.is_file() or proof.is_symlink()
            or proof.suffix.lower() not in EVIDENCE_SUFFIXES
            or proof.name.startswith("ai-proposal-")):
        raise ConfigurationError("実際のゲーム内根拠画像または記録JSONを選択してください")
    if proof.stat().st_size <= 0 or proof.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ConfigurationError("実測根拠ファイルのサイズが不正です")
    # Only the SHA digest is retained in the review; bytes are not uploaded.
    digest = sha256(proof.read_bytes()).hexdigest()
    return {"kind":"USER_INGAME_CONFIRMED","observed_at":when,"sha256":digest}


def make_human_review_packet(*, data_root: str | Path, team_id: str,
                             catalog: dict, option: dict,
                             evidence_files: list[str | Path], rationale: str,
                             observed_moves: list[str] | None = None) -> dict:
    """Build review input from an AI *candidate* and player-independent evidence.

    Validation is structural and provenance-bound, not a proof of the asserted
    move, exact KO, safety, speed or any role guarantee.
    """
    valid = available_ai_reviews(data_root=data_root,team_id=team_id,catalog=catalog)
    if not any(all(x[field] == option.get(field) for field in
                   ("source_path","proposal_index","member_signature","opponent","slot","own"))
               for x in valid):
        raise ConfigurationError("AI候補が現在の構築・マスターに一致しません")
    if not 1 <= len(evidence_files) <= 2:
        raise ConfigurationError("独立した実機根拠ファイルが1～2件必要です")
    if not isinstance(rationale, str) or not 5 <= len(rationale.strip()) <= 2000:
        raise ConfigurationError("自分で確認した観測内容を5～2000文字で記述してください")
    now = datetime.now(timezone.utc).isoformat()
    sources = [_observed_source(p, now) for p in evidence_files]
    if len({x["sha256"] for x in sources}) != len(sources):
        raise ConfigurationError("同じ証拠を2件と数えられません")
    confirmed = len(sources) == 2
    moves = [m.strip() for m in (observed_moves or []) if isinstance(m, str) and m.strip()]
    if confirmed and (not moves or len(moves) > 4 or any(len(m) > 40 for m in moves)):
        raise ConfigurationError("2件レビューには実際に観測した相手技が必要です")
    record = {
        "opponent": option["opponent"],"slot":option["slot"],
        "member_signature":option["member_signature"],
        "status":"CONFIRMED" if confirmed else "CONDITIONAL",
        "reviewed_at":now,"reviewer":"LOCAL_PLAYER_EXPLICIT_APPROVAL",
        "rationale":rationale.strip(),
        "conditions":{"observed_opponent_moves":moves},
        "sources":sources,
    }
    existing_path = Path(data_root) / "matchup-reviews" / (team_id + ".json")
    existing = []
    if existing_path.is_file():
        previous = _load_json(existing_path, MAX_PROPOSAL_BYTES)
        validate_review_packet(
            previous,team_id=team_id,regulation=catalog["regulation"],
            signatures=catalog["member_signatures"],
            opponents={c["opponent"] for c in catalog["cells"]})
        existing = previous["reviews"]
    # Preserve previously confirmed observations, but replace the chosen cell
    # with a new explicit review. Never carry stale team signatures forward.
    remaining = [entry for entry in existing if
                 (entry["opponent"],entry["slot"]) != (option["opponent"],option["slot"])]
    packet = {
        "schema":REVIEW_SCHEMA,"team_id":team_id,
        "regulation":catalog["regulation"],
        "player_reviewed":True,
        "member_signatures":catalog["member_signatures"],
        "reviews":[*remaining,record],
    }
    validate_review_packet(
        packet,team_id=team_id,regulation=catalog["regulation"],
        signatures=catalog["member_signatures"],
        opponents={c["opponent"] for c in catalog["cells"]})
    return packet
