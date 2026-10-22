from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from champions_assist.app_config import (
    AppSettingsStore,
    CaptureConfiguration,
    StorageConfiguration,
    default_app_root,
    migrate_data_directory,
)
from champions_assist.__main__ import resolve_desktop_settings_paths
from champions_assist.capture import Frame, SyntheticFrameSource
from champions_assist.capture_settings import CapturePreviewService
from champions_assist.desktop import DesktopController, DesktopMainWindow, ensure_application
from champions_assist.fsm import MatchController
from champions_assist.settings_dialog import SettingsDialog, RoiPreviewLabel
from champions_assist.settings_management import DesktopSettingsService
from champions_assist.store import BattleStore
from champions_assist.team_management import TeamDocument
from champions_assist.windows_capture import WindowCandidate, WindowGeometry
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest


OPPONENTS = ("Opp1", "Opp2", "Opp3", "Opp4", "Opp5", "Opp6")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_live_masters(root: Path) -> None:
    (root / "Pokemon_Champions_MC_live_decision_gate.txt").write_text("LIVE GATE\n", encoding="utf-8")
    (root / "Pokemon_Champions_MC_live_selection_matrix.txt").write_text("\n".join(f"{name}|matrix" for name in OPPONENTS) + "\n", encoding="utf-8")
    (root / "Pokemon_Champions_MC_60sec_lookup_master.txt").write_text("\n".join(f"{name}|lookup" for name in OPPONENTS) + "\n", encoding="utf-8")
    # The selected private team replaces this placeholder at the next preload.
    shutil.copyfile(PROJECT_ROOT / "masters" / "Pokemon_Champions_MC_team_2026-10-03.csv", root / "Pokemon_Champions_MC_team_2026-10-03.csv")


class _PreviewSource(SyntheticFrameSource):
    def __init__(self) -> None:
        super().__init__((Frame("preview", 1, 10, np.zeros((20, 30, 3), dtype=np.uint8), source="MOCK"),))
        self.last_geometry = WindowGeometry("AVAILABLE", title="BlueStacks App Player", hwnd=77, client_bbox=(1, 2, 31, 22), dpi=192)
        self.last_capture_method = "MOCK_PRINTWINDOW"


