"""#141②: Champions-only, off-match *candidate* ID inventory.

Use the frozen registered team plus the supplied Champions move, learnset and
usable-roster masters to identify MoveKey/LearnsetKey CANDIDATES. These source
keys can resemble pinned Showdown IDs but are NOT proof of identical battle
effects, Mega forms, legal choices, PP, abilities, statistics or RNG.

No live callback, no gameplay recommendation and no rewrite of the team CSV.
Review must independently attest any eventual Showdown binding.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
import re
from typing import Any

from ..preload import FrozenMatchAssets, REQUIRED_MASTER_FILES
from .roster_mapping_readiness import audit_frozen_roster_mapping

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_KEY = re.compile(r"[a-z0-9]+\Z")
_DIGIT_WIDTH = str.maketrans("０１２３４５６７８９", "0123456789")
_LIMIT = 4_000_000


@dataclass(frozen=True)
class MoveKeyCandidate:
    slot: int
    champions_move: str
    key_candidate: str = ""
    match_type: str = "NO_SOURCE_MATCH"
    learnset_membership: str = "UNVERIFIED"


@dataclass(frozen=True)
class MemberKeyCandidate:
    registered_name: str
    base_species: str
    proposed_base_key: str
    form_target: str
    form_status: str
    moves: tuple[MoveKeyCandidate, ...]
    species_source_status: str = "UNVERIFIED"


@dataclass(frozen=True)
class ChampionsKeyCandidateReport:
    status: str
    reason: str
    team_sha256: str = ""
    source_sha256: tuple[tuple[str, str], ...] = ()
    members: tuple[MemberKeyCandidate, ...] = ()
    candidate_species_keys: int = 0
    candidate_move_keys: int = 0
    expected_move_slots: int = 0
    unmapped_slots: tuple[str, ...] = ()
    unverified_forms: tuple[str, ...] = ()
    # Candidate-source matching is *not* an engine/game equivalence proof.
    showdown_mapping_verified: bool = False
    champions_mechanics_verified: bool = False
    usable_in_live: bool = False


def _invalid(reason: str) -> ChampionsKeyCandidateReport:
    return ChampionsKeyCandidateReport("INVALID_SOURCE_OR_FROZEN_TEAM", reason)


def _read_master(raw: bytes, required: frozenset[str], name_key: str
                 ) -> dict[str, dict[str, str]]:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= _LIMIT:
        raise ValueError("INVALID_MASTER_BYTES")
    body = raw.decode("utf-8-sig")
    reader = csv.DictReader(StringIO(body))
    if not required.issubset(reader.fieldnames or ()):
        raise ValueError("REQUIRED_SOURCE_COLUMNS_MISSING")
    mapped: dict[str, dict[str, str]] = {}
    for row in reader:
        label = row.get(name_key)
        if not isinstance(label, str) or not label or label != label.strip():
            raise ValueError("INVALID_OR_MISSING_SOURCE_NAME")
        normalized = label.translate(_DIGIT_WIDTH)
        # A width alias must not collapse two independent move names.
        if normalized in mapped:
            raise ValueError("AMBIGUOUS_OR_DUPLICATE_SOURCE_NAME")
        mapped[normalized] = row
    if not mapped:
        raise ValueError("EMPTY_SOURCE_CATALOG")
    return mapped


def audit_champions_master_key_candidates(
    assets: Any, *,
    move_master_bytes: bytes,
    learnsets_bytes: bytes,
    usable_pokemon_bytes: bytes,
) -> ChampionsKeyCandidateReport:
    """Pure off-match proposal audit from Champions fixed-master source bytes.

    These are pre-match/review inputs, NOT new runtime required masters. No
    result grants LocalSimulatorWorker an executable roster or chooses an ID
    for an unsupported Mega form.
    """
    existing = audit_frozen_roster_mapping(assets)
    if existing.status == "INVALID_FROZEN_ASSETS":
        return _invalid("FOUR_MASTER_SOURCE_INTEGRITY_FAILED")
    if not isinstance(assets, FrozenMatchAssets):
        return _invalid("FROZEN_TEAM_REQUIRED")
    try:
        move_master = _read_master(move_master_bytes, frozenset({
            "MoveKey", "技名"}), "技名")
        learnsets = _read_master(learnsets_bytes, frozenset({
            "ポケモン名", "LearnsetKey", "習得MoveKey一覧"}), "ポケモン名")
        usable = _read_master(usable_pokemon_bytes, frozenset({
            "ポケモン名", "区分"}), "ポケモン名")
        origin = (
            ("team_csv", assets.sha256[REQUIRED_MASTER_FILES[3]]),
            ("champions_move_master", sha256(move_master_bytes).hexdigest()),
            ("champions_linked_learnsets", sha256(learnsets_bytes).hexdigest()),
            ("champions_usable_pokemon", sha256(usable_pokemon_bytes).hexdigest()),
        )
        if any(_SHA.fullmatch(digest) is None for _, digest in origin):
            return _invalid("INVALID_SOURCE_HASH")
        rows: list[MemberKeyCandidate] = []
        missing: list[str] = []
        forms: list[str] = []
        species_count = 0
        move_count = 0
        expected_slots = 0
        for row in assets.own_team:
            name = row["name"]
            if " → " in name:
                if name.count(" → ") != 1:
                    return _invalid("INVALID_REGISTERED_FORM_LABEL")
                base, target = name.split(" → ", 1)
                if not base or not target:
                    return _invalid("INVALID_REGISTERED_FORM_LABEL")
            else:
                base, target = name, ""
            base_rec = learnsets.get(base)
            target_rec = learnsets.get(target) if target else None
            base_usable = usable.get(base)
            target_usable = usable.get(target) if target else None
            base_key = base_rec.get("LearnsetKey", "") if base_rec else ""
            base_ok = (bool(base_key) and _KEY.fullmatch(base_key) is not None
                       and base_usable is not None)
            if not base_ok:
                missing.append(name + ".LearnsetKey")
                base_key = ""
            else:
                species_count += 1
            if target:
                form_status = "FORM_NOT_ENGINE_ATTESTED"
                forms.append(name)
                if (target_rec is None or target_usable is None
                        or not base_ok
                        or target_rec.get("LearnsetKey") != base_key):
                    form_status = "FORM_NOT_CONSISTENT_WITH_CHAMPIONS_MASTERS"
                    missing.append(name + ".form_target")
            else:
                form_status = "NO_FORM_CHANGE_DECLARED"
            learned = (frozenset(x.strip() for x in base_rec[
                "習得MoveKey一覧"].split(",") if x.strip())
                if base_rec and base_ok else frozenset())
            moves: list[MoveKeyCandidate] = []
            for i in range(1, 5):
                move_name = row.get(f"move{i}", "").strip()
                if not move_name:
                    continue
                expected_slots += 1
                source = move_master.get(move_name.translate(_DIGIT_WIDTH))
                key = source.get("MoveKey", "") if source else ""
                valid_key = bool(key) and _KEY.fullmatch(key) is not None
                membership = key in learned if valid_key else False
                status = ("EXACT_CHAMPIONS_NAME" if source and
                          source["技名"] == move_name else
                          "DIGIT_WIDTH_EQUIVALENT" if source else
                          "NO_CHAMPIONS_MOVE_SOURCE")
                if valid_key and membership:
                    move_count += 1
                else:
                    missing.append(name + f".move{i}")
                    key = ""
                moves.append(MoveKeyCandidate(
                    i, move_name, key, status,
                    "IN_CHAMPIONS_LEARNSET" if membership
                    else "NOT_ATTESTED_IN_CHAMPIONS_LEARNSET"))
            rows.append(MemberKeyCandidate(
                name, base, base_key, target, form_status, tuple(moves),
                "IN_CHAMPIONS_USABLE_AND_LEARNSETS" if base_ok
                else "CHAMPIONS_SPECIES_NOT_CONFIRMED"))
        if len(rows) != 6:
            return _invalid("REGISTERED_SIX_MEMBERS_REQUIRED")
        return ChampionsKeyCandidateReport(
            "CHAMPIONS_KEY_CANDIDATES_ONLY" if not missing
            else "SOURCE_CANDIDATES_INCOMPLETE",
            "NO_SHOWDOWN_OR_GAME_COMPATIBILITY_ATTESTATION",
            assets.sha256[REQUIRED_MASTER_FILES[3]], origin,
            tuple(rows), species_count, move_count, expected_slots,
            tuple(missing), tuple(forms))
    except (ValueError, KeyError, UnicodeError, TypeError,
            AttributeError, csv.Error):
        return _invalid("MALFORMED_OR_AMBIGUOUS_CHAMPIONS_MASTER")
