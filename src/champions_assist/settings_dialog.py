"""PySide6 settings dialog integrated into the existing observer desktop."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable

import numpy as np
from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .app_config import CaptureConfiguration, ConfigurationError, StorageConfiguration
from .capture_settings import CapturePreviewResult, CapturePreviewService
from .settings_management import DesktopSettingsService
from .team_management import RegisteredTeam, TeamDocument
from .windows_capture import WindowCandidate


class RoiPreviewLabel(QLabel):
    """Preview image widget that converts a drag rectangle into normalized ROI."""

    roi_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__("プレビュー未取得")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(420, 240)
        self.setStyleSheet("border: 1px solid #666;")
        self._image: np.ndarray | None = None
        self._roi: tuple[float, float, float, float] | None = None
        self._drag_start: QPoint | None = None
        self._drag_end: QPoint | None = None

    @property
    def normalized_roi(self) -> tuple[float, float, float, float] | None:
        return self._roi

    def set_normalized_roi(self, roi: tuple[float, float, float, float] | None) -> None:
        self._roi = roi
        self._redraw()

    def set_frame(self, image: np.ndarray) -> None:
        self._image = image.copy()
        self._redraw()

    def _image_rect(self) -> QRect:
        if self._image is None:
            return QRect()
        height, width = self._image.shape[:2]
        if not width or not height:
            return QRect()
        ratio = min(self.width() / width, self.height() / height)
        shown = QSize(max(1, int(width * ratio)), max(1, int(height * ratio)))
        return QRect((self.width() - shown.width()) // 2, (self.height() - shown.height()) // 2, shown.width(), shown.height())

    def _redraw(self) -> None:
        if self._image is None:
            return
        height, width = self._image.shape[:2]
        qimage = QImage(self._image.data, width, height, width * 3, QImage.Format.Format_RGB888).copy()
        rect = self._image_rect()
        if rect.isEmpty():
            return
        self.setPixmap(QPixmap.fromImage(qimage).scaled(rect.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt callback
        super().resizeEvent(event)
        self._redraw()

    def _normalized_point(self, point: QPoint) -> tuple[float, float] | None:
        rect = self._image_rect()
        if rect.isEmpty() or not rect.contains(point):
            return None
        return ((point.x() - rect.left()) / rect.width(), (point.y() - rect.top()) / rect.height())

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt callback
        if event.button() is Qt.MouseButton.LeftButton and self._normalized_point(event.position().toPoint()) is not None:
            self._drag_start = event.position().toPoint()
            self._drag_end = self._drag_start
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt callback
        if self._drag_start is not None:
            self._drag_end = event.position().toPoint()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt callback
        if self._drag_start is not None and self._drag_end is not None:
            start = self._normalized_point(self._drag_start)
            end = self._normalized_point(event.position().toPoint())
            self._drag_start = self._drag_end = None
            if start is not None and end is not None:
                left, right = sorted((start[0], end[0]))
                top, bottom = sorted((start[1], end[1]))
                if right - left > 0.001 and bottom - top > 0.001:
                    self._roi = (left, top, right, bottom)
                    self.roi_changed.emit(self._roi)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt callback
        super().paintEvent(event)
        if self._roi is None:
            return
        image_rect = self._image_rect()
        if image_rect.isEmpty():
            return
        left, top, right, bottom = self._roi
        overlay = QRect(
            image_rect.left() + int(left * image_rect.width()),
            image_rect.top() + int(top * image_rect.height()),
            max(1, int((right - left) * image_rect.width())),
            max(1, int((bottom - top) * image_rect.height())),
        )
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#00d084"), 2))
        painter.drawRect(overlay)


class _PreviewWorker(QObject):
    completed = Signal(object)

    def __init__(self, service: CapturePreviewService, config: CaptureConfiguration) -> None:
        super().__init__()
        self._service = service
        self._config = config

    @Slot()
    def run(self) -> None:
        self.completed.emit(self._service.preview_once(self._config))


class SettingsDialog(QDialog):
    """Four-tab non-secret settings UI.  It never asks for a model decision."""

    member_columns = (
        "name", "item", "ability", "nature", "ap_h_a_b_c_d_s", "move1", "move2", "move3", "move4", "role", "form_change", "runtime_note",
    )

    def __init__(
        self,
        service: DesktopSettingsService,
        *,
        match_active: Callable[[], bool],
        preview_service: CapturePreviewService | None = None,
        on_active_capture_change: Callable[[], None] | None = None,
        first_launch: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self._match_active = match_active
        self.preview_service = preview_service or CapturePreviewService()
        self._on_active_capture_change = on_active_capture_change
        self._candidates: tuple[WindowCandidate, ...] = ()
        self._preview_thread: QThread | None = None
        self._preview_worker: _PreviewWorker | None = None
        self._selected_team_id = service.settings.teams.active_team_id
        self._editing_document: TeamDocument | None = None
        self._editing_team_id: str | None = None
        self._last_preview: CapturePreviewResult | None = None
        self.setWindowTitle("初回設定" if first_launch else "設定")
        self.setMinimumSize(780, 600)
        self._build_ui()
        self._load_from_settings()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._capture_tab(), "画面キャプチャ")
        self.tabs.addTab(self._storage_tab(), "保存先")
        self.tabs.addTab(self._team_tab(), "自軍構築")
        self.tabs.addTab(self._diagnostics_tab(), "診断")
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox()
        self.test_button = buttons.addButton("動作確認", QDialogButtonBox.ButtonRole.ActionRole)
        self.defaults_button = buttons.addButton("既定値に戻す", QDialogButtonBox.ButtonRole.ResetRole)
        self.save_button = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = buttons.addButton("キャンセル", QDialogButtonBox.ButtonRole.RejectRole)
        self.test_button.clicked.connect(self._start_preview)
        self.defaults_button.clicked.connect(self._restore_defaults)
        self.save_button.clicked.connect(self._save)
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _capture_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QFormLayout()
        self.backend_box = QComboBox()
        self.backend_box.addItem("Windowsネイティブ（推奨）", "WINDOWS_NATIVE")
        self.backend_box.addItem("ADB（代替）", "ADB_SCREENSHOT")
        self.backend_box.addItem("画像/録画リプレイ（検証）", "REPLAY")
        self.window_box = QComboBox()
        refresh = QPushButton("ウィンドウを検索")
        refresh.clicked.connect(self._refresh_windows)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self.window_box, 1)
        row_layout.addWidget(refresh)
        self.adb_path = QLineEdit()
        self.adb_serial = QLineEdit()
        form.addRow("キャプチャ方式", self.backend_box)
        form.addRow("対象ウィンドウ", row)
        form.addRow("ADB 実行ファイル", self.adb_path)
        form.addRow("ADB serial", self.adb_serial)
        layout.addLayout(form)
        self.preview_label = RoiPreviewLabel()
        self.preview_label.roi_changed.connect(self._roi_changed)
        layout.addWidget(self.preview_label, 1)
        self.capture_metrics = QLabel("未取得")
        self.capture_metrics.setWordWrap(True)
        layout.addWidget(self.capture_metrics)
        self.save_preview_button = QPushButton("テスト撮影を保存（明示操作のみ）")
        self.save_preview_button.setEnabled(False)
        self.save_preview_button.clicked.connect(self._save_preview)
        layout.addWidget(self.save_preview_button)
        layout.addWidget(QLabel("ゲーム領域はプレビュー上でドラッグ指定します。選択は正規化ROIとして保存され、画面サイズ変更時は再較正が必要です。"))
        return tab

    def _storage_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        self.data_root = QLineEdit()
        self.captures_root = QLineEdit()
        data_button = QPushButton("フォルダーを選択")
        data_button.clicked.connect(lambda: self._choose_directory(self.data_root))
        captures_button = QPushButton("フォルダーを選択")
        captures_button.clicked.connect(lambda: self._choose_directory(self.captures_root))
        self.save_screenshots = QCheckBox("明示操作時のみキャプチャ画像を保存する")
        self.retention_days = QSpinBox()
        self.retention_days.setRange(0, 3650)
        layout.addRow("対戦データ/SQLite/診断ログ", self._line_with_button(self.data_root, data_button))
        layout.addRow("キャプチャ/較正画像", self._line_with_button(self.captures_root, captures_button))
        layout.addRow("画像保存", self.save_screenshots)
        layout.addRow("保持日数（0=手動管理）", self.retention_days)
        self.storage_status = QLabel("未検証")
        self.storage_status.setWordWrap(True)
        layout.addRow("検証", self.storage_status)
        return tab

    @staticmethod
    def _line_with_button(line: QLineEdit, button: QPushButton) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line, 1)
        layout.addWidget(button)
        return widget

    def _team_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        buttons = QHBoxLayout()
        import_button = QPushButton("CSVを登録")
        import_button.clicked.connect(self._import_team)
        duplicate_button = QPushButton("複製")
        duplicate_button.clicked.connect(self._duplicate_team)
        select_button = QPushButton("次戦に選択")
        select_button.clicked.connect(self._select_team)
        export_button = QPushButton("CSVをエクスポート")
        export_button.clicked.connect(self._export_team)
        for button in (import_button, duplicate_button, select_button, export_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.team_list = QTableWidget(0, 5)
        self.team_list.setHorizontalHeaderLabels(["登録名", "6匹", "検証", "SHA-256", "次戦適用"])
        self.team_list.itemSelectionChanged.connect(self._load_selected_team_editor)
        layout.addWidget(self.team_list, 1)
        self.team_editor = QTableWidget(0, len(self.member_columns))
        self.team_editor.setHorizontalHeaderLabels(self.member_columns)
        layout.addWidget(self.team_editor, 1)
        save_edit = QPushButton("編集した構築CSVを保存")
        save_edit.clicked.connect(self._save_team_edit)
        layout.addWidget(save_edit)
        self.team_status = QLabel("構築を選択してください。手入力値は固定マスターに無い場合、未確認または無効として扱います。")
        self.team_status.setWordWrap(True)
        layout.addWidget(self.team_status)
        return tab

    def _diagnostics_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.diagnostics = QLabel()
        self.diagnostics.setWordWrap(True)
        layout.addWidget(self.diagnostics)
        layout.addWidget(QLabel("APIキー、認証情報、画面内容、ユーザー名は設定JSONや診断表示へ保存しません。"))
        layout.addStretch(1)
        return tab

    def _load_from_settings(self) -> None:
        config = self.service.settings.capture
        index = self.backend_box.findData(config.backend)
        self.backend_box.setCurrentIndex(max(0, index))
        self.adb_path.setText(config.adb_path or "")
        self.adb_serial.setText(config.adb_serial or "")
        self.preview_label.set_normalized_roi(config.game_viewport_normalized)
        storage = self.service.settings.storage
        self.data_root.setText(storage.data_root)
        self.captures_root.setText(storage.captures_root)
        self.save_screenshots.setChecked(storage.save_screenshots)
        self.retention_days.setValue(storage.retention_days)
        self._refresh_windows()
        self._refresh_teams()
        self._refresh_diagnostics()

    def _refresh_windows(self) -> None:
        current = self.service.settings.capture.selected_window_identity
        self._candidates = self.preview_service.list_windows()
        self.window_box.clear()
        for candidate in self._candidates:
            self.window_box.addItem(candidate.display_name(), candidate.identity())
        if not self._candidates:
            self.window_box.addItem("検出対象なし（Windows実機でのみ列挙可能）", None)
        for index, candidate in enumerate(self._candidates):
            if candidate.identity() == current:
                self.window_box.setCurrentIndex(index)
                break

    def _capture_config(self) -> CaptureConfiguration:
        identity = self.window_box.currentData()
        return CaptureConfiguration(
            backend=str(self.backend_box.currentData()),
            preferred_process=self.service.settings.capture.preferred_process,
            preferred_title=self.service.settings.capture.preferred_title,
            selected_window_identity=None if identity is None else dict(identity),
            game_viewport_normalized=self.preview_label.normalized_roi,
            adb_path=self.adb_path.text().strip() or None,
            adb_serial=self.adb_serial.text().strip() or None,
        )

    def _start_preview(self) -> None:
        if self._preview_thread is not None:
            return
        worker = _PreviewWorker(self.preview_service, self._capture_config())
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._preview_complete)
        worker.completed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._preview_thread, self._preview_worker = thread, worker
        thread.start()

    @Slot(object)
    def _preview_complete(self, value: object) -> None:
        result = value if isinstance(value, CapturePreviewResult) else CapturePreviewResult("CAPTURE_FAILED", "invalid preview result")
        if result.image is not None:
            self.preview_label.set_frame(result.image)
            self._last_preview = result
            self.save_preview_button.setEnabled(True)
        self.capture_metrics.setText(
            f"{result.status} / {result.capture_method or '—'} / "
            f"{result.image_size or '—'} px / DPI {result.dpi or '—'} / {result.acquisition_ms or '—'} ms / FPS: 1枚のみ未測定 / {result.message}"
        )
        self._preview_thread = None
        self._preview_worker = None
        self._refresh_diagnostics()

    def _save_preview(self) -> None:
        if self._last_preview is None:
            return
        from time import time

        destination = Path(self.captures_root.text().strip()) / f"manual-preview-{int(time() * 1000)}.png"
        try:
            saved = self.preview_service.save_explicit_frame(self._last_preview, destination)
            self.capture_metrics.setText(f"明示操作で保存しました: {saved.name}（内容は未検証）")
        except ConfigurationError as exc:
            QMessageBox.warning(self, "撮影を保存できません", str(exc))

    def _roi_changed(self, _roi: object) -> None:
        self.capture_metrics.setText("ROIを変更しました。保存後、画面サイズ/DPIの変更時は再較正が必要です。")

    def _choose_directory(self, target: QLineEdit) -> None:
        selected = QFileDialog.getExistingDirectory(self, "保存先を選択", target.text())
        if selected:
            target.setText(selected)

    def _refresh_teams(self) -> None:
        records = self.service.teams.list()
        active = self.service.settings.teams.active_team_id
        pending = self.service.settings.teams.pending_team_id
        self.team_list.setRowCount(len(records))
        for row, record in enumerate(records):
            members = ", ".join(member.get("name", "") for member in TeamDocument.load(record.csv_path).members)
            values = (record.display_name, members, record.validation.status, record.sha256, "予約" if record.team_id == pending else "選択中" if record.team_id == active else "")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, record.team_id if column == 0 else None)
                self.team_list.setItem(row, column, item)

    def _selected_record(self) -> RegisteredTeam | None:
        selected = self.team_list.selectedItems()
        if not selected:
            return None
        return self.service.teams.get(str(self.team_list.item(selected[0].row(), 0).data(Qt.ItemDataRole.UserRole)))

    def _load_selected_team_editor(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        document = TeamDocument.load(record.csv_path)
        self._editing_document, self._editing_team_id = document, record.team_id
        members = document.members
        self.team_editor.setRowCount(len(members))
        for row, member in enumerate(members):
            for column, field in enumerate(self.member_columns):
                value = member.get(field, "")
                candidates = self._field_candidates(field)
                if candidates:
                    selector = QComboBox()
                    selector.setEditable(True)
                    selector.addItems((value, *sorted(candidate for candidate in candidates if candidate != value)))
                    selector.setCurrentText(value)
                    self.team_editor.setCellWidget(row, column, selector)
                else:
                    self.team_editor.setItem(row, column, QTableWidgetItem(value))
        reason = " / ".join(record.validation.reasons[:3]) or "固定マスター照合済み"
        self.team_status.setText(f"{record.validation.status}: {reason}")

    def _field_candidates(self, field: str) -> frozenset[str]:
        catalog = self.service.catalog
        if catalog is None:
            return frozenset()
        if field == "name":
            return catalog.pokemon
        if field == "item":
            return catalog.items
        if field == "ability":
            return catalog.abilities
        if field.startswith("move"):
            return catalog.moves
        return frozenset()

    def _import_team(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, "構築CSVを登録", "", "CSV (*.csv)")
        if not source:
            return
        try:
            record = self.service.import_team(source, match_active=self._match_active())
            self._selected_team_id = record.team_id
            self._refresh_teams()
        except ConfigurationError as exc:
            QMessageBox.warning(self, "構築を登録できません", str(exc))

    def _duplicate_team(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        if self._match_active():
            QMessageBox.warning(self, "構築を複製できません", "試合中の構築編集は次戦前に行ってください")
            return
        clone = self.service.teams.duplicate(record.team_id)
        self._selected_team_id = clone.team_id
        self._refresh_teams()

    def _select_team(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        self._selected_team_id = record.team_id
        self.team_status.setText("保存時に次戦用構築として適用します。試合中は予約状態になります。")

    def _export_team(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        target, _ = QFileDialog.getSaveFileName(self, "構築CSVをエクスポート", f"{record.display_name}.csv", "CSV (*.csv)")
        if target:
            self.service.teams.export_csv(record.team_id, target)

    def _save_team_edit(self) -> None:
        if self._editing_document is None or self._editing_team_id is None:
            return
        rows = [dict(row) for row in self._editing_document.rows]
        mon_rows = [index for index, row in enumerate(rows) if row.get("record") == "MON"]
        for table_row, row_index in enumerate(mon_rows):
            for column, field in enumerate(self.member_columns):
                selector = self.team_editor.cellWidget(table_row, column)
                if isinstance(selector, QComboBox):
                    rows[row_index][field] = selector.currentText().strip()
                else:
                    item = self.team_editor.item(table_row, column)
                    rows[row_index][field] = "" if item is None else item.text().strip()
        updated = self.service.update_team_csv(
            self._editing_team_id,
            TeamDocument(self._editing_document.fieldnames, tuple(rows)).to_text(),
            match_active=self._match_active(),
        )
        self.team_status.setText(f"保存済み: {updated.validation.status}")
        self._refresh_teams()

    def _refresh_diagnostics(self) -> None:
        storage = self.service.settings.storage
        config = self._capture_config()
        self.diagnostics.setText(
            "設定: 保存済みではありません（保存を押すまで反映しません）\n"
            f"キャプチャ方式: {config.backend}\n"
            f"ROI: {'設定済み' if config.game_viewport_normalized else '未設定'}\n"
            f"対戦データ保存先: {storage.data_root}\n"
            f"キャプチャ保存先: {storage.captures_root}\n"
            f"構築: active={self.service.settings.teams.active_team_id or '未選択'}, pending={self.service.settings.teams.pending_team_id or 'なし'}\n"
            "OpenAI API: この画面の操作では送信しません。"
        )

    def _restore_defaults(self) -> None:
        default = self.service.store.load().settings
        self.data_root.setText(default.storage.data_root)
        self.captures_root.setText(default.storage.captures_root)
        self.save_screenshots.setChecked(default.storage.save_screenshots)
        self.retention_days.setValue(default.storage.retention_days)
        self.backend_box.setCurrentIndex(self.backend_box.findData(default.capture.backend))
        self.adb_path.clear()
        self.adb_serial.clear()
        self.preview_label.set_normalized_roi(None)

    def _save(self) -> None:
        try:
            capture = self._capture_config()
            if self._match_active() and capture != self.service.settings.capture and self._on_active_capture_change is not None:
                # There is no live observer worker implementation here to
                # silently retarget.  Explicit save invalidates in-flight AI
                # entitlement before the surrounding runtime can resynchronise.
                self._on_active_capture_change()
            self.service.save_capture(capture)
            storage = StorageConfiguration(
                self.data_root.text().strip(), self.captures_root.text().strip(),
                self.save_screenshots.isChecked(), self.retention_days.value(),
            )
            status = self.service.stage_storage(storage, match_active=self._match_active())
            if self._selected_team_id is not None:
                self.service.select_team(self._selected_team_id, match_active=self._match_active())
            self.storage_status.setText(f"保存先: {status}")
            self.accept()
        except (ConfigurationError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "設定を保存できません", str(exc))
