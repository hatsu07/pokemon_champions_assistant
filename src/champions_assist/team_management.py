"""Local team CSV registration, round-trip editing, and evidence-bounded checks."""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
from pathlib import Path
import shutil
import unicodedata
import uuid
from typing import Iterable

from .app_config import ConfigurationError, atomic_write_text


TEAM_REQUIRED_COLUMNS = (
    "record", "name", "item", "ability", "nature", "ap_h_a_b_c_d_s",
    "move1", "move2", "move3", "move4", "role", "form_change", "runtime_note",
)
TEAM_STATUS_VALIDATED = "VALIDATED"
TEAM_STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
TEAM_STATUS_INVALID = "INVALID"
TEAM_STATUS_SOURCE_MISSING = "SOURCE_MISSING"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _split_variant(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split("→") if item.strip())


def _catalog_key(value: str) -> str:
    """Compare fixed-master labels without changing the CSV's preserved text."""
    return unicodedata.normalize("NFKC", value).strip()


@dataclass(frozen=True)
class TeamDocument:
    fieldnames: tuple[str, ...]
    rows: tuple[dict[str, str], ...]

    @classmethod
    def load(cls, path: str | Path) -> "TeamDocument":
        return cls.from_text(Path(path).read_text(encoding="utf-8-sig"))

    @classmethod
    def blank(cls, display_name: str) -> "TeamDocument":
        """Six-slot GUI draft without inventing Champions move or AP data."""
        name = display_name.strip()
        if not name or len(name) > 100:
            raise ConfigurationError("構築名を1～100文字で入力してください")
        columns = ("record", "date", "reg", *(
            column for column in TEAM_REQUIRED_COLUMNS if column != "record"
        ), "priority", "setup", "recovery", "choice_lock", "action_guarantee")
        team = {column: "" for column in columns}
        team.update(record="TEAM", name=name, role="GUI_DRAFT")
        rows = [team]
        for _ in range(6):
            member = {column: "" for column in columns}
            member["record"] = "MON"
            rows.append(member)
        return cls(tuple(columns), tuple(rows))

    @classmethod
    def from_text(cls, text: str) -> "TeamDocument":
        records = list(csv.reader(StringIO(text)))
        if not records:
            raise ConfigurationError("team CSV has no header")
        header = [str(item) for item in records[0]]
        if not header:
            raise ConfigurationError("team CSV has no header")
        maximum_fields = max([len(header), *(len(row) for row in records[1:])])
        # Some supplied CSVs contain a trailing value beyond the declared
        # header. Preserve it under a deterministic extension column rather
        # than silently losing a TEAM note or MON runtime annotation.
        fieldnames = tuple(header + [f"__extra_{index}" for index in range(len(header) + 1, maximum_fields + 1)])
        if len(fieldnames) != len(set(fieldnames)):
            raise ConfigurationError("team CSV has duplicate columns")
        rows: list[dict[str, str]] = []
        for raw_row in records[1:]:
            if not raw_row:
                continue
            padded = raw_row + [""] * (len(fieldnames) - len(raw_row))
            rows.append({name: padded[index] for index, name in enumerate(fieldnames)})
        return cls(fieldnames, tuple(rows))

    @property
    def team_rows(self) -> tuple[dict[str, str], ...]:
        return tuple(row for row in self.rows if row.get("record") == "TEAM")

    @property
    def members(self) -> tuple[dict[str, str], ...]:
        return tuple(row for row in self.rows if row.get("record") == "MON")

    def to_text(self) -> str:
        buffer = StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=list(self.fieldnames), extrasaction="raise", lineterminator="\n")
        writer.writeheader()
        for row in self.rows:
            writer.writerow({name: row.get(name, "") for name in self.fieldnames})
        return buffer.getvalue()

    def sha256(self) -> str:
        return _sha256_bytes(self.to_text().encode("utf-8"))


@dataclass(frozen=True)
class TeamValidation:
    status: str
    reasons: tuple[str, ...]
    master_sha256: dict[str, str]

    @property
    def selectable_for_next_match(self) -> bool:
        return self.status in {TEAM_STATUS_VALIDATED, TEAM_STATUS_NEEDS_REVIEW}


