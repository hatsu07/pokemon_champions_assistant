"""Champions fixed-master move fields for pinned engine static compatibility.

This source-derived 24-slot inventory can be compared to the actual pinned
Showdown Champions Dex. Equality of type/power/priority/PP/category/accuracy
does not imply parity for effects, multi-hit RNG, Mega-Z, legality, or a real
Battle. No live imports, no cross-game inference and no mutable team rewrite.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
from typing import Any

from ..preload import FrozenMatchAssets, REQUIRED_MASTER_FILES
from .champions_master_key_candidates import ChampionsKeyCandidateReport

_NUMBERS = str.maketrans("０１２３４５６７８９", "0123456789")
_TYPES = {
    "ノーマル": "Normal", "ほのお": "Fire", "みず": "Water",
    "でんき": "Electric", "くさ": "Grass", "こおり": "Ice",
    "かくとう": "Fighting", "どく": "Poison", "じめん": "Ground",
    "ひこう": "Flying", "エスパー": "Psychic", "むし": "Bug",
    "いわ": "Rock", "ゴースト": "Ghost", "ドラゴン": "Dragon",
    "あく": "Dark", "はがね": "Steel", "フェアリー": "Fairy",
}
_CATEGORY = {"物理": "Physical", "特殊": "Special", "変化": "Status"}


@dataclass(frozen=True)
class StaticMoveField:
    registered_member: str
    slot: int
    champions_name: str
    move_id: str
    type: str
    category: str
    base_power: int
    accuracy: int | bool
    pp: int
    priority: int


@dataclass(frozen=True)
class StaticMoveSourceReport:
    status: str
    reason: str
    team_sha256: str = ""
    champions_move_master_sha256: str = ""
    slots: tuple[StaticMoveField, ...] = ()
    source_fields_verified: bool = False
    champions_engine_parity_verified: bool = False
    ranking_status: str = "NO_RANKING"


def _no(reason: str) -> StaticMoveSourceReport:
    return StaticMoveSourceReport("SOURCE_STATIC_FIELDS_REFUSED", reason)


def build_static_move_fields(
    assets: FrozenMatchAssets,
    candidates: ChampionsKeyCandidateReport,
    move_master_bytes: bytes,
) -> StaticMoveSourceReport:
    """Produce exact 6 x 4 fixed-source fields; refuse any ambiguous mapping."""
    if (not isinstance(assets, FrozenMatchAssets)
            or not isinstance(candidates, ChampionsKeyCandidateReport)
            or candidates.status != "CHAMPIONS_KEY_CANDIDATES_ONLY"
            or candidates.candidate_species_keys != 6
            or candidates.candidate_move_keys != 24
            or candidates.expected_move_slots != 24
            or candidates.team_sha256 != assets.sha256.get(REQUIRED_MASTER_FILES[3])
            or not isinstance(move_master_bytes, bytes)
            or not 1 <= len(move_master_bytes) <= 4_000_000):
        return _no("UNBOUND_OR_INCOMPLETE_CHAMPIONS_CANDIDATES")
    digest = sha256(move_master_bytes).hexdigest()
    if ("champions_move_master", digest) not in candidates.source_sha256:
        return _no("CHAMPIONS_MOVE_MASTER_SHA_NOT_BOUND")
    try:
        reader = csv.DictReader(StringIO(move_master_bytes.decode("utf-8-sig")))
        required = {"MoveKey", "技名", "タイプ", "分類", "威力", "命中", "PP", "優先度"}
        if not required.issubset(reader.fieldnames or ()):
            return _no("CHAMPIONS_MASTER_MISSING_COLUMNS")
        by_name: dict[str, dict[str, str]] = {}
        by_key: dict[str, str] = {}
        for row in reader:
            name = row["技名"].translate(_NUMBERS)
            key = row["MoveKey"]
            if not name or not key or name in by_name or (
                    key in by_key and by_key[key] != name):
                return _no("AMBIGUOUS_MASTER_MOVE")
            by_name[name] = row
            by_key[key] = name
        registered = {m["name"]: m for m in assets.own_team}
        if len(registered) != 6 or len(candidates.members) != 6:
            return _no("REGISTERED_TEAM_INCOMPLETE")
        slots: list[StaticMoveField] = []
        for member in candidates.members:
            own = registered.get(member.registered_name)
            if own is None or len(member.moves) != 4:
                return _no("MEMBER_NOT_IN_FROZEN_TEAM")
            for candidate in member.moves:
                registered_name = own.get(f"move{candidate.slot}", "")
                source = by_name.get(registered_name.translate(_NUMBERS))
                if (candidate.slot not in (1, 2, 3, 4)
                        or source is None or not registered_name
                        or candidate.champions_move != registered_name
                        or candidate.key_candidate != source["MoveKey"]
                        or candidate.learnset_membership != "IN_CHAMPIONS_LEARNSET"):
                    return _no("CANDIDATE_NOT_EXACT_REGISTERED_MOVE")
                bp = int(source["威力"])
                pp = int(source["PP"])
                priority = int(source["優先度"])
                acc = (True if source["命中"] == "必中"
                       else int(source["命中"]))
                if (source["タイプ"] not in _TYPES
                        or source["分類"] not in _CATEGORY
                        or bp < 0 or bp > 300 or not 1 <= pp <= 64
                        or not -7 <= priority <= 7
                        or acc is not True and not 1 <= acc <= 100):
                    return _no("UNSUPPORTED_CHAMPIONS_STATIC_FIELD")
                slots.append(StaticMoveField(
                    member.registered_name, candidate.slot,
                    registered_name, candidate.key_candidate,
                    _TYPES[source["タイプ"]], _CATEGORY[source["分類"]],
                    bp, acc, pp, priority))
        if len(slots) != 24 or len({(v.registered_member, v.slot) for v in slots}) != 24:
            return _no("EXPECTED_EXACTLY_24_REGISTERED_SLOTS")
        return StaticMoveSourceReport(
            "CHAMPIONS_STATIC_24_SOURCE_BOUND",
            "PINNED_ENGINE_AND_GAME_EFFECTS_NOT_YET_ATTESTED",
            candidates.team_sha256, digest, tuple(slots),
            source_fields_verified=True)
    except (ValueError, TypeError, KeyError, UnicodeError, AttributeError, csv.Error):
        return _no("UNPARSEABLE_CHAMPIONS_STATIC_FIELDS")
