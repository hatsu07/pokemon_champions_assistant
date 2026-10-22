"""GUI-facing settings coordinator with next-match application boundaries."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from .app_config import (
    AppSettings,
    AppSettingsStore,
    CaptureConfiguration,
    ConfigurationError,
    SettingsLoad,
    StorageConfiguration,
    migrate_data_directory,
    validate_storage_directory,
)
from .team_management import ChampionsTeamCatalog, RegisteredTeam, TeamRepository

if TYPE_CHECKING:  # avoid an import cycle between UI/controller and settings.
    from .fsm import MatchController


@dataclass(frozen=True)
class SettingsApplication:
    storage_changed: bool
    active_team_path: Path | None
    settings: AppSettings


class DesktopSettingsService:
    """Own persisted preferences, private team files, and deferred changes.

    It intentionally has no model adapter, no capture loop and no API call path.
    The caller supplies whether a match is active; team and storage updates then
    remain pending until ``apply_for_next_match``.
    """

    def __init__(self, store: AppSettingsStore, *, master_directory: str | Path) -> None:
        self.store = store
        self.master_directory = Path(master_directory)
        self.load_result: SettingsLoad = store.load()
        self.settings = self.load_result.settings
        try:
            self.catalog: ChampionsTeamCatalog | None = ChampionsTeamCatalog.load(self.master_directory)
        except ConfigurationError:
            self.catalog = None
        self.teams = TeamRepository(Path(self.settings.storage.data_root) / "teams", catalog=self.catalog)

    @property
    def first_launch(self) -> bool:
        return self.load_result.first_launch

    def _save(self) -> None:
        self.store.save(self.settings)

    def save_capture(self, capture: CaptureConfiguration) -> None:
        self.settings = replace(self.settings, capture=capture)
        self._save()

    def import_team(self, source: str | Path, *, match_active: bool, display_name: str | None = None) -> RegisteredTeam:
        if match_active:
            raise ConfigurationError("team import reads files and is available only between matches")
        return self.teams.import_csv(source, display_name=display_name)

    def update_team_csv(self, team_id: str, text: str, *, match_active: bool) -> RegisteredTeam:
        if match_active:
            raise ConfigurationError("team editing is available only between matches")
        return self.teams.update_csv(team_id, text)

    def select_team(self, team_id: str, *, match_active: bool) -> str:
        # Resolve/validate before updating settings.  This does not reread
        # masters: registration stored its fixed-master validation result.
        self.teams.path_for_next_match(team_id)
        teams = self.settings.teams
        if match_active:
            teams = replace(teams, pending_team_id=team_id)
            result = "PENDING_NEXT_MATCH"
        else:
            teams = replace(teams, active_team_id=team_id, pending_team_id=None)
            result = "ACTIVE_FOR_NEXT_MATCH"
        self.settings = replace(self.settings, teams=teams)
        self._save()
        return result

    def stage_storage(self, storage: StorageConfiguration, *, match_active: bool) -> str:
        data_check = validate_storage_directory(storage.data_root)
        capture_check = validate_storage_directory(storage.captures_root)
        if not data_check.writable or not capture_check.writable:
            reason = data_check.reason if not data_check.writable else capture_check.reason
            raise ConfigurationError(f"storage location is unavailable: {reason}")
        if match_active:
            self.settings = replace(self.settings, pending_storage=storage)
            self._save()
            return "PENDING_NEXT_MATCH"
        self._apply_storage(storage)
        return "APPLIED"

    def _apply_storage(self, storage: StorageConfiguration) -> bool:
        previous = self.settings.storage
        changed = previous != storage
        if changed:
            migrate_data_directory(previous.data_root, storage.data_root)
            migrate_data_directory(previous.captures_root, storage.captures_root)
            self.teams = TeamRepository(Path(storage.data_root) / "teams", catalog=self.catalog)
        self.settings = replace(self.settings, storage=storage, pending_storage=None)
        self._save()
        return changed

    def apply_for_next_match(
        self,
        match_controller: "MatchController | None",
        *,
        before_storage_migration: Callable[[], None] | None = None,
    ) -> SettingsApplication:
        """Apply previously deferred state immediately before the four-file load."""
        storage_changed = False
        if self.settings.pending_storage is not None:
            if before_storage_migration is not None:
                before_storage_migration()
            storage_changed = self._apply_storage(self.settings.pending_storage)
        teams = self.settings.teams
        if teams.pending_team_id is not None:
            teams = replace(teams, active_team_id=teams.pending_team_id, pending_team_id=None)
            self.settings = replace(self.settings, teams=teams)
            self._save()
        active_path = None
        if self.settings.teams.active_team_id is not None:
            active_path = self.teams.path_for_next_match(self.settings.teams.active_team_id)
            if match_controller is not None:
                match_controller.set_team_source_for_next_match(active_path)
        elif match_controller is not None:
            match_controller.set_team_source_for_next_match(None)
        return SettingsApplication(storage_changed, active_path, self.settings)
