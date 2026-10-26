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
    QInputDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QMessageBox,
    QPushButton,
    QDoubleSpinBox,
    QSpinBox,
    QScrollArea,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .app_config import CaptureConfiguration, ConfigurationError, StorageConfiguration
from .capture_settings import CapturePreviewResult, CapturePreviewService
from .settings_management import DesktopSettingsService


class _MatchupAIWorker(QThread):
    result_ready = Signal(object)
    progress = Signal(object)

    def __init__(self, service, team_id, model, effort, parent=None):
        super().__init__(parent)
        self.service, self.team_id = service, team_id
        self.model, self.effort = model, effort

    def run(self):
        try:
            from .team_auto_analysis import run_automatic_analysis
            from .siwc_auth import ChatGPTSignIn, ProtectedSessionStore
            from .app_config import default_app_root
            # Import *all* prior v28/v29 batches before a refresh can create
            # a new fingerprint. Legacy JSON/TXT are always read-only here.
            from .matchup_learning_store import MatchupLearningStore
            learning=MatchupLearningStore(self.service.settings.storage.data_root)
            migrated_before=learning.import_legacy_assets()
            # Refresh only BETWEEN matches. Network/age errors preserve the
            # previous last-good Singles evidence; never block analysis.
            refresh_status = "LAST_GOOD"
            try:
                self.service.refresh_public_battle_data(match_active=False)
                refresh_status = "REFRESHED"
            except (OSError, ValueError, RuntimeError, TimeoutError):
                pass
            ready = self.service.prepare_team_live_assets(self.team_id, match_active=False)
            catalog = ready.manifest_path.with_name("matchup-evidence.json")
            auth = ChatGPTSignIn(ProtectedSessionStore(default_app_root()))
            # Public rankings only change evaluation ORDER. They never
            # identify a guaranteed opposing item, move or ability.
            priority = []
            snapshot = self.service.environment.snapshot
            if snapshot.is_file():
                try:
                    import json
                    env = json.loads(snapshot.read_text(encoding="utf-8"))
                    if env.get("format") == "Singles":
                        ranked = sorted(
                            (x for x in env.get("pokemon",[]) if isinstance(x,dict)
                             and isinstance(x.get("name"),str)
                             and type(x.get("position")) is int),
                            key=lambda x:x["position"])
                        priority=[x["name"] for x in ranked]
                except (OSError,ValueError,TypeError,KeyError):
                    pass
            from .team_matchup_reuse import reuse_unchanged_matchups
            reused = reuse_unchanged_matchups(
                catalog, Path(self.service.settings.storage.data_root) / "live-team-assets")
            # Saved batches are useful even after interruption or an API failure.
            # Publication happens only BETWEEN matches, never inside LIVE.
            try:
                report = run_automatic_analysis(
                    catalog, model=self.model, access_token=auth.access_token(),
                    reasoning_effort=self.effort, interrupted=self.isInterruptionRequested,
                    progress=self.progress.emit, priority_opponents=priority,
                    structured=True,
                    token_provider=lambda force: auth.access_token(force_refresh=force))
            finally:
                from .team_ai_matrix import publish_ai_matrix
                integrated = publish_ai_matrix(
                    matrix_path=ready.matrix_path, manifest_path=ready.manifest_path,
                    catalog_path=catalog)
            migrated_after=learning.import_legacy_assets()
            coverage=learning.coverage(catalog)
            self.result_ready.emit({"ok":True, "environment_refresh":refresh_status,
                                    **report, **reused, **integrated,
                                    "legacy_v28_imported":(migrated_before["imported_cells"]+
                                                           migrated_after["imported_cells"]),
                                    "learning_coverage":coverage,
                                    "legacy_files_preserved":True})
        except Exception as exc:
            from .team_ai_analysis import safe_ai_error
            self.result_ready.emit({"ok":False, "error":safe_ai_error(exc)})


class _TeamAnnotationWorker(QThread):
    result_ready=Signal(object)

    def __init__(self, members, team_id, team_sha, model, effort, parent=None):
        super().__init__(parent)
        self.members,self.team_id,self.team_sha=members,team_id,team_sha
        self.model,self.effort=model,effort

    def run(self):
        try:
            from .team_annotation_ai import request_annotation_draft
            from .siwc_auth import ChatGPTSignIn,ProtectedSessionStore
            from .app_config import default_app_root
            token=ChatGPTSignIn(ProtectedSessionStore(default_app_root())).access_token()
            draft=request_annotation_draft(self.members,model=self.model,
                                           access_token=token,
                                           reasoning_effort=self.effort)
            self.result_ready.emit({"ok":True,"team_id":self.team_id,
                                    "team_sha":self.team_sha,"draft":draft})
        except Exception as exc:
            from .team_ai_analysis import safe_ai_error
            self.result_ready.emit({"ok":False,"error":safe_ai_error(exc)})


class _SinglesRefreshWorker(QThread):
    result_ready=Signal(object)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service=service

    def run(self):
        try:
            receipt=self.service.refresh_public_battle_data(match_active=False)
            self.result_ready.emit({"ok":True,"receipt":receipt})
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            self.result_ready.emit({"ok":False,"error":str(exc)[:300]})

from .team_management import RegisteredTeam, TeamDocument
from .windows_capture import WindowCandidate
from .vision_benchmark_dialog import VisionBenchmarkPanel


