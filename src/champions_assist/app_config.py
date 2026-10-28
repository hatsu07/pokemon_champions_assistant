"""Versioned, non-secret desktop configuration and safe local data moves.

The configuration deliberately contains no OpenAI key, token, or credential.
It is separate from match snapshots: a pending team or storage change is only
applied before a subsequent match is preloaded.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid
from typing import Any, Iterable

from .roi import NormalizedROI


SETTINGS_SCHEMA_VERSION = "1.0"
APP_DIRECTORY_NAME = "PokemonChampionsAssistant"
_BACKENDS = frozenset({"WINDOWS_NATIVE", "ADB_SCREENSHOT", "REPLAY"})


class ConfigurationError(ValueError):
    """A local configuration cannot safely be loaded or applied."""


@dataclass(frozen=True)
class CaptureConfiguration:
    backend: str = "WINDOWS_NATIVE"
    preferred_process: str = ""
    preferred_title: str = ""
    selected_window_identity: dict[str, Any] | None = None
    game_viewport_normalized: tuple[float, float, float, float] | None = None
    adb_path: str | None = None
    adb_serial: str | None = None

    def __post_init__(self) -> None:
        if self.backend not in _BACKENDS:
            raise ConfigurationError(f"unsupported capture backend: {self.backend}")
        if self.game_viewport_normalized is not None:
            if len(self.game_viewport_normalized) != 4:
                raise ConfigurationError("game viewport must contain four normalized values")
            NormalizedROI("GAME_VIEWPORT", *self.game_viewport_normalized)

    def as_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "preferred_process": self.preferred_process,
            "preferred_title": self.preferred_title,
            "selected_window_identity": self.selected_window_identity,
            "game_viewport_normalized": None if self.game_viewport_normalized is None else list(self.game_viewport_normalized),
            "adb_path": self.adb_path,
            "adb_serial": self.adb_serial,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CaptureConfiguration":
        viewport = value.get("game_viewport_normalized")
        if viewport is not None:
            if not isinstance(viewport, list) or len(viewport) != 4:
                raise ConfigurationError("game_viewport_normalized must be a four-element list or null")
            viewport_tuple = tuple(float(item) for item in viewport)
        else:
            viewport_tuple = None
        identity = value.get("selected_window_identity")
        if identity is not None and not isinstance(identity, dict):
            raise ConfigurationError("selected_window_identity must be an object or null")
        return cls(
            backend=str(value.get("backend", "WINDOWS_NATIVE")),
            preferred_process=str(value.get("preferred_process", "")),
            preferred_title=str(value.get("preferred_title", "")),
            selected_window_identity=None if identity is None else dict(identity),
            game_viewport_normalized=viewport_tuple,
            adb_path=None if value.get("adb_path") in (None, "") else str(value["adb_path"]),
            adb_serial=None if value.get("adb_serial") in (None, "") else str(value["adb_serial"]),
        )


@dataclass(frozen=True)
class StorageConfiguration:
    data_root: str
    captures_root: str
    save_screenshots: bool = False
    retention_days: int = 30

    def __post_init__(self) -> None:
        if self.retention_days < 0:
            raise ConfigurationError("retention_days cannot be negative")
        if not self.data_root or not self.captures_root:
            raise ConfigurationError("storage paths cannot be empty")

    def as_dict(self) -> dict[str, object]:
        return {
            "data_root": self.data_root,
            "captures_root": self.captures_root,
            "save_screenshots": self.save_screenshots,
            "retention_days": self.retention_days,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StorageConfiguration":
        return cls(
            data_root=str(value["data_root"]),
            captures_root=str(value["captures_root"]),
            save_screenshots=bool(value.get("save_screenshots", False)),
            retention_days=int(value.get("retention_days", 30)),
        )


@dataclass(frozen=True)
class TeamConfiguration:
    active_team_id: str | None = None
    pending_team_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {"active_team_id": self.active_team_id, "pending_team_id": self.pending_team_id}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TeamConfiguration":
        return cls(
            active_team_id=None if value.get("active_team_id") in (None, "") else str(value["active_team_id"]),
            pending_team_id=None if value.get("pending_team_id") in (None, "") else str(value["pending_team_id"]),
        )


@dataclass(frozen=True)
class AppSettings:
    schema_version: str
    capture: CaptureConfiguration
    storage: StorageConfiguration
    teams: TeamConfiguration
    pending_storage: StorageConfiguration | None = None

    @classmethod
    def defaults(cls, app_root: str | Path) -> "AppSettings":
        root = Path(app_root)
        return cls(
            SETTINGS_SCHEMA_VERSION,
            CaptureConfiguration(),
            StorageConfiguration(str(root / "data"), str(root / "captures")),
            TeamConfiguration(),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "capture": self.capture.as_dict(),
            "storage": self.storage.as_dict(),
            "teams": self.teams.as_dict(),
            "pending_storage": None if self.pending_storage is None else self.pending_storage.as_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AppSettings":
        version = str(value.get("schema_version", ""))
        if version != SETTINGS_SCHEMA_VERSION:
            raise ConfigurationError(f"unsupported settings schema version: {version or 'missing'}")
        capture = value.get("capture")
        storage = value.get("storage")
        teams = value.get("teams")
        if not isinstance(capture, dict) or not isinstance(storage, dict) or not isinstance(teams, dict):
            raise ConfigurationError("capture, storage and teams must be objects")
        pending = value.get("pending_storage")
        if pending is not None and not isinstance(pending, dict):
            raise ConfigurationError("pending_storage must be an object or null")
        return cls(
            version,
            CaptureConfiguration.from_dict(capture),
            StorageConfiguration.from_dict(storage),
            TeamConfiguration.from_dict(teams),
            None if pending is None else StorageConfiguration.from_dict(pending),
        )


@dataclass(frozen=True)
class SettingsLoad:
    settings: AppSettings
    source: str
    first_launch: bool
    recovery_detail: str | None = None


@dataclass(frozen=True)
class StorageCheck:
    path: Path
    writable: bool
    free_bytes: int | None
    reason: str | None = None


def default_app_root(*, platform: str | None = None, environ: dict[str, str] | None = None) -> Path:
    """Return the user-data root without creating it.

    Tests pass a platform/environment explicitly.  A Windows process uses
    ``LOCALAPPDATA`` as required by the deployment design; Linux/Docker keeps
    test data under an XDG-compatible user location instead.
    """
    target_platform = os.name if platform is None else platform
    env = os.environ if environ is None else environ
    if target_platform in {"nt", "win32", "windows"}:
        base = env.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_DIRECTORY_NAME
        return Path.home() / "AppData" / "Local" / APP_DIRECTORY_NAME
    return Path(env.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / APP_DIRECTORY_NAME


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":  # Windows ReplaceFile/MoveFile durability is runtime-specific.
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_text(path: str | Path, text: str) -> Path:
    """Write local non-secret state with a same-directory atomic replacement."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return target


