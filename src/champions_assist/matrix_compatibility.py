"""Fail-closed check that the frozen LIVE matrix matches the registered own team.

The shipped # OWN column mapping is specific to the original six members.
Matching only the six opponent row keys cannot establish compatibility.
"""
from __future__ import annotations
import re
from .preload import FrozenMatchAssets
from .species_name_aliases import selection_base_name


def configured_matrix_team(assets: FrozenMatchAssets) -> tuple[str, ...]:
    body = assets.contents.get("Pokemon_Champions_MC_live_selection_matrix.txt", "")
    line = next((line for line in body.splitlines() if line.startswith("# OWN={")), "")
    if not line.endswith("}") or not line.startswith("# OWN={"):
        return ()
    pairs = line[len("# OWN={"):-1].split(",")
    entries = []
    for pair in pairs:
        if not re.fullmatch(r"[A-Z]:[^,:{}]+", pair):
            return ()
        entries.append(pair.split(":", 1)[1])
    return tuple(entries)


def registered_matrix_team(assets: FrozenMatchAssets) -> tuple[str, ...]:
    choices = []
    for member in assets.own_team_names:
        # A registered Mega target belongs to the matrix's Mega column.
        chosen = member.split("→", 1)[-1].strip()
        choices.append(selection_base_name(chosen))
    return tuple(choices)


def matrix_matches_registered_team(assets: FrozenMatchAssets) -> bool:
    matrix = configured_matrix_team(assets)
    own = registered_matrix_team(assets)
    body = assets.contents.get("Pokemon_Champions_MC_live_selection_matrix.txt", "")
    # A newly generated file can have perfectly matching OWN headers but
    # unknown matchup cells. That is not a battle-ready LIVE evaluation.
    if "# DERIVED_STATUS=PROVISIONAL_UNVERIFIED" in body:
        return False
    return len(matrix) == len(own) == 6 and len(set(matrix)) == 6 and set(matrix) == set(own)


def provisional_matrix_matches_registered_team(assets: FrozenMatchAssets) -> bool:
    """Recognize ONLY generator-owned provisional rows for the exact registered six.

    This is permission for clearly tagged AI hypothesis generation, NEVER
    compatibility certification or processor/safe-switch promotion. Old-team
    matrices and arbitrary OWN headers must still fail closed.
    """
    body = assets.contents.get("Pokemon_Champions_MC_live_selection_matrix.txt", "")
    if (not body.startswith("# TEAM_SCOPED_MATRIX v1;")
            or "# DERIVED_STATUS=PROVISIONAL_UNVERIFIED;" not in body
            or "# PROVENANCE=EXACT_REGISTERED_SET_ONLY;" not in body
            or "# UNVERIFIED_CELLS_ARE_NOT_PROCESSORS_OR_SAFE_SWITCHES" in body):
        # The latter line is not part of the generated format; retained for
        # compatibility with separately supplied unsupported source files.
        return False
    matrix = configured_matrix_team(assets)
    own = registered_matrix_team(assets)
    return len(matrix) == len(own) == 6 and len(set(matrix)) == 6 and matrix == own
