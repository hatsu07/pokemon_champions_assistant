"""Compile frozen team-specific matrix evaluations BEFORE matchmaking.

The 14-column Champions matrix already includes six own-team matchup cells
and optional offline AI hints per opponent. Preserve those exact inputs and
their uncertainty labels. Do not infer damage, safe switches or missing cells.
The compiled index is immutable and never performs match-time disk I/O.
"""
from __future__ import annotations

from types import MappingProxyType
import re
from typing import Mapping

from .species_name_aliases import selection_base_name

_UNKNOWN_CELL = frozenset({"", "-", "?,?,?,?,U,0"})
_OWN_HEADER = re.compile(r"^# OWN=\{([^{}]+)\}$")
_SLOT = re.compile(r"(?:^|\s)([A-F]):")


def _own_names(matrix_text: str) -> tuple[str, ...]:
    line = next((line for line in matrix_text.splitlines()
                 if line.startswith("# OWN={")), "")
    match = _OWN_HEADER.fullmatch(line)
    if not match:
        return ()
    fields = match.group(1).split(",")
    if len(fields) != 6 or any(
            not field.startswith(chr(65 + i) + ":")
            for i, field in enumerate(fields)):
        return ()
    names = tuple(field.split(":", 1)[1] for field in fields)
    return names if len(set(names)) == 6 else ()


def _offline_hints(plan: str) -> Mapping[str, str]:
    if not plan.startswith("AI_UNVERIFIED:"):
        return MappingProxyType({})
    body = plan[len("AI_UNVERIFIED:"):].split(" POSTMATCH_UNVERIFIED:", 1)[0]
    markers = list(_SLOT.finditer(body))
    hints = {}
    for i, marker in enumerate(markers):
        start = marker.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(body)
        value = body[start:end].strip()
        if value and value != "?" and value != "-":
            hints[marker.group(1)] = value
    return MappingProxyType(hints)


def _base_name(value: str) -> str:
    return selection_base_name(value.split("→")[-1].strip())


def compile_matchup_cache(matrix_text: str, matrix_rows: Mapping[str, str],
                          own_team: tuple[dict, ...]) -> Mapping[str, Mapping[str, Mapping]]:
    """Own member -> opponent -> immutable cached evidence.

    A matrix header is not proof of exact-set compatibility; consumers must
    separately require reviewed/provisional *current-team* compatibility.
    """
    names = _own_names(matrix_text)
    if len(names) != 6 or len(own_team) != 6:
        return MappingProxyType({})
    positions = {_base_name(name): i for i, name in enumerate(names)}
    registered = [m.get("name", "") for m in own_team]
    if len(set(registered)) != 6 or any(
            _base_name(name) not in positions for name in registered):
        return MappingProxyType({})
    result = {name: {} for name in registered}
    for opponent, line in matrix_rows.items():
        cols = line.split("|")
        if len(cols) != 14 or cols[0] != opponent:
            continue
        hints = _offline_hints(cols[11])
        for member in registered:
            column = positions[_base_name(member)]
            cell = cols[3 + column]
            hint = hints.get(chr(65 + column), "")
            # Older reviewed cells are usable as conditional evidence, as
            # are the team's off-match AI evaluation notes. Neither is proof
            # of an exact KO, opponent set, or safe switch.
            cached = cell not in _UNKNOWN_CELL and not cell.startswith("?,")
            source = ("REVIEWED_CELL" if cached else
                      "AI_HINT" if hint else "MISSING")
            result[member][opponent] = MappingProxyType({
                "source": source,
                "matrix_cell": cell if cached else "",
                "ai_hint": hint,
                "opponent_type": cols[1],
                "opponent_base_speed": cols[2],
                "opponent_warning": cols[10],
                "fast_six_hint": cols[12].split("/")[column]
                if column < len(cols[12].split("/")) else "?",
                "provenance": "FROZEN_CHAMPIONS_TEAM_MATRIX",
            })
    return MappingProxyType({
        name: MappingProxyType(rows) for name, rows in result.items()
    })


def cached_pairs(assets, selected_three: tuple[str, ...],
                 known_opponents: tuple[str, ...], lead: str) -> list[dict]:
    """Ordered 6+12 (or 5+10) without secondary lookup or model inference."""
    cache = assets.matchup_cache_index
    members = (lead, *(name for name in selected_three if name != lead))
    rows = []
    for member in members:
        for opponent in known_opponents:
            cell = cache.get(member, {}).get(opponent)
            if cell is None:
                return []
            rows.append({
                "our_pokemon": member, "opponent_pokemon": opponent,
                **dict(cell),
            })
    return rows


def cached_evidence_count(rows: list[dict]) -> int:
    return sum(r.get("source") in ("REVIEWED_CELL", "AI_HINT")
               for r in rows)
