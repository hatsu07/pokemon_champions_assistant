"""GUI-facing settings coordinator with next-match application boundaries."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import json
from threading import get_ident, Lock
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
from .team_live_assets import PreparedTeamAssets, prepare_team_assets
from .environment_refresh import SinglesEnvironmentStore
from .matchup_evidence import import_reviews
from .matchup_evidence import stable_hash
from .real_battle_validation import import_real_battle_packet

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
        self.environment = SinglesEnvironmentStore(self.settings.storage.data_root)
        self._refresh_lock = Lock()
        self._refresh_owner = None

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

    def create_team(self, display_name: str, *, match_active: bool) -> RegisteredTeam:
        if match_active:
            raise ConfigurationError("試合中は新しい構築を作成できません")
        return self.teams.create_blank(display_name)

    def duplicate_team(self, team_id: str, *, match_active: bool) -> RegisteredTeam:
        if match_active:
            raise ConfigurationError("試合中は構築を複製できません")
        return self.teams.duplicate(team_id)

    def delete_team(self, team_id: str, *, match_active: bool) -> None:
        if match_active:
            raise ConfigurationError("試合中は構築を削除できません")
        self.teams.get(team_id)
        teams = self.settings.teams
        # Forget any active or deferred selection before deleting its backing
        # CSV. An invalid active ID would break the next four-file preload.
        if team_id in (teams.active_team_id, teams.pending_team_id):
            teams = replace(
                teams,
                active_team_id=None if teams.active_team_id == team_id else teams.active_team_id,
                pending_team_id=None if teams.pending_team_id == team_id else teams.pending_team_id,
            )
            self.settings = replace(self.settings, teams=teams)
            self._save()
        self.teams.delete(team_id)

    def update_team_csv(self, team_id: str, text: str, *, match_active: bool) -> RegisteredTeam:
        if match_active:
            raise ConfigurationError("team editing is available only between matches")
        return self.teams.update_csv(team_id, text)

    def revalidate_team(self, team_id: str, *, match_active: bool) -> RegisteredTeam:
        if match_active:
            raise ConfigurationError("試合中は構築を再検証できません")
        return self.teams.revalidate(team_id)

    def prepare_team_live_assets(self, team_id: str, *, match_active: bool = False) -> PreparedTeamAssets:
        if self._refresh_lock.locked() and self._refresh_owner != get_ident():
            raise ConfigurationError("Singlesデータ更新中です。新しい対戦開始はできません")
        if match_active:
            raise ConfigurationError("対戦中はLIVEデータの作成・更新ができません")
        source=self.teams.path_for_next_match(team_id)
        return prepare_team_assets(
            master_dir=self.master_directory,
            data_root=self.settings.storage.data_root,
            team_id=team_id,team_path=source,
            environment_snapshot=self.environment.snapshot,
        )

    def import_matchup_reviews(self, team_id: str, path: str | Path,
                              *, match_active: bool) -> dict:
        """Explicit human review import; never accepts it as LIVE certification."""
        if match_active:
            raise ConfigurationError("対戦中は対面評価レビューを取り込めません")
        ready=self.prepare_team_live_assets(team_id,match_active=False)
        evidence_path=ready.manifest_path.with_name("matchup-evidence.json")
        candidate=json.loads(evidence_path.read_text(encoding="utf-8"))
        destination=import_reviews(
            data_root=self.settings.storage.data_root,team_id=team_id,
            packet_path=Path(path),candidate_catalog=candidate)
        # The stored review hash becomes part of the next immutable team
        # fingerprint. No currently running match is mutated.
        updated=self.prepare_team_live_assets(team_id,match_active=False)
        manifest=json.loads(updated.manifest_path.read_text(encoding="utf-8"))
        return {
            "review_path":str(destination),
            "status":updated.status,
            "review_counts":manifest["review_status_counts"],
            "quality_gate":manifest["quality_gate"],
            "matchup_evidence_path":str(updated.manifest_path.with_name("matchup-evidence.json")),
        }

    def import_real_battle_quality(self, team_id: str, path: str | Path,
                                   *, match_active: bool) -> dict:
        """Audit user-provided real-device evidence offline, never auto READY."""
        if match_active:
            raise ConfigurationError("対戦中は実機検証ログを取り込めません")
        prepared=self.prepare_team_live_assets(team_id,match_active=False)
        catalog=json.loads(prepared.manifest_path.with_name(
            "matchup-evidence.json").read_text(encoding="utf-8"))
        fingerprint=stable_hash({
            "members":catalog["member_signatures"],
            "regulation":catalog["regulation"],
        })
        output,audit=import_real_battle_packet(
            data_root=Path(self.settings.storage.data_root),
            packet_path=Path(path),team_id=team_id,
            team_fingerprint=fingerprint,
            regulation=catalog["regulation"])
        return {"file":str(output),"audit":audit,
                "evidence_template":{
                    "schema":"champions-live-e2e-device-evidence-v1",
                    "team_id":team_id,
                    "team_fingerprint":fingerprint,
                    "regulation":catalog["regulation"],
                }}

    def refresh_public_battle_data(self, *, match_active: bool) -> dict:
        if match_active:
            raise ConfigurationError("対戦中は環境データを更新できません")
        if not self._refresh_lock.acquire(blocking=False):
            raise ConfigurationError("Singles環境データはすでに更新中です")
        self._refresh_owner=get_ident()
        try:
            receipt=self.environment.refresh_public_singles()
            # Prepare the new last-good team snapshot while still excluding
            # a concurrent match start. No mid-battle source mutation.
            active=self.settings.teams.active_team_id
            if active:
                self.prepare_team_live_assets(active, match_active=False)
            return receipt
        finally:
            self._refresh_owner=None
            self._refresh_lock.release()

    def import_user_battle_data(self, path: str | Path, *, match_active: bool) -> dict:
        if match_active:
            raise ConfigurationError("対戦中は環境データを取り込めません")
        receipt=self.environment.import_user_json(path)
        active=self.settings.teams.active_team_id
        if active:
            self.prepare_team_live_assets(active,match_active=False)
        return receipt

    def select_team(self, team_id: str, *, match_active: bool) -> str:
        # The user activates a team, then immediately prepares its two cached
        # match-specific sources. Never regenerate from a live capture loop.
        self.teams.path_for_next_match(team_id)
        if not match_active:
            self.prepare_team_live_assets(team_id,match_active=False)
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
            self.environment = SinglesEnvironmentStore(storage.data_root)
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