def _assert_non_secret(value: Any, *, trail: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            folded = str(key).casefold()
            if any(marker in folded for marker in ("api_key", "token", "secret", "password", "authorization")):
                raise ConfigurationError(f"secret-like setting is forbidden: {'.'.join((*trail, str(key)))}")
            _assert_non_secret(child, trail=(*trail, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_non_secret(child, trail=(*trail, str(index)))


class AppSettingsStore:
    """JSON persistence with version validation and last-good backup recovery."""

    def __init__(self, path: str | Path, *, defaults_root: str | Path | None = None) -> None:
        self.path = Path(path)
        self.defaults_root = Path(defaults_root) if defaults_root is not None else self.path.parent.parent

    @property
    def backup_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".bak")

    def _read(self, path: Path) -> AppSettings:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ConfigurationError("settings root must be an object")
        _assert_non_secret(payload)
        return AppSettings.from_dict(payload)

    def load(self) -> SettingsLoad:
        if not self.path.exists():
            return SettingsLoad(AppSettings.defaults(self.defaults_root), "DEFAULTS", True)
        try:
            return SettingsLoad(self._read(self.path), "PRIMARY", False)
        except (OSError, json.JSONDecodeError, ConfigurationError) as primary_error:
            if self.backup_path.exists():
                try:
                    return SettingsLoad(self._read(self.backup_path), "BACKUP_RECOVERY", False, type(primary_error).__name__)
                except (OSError, json.JSONDecodeError, ConfigurationError):
                    pass
            return SettingsLoad(AppSettings.defaults(self.defaults_root), "DEFAULT_RECOVERY", False, type(primary_error).__name__)

    def save(self, settings: AppSettings) -> Path:
        payload = settings.as_dict()
        _assert_non_secret(payload)
        serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if self.path.exists():
            # Preserve a parseable last-good candidate before replacing primary.
            previous = self.path.read_text(encoding="utf-8")
            try:
                self._read(self.path)
            except (OSError, json.JSONDecodeError, ConfigurationError):
                # A recovered configuration must be saveable. Retain the
                # malformed bytes locally for manual inspection instead of
                # overwriting the known-good .bak with them.
                corrupt = self.path.with_suffix(self.path.suffix + ".corrupt")
                atomic_write_text(corrupt, previous)
            else:
                atomic_write_text(self.backup_path, previous)
        return atomic_write_text(self.path, serialized)


def validate_storage_directory(path: str | Path) -> StorageCheck:
    """Check a user-selected directory without writing any battle data to it."""
    target = Path(path).expanduser()
    try:
        target.mkdir(parents=True, exist_ok=True)
        resolved = target.resolve()
        if resolved == Path(resolved.anchor):
            return StorageCheck(resolved, False, None, "filesystem root is not an allowed data directory")
        probe = resolved / f".pokemon-champions-write-{uuid.uuid4().hex}"
        with probe.open("xb") as stream:
            stream.write(b"probe")
            stream.flush()
            os.fsync(stream.fileno())
        probe.unlink()
        free = shutil.disk_usage(resolved).free
        return StorageCheck(resolved, True, int(free))
    except OSError as exc:
        return StorageCheck(target, False, None, f"{type(exc).__name__}: {exc}")


def _tree_manifest(root: Path) -> tuple[tuple[str, int, str], ...]:
    import hashlib

    entries: list[tuple[str, int, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append((str(path.relative_to(root)), path.stat().st_size, digest))
    return tuple(entries)


def migrate_data_directory(source: str | Path, destination: str | Path) -> Path:
    """Copy and verify a complete local data root before switching settings.

    Source files are never removed.  A failed copy leaves both the configured
    source and any existing destination untouched.
    """
    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    if source_path == destination_path:
        return destination_path
    if not source_path.exists():
        destination_path.mkdir(parents=True, exist_ok=True)
        return destination_path
    if not source_path.is_dir():
        raise ConfigurationError("data source must be a directory")
    check = validate_storage_directory(destination_path)
    if not check.writable:
        raise ConfigurationError(f"destination is not writable: {check.reason}")
    if any(destination_path.iterdir()):
        raise ConfigurationError("destination must be empty before a safe migration")
    staging = destination_path.parent / f".{destination_path.name}.migration-{uuid.uuid4().hex}"
    try:
        shutil.copytree(source_path, staging)
        if _tree_manifest(source_path) != _tree_manifest(staging):
            raise ConfigurationError("copied data did not match source manifest")
        destination_path.rmdir()
        os.replace(staging, destination_path)
        _fsync_directory(destination_path.parent)
        return destination_path
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def replace_settings(settings: AppSettings, **changes: Any) -> AppSettings:
    """Small explicit wrapper that keeps callers from mutating frozen settings."""
    return replace(settings, **changes)
