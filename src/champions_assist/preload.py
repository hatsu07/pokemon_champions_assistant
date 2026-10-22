"""Frozen four-master match preloader.

The loader reads all required source bytes on every new-match request.  A live
session receives only the resulting immutable in-memory contents and indexes; it
has no path back to the filesystem.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


REQUIRED_MASTER_FILES = (
    "Pokemon_Champions_MC_live_decision_gate.txt",
    "Pokemon_Champions_MC_live_selection_matrix.txt",
    "Pokemon_Champions_MC_60sec_lookup_master.txt",
    "Pokemon_Champions_MC_team_2026-10-03.csv",
)


class PreloadError(RuntimeError):
    """All failed file reads are reported together; partial preload is unusable."""

    def __init__(self, failures: Mapping[str, str]) -> None:
        self.failures = dict(failures)
        super().__init__("preload incomplete: " + ", ".join(sorted(failures)))


def _indexed_pipe_rows(contents: str, source_name: str) -> Mapping[str, str]:
    rows: dict[str, str] = {}
    for raw_line in contents.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key = line.split("|", 1)[0]
        if not key or key in rows:
            raise ValueError(f"{source_name}: duplicate or empty key: {key!r}")
        rows[key] = line
    if not rows:
        raise ValueError(f"{source_name}: no data rows")
    return MappingProxyType(rows)


def _parse_team(contents: str) -> tuple[dict[str, str], ...]:
    reader = csv.DictReader(contents.splitlines())
    team = tuple(row for row in reader if row.get("record") == "MON")
    if len(team) != 6 or any(not row.get("name") for row in team):
        raise ValueError("team master must contain six named MON records")
    return team


@dataclass(frozen=True)
class FrozenMatchAssets:
    """Match-scoped immutable master text, hashes, and fast indexes."""

    contents: Mapping[str, str]
    sha256: Mapping[str, str]
    matrix_index: Mapping[str, str]
    lookup_index: Mapping[str, str]
    own_team: tuple[dict[str, str], ...]
    frozen_at: str

    @property
    def manifest(self) -> tuple[dict[str, str], ...]:
        return tuple(
            {"name": name, "sha256": self.sha256[name]} for name in REQUIRED_MASTER_FILES
        )

    @property
    def own_team_names(self) -> tuple[str, ...]:
        return tuple(member["name"] for member in self.own_team)

    @property
    def gate_sha256(self) -> str:
        return self.sha256["Pokemon_Champions_MC_live_decision_gate.txt"]

    def matrix_rows_for(self, opponent_preview_six: tuple[str, ...]) -> tuple[str, ...]:
        """Return six cached rows in one call; never inspect a master file again."""
        if len(opponent_preview_six) != 6 or len(set(opponent_preview_six)) != 6:
            raise ValueError("opponent preview must contain six distinct exact names")
        missing = [name for name in opponent_preview_six if name not in self.matrix_index]
        if missing:
            raise KeyError(f"matrix rows unavailable: {', '.join(missing)}")
        return tuple(self.matrix_index[name] for name in opponent_preview_six)


class MasterPreloader:
    """Only this class touches required-master paths, once per load call.

    ``team_source`` is a registered user CSV selected for a future match.  It
    replaces only the fourth live input at load time; the in-memory asset still
    uses the canonical contract filename and is immutable after this call.
    """

    def __init__(self, master_directory: str | Path, *, team_source: str | Path | None = None) -> None:
        self._master_directory = Path(master_directory)
        self._team_source = None if team_source is None else Path(team_source)

    def load_for_new_match(self) -> FrozenMatchAssets:
        raw_bytes: dict[str, bytes] = {}
        failures: dict[str, str] = {}
        for filename in REQUIRED_MASTER_FILES:
            try:
                source = self._team_source if filename == REQUIRED_MASTER_FILES[3] and self._team_source is not None else self._master_directory / filename
                body = source.read_bytes()
                if not body:
                    raise ValueError("empty file")
                raw_bytes[filename] = body
            except (OSError, ValueError) as exc:
                failures[filename] = str(exc)
        if failures:
            raise PreloadError(failures)

        contents = {name: body.decode("utf-8-sig") for name, body in raw_bytes.items()}
        try:
            matrix = _indexed_pipe_rows(contents[REQUIRED_MASTER_FILES[1]], REQUIRED_MASTER_FILES[1])
            lookup = _indexed_pipe_rows(contents[REQUIRED_MASTER_FILES[2]], REQUIRED_MASTER_FILES[2])
            if set(matrix) != set(lookup):
                raise ValueError("matrix and lookup keys differ; refusing an implicit join")
            team = _parse_team(contents[REQUIRED_MASTER_FILES[3]])
        except (UnicodeDecodeError, csv.Error, ValueError) as exc:
            raise PreloadError({"compiled_indexes": str(exc)}) from exc

        return FrozenMatchAssets(
            contents=MappingProxyType(contents),
            sha256=MappingProxyType({name: hashlib.sha256(body).hexdigest() for name, body in raw_bytes.items()}),
            matrix_index=matrix,
            lookup_index=lookup,
            own_team=team,
            frozen_at=datetime.now(timezone.utc).isoformat(),
        )
