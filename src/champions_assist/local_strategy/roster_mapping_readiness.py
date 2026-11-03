"""Off-match preflight: exact frozen own-team Showdown ID *field* readiness.

The pinned worker requires explicit species and move IDs from the match's
registered Champions team CSV. The shipped real team may lack those fields.
This inventory uses only four already-preloaded master bytes and never guesses
Japanese name -> engine ID, move compatibility, PP, opponent roster or RNG.

Passing the structural check is NOT a validated Champions mechanic, playable
Battle, real engine execution, or permission to recommend a game action.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any

from ..preload import FrozenMatchAssets, REQUIRED_MASTER_FILES, _parse_team

_ID = re.compile(r"[a-z0-9]+\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class RosterMappingReadiness:
    status: str
    reason: str
    team_sha256: str = ""
    inspected_members: int = 0
    checked_move_slots: int = 0
    missing_fields: tuple[str, ...] = ()
    invalid_fields: tuple[str, ...] = ()
    # This is only the same restricted *field format* accepted by the
    # pinned worker. It is not proof that values identify the right Pokémon.
    worker_id_fields_structurally_complete: bool = False
    mapping_independently_attested: bool = False
    champions_mechanics_verified: bool = False
    real_game_battle_reconstructed: bool = False
    ready_for_live: bool = False


def _invalid(reason: str) -> RosterMappingReadiness:
    return RosterMappingReadiness("INVALID_FROZEN_ASSETS", reason)


def audit_frozen_roster_mapping(assets: Any) -> RosterMappingReadiness:
    """Pure in-memory audit, appropriate before matching or after a battle.

    Unlike _verified_roster_binding this does not require a synthetic
    DecisionWindow, which would itself be a misleading fabricated game state.
    We still enforce the worker's exact CSV and SHA manifest invariants.
    """
    if not isinstance(assets, FrozenMatchAssets):
        return _invalid("MATCH_SCOPED_FOUR_MASTER_ASSETS_REQUIRED")
    try:
        team_key = REQUIRED_MASTER_FILES[3]
        for name in REQUIRED_MASTER_FILES:
            text = assets.contents[name]
            actual_sha = assets.sha256[name]
            if (not isinstance(text, str) or not isinstance(actual_sha, str)
                    or _SHA.fullmatch(actual_sha) is None):
                return _invalid("INVALID_MASTER_OR_SHA")
            raw = text.encode("utf-8")
            # Preloader decodes UTF-8-SIG and retains SHA of original bytes.
            if (sha256(raw).hexdigest() != actual_sha and
                    sha256(b"\xef\xbb\xbf" + raw).hexdigest() != actual_sha):
                return _invalid("MASTER_SHA_DOES_NOT_MATCH_FROZEN_BYTES")
        registered = _parse_team(assets.contents[team_key])
        if registered != assets.own_team:
            return _invalid("TEAM_CSV_AND_FROZEN_ROSTER_DIFFER")
        if len(registered) != 6 or len({
                r.get("name") for r in registered}) != 6:
            return _invalid("FROZEN_TEAM_REQUIRES_SIX_DISTINCT_NAMES")
        missing: list[str] = []
        invalid: list[str] = []
        ids: set[str] = set()
        checked_moves = 0
        for row in registered:
            name = row["name"]
            if not isinstance(name, str) or not name.strip():
                return _invalid("INVALID_FROZEN_MEMBER_NAME")
            species_id = row.get("showdown_species_id")
            if not isinstance(species_id, str) or not species_id:
                missing.append(name + ".showdown_species_id")
            elif _ID.fullmatch(species_id) is None or species_id in ids:
                invalid.append(name + ".showdown_species_id")
            else:
                ids.add(species_id)
            move_ids: set[str] = set()
            for index in range(1, 5):
                move = row.get(f"move{index}", "")
                move_id = row.get(f"showdown_move{index}_id")
                if move:
                    checked_moves += 1
                    if not isinstance(move_id, str) or not move_id:
                        missing.append(name + f".showdown_move{index}_id")
                    elif (_ID.fullmatch(move_id) is None
                          or move_id in move_ids):
                        invalid.append(name + f".showdown_move{index}_id")
                    else:
                        move_ids.add(move_id)
                elif move_id:
                    invalid.append(name + f".showdown_move{index}_id")
            if not move_ids and not any(
                    f.startswith(name + ".showdown_move") for f in missing):
                invalid.append(name + ".no_mapped_moves")
        if missing or invalid:
            return RosterMappingReadiness(
                "MAPPING_FIELDS_INCOMPLETE",
                "OWN_SPECIES_OR_MOVE_IDS_REQUIRE_EXPLICIT_REVIEW",
                assets.sha256[team_key], len(registered), checked_moves,
                tuple(missing), tuple(invalid))
        return RosterMappingReadiness(
            "STRUCTURAL_IDS_PRESENT_UNATTESTED",
            "ID_STRINGS_NOT_INDEPENDENTLY_VERIFIED_AGAINST_CHAMPIONS_OR_SHOWDOWN",
            assets.sha256[team_key], len(registered), checked_moves,
            worker_id_fields_structurally_complete=True)
    except (AttributeError, KeyError, TypeError, ValueError, UnicodeError):
        return _invalid("MALFORMED_FROZEN_TEAM_OR_MASTER")