@dataclass(frozen=True)
class ChampionsTeamCatalog:
    pokemon: frozenset[str]
    moves: frozenset[str]
    items: frozenset[str]
    abilities: frozenset[str]
    learnsets: dict[str, frozenset[str]]
    master_sha256: dict[str, str]

    @staticmethod
    def _read_rows(path: Path) -> tuple[list[dict[str, str]], str]:
        body = path.read_bytes()
        rows = list(csv.DictReader(body.decode("utf-8-sig").splitlines()))
        return rows, "sha256:" + hashlib.sha256(body).hexdigest()

    @classmethod
    def load(cls, master_directory: str | Path) -> "ChampionsTeamCatalog":
        directory = Path(master_directory)
        files = {
            "pokemon": directory / "Pokemon_Champions_MC_usable_pokemon.csv",
            "moves": directory / "Pokemon_Champions_MC_move_master.csv",
            "items": directory / "Pokemon_Champions_MC_usable_held_items.csv",
            "abilities": directory / "Pokemon_Champions_MC_ability_master.csv",
            "learnsets": directory / "Pokemon_Champions_MC_pokemon_learnsets_linked.csv",
        }
        try:
            parsed = {key: cls._read_rows(path) for key, path in files.items()}
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            raise ConfigurationError(f"Champions master catalogue unavailable: {type(exc).__name__}") from exc
        learnsets = {
            _catalog_key(row.get("ポケモン名", "")): frozenset(_catalog_key(name) for name in row.get("習得技名一覧", "").split("、") if name.strip())
            for row in parsed["learnsets"][0]
            if row.get("ポケモン名")
        }
        return cls(
            pokemon=frozenset(_catalog_key(row.get("ポケモン名", "")) for row in parsed["pokemon"][0] if row.get("ポケモン名")),
            moves=frozenset(_catalog_key(row.get("技名", "")) for row in parsed["moves"][0] if row.get("技名")),
            items=frozenset(_catalog_key(row.get("道具名", "")) for row in parsed["items"][0] if row.get("道具名")),
            abilities=frozenset(_catalog_key(row.get("特性名", "")) for row in parsed["abilities"][0] if row.get("特性名")),
            learnsets=learnsets,
            master_sha256={key: digest for key, (_rows, digest) in parsed.items()},
        )


def validate_team_document(document: TeamDocument, catalog: ChampionsTeamCatalog | None) -> TeamValidation:
    reasons: list[str] = []
    missing = [column for column in TEAM_REQUIRED_COLUMNS if column not in document.fieldnames]
    if missing:
        reasons.append("必須列不足: " + ", ".join(missing))
    if len(document.team_rows) != 1:
        reasons.append("TEAM 行は1件必要です")
    members = document.members
    if len(members) != 6:
        reasons.append("MON 行は6件必要です")
    names = [row.get("name", "").strip() for row in members]
    if any(not name for name in names):
        reasons.append("MON 名が空です")
    if len(set(names)) != len(names):
        reasons.append("MON 名が重複しています")
    for member in members:
        name = member.get("name", "").strip()
        missing_moves = [key for key in ("move1", "move2", "move3", "move4") if not member.get(key, "").strip()]
        if missing_moves:
            reasons.append(f"{name or '名称不明'}: 技が4件揃っていません")
        for key in ("item", "ability", "nature", "ap_h_a_b_c_d_s"):
            if not member.get(key, "").strip():
                reasons.append(f"{name or '名称不明'}: {key} が空です")
    if reasons:
        return TeamValidation(TEAM_STATUS_INVALID, tuple(reasons), {} if catalog is None else dict(catalog.master_sha256))
    if catalog is None:
        return TeamValidation(TEAM_STATUS_SOURCE_MISSING, ("Champions 固定マスターを読み込めません",), {})

    catalogue_reasons: list[str] = []
    review_reasons: list[str] = []
    for member in members:
        raw_name = member["name"].strip()
        variants = tuple(_catalog_key(value) for value in _split_variant(raw_name))
        if not variants or any(name not in catalog.pokemon for name in variants):
            catalogue_reasons.append(f"{raw_name}: ポケモンが固定マスターにありません")
            continue
        base_name = variants[0]
        for item in (_catalog_key(value) for value in _split_variant(member["ability"])):
            if item not in catalog.abilities:
                catalogue_reasons.append(f"{raw_name}: 特性 {item} が固定マスターにありません")
        if _catalog_key(member["item"]) not in catalog.items:
            catalogue_reasons.append(f"{raw_name}: 道具が固定マスターにありません")
        known_moves = catalog.learnsets.get(base_name)
        if known_moves is None:
            review_reasons.append(f"{raw_name}: 習得技マスターに対象形式がありません")
        for key in ("move1", "move2", "move3", "move4"):
            move = _catalog_key(member[key])
            if move not in catalog.moves:
                catalogue_reasons.append(f"{raw_name}: 技 {move} が固定マスターにありません")
            elif known_moves is not None and move not in known_moves:
                catalogue_reasons.append(f"{raw_name}: 技 {move} は確認済み習得技にありません")
        # No current fixed master establishes general AP/nature constraints.
        review_reasons.append(f"{raw_name}: 性格/AP配分のChampions検証根拠は未収録")
    if catalogue_reasons:
        return TeamValidation(TEAM_STATUS_INVALID, tuple(catalogue_reasons), dict(catalog.master_sha256))
    if review_reasons:
        return TeamValidation(TEAM_STATUS_NEEDS_REVIEW, tuple(review_reasons), dict(catalog.master_sha256))
    return TeamValidation(TEAM_STATUS_VALIDATED, (), dict(catalog.master_sha256))


