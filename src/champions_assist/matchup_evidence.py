"""Offline Pokémon Champions matchup facts and explicit human-reviewed evidence.

FACTS are restricted to the checked-in Champions masters. Missing effectiveness,
damage, hidden opponent sets and speed investment are NEVER inferred. Review
packets are a user action between matches; AI observations alone cannot approve
an opponent set, a processor, or a safe hard switch.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from .app_config import ConfigurationError, atomic_write_text
from .species_name_aliases import selection_base_name

SCHEMA = "champions-matchup-evidence-v1"
REVIEW_SCHEMA = "champions-matchup-review-v1"
MAX_REVIEW_BYTES = 2 * 1024 * 1024
SOURCE_FILES = (
    "Pokemon_Champions_MC_usable_pokemon.csv",
    "Pokemon_Champions_MC_move_master.csv",
    "Pokemon_Champions_MC_pokemon_learnsets_linked.csv",
)
UNKNOWN = "UNKNOWN"


def digest(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def stable_hash(value: object) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8"))


def member_signature(member: dict[str, str], fields: tuple[str, ...]) -> str:
    return stable_hash({key:(member.get(key) or "").strip() for key in fields})


def _rows(path: Path) -> list[dict]:
    return list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))


def _number(raw: object) -> int | None:
    try:
        return int(str(raw))
    except (ValueError, TypeError):
        return None


def _lookup_species(name: str, records: dict[str, dict]) -> dict | None:
    # Exact species/form first. Never silently map normal->Mega or regional forms.
    return records.get(name) or records.get(selection_base_name(name))


def _species_key(member: dict) -> str:
    name = (member.get("name") or "").split("→")[-1].strip()
    return selection_base_name(name)


def _source_hashes(master: Path) -> dict[str, str]:
    return {name:digest((master/name).read_bytes()) for name in SOURCE_FILES}


def build_candidate_catalog(*, master_dir: Path, team: tuple[dict, ...],
                            matrix_rows: dict[str, list[str]],
                            fields: tuple[str, ...], regulation: str) -> dict:
    """Generate one independent, non-tactical evidence record per 358x6 cell."""
    master = Path(master_dir)
    species = {row["ポケモン名"]:row for row in _rows(master/SOURCE_FILES[0])}
    moves = {row["技名"]:row for row in _rows(master/SOURCE_FILES[1])}
    learnsets = {row["ポケモン名"]:row for row in _rows(master/SOURCE_FILES[2])}
    signatures = [member_signature(member, fields) for member in team]
    evidence = []
    for opponent, row in matrix_rows.items():
        opp = _lookup_species(opponent, species)
        opp_ls = _lookup_species(opponent, learnsets)
        opponent_move_names = []
        if opp_ls is not None:
            opponent_move_names = [
                m for m in opp_ls.get("習得技名一覧", "").split("、") if m in moves
            ]
        potential_priority = [
            name for name in opponent_move_names
            if (_number(moves[name].get("優先度")) or 0) > 0
        ]
        for slot, member in enumerate(team):
            own_name = _species_key(member)
            own = _lookup_species(own_name, species)
            own_ls = _lookup_species(own_name, learnsets)
            known_moves = set(
                (own_ls or {}).get("習得技名一覧", "").split("、")
            ) if own_ls is not None else set()
            registered = []
            missing = []
            for i in range(1,5):
                name = (member.get("move"+str(i)) or "").strip()
                info = moves.get(name)
                if not name or info is None or name not in known_moves:
                    missing.append(name or f"move{i}")
                    continue
                registered.append({
                    "name":name,
                    "type":info.get("タイプ", ""),
                    "category":info.get("分類", ""),
                    "base_power":_number(info.get("威力")),
                    "priority":_number(info.get("優先度")),
                    "move_master_key":info.get("MoveKey", ""),
                })
            reasons = []
            if own is None:reasons.append("OWN_SPECIES_MASTER_NOT_FOUND")
            if opp is None:reasons.append("OPPONENT_SPECIES_MASTER_NOT_FOUND")
            if own_ls is None:reasons.append("OWN_LEARNSET_NOT_FOUND")
            if opp_ls is None:reasons.append("OPPONENT_LEARNSET_NOT_FOUND")
            if missing:reasons.append("REGISTERED_MOVE_NOT_IN_CHAMPIONS_MASTER")
            def traits(x):
                if x is None:return {"types":[],"base_speed":None}
                return {"types":[t for t in (x.get("タイプ1"),x.get("タイプ2")) if t],
                        "base_speed":_number(x.get("S")),
                        "stats":{n:_number(x.get(n)) for n in ("H","A","B","C","D","S")},
                        "species_source":x.get("基礎データ出典URL", "")}
            evidence.append({
                "opponent":opponent, "slot":slot,
                "own":own_name,"member_signature":signatures[slot],
                "own_species":traits(own),"opponent_species":traits(opp),
                "registered_moves":registered,
                "registered_build":{
                    "item":member.get("item", ""),
                    "ability":member.get("ability", ""),
                    "nature":member.get("nature", ""),
                    "effort_distribution":member.get("ap_h_a_b_c_d_s", ""),
                    "form_change":member.get("form_change", ""),
                    "choice_lock_declared":member.get("choice_lock", ""),
                },
                "opponent_priority_candidates":potential_priority,
                "opponent_potential_high_power_moves":[
                    {"name":x,"base_power":_number(moves[x].get("威力"))}
                    for x in sorted(
                        opponent_move_names,
                        key=lambda x:_number(moves[x].get("威力")) or 0,
                        reverse=True
                    )[:5]
                ],
                "opponent_set":UNKNOWN,
                "move_effectiveness":UNKNOWN,
                "actual_damage":UNKNOWN,
                "speed_order_after_stats_items":UNKNOWN,
                "safe_hard_switch":UNKNOWN,
                "direct_processor":UNKNOWN,
                "evaluation":"UNKNOWN",
                "missing_source":reasons,
            })
    return {
        "schema":SCHEMA,"generated_at":datetime.now(timezone.utc).isoformat(),
        "regulation":regulation,"member_signatures":signatures,
        "master_sha256":_source_hashes(master),
        "source_scope":"CHAMPIONS_FIXED_MASTERS_ONLY",
        "limitations":"BASE_SPEED_IS_NOT_OBSERVED_ORDER;POWER_IS_NOT_DAMAGE;"
                      "LEARNSET_IS_NOT_OPPONENT_BUILD;UNMAPPED_EFFECTS_EXCLUDED",
        "opponents":len(matrix_rows),"total_cells":len(evidence),
        "cells":evidence,
    }


def _review_source(item: object) -> bool:
    if not isinstance(item, dict):return False
    if item.get("kind") not in ("OFFICIAL_CHAMPIONS","USER_INGAME_CONFIRMED",
                                "HUMAN_REVIEWED_BATTLE_RECORD"):return False
    when = item.get("observed_at")
    if not isinstance(when,str) or len(when)<10 or len(when)>48:return False
    try:
        parsed=datetime.fromisoformat(when.replace("Z","+00:00"))
        if parsed.tzinfo is None:return False
    except ValueError:
        return False
    locator = item.get("uri", "")
    proof = item.get("sha256", "")
    if locator and (not isinstance(locator,str) or len(locator)>1000):return False
    if proof and (not isinstance(proof,str) or len(proof)!=64
                  or any(c not in "0123456789abcdef" for c in proof)):return False
    return bool(locator or proof)


def validate_review_packet(packet: object, *, team_id: str,
                           regulation: str, signatures: list[str],
                           opponents: set[str]) -> list[dict]:
    """Validate conservative manually-attested conditional matchup records.

    This validates their *structure and scope*, not the truth of visual damage
    observations. Never derives READY certification from player assertions.
    """
    if not isinstance(packet,dict) or packet.get("schema")!=REVIEW_SCHEMA:
        raise ConfigurationError("対面レビューJSONのバージョンが不正です")
    if packet.get("team_id")!=team_id or packet.get("member_signatures")!=signatures:
        raise ConfigurationError("レビュー対象の構築・技・持ち物・配分が異なります")
    if packet.get("regulation")!=regulation:
        raise ConfigurationError("レビュー対象のレギュレーションが異なります")
    if packet.get("player_reviewed") is not True:
        raise ConfigurationError("プレイヤーのレビュー承認がありません")
    entries=packet.get("reviews")
    if not isinstance(entries,list) or len(entries)>len(opponents)*6:
        raise ConfigurationError("レビュー行数が不正です")
    normalized,seen=[],set()
    for cell in entries:
        if not isinstance(cell,dict):raise ConfigurationError("レビュー行の形式が不正です")
        opponent=cell.get("opponent")
        slot=cell.get("slot")
        if opponent not in opponents or type(slot) is not int or not 0<=slot<6:
            raise ConfigurationError("レビュー対象の対面がありません")
        if (opponent,slot) in seen:raise ConfigurationError("レビュー対象の重複")
        seen.add((opponent,slot))
        if cell.get("member_signature")!=signatures[slot]:
            raise ConfigurationError("レビュー対象の型が一致しません")
        status=cell.get("status")
        if status not in ("CONDITIONAL","CONFIRMED"):
            raise ConfigurationError("レビュー結果はCONDITIONAL/CONFIRMEDのみ")
        sources=cell.get("sources")
        if (not isinstance(sources,list) or not sources
                or len(sources)>10 or not all(_review_source(x) for x in sources)):
            raise ConfigurationError("Champions固有の日時・根拠リンクまたはSHAがありません")
        reviewed_at=cell.get("reviewed_at")
        if not isinstance(reviewed_at,str):
            raise ConfigurationError("承認日時がありません")
        try:
            if datetime.fromisoformat(reviewed_at.replace("Z","+00:00")).tzinfo is None:
                raise ValueError("timezone")
        except ValueError:
            raise ConfigurationError("承認日時はタイムゾーン付きISO日時で指定してください")
        if not isinstance(cell.get("reviewer"),str) or not cell["reviewer"].strip():
            raise ConfigurationError("レビュー担当者がありません")
        rationale=cell.get("rationale")
        if not isinstance(rationale,str) or not 5<=len(rationale)<=2000:
            raise ConfigurationError("対面評価理由を記載してください")
        if status=="CONFIRMED":
            # Human-reviewed direct screenshots alone cannot verify matchup
            # universality. Demand explicit bounded conditions and TWO source
            # observations; still never auto-enable LIVE.
            conditions=cell.get("conditions")
            if not isinstance(conditions,dict) or not conditions.get("observed_opponent_moves"):
                raise ConfigurationError("相手の実測技条件が必要")
            if len(sources)<2:
                raise ConfigurationError("CONFIRMEDは独立した2件以上の根拠が必要")
            fingerprints={(source.get("kind"),source.get("uri",""),
                           source.get("sha256","")) for source in sources}
            if len(fingerprints)<2:
                raise ConfigurationError("CONFIRMEDは別々の根拠が2件必要")
        # Reject tactical text injection into lookup/matrix entirely. The
        # reviewed packet remains a sidecar for manual audit only.
        normalized.append({
            "opponent":opponent,"slot":slot,"member_signature":signatures[slot],
            "status":status,"rationale":rationale,"conditions":cell.get("conditions", {}),
            "sources":sources,"reviewed_at":cell["reviewed_at"],"reviewer":cell["reviewer"],
            "processing_role":"UNVERIFIED","safe_switch_role":"UNVERIFIED",
        })
    return normalized


def import_reviews(*, data_root: Path, team_id: str, packet_path: Path,
                   candidate_catalog: dict) -> Path:
    """Explicit, player-triggered save with last-good preservation on errors."""
    path=Path(packet_path)
    if path.stat().st_size>MAX_REVIEW_BYTES:
        raise ConfigurationError("対面レビューのファイルサイズ上限を超えました")
    packet=json.loads(path.read_text(encoding="utf-8-sig"))
    approved=validate_review_packet(
        packet,team_id=team_id,
        signatures=candidate_catalog["member_signatures"],
        regulation=candidate_catalog["regulation"],
        opponents={cell["opponent"] for cell in candidate_catalog["cells"]},
    )
    target=Path(data_root)/"matchup-reviews"/(team_id+".json")
    body={"schema":REVIEW_SCHEMA,"team_id":team_id,
          "regulation":candidate_catalog["regulation"],
          "member_signatures":candidate_catalog["member_signatures"],
          "player_reviewed":True,"reviews":approved}
    atomic_write_text(target,json.dumps(body,ensure_ascii=False,sort_keys=True,indent=2))
    return target


def certified_review_counts(review_path: Path, *, team_id: str,
                            candidate_catalog: dict) -> dict:
    if not review_path.is_file():return {"CONFIRMED":0,"CONDITIONAL":0}
    raw=review_path.read_bytes()
    if len(raw)>MAX_REVIEW_BYTES:raise ConfigurationError("レビューがサイズ上限超過")
    packet=json.loads(raw)
    entries=validate_review_packet(
        packet,team_id=team_id,
        signatures=candidate_catalog["member_signatures"],
        regulation=candidate_catalog["regulation"],
        opponents={cell["opponent"] for cell in candidate_catalog["cells"]},
    )
    return {"CONFIRMED":sum(c["status"]=="CONFIRMED" for c in entries),
            "CONDITIONAL":sum(c["status"]=="CONDITIONAL" for c in entries)}