class RoiPreviewLabel(QLabel):
    """Editable ROI overlay in the displayed image's normalized coordinates.

    Supports creation, live preview, moving and eight resize handles. It draws
    over a full-widget QLabel pixmap rather than assuming the image is at (0, 0).
    """

    roi_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__("プレビュー未取得")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(420, 240)
        self.setStyleSheet("border: 1px solid #666;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._image: np.ndarray | None = None
        self._roi: tuple[float, float, float, float] | None = None
        self._drag_start: tuple[float, float] | None = None
        self._drag_origin: tuple[float, float, float, float] | None = None
        self._drag_mode: str | None = None
        self._preview_roi: tuple[float, float, float, float] | None = None

    @property
    def normalized_roi(self) -> tuple[float, float, float, float] | None:
        return self._roi

    @property
    def image_size(self) -> tuple[int, int] | None:
        if self._image is None:
            return None
        height, width = self._image.shape[:2]
        return width, height

    def set_normalized_roi(self, roi: tuple[float, float, float, float] | None) -> None:
        if roi is not None:
            from .roi import NormalizedROI
            NormalizedROI("GAME_VIEWPORT", *roi)
        self._roi = roi
        self._preview_roi = None
        self.update()

    def set_frame(self, image: np.ndarray) -> None:
        self._image = image.copy()
        self._redraw()

    def _image_rect(self) -> QRect:
        if self._image is None:
            return QRect()
        height, width = self._image.shape[:2]
        if width <= 0 or height <= 0:
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
        if not rect.isEmpty():
            canvas = QPixmap(self.size())
            canvas.fill(QColor("#202020"))
            painter = QPainter(canvas)
            painter.drawImage(rect, qimage)
            painter.end()
            self.setPixmap(canvas)
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._redraw()

    def _normalized_point(self, point: QPoint, *, clamp: bool = False) -> tuple[float, float] | None:
        rect = self._image_rect()
        if rect.isEmpty() or (not clamp and not rect.contains(point)):
            return None
        x = (point.x() - rect.left()) / rect.width()
        y = (point.y() - rect.top()) / rect.height()
        return (min(1.0, max(0.0, x)), min(1.0, max(0.0, y)))

    def _handle_at(self, point: QPoint) -> str | None:
        roi = self._roi
        rect = self._image_rect()
        if roi is None or rect.isEmpty():
            return None
        l, t, r, b = roi
        px = (rect.left() + l * rect.width(), rect.left() + r * rect.width())
        py = (rect.top() + t * rect.height(), rect.top() + b * rect.height())
        handles = {"nw": (px[0], py[0]), "n": ((px[0]+px[1])/2, py[0]), "ne": (px[1], py[0]),
                   "w": (px[0], (py[0]+py[1])/2), "e": (px[1], (py[0]+py[1])/2),
                   "sw": (px[0], py[1]), "s": ((px[0]+px[1])/2, py[1]), "se": (px[1], py[1])}
        for name, (hx, hy) in handles.items():
            if abs(point.x()-hx) <= 9 and abs(point.y()-hy) <= 9:
                return name
        normalized = self._normalized_point(point)
        if normalized and l < normalized[0] < r and t < normalized[1] < b:
            return "move"
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() is Qt.MouseButton.LeftButton:
            point = self._normalized_point(event.position().toPoint())
            if point is not None:
                self.setFocus()
                self._drag_mode = self._handle_at(event.position().toPoint()) or "new"
                self._drag_start = point
                self._drag_origin = self._roi
                self._preview_roi = self._roi
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_mode and self._drag_start:
            point = self._normalized_point(event.position().toPoint(), clamp=True)
            if point:
                self._preview_roi = self._dragged_roi(point)
                self.update()
            event.accept()
            return
        mode = self._handle_at(event.position().toPoint())
        if mode == "move":
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif mode in {"nw", "se"}:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif mode in {"ne", "sw"}:
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif mode in {"n", "s"}:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif mode in {"w", "e"}:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)
        super().mouseMoveEvent(event)

    def _dragged_roi(self, end: tuple[float, float]) -> tuple[float, float, float, float] | None:
        if self._drag_start is None or self._drag_mode is None:
            return None
        x0, y0 = self._drag_start
        x1, y1 = end
        if self._drag_mode == "new":
            return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
        if self._drag_origin is None:
            return None
        l, t, r, b = self._drag_origin
        if self._drag_mode == "move":
            dx = max(-l, min(1-r, x1-x0))
            dy = max(-t, min(1-b, y1-y0))
            return l+dx, t+dy, r+dx, b+dy
        gap = 0.001
        if "w" in self._drag_mode:
            l = max(0., min(r-gap, x1))
        if "e" in self._drag_mode:
            r = min(1., max(l+gap, x1))
        if "n" in self._drag_mode:
            t = max(0., min(b-gap, y1))
        if "s" in self._drag_mode:
            b = min(1., max(t+gap, y1))
        return l, t, r, b

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() is Qt.MouseButton.LeftButton and self._drag_mode:
            point = self._normalized_point(event.position().toPoint(), clamp=True)
            roi = self._dragged_roi(point) if point else None
            self._drag_mode = None
            self._drag_start = None
            self._drag_origin = None
            self._preview_roi = None
            if roi is not None and roi[2]-roi[0] > 0.001 and roi[3]-roi[1] > 0.001:
                self._roi = roi
                self.roi_changed.emit(roi)
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._roi is not None and self.image_size is not None:
            directions = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                          Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
            if event.key() in directions:
                dx, dy = directions[event.key()]
                w, h = self.image_size
                step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
                l, t, r, b = self._roi
                off_x = max(-l, min(1-r, dx*step/w))
                off_y = max(-t, min(1-b, dy*step/h))
                self.set_normalized_roi((l+off_x, t+off_y, r+off_x, b+off_y))
                self.roi_changed.emit(self._roi)
                event.accept()
                return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        roi = self._preview_roi if self._drag_mode else self._roi
        if roi is None:
            return
        image_rect = self._image_rect()
        if image_rect.isEmpty():
            return
        l, t, r, b = roi
        overlay = QRect(image_rect.left() + round(l*image_rect.width()),
                        image_rect.top() + round(t*image_rect.height()),
                        max(1, round((r-l)*image_rect.width())),
                        max(1, round((b-t)*image_rect.height())))
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#00d084"), 2))
        painter.fillRect(overlay, QColor(0, 208, 132, 36))
        painter.drawRect(overlay)
        if not self._drag_mode and self._roi is not None:
            cx, cy = overlay.center().x(), overlay.center().y()
            for x in (overlay.left(), cx, overlay.right()):
                for y in (overlay.top(), cy, overlay.bottom()):
                    painter.fillRect(QRect(x-4, y-4, 9, 9), QColor("#ffffff"))
                    painter.drawRect(QRect(x-4, y-4, 9, 9))
        painter.end()


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
        self._team_validation_refreshed = False
        self._singles_worker = None
        self._matchup_ai_worker = None
        self._annotation_worker = None
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
        self.tabs.addTab(self._chatgpt_tab(), "ChatGPT")
        self.tabs.addTab(self._gpu_tab(), "画面分類GPU")
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
        self.roi_fields: dict[str, QSpinBox] = {}
        coords = QHBoxLayout()
        for name, label in (("left", "左"), ("top", "上"), ("right", "右"), ("bottom", "下")):
            coords.addWidget(QLabel(label))
            field = QSpinBox()
            field.setRange(0, 100000)
            field.setSuffix(" px")
            field.setEnabled(False)
            field.valueChanged.connect(self._roi_field_changed)
            coords.addWidget(field)
            self.roi_fields[name] = field
        clear_roi = QPushButton("ROIを解除")
        clear_roi.clicked.connect(lambda: self._set_roi_from_controls(None))
        coords.addWidget(clear_roi)
        layout.addLayout(coords)
        layout.addWidget(QLabel("矩形内をドラッグして移動、8個のハンドルでサイズ変更。矢印キー=1px、Shift+矢印=10px。"))
        self.capture_metrics = QLabel("未取得")
        self.capture_metrics.setWordWrap(True)
        layout.addWidget(self.capture_metrics)
        self.save_preview_button = QPushButton("テスト撮影を保存（明示操作のみ）")
        self.save_preview_button.setEnabled(False)
        self.save_preview_button.clicked.connect(self._save_preview)
        layout.addWidget(self.save_preview_button)
        self.calibrate_button = QPushButton("認識較正を開く（ローカル・オフライン）")
        self.calibrate_button.clicked.connect(self._open_calibration)
        layout.addWidget(self.calibrate_button)
        self.vision_benchmark_button = QPushButton("ChatGPTタブで画像認識を実測")
        self.vision_benchmark_button.clicked.connect(self._show_chatgpt_tab)
        layout.addWidget(self.vision_benchmark_button)
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
        create_button = QPushButton("新規作成")
        create_button.clicked.connect(self._create_team)
        import_button = QPushButton("CSVを登録")
        import_button.clicked.connect(self._import_team)
        duplicate_button = QPushButton("複製")
        duplicate_button.clicked.connect(self._duplicate_team)
        select_button = QPushButton("次戦に選択")
        select_button.clicked.connect(self._select_team)
        delete_button = QPushButton("削除")
        delete_button.clicked.connect(self._delete_team)
        export_button = QPushButton("CSVをエクスポート")
        export_button.clicked.connect(self._export_team)
        for button in (create_button, import_button, duplicate_button, select_button, export_button, delete_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        layout.addWidget(QLabel("role（役割）は任意。AIの役割・変化・注意案は空欄だけに仮入力し、構築を保存するまで確定しません。"))
        self.annotation_button=QPushButton("選択構築のrole・form_change・runtime_noteをAIで補助入力")
        self.annotation_button.clicked.connect(self._start_team_annotation)
        layout.addWidget(self.annotation_button)
        self.team_list = QTableWidget(0, 5)
        self.team_list.setHorizontalHeaderLabels(["登録名", "6匹", "検証", "SHA-256", "次戦適用"])
        self.team_list.itemSelectionChanged.connect(self._load_selected_team_editor)
        layout.addWidget(self.team_list, 1)
        self.team_editor = QTableWidget(0, len(self.member_columns))
        self.team_editor.setHorizontalHeaderLabels(tuple("role（任意・AI判断）" if field == "role" else field for field in self.member_columns))
        layout.addWidget(self.team_editor, 1)
        save_edit = QPushButton("構築を保存")
        save_edit.clicked.connect(self._save_team_edit)
        layout.addWidget(save_edit)
        self.team_status = QLabel("構築を選択してください。手入力値は固定マスターに無い場合、未確認または無効として扱います。")
        self.team_status.setWordWrap(True)
        layout.addWidget(self.team_status)
        source_row=QHBoxLayout()
        self.singles_refresh_button=QPushButton("シングル環境データを更新（対戦前）")
        self.singles_refresh_button.clicked.connect(self._refresh_singles)
        source_row.addWidget(self.singles_refresh_button)
        self.singles_import_button=QPushButton("ゲーム内統計JSONを取り込む（任意）")
        self.singles_import_button.clicked.connect(self._import_singles)
        source_row.addWidget(self.singles_import_button)
        layout.addLayout(source_row)
        self.singles_source_status=QLabel(self._singles_source_message())
        self.singles_source_status.setWordWrap(True)
        layout.addWidget(self.singles_source_status)
        review_row=QHBoxLayout()
        self.matchup_candidates_button=QPushButton("AI対面評価の進捗・根拠不足を確認")
        self.matchup_candidates_button.clicked.connect(self._show_matchup_candidates)
        review_row.addWidget(self.matchup_candidates_button)
        layout.addLayout(review_row)
        self.matchup_ai_button=QPushButton("全対面LIVEマトリクスをAI作成・再開（未検証・試合前）")
        self.matchup_ai_button.clicked.connect(self._start_matchup_ai)
        layout.addWidget(self.matchup_ai_button)
        self.matchup_stop_button=QPushButton("自動解析を停止（現在の通信終了後）")
        self.matchup_stop_button.setEnabled(False)
        self.matchup_stop_button.clicked.connect(self._stop_matchup_ai)
        layout.addWidget(self.matchup_stop_button)
        self.device_quality_button=QPushButton(
            "実機対戦90/40/45秒ログを検証（対戦前）")
        self.device_quality_button.clicked.connect(self._import_real_battle_quality)
        layout.addWidget(self.device_quality_button)
        self.matchup_review_status=QLabel(
            "対面根拠は固定マスターの事実と未評価箇所を記録します。"
            "承認済みでも実戦E2E未確認の新構築をLIVE_READYにはしません。")
        self.matchup_review_status.setWordWrap(True)
        layout.addWidget(self.matchup_review_status)
        layout.addWidget(QLabel(
            "構築選択で構築別LIVEひな形を生成。AI作成した対面仮説は次戦の選出とPREPで参照。"
            "未検証対面を安全受けや確定処理役に昇格しません。"
        ))
        layout.addWidget(QLabel("検証の詳細（不足・不一致のすべて）"))
        self.team_validation_details = QPlainTextEdit()
        self.team_validation_details.setReadOnly(True)
        self.team_validation_details.setMaximumHeight(110)
        self.team_validation_details.setPlainText("構築を選択してください")
        layout.addWidget(self.team_validation_details)
        return tab

    def _chatgpt_tab(self) -> QWidget:
        tab = QWidget()
        self.chatgpt_tab = tab
        layout = QVBoxLayout(tab)
        # The login, model/effort selection and recognition benchmarks belong
        # directly to Settings; opening a second dialog hid all these controls.
        self.chatgpt_panel = VisionBenchmarkPanel(
            parent=tab,
            master_directory=self.service.master_directory,
            context_provider=self._chatgpt_context,
        )
        scroller = QScrollArea(tab)
        scroller.setWidgetResizable(True)
        scroller.setWidget(self.chatgpt_panel)
        layout.addWidget(scroller)
        return tab

    def _gpu_tab(self) -> QWidget:
        """Local ONNX inference only, independent from the ChatGPT API."""
        tab=QWidget()
        layout=QVBoxLayout(tab)
        form=QFormLayout()
        self.screen_device_box=QComboBox()
        self.screen_device_box.setObjectName("screenClassifierDevice")
        for label,key in (
            ("自動（GPU優先・利用不可ならCPU）","auto"),
            ("DirectML GPU（Windows / AMD・Intel・NVIDIA）","directml"),
            ("CUDA GPU（対応ランタイムが必要）","cuda"),
            ("GPU優先（DirectML → CUDA）","gpu"),
            ("CPUのみ","cpu"),
        ):
            self.screen_device_box.addItem(label,key)
        self.screen_gpu_id=QSpinBox()
        self.screen_gpu_id.setObjectName("screenClassifierGpuIndex")
        self.screen_gpu_id.setRange(0,7)
        self.screen_device_box.currentIndexChanged.connect(
            lambda:self.screen_gpu_id.setEnabled(
                self.screen_device_box.currentData()!="cpu"))
        self.screen_provider_status=QLabel()
        self.screen_provider_status.setWordWrap(True)
        form.addRow("ONNX推論デバイス",self.screen_device_box)
        form.addRow("GPUアダプター番号",self.screen_gpu_id)
        form.addRow("実行環境",self.screen_provider_status)
        layout.addLayout(form)
        layout.addWidget(QLabel(
            "GPU番号0は通常メイン画面のGPUです。複数搭載時はWindowsの"
            "タスクマネージャーで番号を確認してください。実際に有効化された"
            "ONNXプロバイダーは対戦支援画面に表示されます。"
            "設定変更は次回の自動AI支援開始時に反映されます。"))
        layout.addStretch(1)
        return tab

    def _show_chatgpt_tab(self) -> None:
        self.tabs.setCurrentWidget(self.chatgpt_tab)

    def _chatgpt_context(self):
        # Resolve the in-dialog next-match team and edited capture settings at
        # action time, not from the snapshot used when Settings first opened.
        team_path = None
        if self._selected_team_id:
            try:
                team_path = self.service.teams.path_for_next_match(self._selected_team_id)
            except ConfigurationError:
                pass
        return self.service.master_directory, team_path, self._capture_config()

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
        self._sync_roi_fields()
        storage = self.service.settings.storage
        self.data_root.setText(storage.data_root)
        self.captures_root.setText(storage.captures_root)
        self.save_screenshots.setChecked(storage.save_screenshots)
        self.retention_days.setValue(storage.retention_days)
        self._refresh_windows()
        self._refresh_teams()
        from .vision_preferences import VisionPreferenceStore
        preference=VisionPreferenceStore(self.service.store.path.parent.parent)
        index=self.screen_device_box.findData(preference.screen_device())
        self.screen_device_box.setCurrentIndex(max(0,index))
        self.screen_gpu_id.setValue(preference.screen_gpu_id())
        try:
            import onnxruntime as ort
            available=", ".join(ort.get_available_providers())
        except (ImportError,OSError,RuntimeError):
            available="onnxruntimeが利用できません"
        self.screen_provider_status.setText("利用可能なプロバイダー: "+available)
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
            self._sync_roi_fields()
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
            roi = self.preview_label.normalized_roi
            if roi is None:
                QMessageBox.warning(self, "撮影を保存できません", "先にゲーム領域のROIを指定してください。")
                return
            saved = self.preview_service.save_explicit_frame(self._last_preview, destination, roi=roi)
            self.capture_metrics.setText(f"明示操作で保存しました: {saved.name}（内容は未検証）")
        except ConfigurationError as exc:
            QMessageBox.warning(self, "撮影を保存できません", str(exc))

    def _open_calibration(self) -> None:
        from .ui_calibration_dialog import UiCalibrationDialog
        from .ui_calibration import LocalUiCalibration
        current = self._last_preview
        roi = self.preview_label.normalized_roi
        image = None
        if current is not None and current.image is not None and roi is not None:
            try:
                image = LocalUiCalibration.crop_viewport(current.image, roi)
            except ValueError as exc:
                QMessageBox.warning(self, "較正できません", str(exc))
                return
        self._calibration_dialog = UiCalibrationDialog(
            storage_root=Path(self.captures_root.text().strip()),
            initial_image=image,
            parent=self,
        )
        self._calibration_dialog.show()

    def _sync_roi_fields(self) -> None:
        size = self.preview_label.image_size
        roi = self.preview_label.normalized_roi
        for name, field in self.roi_fields.items():
            field.blockSignals(True)
            field.setEnabled(size is not None)
            if size is not None:
                index = {"left": 0, "top": 1, "right": 2, "bottom": 3}[name]
                limit = size[0] if name in {"left", "right"} else size[1]
                field.setMaximum(limit)
                if roi is not None:
                    field.setValue(round(roi[index] * limit))
            field.blockSignals(False)

    def _set_roi_from_controls(self, roi: tuple[float, float, float, float] | None) -> None:
        self.preview_label.set_normalized_roi(roi)
        self._sync_roi_fields()
        self._roi_changed(roi)

    def _roi_field_changed(self) -> None:
        size = self.preview_label.image_size
        if size is None:
            return
        w, h = size
        values = tuple(self.roi_fields[name].value() for name in ("left", "top", "right", "bottom"))
        l, t, r, b = values
        if not (0 <= l < r <= w and 0 <= t < b <= h):
            return
        self._set_roi_from_controls((l/w, t/h, r/w, b/h))

    def _roi_changed(self, _roi: object) -> None:
        self._sync_roi_fields()
        self.capture_metrics.setText("ROIを変更しました。保存後、画面サイズ/DPIの変更時は再較正が必要です。")

    def _choose_directory(self, target: QLineEdit) -> None:
        selected = QFileDialog.getExistingDirectory(self, "保存先を選択", target.text())
        if selected:
            target.setText(selected)

    def _refresh_teams(self, focus_id: str | None = None) -> None:
        # Validation status is persisted in the index. On an updated app,
        # older INVALID results (such as mandatory role) need recomputation.
        # Only read private team files between matches, never during LIVE.
        if not self._team_validation_refreshed and not self._match_active():
            for stored in self.service.teams.list():
                self.service.revalidate_team(stored.team_id, match_active=False)
            self._team_validation_refreshed = True
        records = self.service.teams.list()
        active = self.service.settings.teams.active_team_id
        pending = self.service.settings.teams.pending_team_id
        wanted = focus_id or self._editing_team_id or active
        was_blocked = self.team_list.blockSignals(True)
        try:
            self.team_list.setRowCount(len(records))
            focus_row = None
            for row, record in enumerate(records):
                members = ", ".join(member.get("name", "") or "（未設定）" for member in TeamDocument.load(record.csv_path).members)
                values = (record.display_name, members, record.validation.status,
                          record.sha256, "予約" if record.team_id == pending else
                          "選択中" if record.team_id == active else "")
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, record.team_id if column == 0 else None)
                    self.team_list.setItem(row, column, item)
                if record.team_id == wanted:
                    focus_row = row
            self.team_list.clearSelection()
            if focus_row is not None:
                self.team_list.setCurrentCell(focus_row, 0)
                self.team_list.selectRow(focus_row)
        finally:
            self.team_list.blockSignals(was_blocked)
        self._load_selected_team_editor()

    def _selected_record(self) -> RegisteredTeam | None:
        selected = self.team_list.selectedItems()
        if not selected:
            return None
        return self.service.teams.get(str(self.team_list.item(selected[0].row(), 0).data(Qt.ItemDataRole.UserRole)))

    def _load_selected_team_editor(self) -> None:
        record = self._selected_record()
        if record is None:
            self._editing_document = None
            self._editing_team_id = None
            self.team_editor.setRowCount(0)
            self.team_validation_details.setPlainText("構築を選択してください")
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
        self._show_team_validation(record)

    def _show_team_validation(self, record: RegisteredTeam) -> None:
        reasons = record.validation.reasons
        if reasons:
            only_master_limit=all(
                "性格/AP配分のChampions検証根拠は未収録" in reason for reason in reasons)
            self.team_status.setText(
                (f"構築の手動修正は不要：固定マスターの性格/AP検証機能が"
                 f"{len(reasons)}匹分未収録（AI対面解析は利用可）。"
                 if only_master_limit else
                 f"{record.validation.status}: {len(reasons)}件の確認事項。"
                 " 詳細を下に表示します。")
            )
            self.team_validation_details.setPlainText(
                "\n".join(f"{index}. {reason}" for index, reason in enumerate(reasons, 1))
            )
        else:
            self.team_status.setText(f"{record.validation.status}: 固定マスター照合済み")
            self.team_validation_details.setPlainText("検証上の不足事項はありません。")

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

    def _create_team(self) -> None:
        if self._match_active():
            QMessageBox.warning(self, "新規作成できません", "試合終了後に作成してください。")
            return
        name, accepted = QInputDialog.getText(self, "新しい構築", "構築名を入力してください:")
        if not accepted:
            return
        try:
            record = self.service.create_team(name.strip(), match_active=False)
            self._refresh_teams(focus_id=record.team_id)
            self.team_status.setText(
                "新規構築を作成しました。6匹・持ち物・特性・性格・AP配分・4技を入力して"
                "「構築を保存」してください。完成するまで次戦には使用できません。"
            )
        except (ConfigurationError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "構築を作成できません", str(exc))

    def _delete_team(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        if self._match_active():
            QMessageBox.warning(self, "構築を削除できません", "試合中の構築は削除できません。")
            return
        active = self.service.settings.teams.active_team_id
        marker = "（次戦に選択中）" if record.team_id == active else ""
        choice = QMessageBox.question(
            self, "構築の削除を確認",
            f"「{record.display_name}」{marker} を登録一覧から削除しますか？\\n"
            "この構築の保存データも削除され、元に戻せません。"
            "他の構築と固定マスターは変更されません。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.delete_team(record.team_id, match_active=False)
            if self._selected_team_id == record.team_id:
                self._selected_team_id = None
            if self._editing_team_id == record.team_id:
                self._editing_team_id = None
                self._editing_document = None
            self._refresh_teams()
            self.team_status.setText("削除しました。次戦に使う構築は「次戦に選択」で指定してください。")
        except (ConfigurationError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "構築を削除できません", str(exc))

    def _import_team(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, "構築CSVを登録", "", "CSV (*.csv)")
        if not source:
            return
        try:
            record = self.service.import_team(source, match_active=self._match_active())
            self._refresh_teams(focus_id=record.team_id)
            self.team_status.setText("CSVを登録しました。次戦に使う場合は「次戦に選択」を押してください。")
        except ConfigurationError as exc:
            QMessageBox.warning(self, "構築を登録できません", str(exc))

    def _duplicate_team(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        if self._match_active():
            QMessageBox.warning(self, "構築を複製できません", "試合中の構築編集は次戦前に行ってください")
            return
        try:
            clone = self.service.duplicate_team(record.team_id, match_active=False)
            self._refresh_teams(focus_id=clone.team_id)
            self.team_status.setText("複製しました。元の構築は変更されません。次戦への切替は別途選択してください。")
        except (ConfigurationError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "構築を複製できません", str(exc))

    def _start_team_annotation(self) -> None:
        if self._annotation_worker is not None or self._match_active():
            return
        record=self._selected_record()
        if record is None:
            return
        # Refuse stale editors; suggestions refer to the exact saved CSV.
        if self._editing_team_id!=record.team_id or self._editing_document is None:
            return
        from .vision_preferences import VisionPreferenceStore
        from .app_config import default_app_root
        prefs=VisionPreferenceStore(default_app_root())
        model=prefs.selected_model()
        if not model:
            QMessageBox.warning(self,"モデル未設定","ChatGPTタブでモデルを選んでください。")
            return
        answer=QMessageBox.question(
            self,"構築AI入力案",
            "登録済みの6匹・技・持ち物・配分だけをChatGPTに送信して、"
            "役割と注意の入力案を作成します。既存値は上書きせず、"
            "構築保存まで反映しません。開始しますか？",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer!=QMessageBox.StandardButton.Yes:
            return
        # Refuse stale registered data when the user has unsaved form edits.
        # Never silently persist these edits just to run the helper.
        doc=self._editing_document
        for row,member in enumerate(doc.members):
            for column,field in enumerate(self.member_columns):
                widget=self.team_editor.cellWidget(row,column)
                item=self.team_editor.item(row,column)
                current=(widget.currentText() if isinstance(widget,QComboBox)
                         else item.text() if item is not None else "").strip()
                if current!=(member.get(field) or "").strip():
                    self.team_status.setText(
                        "先に現在の編集を「構築を保存」で確定してください。"
                        "AIは未保存の構築を解析しません。")
                    return
        self.annotation_button.setEnabled(False)
        self.team_editor.setEnabled(False)
        self._annotation_worker=_TeamAnnotationWorker(
            [dict(x) for x in doc.members],record.team_id,doc.sha256(),
            model,prefs.reasoning_effort(),self)
        self._annotation_worker.result_ready.connect(self._team_annotation_result)
        self._annotation_worker.finished.connect(self._team_annotation_finished)
        self._annotation_worker.start()

    def _team_annotation_result(self, result: dict) -> None:
        if not result.get("ok"):
            self.team_status.setText("構築AI補助入力失敗："+result.get("error","不明"))
            return
        if (self._editing_document is None or self._editing_team_id!=result["team_id"]
                or self._editing_document.sha256()!=result["team_sha"]):
            self.team_status.setText("構築が変更されたためAI入力案を破棄しました")
            return
        changed=0
        for row,proposal in enumerate(result["draft"]):
            for field in ("role","form_change","runtime_note"):
                col=self.member_columns.index(field)
                widget=self.team_editor.cellWidget(row,col)
                item=self.team_editor.item(row,col)
                current=(widget.currentText() if isinstance(widget,QComboBox)
                         else item.text() if item is not None else "").strip()
                if not current and proposal[field].strip():
                    if isinstance(widget,QComboBox):
                        widget.setCurrentText(proposal[field])
                    else:
                        self.team_editor.setItem(row,col,QTableWidgetItem(proposal[field]))
                    changed+=1
        self.team_status.setText(
            f"AIの入力案 {changed}欄を空欄に仮入力。確認後、構築を保存してください。"
            " 未検証の戦術提案であり、自動確定はしません。")

    def _team_annotation_finished(self) -> None:
        worker=self._annotation_worker
        self._annotation_worker=None
        self.annotation_button.setEnabled(True)
        self.team_editor.setEnabled(True)
        if worker is not None:
            worker.deleteLater()

    def _singles_source_message(self) -> str:
        try:
            receipt=self.service.environment.summary()
        except (OSError,ValueError,RuntimeError):
            return "環境データ: 出典情報を読めません"
        if receipt.get("status")=="MISSING":
            return "環境データ: 未取得（Champions固定マスターだけで仮生成）"
        return ("Singles環境データ: "+str(receipt.get("source_kind"))+
                " / "+str(receipt.get("generated_at"))+
                " / "+str(receipt.get("number_of_pokemon"))+
                "件（採用率は相手の確定型ではありません）")

    def _refresh_singles(self) -> None:
        if self._match_active():
            QMessageBox.warning(self,"更新不可","対戦が終わってから更新してください。")
            return
        if self._singles_worker is not None:return
        self.singles_refresh_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.singles_source_status.setText("Singles環境データ取得中（対戦中には取得しません）")
        self._singles_worker=_SinglesRefreshWorker(self.service,self)
        self._singles_worker.result_ready.connect(self._singles_refreshed)
        self._singles_worker.finished.connect(self._singles_refresh_finished)
        self._singles_worker.start()

    @Slot(object)
    def _singles_refreshed(self,result:dict) -> None:
        if result.get("ok"):
            self.singles_source_status.setText(self._singles_source_message())
        else:
            self.singles_source_status.setText(
                "Singles環境データ更新失敗（前回データを維持）: "+
                str(result.get("error","UNKNOWN")))

    @Slot()
    def _singles_refresh_finished(self) -> None:
        worker=self._singles_worker
        self._singles_worker=None
        self.singles_refresh_button.setEnabled(True)
        self.save_button.setEnabled(True)
        if worker is not None:worker.deleteLater()

    def _import_singles(self) -> None:
        if self._match_active():
            QMessageBox.warning(self,"取込不可","対戦が終わってから取り込んでください。")
            return
        name,_=QFileDialog.getOpenFileName(
            self,"ゲーム内Singlesランキング情報JSONを選択","","JSON (*.json)")
        if not name:return
        try:
            self.service.import_user_battle_data(name,match_active=False)
            self.singles_source_status.setText(self._singles_source_message())
        except (OSError,ValueError,RuntimeError) as exc:
            QMessageBox.warning(self,"統計JSONを取り込めません",str(exc))

    def _show_matchup_candidates(self) -> None:
        if self._match_active():
            QMessageBox.warning(self,"確認不可","対戦が終わってから確認してください。")
            return
        record=self._selected_record()
        if record is None:return
        try:
            from .matchup_learning_store import MatchupLearningStore
            from .team_matchup_reuse import reuse_unchanged_matchups
            from .team_ai_matrix import publish_ai_matrix
            from .postmatch_matrix_notes import publish_postmatch_notes
            store=MatchupLearningStore(self.service.settings.storage.data_root)
            migration=store.import_legacy_assets()
            ready=self.service.prepare_team_live_assets(record.team_id,match_active=False)
            catalog=ready.manifest_path.with_name("matchup-evidence.json")
            reused=reuse_unchanged_matchups(
                catalog,Path(self.service.settings.storage.data_root)/"live-team-assets")
            if reused["reused_cells"]:
                publish_ai_matrix(
                    matrix_path=ready.matrix_path,manifest_path=ready.manifest_path,
                    catalog_path=catalog)
                from hashlib import sha256
                publish_postmatch_notes(
                    data_root=self.service.settings.storage.data_root,
                    team_id=record.team_id,
                    manifest_path=ready.manifest_path,matrix_path=ready.matrix_path,
                    team_signature=sha256(Path(record.csv_path).read_bytes()).hexdigest())
                migration=store.import_legacy_assets()
            coverage=store.coverage(catalog)
            audit=self.service.sqlite_migration_report or {}
            migration_label=(
                f"SQLite移行監査: {audit.get('status','未確認')}、"
                f"元ファイルSHA一致 {audit.get('verified_files','?')}/"
                f"{audit.get('expected_files','?')}件。"
                if audit else "SQLite移行監査: 未実施。"
            )
            self.matchup_review_status.setText(
                f"AI評価済み {coverage['ai_evaluated']}/{coverage['total_cells']}対面 "
                f"（構造化v2 {coverage['structured_v2']}）／"
                f"未解析 {coverage['not_analyzed']}／"
                f"根拠不足 {coverage['source_gaps']}。"
                f" 実戦後AI評価 {coverage['postmatch_reviews']}件。"
                f" SQLite全履歴 {migration['stored_cells_all_versions']}対面、"
                f"旧形式差分再利用 {reused['reused_cells']}件。"
                f" {migration_label}"
                f" v28・v29の元CSV/JSONは消去しません。"
                f"\nデータ保存先: {catalog.parent}"
            )
        except (OSError,ValueError,RuntimeError,KeyError) as exc:
            QMessageBox.warning(self,"AI評価の状況確認に失敗",str(exc)[:250])

    def _start_matchup_ai(self) -> None:
        if self._match_active() or self._matchup_ai_worker is not None:
            QMessageBox.warning(self,"実行不可","対戦中または解析中には実行できません。")
            return
        record=self._selected_record()
        if record is None:return
        from .vision_preferences import VisionPreferenceStore
        from .app_config import default_app_root
        prefs=VisionPreferenceStore(default_app_root())
        model=prefs.selected_model()
        if not model:
            QMessageBox.warning(self,"モデル未選択","ChatGPTタブでログインし、モデルを選択してください。")
            return
        answer=QMessageBox.question(
            self,"ChatGPTへの送信確認",
            "選択構築×相手358種類の全2,148対面を対象に、24～36対面ずつ"
            "構造化AI解析します（通信上限180回、再試行を含む）。"
            "固定マスター不足の対面は未解析として記録されます。"
            "旧形式の文章評価もv2構造化形式では再解析対象です。"
            "他構築で根拠が同一のv2結果を再利用し、成功分を保存します。"
            "利用枠を消費するため、必要なら途中停止してください。"
            "実機証拠や対戦ログは送信しません。AIの推測は戦術の確定評価に昇格しません。開始しますか？",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer!=QMessageBox.StandardButton.Yes:return
        self.matchup_ai_button.setEnabled(False)
        self.matchup_stop_button.setEnabled(True)
        self.save_button.setEnabled(False)
        self.matchup_review_status.setText("構築別AI対面解析を自動実行中（試合開始はしないでください）")
        self._matchup_ai_worker=_MatchupAIWorker(self.service,record.team_id,model,prefs.reasoning_effort(),self)
        self._matchup_ai_worker.progress.connect(self._matchup_ai_progress)
        self._matchup_ai_worker.result_ready.connect(self._matchup_ai_result)
        self._matchup_ai_worker.finished.connect(self._matchup_ai_finished)
        self._matchup_ai_worker.start()

    def _matchup_ai_progress(self, report: dict) -> None:
        self.matchup_review_status.setText(
            f"ChatGPTで対面を構造化解析中: 今回 {report['count']}件、"
            f"成功 {report['batches']}回／通信試行 {report.get('attempts',report['batches'])}回。"
            f" 現在のバッチ {report.get('batch_size',12)}対面。"
            " 確定KO・処理役・安全受けは未認定です。")

    def _stop_matchup_ai(self) -> None:
        if self._matchup_ai_worker is not None:
            self._matchup_ai_worker.requestInterruption()
            self.matchup_stop_button.setEnabled(False)
            self.matchup_review_status.setText(
                "停止要求を受け付けました。現在のChatGPT通信の終了後に中断します。")

    def _matchup_ai_result(self, result: dict) -> None:
        if result["ok"]:
            label={"COMPLETE":"構造化対面評価案の全件生成完了",
                   "SOURCE_GAPS":"解析可能な対面は完了・固定マスター不足あり",
                   "INTERRUPTED":"自動解析を中断",
                   "BATCH_LIMIT":"自動解析の上限に到達"}.get(result["status"],result["status"])
            self.matchup_review_status.setText(
                f"{label}。今回生成: {result['count']}件、"
                f"差分再利用: {result.get('reused_cells',0)}件、"
                f"通信試行 {result.get('attempts',result['batches'])}回"
                f"（成功{result['batches']}・再試行{result.get('retries',0)}）。"
                f" マスター不足 {result.get('source_gaps',0)}対面。"
                + (" 不足内訳: "+", ".join(
                    f"{reason}={count}" for reason,count in
                    result.get("source_gap_reasons",{}).items())+"。"
                   if result.get("source_gap_reasons") else "")
                + f" LIVEマトリクスにAI評価案 {result.get('ai_proposal_cells',0)}件を反映。"
                + f" SQLiteへ移行済み {result.get('legacy_v28_imported',0)}件（元のv28ファイルは保持）。"
                " 処理役・安全受け・KOは未検証であり暫定推奨です。")
        else:
            self.matchup_review_status.setText(
                "AI自動解析停止・今回成功分は保存済み: "+str(result["error"]))

    def _matchup_ai_finished(self) -> None:
        self._matchup_ai_worker=None
        self.matchup_ai_button.setEnabled(True)
        self.matchup_stop_button.setEnabled(False)
        self.save_button.setEnabled(True)

    def _import_matchup_reviews(self) -> None:
        if self._match_active():
            QMessageBox.warning(self,"取り込み不可","対戦が終わってから取り込んでください。")
            return
        record=self._selected_record()
        if record is None:return
        filename,_=QFileDialog.getOpenFileName(
            self,"プレイヤー確認済みChampions対面レビューJSON","","JSON (*.json)")
        if not filename:return
        try:
            result=self.service.import_matchup_reviews(
                record.team_id,filename,match_active=False)
            counts=result["review_counts"]
            self.matchup_review_status.setText(
                f"レビュー保持: CONFIRMED {counts['CONFIRMED']} / "
                f"CONDITIONAL {counts['CONDITIONAL']}。"
                " 相手の型と安全受けは自動確定しません。"
                " \n根拠: "+result["matchup_evidence_path"]
            )
        except (OSError,ValueError,RuntimeError) as exc:
            QMessageBox.warning(self,"対面レビューJSONを取り込めません",str(exc))

    def _import_real_battle_quality(self) -> None:
        if self._match_active():
            QMessageBox.warning(self,"検証不可","対戦が終わってから取り込んでください。")
            return
        record=self._selected_record()
        if record is None:return
        file,_=QFileDialog.getOpenFileName(
            self,"実機対戦の証拠付きタイムラインJSONを選択","","JSON (*.json)")
        if not file:return
        try:
            result=self.service.import_real_battle_quality(
                record.team_id,file,match_active=False)
            audit=result["audit"]
            self.matchup_review_status.setText(
                f"実機証拠を検査：{audit['match_count']}試合 / "
                f"選出・PREP・各ターン期限:"
                f"{'PASS' if audit['all_deadlines_met'] else 'FAIL'} / "
                f"認識・古い応答:"
                f"{'PASS' if audit['no_screen_or_stale_advice_errors'] else 'FAIL'}。"
                "\\n※実機証拠の検査だけではLIVE_READYになりません。"
                "\\n"+result["file"]
            )
        except (OSError,ValueError,RuntimeError) as exc:
            QMessageBox.warning(self,"実機E2E検証JSONを取り込めません",str(exc))

    def closeEvent(self,event):
        if self._matchup_ai_worker is not None and self._matchup_ai_worker.isRunning():
            self.matchup_review_status.setText("AI解析中は設定画面を閉じられません")
            event.ignore()
            return
        if self._singles_worker is not None and self._singles_worker.isRunning():
            self.singles_source_status.setText("更新終了まで設定画面は閉じられません")
            event.ignore()
            return
        super().closeEvent(event)

    def _select_team(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        try:
            if not self._match_active():
                # Save the visible six before validating/activating them.
                # This prevents an old validation snapshot from blocking a
                # now-valid team and stops unsaved edits from being ignored.
                if self._editing_team_id == record.team_id:
                    self._persist_team_editor()
                record = self.service.revalidate_team(record.team_id, match_active=False)
            if not record.validation.selectable_for_next_match:
                reasons = record.validation.reasons
                details = "\n".join(f"{i}. {value}" for i, value in enumerate(reasons, 1))
                QMessageBox.warning(
                    self, "次戦に選択できません",
                    "構築の検証が完了していません。以下を確認してください。\n\n"
                    + (details or record.validation.status),
                )
                self._refresh_teams(focus_id=record.team_id)
                return
            self._selected_team_id = record.team_id
            self._refresh_teams(focus_id=record.team_id)
            self.team_status.setText("下の「保存」で次戦用構築を適用し、構築別LIVEデータを準備します。")
        except (ConfigurationError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "次戦に選択できません", str(exc))

    def _export_team(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        target, _ = QFileDialog.getSaveFileName(self, "構築CSVをエクスポート", f"{record.display_name}.csv", "CSV (*.csv)")
        if target:
            self.service.teams.export_csv(record.team_id, target)

    def _persist_team_editor(self) -> RegisteredTeam | None:
        """Save the currently visible editor without changing next-match selection.

        The main settings Save button and the explicit team Save button must
        share this exact path. Never overwrite a team using stale editor data
        when no team is currently being edited.
        """
        if self._editing_document is None or self._editing_team_id is None:
            return None
        rows = [dict(row) for row in self._editing_document.rows]
        mon_rows = [index for index, row in enumerate(rows) if row.get("record") == "MON"]
        if len(mon_rows) != self.team_editor.rowCount():
            raise ConfigurationError("構築の編集行数が一致しません。保存せずに確認してください。")
        for table_row, row_index in enumerate(mon_rows):
            for column, field in enumerate(self.member_columns):
                selector = self.team_editor.cellWidget(table_row, column)
                if isinstance(selector, QComboBox):
                    rows[row_index][field] = selector.currentText().strip()
                else:
                    item = self.team_editor.item(table_row, column)
                    rows[row_index][field] = "" if item is None else item.text().strip()
        document = TeamDocument(self._editing_document.fieldnames, tuple(rows))
        if document.to_text() == self._editing_document.to_text():
            if self._match_active():
                return self.service.teams.get(self._editing_team_id)
            return self.service.revalidate_team(self._editing_team_id, match_active=False)
        updated = self.service.update_team_csv(
            self._editing_team_id,
            document.to_text(),
            match_active=self._match_active(),
        )
        self._editing_document = document
        return updated

    def _save_team_edit(self) -> None:
        try:
            updated = self._persist_team_editor()
            if updated is None:
                return
            self._refresh_teams(focus_id=updated.team_id)
            self._show_team_validation(updated)
        except (ConfigurationError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "構築を保存できません", str(exc))

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
        self._set_roi_from_controls(None)
        self.screen_device_box.setCurrentIndex(
            self.screen_device_box.findData("auto"))
        self.screen_gpu_id.setValue(0)

    def _save(self) -> None:
        if self._matchup_ai_worker is not None:
            return
        if self._chatgpt_job_busy():
            QMessageBox.information(self, "ChatGPT処理中", "ChatGPTの処理が終わってから設定を保存してください。")
            self._show_chatgpt_tab()
            return
        try:
            # Persist unsaved table edits before closing settings, including
            # draft teams whose validation is not complete yet.
            self._persist_team_editor()
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
            from .vision_preferences import VisionPreferenceStore
            VisionPreferenceStore(self.service.store.path.parent.parent).save_screen_device(
                str(self.screen_device_box.currentData()),
                self.screen_gpu_id.value())
            self.storage_status.setText(f"保存先: {status}")
            self.accept()
        except (ConfigurationError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "設定を保存できません", str(exc))

    def _chatgpt_job_busy(self) -> bool:
        return self.chatgpt_panel.busy

    def accept(self) -> None:
        if self._matchup_ai_worker is not None:return
        if self._chatgpt_job_busy():
            QMessageBox.information(self, "ChatGPT処理中", "ChatGPTの処理が終わってから設定を閉じてください。")
            self._show_chatgpt_tab()
            return
        super().accept()

    def reject(self) -> None:
        if self._annotation_worker is not None:
            return
        if self._matchup_ai_worker is not None:return
        if self._chatgpt_job_busy():
            QMessageBox.information(self, "ChatGPT処理中", "ChatGPTの処理が終わってから設定を閉じてください。")
            self._show_chatgpt_tab()
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._annotation_worker is not None and self._annotation_worker.isRunning():
            self.team_status.setText("構築AI入力案の通信終了後に閉じてください")
            event.ignore()
            return
        # A live QThread is a child of the embedded panel. Destroying Settings
        # while it runs can crash Qt ("QThread: Destroyed while thread is still
        # running"). Never close the dialog until the worker cleans up.
        if self._chatgpt_job_busy():
            event.ignore()
            self._show_chatgpt_tab()
            return
        super().closeEvent(event)