@dataclass(frozen=True)
class RegisteredTeam:
    team_id: str
    display_name: str
    csv_path: str
    source_path: str | None
    imported_at: str
    updated_at: str
    sha256: str
    validation: TeamValidation

    def as_dict(self) -> dict[str, object]:
        return {
            "team_id": self.team_id,
            "display_name": self.display_name,
            "csv_path": self.csv_path,
            "source_path": self.source_path,
            "imported_at": self.imported_at,
            "updated_at": self.updated_at,
            "sha256": self.sha256,
            "validation": {
                "status": self.validation.status,
                "reasons": list(self.validation.reasons),
                "master_sha256": self.validation.master_sha256,
            },
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "RegisteredTeam":
        validation = value.get("validation")
        if not isinstance(validation, dict):
            raise ConfigurationError("team registration has no validation")
        return cls(
            team_id=str(value["team_id"]),
            display_name=str(value["display_name"]),
            csv_path=str(value["csv_path"]),
            source_path=None if value.get("source_path") is None else str(value["source_path"]),
            imported_at=str(value["imported_at"]),
            updated_at=str(value["updated_at"]),
            sha256=str(value["sha256"]),
            validation=TeamValidation(
                str(validation["status"]),
                tuple(str(reason) for reason in validation.get("reasons", [])),
                {str(key): str(item) for key, item in dict(validation.get("master_sha256", {})).items()},
            ),
        )


class TeamRepository:
    """Private user-data team store; it never writes into ``masters/``."""

    def __init__(self, root: str | Path, *, catalog: ChampionsTeamCatalog | None) -> None:
        self.root = Path(root)
        self.catalog = catalog

    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    def _load_index(self) -> dict[str, RegisteredTeam]:
        if not self.index_path.exists():
            return {}
        payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ConfigurationError("team index must be a list")
        return {record.team_id: record for record in (RegisteredTeam.from_dict(item) for item in payload)}

    def _save_index(self, records: Iterable[RegisteredTeam]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = [record.as_dict() for record in sorted(records, key=lambda item: item.team_id)]
        atomic_write_text(self.index_path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    def list(self) -> tuple[RegisteredTeam, ...]:
        return tuple(sorted(self._load_index().values(), key=lambda item: (item.display_name, item.team_id)))

    def get(self, team_id: str) -> RegisteredTeam:
        try:
            return self._load_index()[team_id]
        except KeyError as exc:
            raise ConfigurationError(f"unknown team: {team_id}") from exc

    def import_csv(self, source: str | Path, *, display_name: str | None = None) -> RegisteredTeam:
        source_path = Path(source)
        document = TeamDocument.load(source_path)
        validation = validate_team_document(document, self.catalog)
        team_id = uuid.uuid4().hex
        destination = self.root / f"{team_id}.csv"
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_text(destination, document.to_text())
        now = _utc_now()
        team_name = display_name or (document.team_rows[0].get("date") if document.team_rows else None) or source_path.stem
        record = RegisteredTeam(team_id, str(team_name), str(destination), str(source_path), now, now, document.sha256(), validation)
        records = self._load_index()
        records[team_id] = record
        self._save_index(records.values())
        return record

    def create_blank(self, display_name: str) -> RegisteredTeam:
        """Register an editable six-slot draft without importing any CSV."""
        document = TeamDocument.blank(display_name)
        team_id = uuid.uuid4().hex
        destination = self.root / f"{team_id}.csv"
        records = self._load_index()
        now = _utc_now()
        record = RegisteredTeam(
            team_id, display_name.strip(), str(destination), None, now, now,
            document.sha256(), validate_team_document(document, self.catalog),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_text(destination, document.to_text())
        try:
            records[team_id] = record
            self._save_index(records.values())
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return record

    def update_csv(self, team_id: str, text: str) -> RegisteredTeam:
        document = TeamDocument.from_text(text)
        validation = validate_team_document(document, self.catalog)
        records = self._load_index()
        previous = records.get(team_id)
        if previous is None:
            raise ConfigurationError(f"unknown team: {team_id}")
        destination = Path(previous.csv_path)
        atomic_write_text(destination, document.to_text())
        updated = RegisteredTeam(
            previous.team_id, previous.display_name, str(destination), previous.source_path,
            previous.imported_at, _utc_now(), document.sha256(), validation,
        )
        records[team_id] = updated
        self._save_index(records.values())
        return updated

    def revalidate(self, team_id: str) -> RegisteredTeam:
        """Re-evaluate a saved team with today's bundled Champions validation.

        The index's status is a snapshot and may have been computed by an older
        app version (for example, when role was incorrectly mandatory). Do not
        require a content edit simply to refresh the status. No battle-session
        assets are read or modified here.
        """
        records = self._load_index()
        try:
            previous = records[team_id]
        except KeyError as exc:
            raise ConfigurationError(f"unknown team: {team_id}") from exc
        document = TeamDocument.load(previous.csv_path)
        validation = validate_team_document(document, self.catalog)
        digest = document.sha256()
        if previous.validation == validation and previous.sha256 == digest:
            return previous
        refreshed = replace(
            previous,
            validation=validation,
            sha256=digest,
            updated_at=_utc_now() if digest != previous.sha256 else previous.updated_at,
        )
        records[team_id] = refreshed
        self._save_index(records.values())
        return refreshed

    def export_csv(self, team_id: str, destination: str | Path) -> Path:
        record = self.get(team_id)
        text = Path(record.csv_path).read_text(encoding="utf-8")
        target = Path(destination)
        atomic_write_text(target, text)
        return target

    def duplicate(self, team_id: str, *, display_name: str | None = None) -> RegisteredTeam:
        record = self.get(team_id)
        clone_path = self.root / f"{uuid.uuid4().hex}.csv"
        self.root.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(record.csv_path, clone_path)
        document = TeamDocument.load(clone_path)
        clone = RegisteredTeam(
            clone_path.stem, display_name or f"{record.display_name} (copy)", str(clone_path), record.csv_path,
            _utc_now(), _utc_now(), document.sha256(), validate_team_document(document, self.catalog),
        )
        records = self._load_index()
        records[clone.team_id] = clone
        self._save_index(records.values())
        return clone

    def delete(self, team_id: str) -> None:
        records = self._load_index()
        record = records.pop(team_id, None)
        if record is None:
            raise ConfigurationError(f"unknown team: {team_id}")
        # Commit the index change before deleting private content, so a
        # failed index write cannot leave a reference to a missing CSV.
        self._save_index(records.values())
        Path(record.csv_path).unlink(missing_ok=True)

    def path_for_next_match(self, team_id: str) -> Path:
        record = self.get(team_id)
        if not record.validation.selectable_for_next_match:
            raise ConfigurationError("INVALID or SOURCE_MISSING team cannot be selected for a new match")
        path = Path(record.csv_path)
        if not path.is_file():
            raise ConfigurationError("registered team CSV is missing")
        return path