class Phase8SettingsTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = ensure_application()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.master_root = self.root / "masters"
        self.master_root.mkdir()
        write_live_masters(self.master_root)
        self.app_root = self.root / "app"
        self.settings_store = AppSettingsStore(self.app_root / "config" / "settings.json", defaults_root=self.app_root)
        self.service = DesktopSettingsService(self.settings_store, master_directory=PROJECT_ROOT / "masters")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_roi_interactive_creation_move_resize_pixel_edit_and_nudge(self) -> None:
        label = RoiPreviewLabel()
        changes: list[object] = []
        label.roi_changed.connect(changes.append)
        label.resize(600, 360)
        label.set_frame(np.zeros((200, 400, 3), dtype=np.uint8))
        label.show()
        self.application.processEvents()
        try:
            frame_rect = label._image_rect()
            start = QPoint(frame_rect.left() + 40, frame_rect.top() + 30)
            finish = QPoint(frame_rect.left() + 300, frame_rect.top() + 150)
            QTest.mousePress(label, Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(label, finish)
            self.assertIsNotNone(label._preview_roi)  # changes before mouse release
            QTest.mouseRelease(label, Qt.MouseButton.LeftButton, pos=finish)
            self.assertIsNotNone(label.normalized_roi)
            first = label.normalized_roi
            center = QPoint((start.x() + finish.x()) // 2, (start.y() + finish.y()) // 2)
            shifted = center + QPoint(10, 10)
            QTest.mousePress(label, Qt.MouseButton.LeftButton, pos=center)
            QTest.mouseMove(label, shifted)
            QTest.mouseRelease(label, Qt.MouseButton.LeftButton, pos=shifted)
            moved = label.normalized_roi
            self.assertGreater(moved[0], first[0])
            self.assertGreater(moved[1], first[1])
            QTest.keyClick(label, Qt.Key.Key_Right)
            self.assertGreater(label.normalized_roi[0], moved[0])
            self.assertGreaterEqual(len(changes), 3)
        finally:
            label.close()

    def test_roi_settings_coordinate_fields_and_previous_normalized_format(self) -> None:
        dialog = SettingsDialog(self.service, match_active=lambda: False)
        try:
            dialog.preview_label.set_frame(np.zeros((200, 400, 3), dtype=np.uint8))
            dialog._set_roi_from_controls((0.1, 0.2, 0.9, 0.8))
            self.assertEqual(
                tuple(dialog.roi_fields[n].value() for n in ("left", "top", "right", "bottom")),
                (40, 40, 360, 160),
            )
            dialog.roi_fields["left"].setValue(45)
            self.assertAlmostEqual(dialog.preview_label.normalized_roi[0], 45/400)
            self.assertEqual(dialog._capture_config().game_viewport_normalized, dialog.preview_label.normalized_roi)
        finally:
            dialog.close()

    def test_windows_default_path_round_trip_and_corruption_recovers_last_good_nonsecret_config(self) -> None:
        self.assertEqual(
            default_app_root(platform="win32", environ={"LOCALAPPDATA": r"C:\\Users\\tester\\AppData\\Local"}),
            Path(r"C:\\Users\\tester\\AppData\\Local") / "PokemonChampionsAssistant",
        )
        self.settings_store.save(self.service.settings)
        updated = StorageConfiguration(str(self.root / "data-two"), str(self.root / "captures-two"), True, 45)
        self.service.stage_storage(updated, match_active=False)
        self.settings_store.path.write_text("{ damaged", encoding="utf-8")
        recovered = self.settings_store.load()
        self.assertEqual(recovered.source, "BACKUP_RECOVERY")
        self.assertNotIn("api_key", self.settings_store.backup_path.read_text(encoding="utf-8").casefold())
        self.settings_store.save(recovered.settings)
        self.assertTrue(self.settings_store.path.with_suffix(".json.corrupt").exists())
        self.assertEqual(self.settings_store.load().source, "PRIMARY")
        validation_config = self.root / "portable-validation" / "config" / "settings.json"
        validation_root, resolved_config = resolve_desktop_settings_paths(validation_config)
        self.assertEqual(validation_root, validation_config.parent.parent)
        self.assertEqual(resolved_config, validation_config)

    def test_storage_migration_copies_verifies_and_keeps_source_on_failure(self) -> None:
        source = self.root / "source-data"
        source.mkdir()
        (source / "battle.sqlite3").write_bytes(b"sqlite-evidence")
        destination = self.root / "destination-data"
        result = migrate_data_directory(source, destination)
        self.assertEqual(result, destination.resolve())
        self.assertEqual((destination / "battle.sqlite3").read_bytes(), b"sqlite-evidence")
        self.assertTrue((source / "battle.sqlite3").exists())
        occupied = self.root / "occupied"
        occupied.mkdir()
        (occupied / "do-not-replace").write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "empty"):
            migrate_data_directory(source, occupied)
        self.assertEqual((occupied / "do-not-replace").read_text(encoding="utf-8"), "keep")

    def test_mock_window_selection_preview_and_stale_identity_are_read_only(self) -> None:
        candidate = WindowCandidate(77, 88, "HD-Player.exe", "BlueStacks App Player", (1, 2, 31, 22), 192, False)
        service = CapturePreviewService(
            window_enumerator=lambda: (candidate,),
            native_source_factory=lambda _candidate: _PreviewSource(),
            clock_ms=iter((100, 147, 200)).__next__,
        )
        config = CaptureConfiguration(selected_window_identity=candidate.identity())
        result = service.preview_once(config)
        self.assertEqual(result.status, "CAPTURED_READ_ONLY_UNVERIFIED_CONTENT")
        self.assertEqual(result.image_size, (30, 20))
        self.assertEqual(result.dpi, 192)
        self.assertEqual(result.acquisition_ms, 47)
        stale = service.preview_once(CaptureConfiguration(selected_window_identity={**candidate.identity(), "hwnd": 999}))
        self.assertEqual(stale.status, "WINDOW_IDENTITY_UNAVAILABLE")
        minimized = WindowCandidate(78, 89, "HD-Player.exe", "BlueStacks App Player", None, 192, True)
        blocked = CapturePreviewService(window_enumerator=lambda: (minimized,)).preview_once(
            CaptureConfiguration(selected_window_identity=minimized.identity())
        )
        self.assertEqual(blocked.status, "WINDOW_MINIMIZED")

    def test_save_preview_uses_selected_roi_and_never_overwrites(self) -> None:
        import cv2
        from champions_assist.capture_settings import CapturePreviewResult

        image = np.zeros((100, 200, 3), dtype=np.uint8)
        image[20:80, 40:160] = (255, 60, 20)
        preview = CapturePreviewResult("CAPTURED_READ_ONLY_UNVERIFIED_CONTENT", "test", image=image)
        target = self.root / "capture" / "cropped.png"
        service = CapturePreviewService()
        service.save_explicit_frame(preview, target, roi=(0.2, 0.2, 0.8, 0.8))
        saved = cv2.imread(str(target))
        self.assertIsNotNone(saved)
        self.assertEqual(saved.shape[:2], (60, 120))
        self.assertEqual(image.shape[:2], (100, 200))  # original unchanged
        with self.assertRaisesRegex(ValueError, "overwrite"):
            service.save_explicit_frame(preview, target, roi=(0.2, 0.2, 0.8, 0.8))

    def test_save_preview_rejects_invalid_roi(self) -> None:
        from champions_assist.capture_settings import CapturePreviewResult
        preview = CapturePreviewResult("CAPTURED_READ_ONLY_UNVERIFIED_CONTENT", "test", image=np.zeros((100, 200, 3), dtype=np.uint8))
        with self.assertRaisesRegex(ValueError, "invalid game viewport ROI"):
            CapturePreviewService.save_explicit_frame(preview, self.root / "invalid.png", roi=(0.5, 0.5, 0.5, 0.8))
        self.assertFalse((self.root / "invalid.png").exists())

    def test_team_import_round_trip_and_deferred_switch_feed_the_next_preload_only(self) -> None:
        initial = self.service.import_team(PROJECT_ROOT / "masters" / "Pokemon_Champions_MC_team_2026-10-03.csv", match_active=False)
        self.assertEqual(len((Path(initial.csv_path)).read_text(encoding="utf-8").splitlines()), 8)
        self.assertTrue(initial.validation.selectable_for_next_match)
        initial_document = TeamDocument.load(initial.csv_path)
        edited_rows = [dict(row) for row in initial_document.rows]
        next(row for row in edited_rows if row.get("record") == "MON")["runtime_note"] = "LOCAL_EDIT_PRESERVED"
        edited = self.service.update_team_csv(initial.team_id, TeamDocument(initial_document.fieldnames, tuple(edited_rows)).to_text(), match_active=False)
        exported = self.root / "exported-team.csv"
        self.service.teams.export_csv(edited.team_id, exported)
        self.assertIn("LOCAL_EDIT_PRESERVED", exported.read_text(encoding="utf-8"))
        self.service.select_team(initial.team_id, match_active=False)
        source_text = Path(initial.csv_path).read_text(encoding="utf-8")
        alternate_source = self.root / "alternate.csv"
        alternate_source.write_text(source_text.replace("BASE_SELECT=", "BASE_SELECT_NOTE="), encoding="utf-8")
        alternate = self.service.import_team(alternate_source, match_active=False)

        store = BattleStore(Path(self.service.settings.storage.data_root) / "battle.sqlite3")
        controller = DesktopController(
            store=store,
            match_controller=MatchController(self.master_root, store=store),
            settings_service=self.service,
            clock_ms=lambda: 100,
        )
        try:
            first = controller.start_new_match("settings-first")
            first_team_hash = first.assets.sha256["Pokemon_Champions_MC_team_2026-10-03.csv"]
            self.assertEqual(first.assets.own_team_names[0], "マスカーニャ")
            self.assertEqual(self.service.select_team(alternate.team_id, match_active=True), "PENDING_NEXT_MATCH")
            self.assertEqual(first.assets.sha256["Pokemon_Champions_MC_team_2026-10-03.csv"], first_team_hash)
            second = controller.start_new_match("settings-second")
            self.assertEqual(self.service.settings.teams.active_team_id, alternate.team_id)
            self.assertIsNone(self.service.settings.teams.pending_team_id)
            self.assertNotEqual(second.assets.sha256["Pokemon_Champions_MC_team_2026-10-03.csv"], first_team_hash)
        finally:
            controller.store.close()

    def test_offscreen_settings_dialog_persists_selection_and_never_requests_a_model_job(self) -> None:
        team = self.service.import_team(PROJECT_ROOT / "masters" / "Pokemon_Champions_MC_team_2026-10-03.csv", match_active=False)
        store = BattleStore(Path(self.temp.name) / "dialog.sqlite3")
        controller = DesktopController(store=store, match_controller=MatchController(self.master_root, store=store))
        jobs: list[object] = []
        controller.model_job_requested.connect(jobs.append)
        dialog = SettingsDialog(self.service, match_active=lambda: False)
        try:
            dialog.data_root.setText(str(self.root / "dialog-data"))
            dialog.captures_root.setText(str(self.root / "dialog-captures"))
            dialog.save_screenshots.setChecked(True)
            dialog.preview_label.set_normalized_roi((0.1, 0.2, 0.9, 0.8))
            dialog._selected_team_id = team.team_id
            dialog._save()
            restored = AppSettingsStore(self.settings_store.path, defaults_root=self.app_root).load().settings
            self.assertEqual(restored.teams.active_team_id, team.team_id)
            self.assertTrue(restored.storage.save_screenshots)
            self.assertEqual(restored.capture.game_viewport_normalized, (0.1, 0.2, 0.9, 0.8))
            self.assertEqual(jobs, [])
        finally:
            dialog.close()
            store.close()

    def test_active_capture_change_requires_explicit_stale_result_invalidation_handler(self) -> None:
        invalidations: list[str] = []
        dialog = SettingsDialog(
            self.service,
            match_active=lambda: True,
            on_active_capture_change=lambda: invalidations.append("STOP_AND_RESYNC_REQUIRED"),
        )
        try:
            dialog.backend_box.setCurrentIndex(dialog.backend_box.findData("ADB_SCREENSHOT"))
            dialog.data_root.setText(self.service.settings.storage.data_root)
            dialog.captures_root.setText(self.service.settings.storage.captures_root)
            dialog._save()
            self.assertEqual(invalidations, ["STOP_AND_RESYNC_REQUIRED"])
            self.assertEqual(self.service.settings.capture.backend, "ADB_SCREENSHOT")
            self.assertIsNotNone(self.service.settings.pending_storage)
        finally:
            dialog.close()

    def test_pending_storage_switches_at_next_match_and_blocks_old_database_worker(self) -> None:
        store = BattleStore(Path(self.service.settings.storage.data_root) / "battle.sqlite3")
        controller = DesktopController(
            store=store,
            match_controller=MatchController(self.master_root, store=store),
            settings_service=self.service,
            clock_ms=lambda: 100,
        )
        try:
            controller.start_new_match("storage-first")
            next_storage = StorageConfiguration(str(self.root / "migrated-data"), str(self.root / "migrated-captures"))
            self.assertEqual(self.service.stage_storage(next_storage, match_active=True), "PENDING_NEXT_MATCH")
            controller.start_new_match("storage-second")
            self.assertEqual(controller.store.database_path.parent, Path(next_storage.data_root))
            controller.set_api_enabled(True)
            self.assertIsNone(controller.request_model_job("SELECT"))
        finally:
            controller.store.close()

    def test_desktop_settings_button_does_not_mutate_match_or_emit_model_job(self) -> None:
        store = BattleStore(Path(self.temp.name) / "window.sqlite3")
        controller = DesktopController(store=store, match_controller=MatchController(self.master_root, store=store), settings_service=self.service)
        jobs: list[object] = []
        controller.model_job_requested.connect(jobs.append)
        window = DesktopMainWindow(controller, settings_service=self.service)
        try:
            window.show()
            self.application.processEvents()
            before = controller.session
            window._open_settings()
            self.application.processEvents()
            self.assertIs(controller.session, before)
            self.assertEqual(jobs, [])
        finally:
            window.close()
            store.close()

    def test_settings_validation_mode_disables_match_and_api_controls(self) -> None:
        store = BattleStore(Path(self.temp.name) / "validation-window.sqlite3")
        controller = DesktopController(store=store, match_controller=MatchController(self.master_root, store=store), settings_service=self.service)
        window = DesktopMainWindow(controller, settings_service=self.service, settings_validation_only=True)
        try:
            self.assertFalse(window.start_match_button.isEnabled())
            self.assertFalse(window.api_enabled_box.isEnabled())
            self.assertFalse(window.send_model_button.isEnabled())
        finally:
            window.close()
            store.close()


if __name__ == "__main__":
    unittest.main()
