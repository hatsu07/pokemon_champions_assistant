"""Opt-in screen dataset recording and offline human-labelled review.

- Save bounded unlabelled JPEGs to disk; do not retain entire matches in RAM.
- Fixed max disk quota and minimum free space prevent runaway recording.
- Review with 100-file pages and promote only human-approved files to PNG.
- No network calls or game control. Saved labelled training data is immutable.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import os
from threading import Event
from time import monotonic

import cv2
import numpy as np
from PySide6.QtCore import QObject, Qt, QThread, QProcess, Signal, Slot, QSize
from PySide6.QtGui import QImage, QPixmap, QIcon
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QInputDialog, QPushButton,
    QScrollArea, QVBoxLayout, QWidget, QAbstractItemView, QPlainTextEdit,
    QListView,
)

from .app_config import AppSettingsStore, CaptureConfiguration, default_app_root
from .auto_match_capture import AutoCaptureUnavailable, capture_for_chatgpt
from .capture_settings import CapturePreviewService
from .screen_classifier import LABELS
from .screen_auto_label import analyze_staged_session, load_suggestion_model
from .screen_dataset import (
    LabelledScreenDataset, UnlabelledScreenStaging, StagingLimitReached,
    new_session_id, MIB,
)

SCREEN_LABELS={
    "MATCHMAKING":"マッチング待機・準備",
    "TEAM_SELECTION":"3匹を選ぶ選出画面",
    "MOVE_MENU":"技選択画面",
    "PARTY_MENU":"交代先選択画面",
    "BATTLE_EVENT":"攻撃・交代・メッセージ等の演出",
    "RESULT":"勝敗・対戦終了",
    "UNKNOWN":"その他・判別不能",
}
PAGE_SIZE=100


class _ReadOnlyCollectorWorker(QObject):
    """Capture/store in a worker thread; UI only sees small periodic previews."""
    preview=Signal(object)
    progress=Signal(object)
    error=Signal(str)
    finished=Signal()

    def __init__(self, config: CaptureConfiguration, *, session_id: str,
                 staging: UnlabelledScreenStaging, one_shot=False,
                 fps: int=5, capture=capture_for_chatgpt, clock=monotonic):
        super().__init__()
        if fps not in (2,5,10):
            raise ValueError("UNSUPPORTED_CAPTURE_FPS")
        self.config=config
        self.session_id=session_id
        self.staging=staging
        self.one_shot=one_shot
        self.fps=fps
        self.capture=capture
        self.clock=clock
        self._stop=Event()
        self.saved=0

    def stop(self):
        self._stop.set()

    @Slot()
    def run(self):
        last_preview=last_progress=float("-inf")
        try:
            while not self._stop.is_set():
                tick=self.clock()
                try:
                    frame=self.capture(self.config)
                    if not isinstance(frame,np.ndarray):
                        raise ValueError("CAPTURE_RESULT_INVALID")
                    self.staging.save_unlabelled(frame,self.session_id)
                    self.saved+=1
                    if self.clock()-last_preview>=0.5 or self.one_shot:
                        self.preview.emit(frame)
                        last_preview=self.clock()
                    if self.clock()-last_progress>=1.0 or self.one_shot:
                        self.progress.emit({
                            "saved":self.saved,"session_id":self.session_id,
                            "bytes":self.staging.used_bytes()})
                        last_progress=self.clock()
                except StagingLimitReached as exc:
                    self.error.emit(str(exc)+" / 容量上限に達したため収集停止")
                    break
                except AutoCaptureUnavailable as exc:
                    self.error.emit(str(exc)+" / 撮影失敗、再試行")
                except (OSError,ValueError,RuntimeError) as exc:
                    self.error.emit(type(exc).__name__+" / 撮影・保存失敗、再試行")
                if self.one_shot:
                    break
                self._stop.wait(max(0.0,1.0/self.fps-(self.clock()-tick)))
        finally:
            self.progress.emit({
                "saved":self.saved,"session_id":self.session_id,
                "bytes":self.staging.used_bytes()})
            self.finished.emit()


class _AutoLabelWorker(QObject):
    """Read-only, offline image analysis in a worker thread."""
    progress=Signal(object)
    error=Signal(str)
    proposals=Signal(object)
    finished=Signal()

    def __init__(self, paths, model_path=None):
        super().__init__()
        self.paths=tuple(paths)
        self.model_path=model_path

    @Slot()
    def run(self):
        try:
            classifier=(load_suggestion_model(self.model_path)
                        if self.model_path is not None else None)
            groups=analyze_staged_session(
                self.paths,classifier=classifier,
                progress=lambda done,total:self.progress.emit({"done":done,"total":total}))
            self.proposals.emit(groups)
        except (OSError,ValueError,RuntimeError,ImportError) as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.finished.emit()


class ScreenDatasetCollector(QMainWindow):
    """Capture labelled data in two separate opt-in steps."""

    def __init__(self, *, config_path: str|Path|None=None,
                 dataset_root: str|Path|None=None, capture=capture_for_chatgpt,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pokémon Champions — 学習画面収集（開発用・未ラベル一時保存）")
        self.resize(920,780)
        self._settings_store=AppSettingsStore(
            config_path or default_app_root()/"config"/"settings.json")
        self.capture_config=self._settings_store.load().settings.capture
        self.dataset=LabelledScreenDataset(
            dataset_root or default_app_root()/"private-training"/"screens")
        self.staging=UnlabelledScreenStaging(self.dataset.root)
        self.session_id=new_session_id()
        self._capture=capture
        self._thread=None
        self._worker=None
        self._last_capture_issue=None
        self._current_files=[]
        self._review_session=None
        self._page=0
        self._auto_thread=None
        self._auto_worker=None
        self._auto_groups=[]
        self._auto_frame_overrides={}
        self._auto_frame_exclusions=set()
        self._auto_reviewed_groups=set()
        self._auto_group_label_overrides={}
        self._auto_model_path=None
        self._auto_error=None
        self._gpu_process=None
        self._gpu_repo=""
        self._build()
        self._update_source_label()
        self._refresh_review_sessions()
        self._update_counts()

    def _build(self):
        scroll=QScrollArea(self)
        scroll.setWidgetResizable(True)
        body=QWidget()
        layout=QVBoxLayout(body)
        intro=QLabel(
            "画面収集は明示的な許可後に未ラベルJPEGとしてローカル自動保存します。"
            "150枚に達しても停止しません。保存先の容量上限・空き容量は保護されます。"
            "ChatGPT送信・ゲーム操作はありません。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.source_label=QLabel()
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)
        row=QHBoxLayout()
        self.reload_button=QPushButton("メインアプリの撮影設定を再読込")
        self.reload_button.clicked.connect(self._reload_capture)
        row.addWidget(self.reload_button)
        self.rebind_button=QPushButton("ミラーリングウィンドウを選び直す")
        self.rebind_button.clicked.connect(self._rebind_window)
        row.addWidget(self.rebind_button)
        layout.addLayout(row)
        self.directory_label=QLabel("保存先: "+str(self.dataset.root))
        self.directory_label.setWordWrap(True)
        layout.addWidget(self.directory_label)
        self.folder_button=QPushButton("保存先を変更")
        self.folder_button.clicked.connect(self._choose_folder)
        layout.addWidget(self.folder_button)
        self.session_label=QLabel("記録対象の対戦セッションID: "+self.session_id)
        layout.addWidget(self.session_label)
        self.session_button=QPushButton("次の対戦セッションを作成（過去の未ラベル画像は保持）")
        self.session_button.clicked.connect(self._new_session)
        layout.addWidget(self.session_button)

        controls=QHBoxLayout()
        controls.addWidget(QLabel("収集速度"))
        self.fps_box=QComboBox()
        for fps in (2,5,10):
            self.fps_box.addItem(f"{fps} fps（{int(1000/fps)}ms間隔）",fps)
        self.fps_box.setCurrentIndex(self.fps_box.findData(5))
        controls.addWidget(self.fps_box)
        controls.addWidget(QLabel("未ラベル画像の容量上限"))
        self.quota_box=QComboBox()
        for gib in (2,5,10):
            self.quota_box.addItem(f"{gib} GB",gib)
        self.quota_box.setCurrentIndex(self.quota_box.findData(2))
        self.quota_box.currentIndexChanged.connect(self._quota_changed)
        controls.addWidget(self.quota_box)
        layout.addLayout(controls)
        self.consent=QCheckBox(
            "このゲーム画面を未ラベル画像としてローカルディスクに自動保存することに同意する")
        layout.addWidget(self.consent)
        buttons=QHBoxLayout()
        self.start_button=QPushButton("自動収集開始（未ラベル保存）")
        self.start_button.clicked.connect(lambda:self._start(one_shot=False))
        buttons.addWidget(self.start_button)
        self.single_button=QPushButton("1枚撮影・未ラベル保存")
        self.single_button.clicked.connect(lambda:self._start(one_shot=True))
        buttons.addWidget(self.single_button)
        self.stop_button=QPushButton("収集停止")
        self.stop_button.clicked.connect(self._stop)
        buttons.addWidget(self.stop_button)
        layout.addLayout(buttons)
        self.status=QLabel("待機中 / 未ラベル画像はローカルのみ / AI送信なし")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.capture_count=QLabel("今回の収集0枚")
        layout.addWidget(self.capture_count)
        self.preview=QLabel("収集中の画面または選択した未ラベル画像を表示します")
        self.preview.setMinimumHeight(230)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet(
            "QLabel { background-color:#181b22;color:white;border:1px solid #666; }")
        layout.addWidget(self.preview)

        layout.addWidget(QLabel("対戦終了後のラベル付け：保存済み未ラベル画像をページ単位で表示"))
        rev=QHBoxLayout()
        self.review_session_box=QComboBox()
        self.review_session_box.currentIndexChanged.connect(self._review_session_changed)
        rev.addWidget(self.review_session_box)
        self.refresh_button=QPushButton("未ラベル画像を再読込")
        self.refresh_button.clicked.connect(self._refresh_review_sessions)
        rev.addWidget(self.refresh_button)
        layout.addLayout(rev)
        self.review_count=QLabel("未ラベル画像なし")
        layout.addWidget(self.review_count)
        self.review_list=QListWidget()
        self.review_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.review_list.setMinimumHeight(130)
        self.review_list.currentItemChanged.connect(self._show_current_frame)
        layout.addWidget(self.review_list)

        layout.addWidget(QLabel("自動ラベル候補：AIは提案のみ。承認まで正解データには入りません"))
        auto_buttons=QHBoxLayout()
        self.model_select_button=QPushButton("実験ONNXモデルを選択")
        self.model_select_button.clicked.connect(self._select_auto_model)
        auto_buttons.addWidget(self.model_select_button)
        self.auto_analyze_button=QPushButton("この対戦を解析・グループ化")
        self.auto_analyze_button.clicked.connect(self._start_auto_analysis)
        auto_buttons.addWidget(self.auto_analyze_button)
        layout.addLayout(auto_buttons)
        self.auto_model_label=QLabel("AI未設定：画像の類似グループ化のみ実行")
        self.auto_model_label.setWordWrap(True)
        layout.addWidget(self.auto_model_label)
        self.auto_progress=QLabel("未解析")
        layout.addWidget(self.auto_progress)
        self.auto_list=QListWidget()
        self.auto_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.auto_list.setToolTip(
            "Ctrl/Shiftで複数グループを選択し、確認済みグループをまとめてPNG保存できます。")
        self.auto_list.setMinimumHeight(160)
        self.auto_list.currentItemChanged.connect(self._on_group_selected)
        layout.addWidget(self.auto_list)
        self.auto_select_reviewed_button=QPushButton("確認済みグループをすべて選択")
        self.auto_select_reviewed_button.clicked.connect(self._select_all_reviewed_groups)
        layout.addWidget(self.auto_select_reviewed_button)
        layout.addWidget(QLabel(
            "Ctrl/Shiftで複数グループを選択、または「確認済みグループをすべて選択」。"
            "各グループを表示して全画像確認済みにしてから一括保存できます。"))
        reps=QHBoxLayout()
        self.auto_representatives=[]
        for title in ("先頭","中央","末尾"):
            column=QVBoxLayout()
            column.addWidget(QLabel(title))
            thumbnail=QLabel("未表示")
            thumbnail.setMinimumSize(150,100)
            thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumbnail.setStyleSheet("QLabel { background:#181b22;color:white; }")
            self.auto_representatives.append(thumbnail)
            column.addWidget(thumbnail)
            reps.addLayout(column)
        layout.addLayout(reps)
        all_frames_info=QLabel(
            "グループ内の全画像（最大30枚）を時系列で表示。⚠はAIの弱い予測や画面差分の注意箇所です。"
            "サムネイルをクリックすると上の大きなプレビューに表示されます。")
        all_frames_info.setWordWrap(True)
        layout.addWidget(all_frames_info)
        self.auto_frames_list=QListWidget()
        self.auto_frames_list.setViewMode(QListView.ViewMode.IconMode)
        self.auto_frames_list.setFlow(QListView.Flow.LeftToRight)
        self.auto_frames_list.setWrapping(True)
        self.auto_frames_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.auto_frames_list.setIconSize(QSize(145,88))
        self.auto_frames_list.setGridSize(QSize(175,134))
        self.auto_frames_list.setSpacing(3)
        self.auto_frames_list.setWordWrap(True)
        self.auto_frames_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.auto_frames_list.setMinimumHeight(340)
        self.auto_frames_list.currentItemChanged.connect(self._on_auto_frame_selected)
        layout.addWidget(self.auto_frames_list)
        # Keep the auto-label edit choice immediately beside the grouped
        # thumbnail grid. The old label_box below is for manual review only.
        auto_label_controls=QHBoxLayout()
        auto_label_controls.addWidget(QLabel("グループ／選択画像の訂正先ラベル"))
        self.auto_label_box=QComboBox()
        self.auto_label_box.setObjectName("autoGroupCorrectionLabel")
        self.auto_label_box.addItem("訂正先を選択",None)
        for label in LABELS:
            self.auto_label_box.addItem(f"{label} — {SCREEN_LABELS[label]}",label)
        self.auto_label_box.setMinimumWidth(280)
        self.auto_label_box.setToolTip(
            "選択した画像だけの訂正と、グループ全体のラベル訂正に共通です。"
            "従来の手動保存用ラベルとは独立しています。")
        auto_label_controls.addWidget(self.auto_label_box)
        self.auto_group_draft_button=QPushButton("グループラベルを訂正（保存は後で）")
        self.auto_group_draft_button.clicked.connect(self._stage_auto_group_label)
        auto_label_controls.addWidget(self.auto_group_draft_button)
        auto_label_controls.addStretch(1)
        layout.addLayout(auto_label_controls)
        correction_controls=QHBoxLayout()
        self.auto_frame_fix_button=QPushButton("選択した画像だけラベル訂正")
        self.auto_frame_fix_button.clicked.connect(self._correct_selected_auto_frames)
        correction_controls.addWidget(self.auto_frame_fix_button)
        self.auto_frame_exclude_button=QPushButton("選択画像を一括保存から除外（JPEGは保持）")
        self.auto_frame_exclude_button.clicked.connect(self._exclude_selected_auto_frames)
        correction_controls.addWidget(self.auto_frame_exclude_button)
        self.auto_frame_reset_button=QPushButton("選択画像の訂正・除外を取り消す")
        self.auto_frame_reset_button.clicked.connect(self._reset_selected_auto_frames)
        correction_controls.addWidget(self.auto_frame_reset_button)
        layout.addLayout(correction_controls)
        self.auto_review_confirm=QCheckBox("このグループの全画像を一覧で確認した（AIの予測だけでは正解になりません）")
        self.auto_review_confirm.toggled.connect(self._set_auto_group_reviewed)
        layout.addWidget(self.auto_review_confirm)
        approve_buttons=QHBoxLayout()
        self.auto_accept_button=QPushButton("選択グループをAI候補で承認・PNG保存")
        self.auto_accept_button.clicked.connect(lambda:self._approve_auto_groups(use_suggestion=True))
        approve_buttons.addWidget(self.auto_accept_button)
        self.auto_correct_button=QPushButton("選択ラベルに訂正してグループ保存")
        self.auto_correct_button.clicked.connect(lambda:self._approve_auto_groups(use_suggestion=False))
        approve_buttons.addWidget(self.auto_correct_button)
        layout.addLayout(approve_buttons)
        self.auto_bulk_button=QPushButton("選択した確認済みグループをまとめて承認・PNG保存")
        self.auto_bulk_button.setObjectName("autoBulkApproveReviewedGroups")
        self.auto_bulk_button.setToolTip(
            "Ctrl/Shiftで選択したグループを1度の確認で保存。"
            "未確認・ラベル未確定のグループがあれば保存しません。")
        self.auto_bulk_button.clicked.connect(self._approve_selected_auto_groups)
        layout.addWidget(self.auto_bulk_button)
        gpu_controls=QHBoxLayout()
        self.gpu_retrain_button=QPushButton("承認済みデータをGPU再学習（WSLで明示実行）")
        self.gpu_retrain_button.clicked.connect(self._start_gpu_retrain)
        gpu_controls.addWidget(self.gpu_retrain_button)
        self.gpu_command_button=QPushButton("GPU再学習コマンドをコピー")
        self.gpu_command_button.clicked.connect(self._show_retrain_instructions)
        gpu_controls.addWidget(self.gpu_command_button)
        layout.addLayout(gpu_controls)
        self.gpu_log=QPlainTextEdit()
        self.gpu_log.setReadOnly(True)
        self.gpu_log.setMaximumBlockCount(90)
        self.gpu_log.setMinimumHeight(100)
        self.gpu_log.setPlaceholderText("GPU再学習の出力・モデル保存先を表示します")
        layout.addWidget(self.gpu_log)
        page=QHBoxLayout()
        self.previous_button=QPushButton("前の100枚")
        self.previous_button.clicked.connect(self._previous_page)
        page.addWidget(self.previous_button)
        self.next_button=QPushButton("次の100枚")
        self.next_button.clicked.connect(self._next_page)
        page.addWidget(self.next_button)
        layout.addLayout(page)
        self.label_box=QComboBox()
        self.label_box.addItem("正解ラベルを選択してください",None)
        for label in LABELS:
            self.label_box.addItem(f"{label} — {SCREEN_LABELS[label]}",label)
        layout.addWidget(self.label_box)
        labels=QHBoxLayout()
        self.save_button=QPushButton("選択画像を正解ラベルでPNG保存")
        self.save_button.clicked.connect(self._save_selected)
        labels.addWidget(self.save_button)
        self.discard_button=QPushButton("選択した未ラベル画像を破棄")
        self.discard_button.clicked.connect(self._discard_selected)
        labels.addWidget(self.discard_button)
        layout.addLayout(labels)
        self.relabel_button=QPushButton("保存済み正解ラベルを訂正")
        self.relabel_button.clicked.connect(self._relabel_saved)
        layout.addWidget(self.relabel_button)
        self.audit_label=QLabel()
        self.audit_label.setWordWrap(True)
        layout.addWidget(self.audit_label)
        self.audit_button=QPushButton("ラベル別件数を再集計")
        self.audit_button.clicked.connect(self._update_counts)
        layout.addWidget(self.audit_button)
        note=QLabel(
            "未ラベルは screens/_UNLABELLED/<対戦ID>/ に保存されます。"
            "人がラベルを確定した画像だけ screens/<LABEL>/<対戦ID>__*.png に変換します。"
            "同じ対戦で途中再開しても対戦IDを変えず、次の対戦でのみ新規セッションを作成してください。")
        note.setWordWrap(True)
        layout.addWidget(note)
        scroll.setWidget(body)
        self.setCentralWidget(scroll)
        self._set_capture_busy(False)

    def _set_capture_busy(self, busy):
        busy=busy or self._auto_thread is not None or self._gpu_process is not None
        for item in (self.start_button,self.single_button,self.reload_button,
                     self.rebind_button,self.session_button,self.folder_button,
                     self.quota_box,self.fps_box,self.refresh_button,
                     self.save_button,self.discard_button,self.relabel_button,
                     self.model_select_button,self.auto_analyze_button,
                     self.auto_accept_button,self.auto_correct_button,
                     self.gpu_retrain_button,self.gpu_command_button,
                     self.auto_frame_fix_button,self.auto_frame_exclude_button,
                     self.auto_frame_reset_button,self.auto_review_confirm,
                     self.auto_label_box,self.auto_group_draft_button,
                     self.auto_bulk_button,self.auto_select_reviewed_button):
            item.setEnabled(not busy)
        self.stop_button.setEnabled(self._thread is not None)

    def _quota_changed(self, _index):
        if self._thread is None:
            gib=self.quota_box.currentData()
            if gib:
                self.staging.quota_bytes=int(gib)*1024**3

    def _update_source_label(self):
        config=self.capture_config
        target=config.selected_window_identity or {}
        title=target.get("title") or ("ADB: "+str(config.adb_serial)
                                    if config.adb_serial else "未指定")
        self.source_label.setText(
            f"撮影方式: {config.backend} / 対象: {title} / "
            f"ROI: {config.game_viewport_normalized or '未設定'}")

    def _reload_capture(self):
        if self._thread is not None:return
        self.capture_config=self._settings_store.load().settings.capture
        self._update_source_label()
        self.status.setText("キャプチャ設定を再読込しました")

    def _rebind_window(self):
        if self._thread is not None or self.capture_config.backend!="WINDOWS_NATIVE":
            self.status.setText("Windowsネイティブ撮影・収集停止中のみ対象を変更できます")
            return
        candidates=CapturePreviewService().list_windows()
        if not candidates:
            self.status.setText("撮影対象ウィンドウがありません")
            return
        names=[w.display_name() for w in candidates]
        picked,accepted=QInputDialog.getItem(
            self,"ミラーリング対象を選択","現在表示しているウィンドウを選択",names,0,False)
        if not accepted:return
        chosen=candidates[names.index(picked)]
        updated=replace(self.capture_config,selected_window_identity=chosen.identity())
        from dataclasses import replace as replace_settings
        old=self._settings_store.load().settings
        self._settings_store.save(replace_settings(old,capture=updated))
        self.capture_config=updated
        self._update_source_label()
        self.status.setText("対象ウィンドウ変更済み。撮影して確認してください")

    def _choose_folder(self):
        if self._thread is not None or self._auto_thread is not None or self._gpu_process is not None:return
        value=QFileDialog.getExistingDirectory(
            self,"保存先を選択",str(self.dataset.root.parent))
        if value:
            # Cached proposals belong to the old dataset, even if a session id repeats.
            self._clear_auto_groups()
            self._review_session=None
            self.dataset=LabelledScreenDataset(value)
            self.staging=UnlabelledScreenStaging(value,
                       quota_bytes=int(self.quota_box.currentData())*1024**3)
            self.directory_label.setText("保存先: "+str(self.dataset.root))
            self._refresh_review_sessions()
            self._update_counts()

    def _new_session(self):
        if self._thread is not None or self._auto_thread is not None or self._gpu_process is not None:return
        self.session_id=new_session_id()
        self.session_label.setText("記録対象の対戦セッションID: "+self.session_id)
        self.capture_count.setText("今回の収集0枚")
        self.status.setText("新しい対戦セッション作成済み。過去画像は残っています")

    def _start(self, *, one_shot: bool):
        if self._thread is not None or self._auto_thread is not None or self._gpu_process is not None:return
        if not self.consent.isChecked():
            self.status.setText("未ラベル画像のディスク自動保存への同意が必要です")
            return
        if self.capture_config.game_viewport_normalized is None:
            self.status.setText("ゲームの撮影領域ROIが未設定です")
            return
        if (self.capture_config.backend=="WINDOWS_NATIVE"
                and not self.capture_config.selected_window_identity):
            self.status.setText("撮影ウィンドウが未指定です")
            return
        thread=QThread(self)
        worker=_ReadOnlyCollectorWorker(
            self.capture_config,session_id=self.session_id,
            staging=self.staging,one_shot=one_shot,
            fps=int(self.fps_box.currentData()),capture=self._capture)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.preview.connect(self._on_preview)
        worker.progress.connect(self._on_capture_progress)
        worker.error.connect(self._on_capture_error)
        worker.finished.connect(thread.quit,Qt.ConnectionType.DirectConnection)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_worker_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread=thread
        self._worker=worker
        self._last_capture_issue=None
        self._set_capture_busy(True)
        self.status.setText(
            "1枚の未ラベル保存中" if one_shot else
            f"収集中（{self.fps_box.currentData()}fps / 未ラベルディスク保存）")
        thread.start()

    def _stop(self):
        if self._worker is not None:self._worker.stop()
        self.stop_button.setEnabled(False)
        if self._thread is not None and self._last_capture_issue is None:
            self.status.setText("収集停止処理中（未ラベル画像は保持）")

    @Slot(object)
    def _on_capture_progress(self, report):
        self.capture_count.setText(
            f"今回の収集 {report['saved']} 枚 / 未ラベル領域 {report['bytes']/MIB:.1f} MiB / "
            f"容量上限 {self.staging.quota_bytes/(1024**3):g} GB")

    @Slot(object)
    def _on_preview(self,image):
        self._render_preview(image)

    def _render_preview(self,image):
        height,width=image.shape[:2]
        rgb=np.ascontiguousarray(image)
        qimg=QImage(rgb.data,width,height,rgb.strides[0],QImage.Format.Format_RGB888).copy()
        pix=QPixmap.fromImage(qimg).scaled(
            max(1,self.preview.width()-8),max(1,self.preview.height()-8),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(pix)

    @Slot(str)
    def _on_capture_error(self, reason):
        self._last_capture_issue=reason
        self.status.setText("撮影・保存エラー: "+reason)

    @Slot()
    def _on_worker_finished(self):
        self._thread=None
        self._worker=None
        self._set_capture_busy(False)
        if self._last_capture_issue is None:
            self.status.setText("収集停止。画像は未ラベルとしてローカル保存済み")
        self._refresh_review_sessions(select=self.session_id)

    def _refresh_review_sessions(self, *, select=None):
        self.review_session_box.blockSignals(True)
        current=select or self.review_session_box.currentData()
        self.review_session_box.clear()
        sessions=self.staging.sessions()
        if not sessions:
            self.review_session_box.addItem("未ラベル画像なし",None)
        else:
            for session in sessions:
                self.review_session_box.addItem(session,session)
            if current in sessions:
                self.review_session_box.setCurrentIndex(
                    self.review_session_box.findData(current))
        self.review_session_box.blockSignals(False)
        self._review_session_changed(0)

    def _review_session_changed(self,_index):
        if self._auto_thread is not None:
            return
        selected_session=self.review_session_box.currentData()
        if selected_session!=self._review_session:
            # A different match cannot reuse another match's AI suggestions.
            self._clear_auto_groups()
            self._page=0
        self._review_session=selected_session
        self._reload_review_files()

    def _reload_review_files(self):
        # A save changes staged JPGs, not the predictions of the remaining
        # frames. Never discard all proposals and require an expensive re-run.
        self._current_files=(self.staging.session_files(self._review_session)
                             if self._review_session else [])
        self._reconcile_auto_groups(set(self._current_files))
        self._show_review_page()

    def _show_review_page(self):
        n=len(self._current_files)
        max_page=max(0,(n-1)//PAGE_SIZE)
        self._page=min(max_page,max(0,self._page))
        visible=self._current_files[self._page*PAGE_SIZE:(self._page+1)*PAGE_SIZE]
        self.review_list.clear()
        for path in visible:
            item=QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole,str(path))
            self.review_list.addItem(item)
        self.review_count.setText(
            f"未ラベル {n} 枚 / ページ {self._page+1}/{max_page+1} / "
            f"1ページ最大{PAGE_SIZE}枚（150枚停止なし）")
        self.previous_button.setEnabled(self._page>0)
        self.next_button.setEnabled(self._page<max_page)
        if visible and self._thread is None:
            self.review_list.setCurrentRow(0)

    def _previous_page(self):
        self._page-=1
        self._show_review_page()

    def _next_page(self):
        self._page+=1
        self._show_review_page()

    @Slot(object,object)
    def _show_current_frame(self,current,_previous):
        if current is None or self._thread is not None:return
        path=Path(current.data(Qt.ItemDataRole.UserRole))
        if not path.is_file():return
        bgr=cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_COLOR)
        if bgr is None:
            self.status.setText("未ラベル画像が壊れている可能性があります")
            return
        self._render_preview(cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB))

    def _selected_staged_paths(self):
        selected=self.review_list.selectedItems()
        if not selected and self.review_list.currentItem() is not None:
            selected=[self.review_list.currentItem()]
        return [Path(i.data(Qt.ItemDataRole.UserRole)) for i in selected]

    def _save_selected(self):
        if self._thread is not None or self._auto_thread is not None or self._gpu_process is not None:return
        label=self.label_box.currentData()
        frames=self._selected_staged_paths()
        if label not in LABELS or not frames:
            self.status.setText("画像と正解ラベルを選択してください")
            return
        accepted=QMessageBox.question(
            self,"学習データへの変換を確認",
            f"{len(frames)}枚を確認済みラベル {label} のPNGへ保存しますか？",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        if accepted!=QMessageBox.StandardButton.Yes:return
        saved=0
        for file in frames:
            try:
                self.staging.label(file,label,self.dataset)
                saved+=1
            except (OSError,ValueError) as exc:
                self.status.setText(
                    f"{saved}枚成功、エラー：{type(exc).__name__}。未処理画像は保持")
                break
        else:
            self.status.setText(f"{saved}枚を {label} に分類保存しました")
        self._reload_review_files()
        self._update_counts()

    def _discard_selected(self):
        if self._thread is not None or self._auto_thread is not None or self._gpu_process is not None:return
        frames=self._selected_staged_paths()
        if not frames:return
        accepted=QMessageBox.question(
            self,"未ラベル画像の削除確認",
            f"選択した{len(frames)}枚を完全に削除しますか？元に戻せません。",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        if accepted!=QMessageBox.StandardButton.Yes:return
        removed=0
        try:
            for file in frames:
                self.staging.remove_staged(file)
                removed+=1
        except (OSError,ValueError) as exc:
            self.status.setText(f"{removed}枚削除後にエラー: {type(exc).__name__}")
        else:
            self.status.setText(f"未ラベル画像 {removed}枚を削除しました")
        self._reload_review_files()

    def _relabel_saved(self):
        if self._thread is not None or self._auto_thread is not None or self._gpu_process is not None:return
        path,_=QFileDialog.getOpenFileName(
            self,"保存済み画像の正解ラベルを訂正",str(self.dataset.root),"画像 (*.png)")
        if not path:return
        chosen,ok=QInputDialog.getItem(
            self,"正解ラベル","正しいラベル",list(LABELS),0,False)
        if not ok:return
        try:
            self.dataset.relabel(path,chosen)
        except (ValueError,OSError) as exc:
            self.status.setText("ラベル変更失敗: "+type(exc).__name__)
        else:
            self.status.setText("ラベルを "+chosen+" に訂正しました")
        self._update_counts()

    def _update_counts(self):
        audit=self.dataset.audit()
        items=[f"{label}:{audit['counts'][label]}枚"
               f"（{audit['session_counts'][label]}対戦）" for label in LABELS]
        self.audit_label.setText(
            f"保存済み合計 {audit['total']} 枚 / 対戦セッション {audit['all_sessions']} 件\n"
            +"  |  ".join(items))

    def _clear_auto_groups(self):
        self._auto_groups=[]
        self._auto_frame_overrides.clear()
        self._auto_frame_exclusions.clear()
        self._auto_reviewed_groups.clear()
        self._auto_group_label_overrides.clear()
        if hasattr(self,"auto_list"):
            self.auto_list.clear()
            self.auto_frames_list.clear()
            self.auto_review_confirm.setChecked(False)
            for thumb in self.auto_representatives:
                thumb.clear()
                thumb.setText("未表示")

    def _select_auto_model(self):
        if self._thread is not None or self._auto_thread is not None or self._gpu_process is not None:return
        path,_=QFileDialog.getOpenFileName(
            self,"実験用ONNXモデルを選択（同じ場所にJSON必須）",
            str(self.dataset.root.parent),
            "ONNX model (champions_screen_classifier.onnx)")
        if path:
            self._auto_model_path=Path(path)
            self.auto_model_label.setText(
                "開発用モデル: "+str(self._auto_model_path)+
                " / validated=falseは候補生成にのみ使用")

    def _start_auto_analysis(self):
        if self._thread is not None or self._auto_thread is not None or self._gpu_process is not None:return
        if self._review_session is None:
            self.auto_progress.setText("解析する対戦を選択してください")
            return
        paths=self.staging.session_files(self._review_session)
        if not paths:
            self.auto_progress.setText("未ラベルJPEGがありません")
            return
        self._clear_auto_groups()
        self._auto_error=None
        thread=QThread(self)
        worker=_AutoLabelWorker(paths,self._auto_model_path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._auto_on_progress)
        worker.error.connect(self._auto_on_error)
        worker.proposals.connect(self._auto_on_proposals)
        worker.finished.connect(thread.quit,Qt.ConnectionType.DirectConnection)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._auto_on_finished)
        thread.finished.connect(thread.deleteLater)
        self._auto_thread=thread
        self._auto_worker=worker
        self._set_capture_busy(True)
        self.review_session_box.setEnabled(False)
        self.auto_progress.setText(f"解析開始: {len(paths)}枚（ローカル処理・画像送信なし）")
        thread.start()

    @Slot(object)
    def _auto_on_progress(self,info):
        self.auto_progress.setText(
            f"解析中 {info['done']} / {info['total']}枚。未確認ラベルは保存しません")

    @Slot(str)
    def _auto_on_error(self,reason):
        self._auto_error=reason
        self.auto_progress.setText("自動解析失敗: "+reason)

    @Slot(object)
    def _auto_group_row_text(self, index: int) -> str:
        group=self._auto_groups[index]
        candidate=self._auto_group_label_overrides.get(index,group.candidate)
        status="✓確認済" if index in self._auto_reviewed_groups else "要確認"
        changed="（訂正済）" if index in self._auto_group_label_overrides else ""
        return (f"{index+1:03d} | {status} | {candidate or '候補不明'}{changed} | "
                f"{group.count}枚 | 信頼度下限 {group.confidence:.1%}")

    def _update_auto_group_row(self,index: int):
        item=self.auto_list.item(index)
        if item is not None:
            item.setText(self._auto_group_row_text(index))

    def _render_auto_group_list(self, *, selection: int=0):
        """Repaint from cached suggestions without invoking the model."""
        self.auto_list.clear()
        for index,_group in enumerate(self._auto_groups):
            row=QListWidgetItem(self._auto_group_row_text(index))
            row.setData(Qt.ItemDataRole.UserRole,index)
            self.auto_list.addItem(row)
        if self._auto_groups:
            self.auto_list.setCurrentRow(min(max(0,selection),len(self._auto_groups)-1))

    def _reconcile_auto_groups(self, staged_files: set[Path]):
        """Prune already promoted/deleted JPGs, retain all untouched proposals.

        Remaining groups preserve their per-frame AI evidence and corrections.
        Only modified groups lose their 'fully reviewed' acknowledgement.
        No ONNX inference or grouping work is performed here.
        """
        if not self._auto_groups:
            return
        current,_=self._current_auto_group()
        old_groups=self._auto_groups
        old_reviewed=set(self._auto_reviewed_groups)
        retained=[]
        reviewed=set()
        remapped={}
        selected=None
        for original_index,group in enumerate(old_groups):
            files=tuple(path for path in group.files if path in staged_files)
            if not files:
                continue
            if files==group.files:
                remaining_group=group
            else:
                # Keep cached per-frame evidence and only recalculate the
                # representative thumbnails / displayed minimum confidence.
                details=tuple(d for d in group.frame_details if d.path in staged_files)
                reps=(files if len(files)<3 else
                      (files[0],files[len(files)//2],files[-1]))
                remaining_group=replace(
                    group, files=files, representative=tuple(reps),
                    frame_details=details,
                    confidence=(min(d.confidence for d in details)
                                if details else group.confidence),
                    margin=(min(d.margin for d in details)
                            if details else group.margin))
            new_index=len(retained)
            retained.append(remaining_group)
            remapped[original_index]=new_index
            if (original_index in old_reviewed and
                    files==group.files):
                reviewed.add(new_index)
            if original_index==current:
                selected=new_index
        self._auto_groups=retained
        live_group_paths={path for group in retained for path in group.files}
        self._auto_frame_overrides={
            path:label for path,label in self._auto_frame_overrides.items()
            if path in live_group_paths}
        self._auto_frame_exclusions.intersection_update(live_group_paths)
        self._auto_reviewed_groups=reviewed
        self._auto_group_label_overrides={
            remapped[old_index]:label
            for old_index,label in self._auto_group_label_overrides.items()
            if old_index in remapped}
        # If the selected group was completed, select the next group in place
        # (or the previous one if the completed group was last).
        self._render_auto_group_list(
            selection=(selected if selected is not None else
                       (current if current is not None else 0)))

    def _auto_on_proposals(self,groups):
        self._auto_frame_overrides.clear()
        self._auto_frame_exclusions.clear()
        self._auto_reviewed_groups.clear()
        self._auto_group_label_overrides.clear()
        self._auto_groups=list(groups)
        self._render_auto_group_list()
        self.auto_progress.setText(
            f"解析完了：{sum(g.count for g in groups)}枚 / {len(groups)}グループ。"
            "グループ内の全画像を一覧確認してから一括承認してください。")

    @Slot()
    def _auto_on_finished(self):
        self._auto_thread=None
        self._auto_worker=None
        self._set_capture_busy(False)
        self.review_session_box.setEnabled(True)

    @Slot(object,object)
    def _on_group_selected(self,current,_previous):
        self.auto_frames_list.clear()
        for thumb in self.auto_representatives:
            thumb.clear()
            thumb.setText("未表示")
        self.auto_review_confirm.blockSignals(True)
        self.auto_review_confirm.setChecked(False if current is None else
            current.data(Qt.ItemDataRole.UserRole) in self._auto_reviewed_groups)
        self.auto_review_confirm.blockSignals(False)
        if current is None:return
        index=current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(index,int) or index>=len(self._auto_groups):return
        group=self._auto_groups[index]
        for thumb,path in zip(self.auto_representatives,group.representative):
            try:
                frame=cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_COLOR)
                if frame is None:continue
                rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
                height,width=rgb.shape[:2]
                qimg=QImage(rgb.data,width,height,rgb.strides[0],
                            QImage.Format.Format_RGB888).copy()
                thumb.setPixmap(QPixmap.fromImage(qimg).scaled(
                    170,110,Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
            except (OSError,ValueError):
                thumb.setText("画像読込不可")
        self._populate_auto_frames(group)

    def _current_auto_group(self):
        item=self.auto_list.currentItem()
        if item is None:
            return None,None
        index=item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(index,int) or not 0<=index<len(self._auto_groups):
            return None,None
        return index,self._auto_groups[index]

    def _populate_auto_frames(self,group):
        self.auto_frames_list.clear()
        details={detail.path:detail for detail in group.frame_details}
        for number,path in enumerate(group.files,start=1):
            detail=details.get(path)
            override=self._auto_frame_overrides.get(path)
            excluded=path in self._auto_frame_exclusions
            candidate=(override or
                       (detail.predicted_label if detail is not None else group.candidate))
            confidence=detail.confidence if detail is not None else group.confidence
            warnings=detail.review_flags if detail is not None else ()
            if excluded:
                status="除外"
            elif override is not None:
                status="訂正済"
            elif warnings or group.candidate is None:
                status="⚠要確認"
            else:
                status="AI候補"
            text=f"{number:02d} {status}\n{candidate or '未判定'} {confidence:.0%}"
            item=QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole,str(path))
            item.setToolTip(
                f"{path.name}\n予測:{(detail.predicted_label if detail else group.candidate) or 'なし'}\n"
                f"注意:{', '.join(warnings) if warnings else 'なし'}\n"
                f"部分訂正:{override or 'なし'} / 除外:{excluded}")
            try:
                bgr=cv2.imdecode(np.fromfile(path,dtype=np.uint8),cv2.IMREAD_COLOR)
                if bgr is None:
                    item.setText(f"{number:02d} ⚠画像読込不可")
                else:
                    rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
                    height,width=rgb.shape[:2]
                    qimg=QImage(rgb.data,width,height,rgb.strides[0],
                                QImage.Format.Format_RGB888).copy()
                    item.setIcon(QIcon(QPixmap.fromImage(qimg).scaled(
                        145,88,Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)))
            except (OSError,ValueError):
                item.setText(f"{number:02d} ⚠画像読込不可")
            self.auto_frames_list.addItem(item)

    @Slot(object,object)
    def _on_auto_frame_selected(self,current,_previous):
        if current is None:return
        # Existing full-size preview lets users inspect any middle frame.
        self._show_current_frame(current,None)

    def _selected_auto_frame_paths(self):
        return [Path(item.data(Qt.ItemDataRole.UserRole))
                for item in self.auto_frames_list.selectedItems()]

    def _invalidate_auto_review(self):
        index,group=self._current_auto_group()
        if group is None:return
        self._auto_reviewed_groups.discard(index)
        self.auto_review_confirm.setChecked(False)
        self._update_auto_group_row(index)
        self._populate_auto_frames(group)

    def _set_auto_group_reviewed(self,checked):
        index,group=self._current_auto_group()
        if group is None:return
        if checked:self._auto_reviewed_groups.add(index)
        else:self._auto_reviewed_groups.discard(index)
        self._update_auto_group_row(index)

    def _stage_auto_group_label(self):
        if self._thread is not None or self._auto_thread is not None or self._gpu_process is not None:return
        index,group=self._current_auto_group()
        label=self.auto_label_box.currentData()
        if group is None or label not in LABELS:
            self.auto_progress.setText("グループと訂正先ラベルを選んでください")
            return
        self._auto_group_label_overrides[index]=label
        self._invalidate_auto_review()
        self.auto_progress.setText(
            f"グループ{index+1}を{label}に仮訂正しました。"
            "全画像を再確認してチェックすると、一括保存の対象にできます。")

    def _correct_selected_auto_frames(self):
        if self._thread is not None or self._auto_thread is not None or self._gpu_process is not None:return
        chosen=self.auto_label_box.currentData()
        paths=self._selected_auto_frame_paths()
        if chosen not in LABELS or not paths:
            self.auto_progress.setText("訂正する画像を選択し、正解ラベルをプルダウンで指定してください")
            return
        for path in paths:
            self._auto_frame_overrides[path]=chosen
            self._auto_frame_exclusions.discard(path)
        self._invalidate_auto_review()
        self.auto_progress.setText(f"{len(paths)}枚を{chosen}に部分訂正。全画像の再確認後に保存してください")

    def _exclude_selected_auto_frames(self):
        if self._thread is not None or self._auto_thread is not None or self._gpu_process is not None:return
        paths=self._selected_auto_frame_paths()
        if not paths:
            self.auto_progress.setText("除外する画像を選択してください")
            return
        self._auto_frame_exclusions.update(paths)
        self._invalidate_auto_review()
        self.auto_progress.setText(f"{len(paths)}枚を保存対象から除外。元のJPEGは保持します")

    def _reset_selected_auto_frames(self):
        if self._thread is not None or self._auto_thread is not None or self._gpu_process is not None:return
        paths=self._selected_auto_frame_paths()
        if not paths:
            self.auto_progress.setText("元に戻す画像を選択してください")
            return
        for path in paths:
            self._auto_frame_overrides.pop(path,None)
            self._auto_frame_exclusions.discard(path)
        self._invalidate_auto_review()
        self.auto_progress.setText(f"{len(paths)}枚をAI候補・未除外状態に戻しました")

    def _approve_auto_groups(self, *, use_suggestion: bool):
        """Preserve the existing current-group buttons for compatibility."""
        # Keep the PR #30 multi-selection action compatible with this dedicated batch UI.
        if use_suggestion and len(self.auto_list.selectedItems()) > 1:
            self._approve_selected_auto_groups()
            return
        index,group=self._current_auto_group()
        if group is None:
            self.auto_progress.setText("承認するグループを選択してください")
            return
        forced={}
        if not use_suggestion:
            label=self.auto_label_box.currentData()
            if label not in LABELS:
                self.auto_progress.setText("サムネイル直下の訂正先ラベルを選択してください")
                return
            forced[index]=label
        self._commit_auto_groups([index],forced=forced)

    def _select_all_reviewed_groups(self):
        if self._thread is not None or self._auto_thread is not None or self._gpu_process is not None:
            return
        self.auto_list.clearSelection()
        for index in sorted(self._auto_reviewed_groups):
            item=self.auto_list.item(index)
            if item is not None:
                item.setSelected(True)
        self.auto_progress.setText(
            f"確認済み{len(self.auto_list.selectedItems())}グループを選択しました。"
            "一括保存ボタンで保存前にラベルを検査します。")

    def _select_all_reviewed_auto_groups(self):
        """Compatibility with PR #30 select-all-reviewed control/tests."""
        self._select_all_reviewed_groups()

    def _approve_selected_auto_groups(self):
        """Batch selected *individually reviewed* groups, preserving each label."""
        indices=sorted({
            item.data(Qt.ItemDataRole.UserRole) for item in self.auto_list.selectedItems()
            if isinstance(item.data(Qt.ItemDataRole.UserRole),int)})
        self._commit_auto_groups(indices)

    def _commit_auto_groups(self,indices: list[int],*,forced: dict[int,str]|None=None):
        if self._thread is not None or self._auto_thread is not None or self._gpu_process is not None:
            return
        if not indices:
            self.auto_progress.setText("保存するグループをCtrl/Shiftで選択してください")
            return
        if len(set(indices))!=len(indices) or any(
                index<0 or index>=len(self._auto_groups) for index in indices):
            self.auto_progress.setText("グループ一覧が更新されました。選択し直してください")
            return
        unchecked=[index+1 for index in indices
                   if index not in self._auto_reviewed_groups]
        if unchecked:
            self.auto_progress.setText(
                "未確認グループが含まれます："+", ".join(map(str,unchecked))+
                "。各グループの全画像を確認してチェックしてください")
            return
        forced=forced or {}
        requested=[]
        corrected=0
        excluded=0
        for index in indices:
            group=self._auto_groups[index]
            base=forced.get(
                index,self._auto_group_label_overrides.get(index,group.candidate))
            group_paths=[
                (path,self._auto_frame_overrides.get(path,base))
                for path in group.files if path not in self._auto_frame_exclusions]
            if not group_paths:
                self.auto_progress.setText(
                    f"グループ{index+1}は全画像が除外されています。保存できません")
                return
            if any(label not in LABELS for _,label in group_paths):
                self.auto_progress.setText(
                    f"グループ{index+1}にラベル未確定画像があります。"
                    "グループまたは画像単位で訂正してください")
                return
            requested.extend(group_paths)
            corrected+=sum(path in self._auto_frame_overrides for path,_ in group_paths)
            excluded+=group.count-len(group_paths)
        if len({path for path,_ in requested})!=len(requested):
            self.auto_progress.setText("選択グループ間に画像の重複があります。再解析してください")
            return
        accepted=QMessageBox.question(
            self,"確認済みグループを一括PNG保存",
            f"{len(indices)}グループ・{len(requested)}枚をPNG保存します。"
            f" 個別訂正{corrected}枚、除外{excluded}枚。\n"
            "各グループの確定ラベルと個別訂正を使います。"
            "選択グループの全画像を確認しましたか？\n"
            "AIの予測だけでは正解とみなせません。"
            "除外したJPEGは保存せず、そのまま保持します。",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        if accepted!=QMessageBox.StandardButton.Yes:
            return
        completed=0
        try:
            for path,label in requested:
                self.staging.label(path,label,self.dataset)
                completed+=1
        except (OSError,ValueError) as exc:
            message=(f"{len(indices)}グループ中{completed}/{len(requested)}枚保存後に"
                     f"{type(exc).__name__}で停止。未保存JPEGは保持しました")
        else:
            message=(f"{len(indices)}グループ・{completed}枚をPNG保存しました。"
                     f"除外{excluded}枚のJPEGは保持しました")
        # Exactly one refresh after a successful/partially successful batch;
        # per-group corrections for unaffected frames remain cached.
        self._reload_review_files()
        self._update_counts()
        self.auto_progress.setText(message)

    def _show_retrain_instructions(self):
        if self._thread is not None or self._auto_thread is not None or self._gpu_process is not None:return
        command="bash ./scripts/train_screen_classifier_docker.sh --gpu --epochs 12 --pretrained --archive-output"
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(command)
        QMessageBox.information(
            self,"GPU再学習（確認済みPNGのみ）",
            "WSLのリポジトリで、コピーした次のコマンドを明示的に実行してください。\n"
            +command+"\n"
            "ラベルを承認したPNGのみ学習されます。生成モデルを自動採用せず、"
            "検証指標と独立した対戦で比較してから次回の候補生成に使ってください。"
            "新しいモデルは private-training/output/run-*/ に保存されます。")

    def _start_gpu_retrain(self):
        # Explicit user-initiated WSL action: no unattended model retraining.
        if self._thread is not None or self._auto_thread is not None or self._gpu_process is not None:
            return
        if os.name != "nt":
            self.gpu_log.appendPlainText(
                "Windowsの収集アプリで使用してください。WSLではコピーしたコマンドを実行します。")
            return
        repo,accepted=QInputDialog.getText(
            self,"WSLのリポジトリパス",
            "WSLのLinux絶対パスを入力（WSLターミナルの pwd の結果）",
            text=self._gpu_repo)
        if not accepted:return
        repo=repo.strip()
        if not repo.startswith("/") or "\x00" in repo or "\n" in repo:
            self.gpu_log.appendPlainText("WSLの絶対パスが必要です")
            return
        yes=QMessageBox.question(
            self,"GPUで再学習しますか？",
            "人間が承認したPNGでGPU学習します（ImageNet事前学習済み重み使用）。"
            "ユーザーの画像はアップロードしません。"
            "出力はWSLのprivate-training/output/run-*/に別保存されます。"
            "結果は自動採用されません。開始しますか？",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        if yes!=QMessageBox.StandardButton.Yes:return
        self._gpu_repo=repo
        self.gpu_log.clear()
        self.gpu_log.appendPlainText("GPU学習を起動します / WSL: "+repo)
        proc=QProcess(self)
        proc.setProgram("wsl.exe")
        proc.setArguments([
            "--cd",repo,"--exec","bash","./scripts/train_screen_classifier_docker.sh",
            "--gpu","--epochs","12","--pretrained","--archive-output"])
        proc.readyReadStandardOutput.connect(
            lambda:self._append_gpu_output(bytes(proc.readAllStandardOutput())))
        proc.readyReadStandardError.connect(
            lambda:self._append_gpu_output(bytes(proc.readAllStandardError())))
        proc.errorOccurred.connect(self._gpu_error)
        proc.finished.connect(self._gpu_finished)
        self._gpu_process=proc
        self._set_capture_busy(True)
        proc.start()

    def _append_gpu_output(self,data):
        if not data:return
        self.gpu_log.appendPlainText(data.decode("utf-8",errors="replace").strip())

    @Slot(object)
    def _gpu_error(self,error):
        self.gpu_log.appendPlainText(f"WSL GPU学習エラー: {error}")
        if (self._gpu_process is not None and
                self._gpu_process.state()==QProcess.ProcessState.NotRunning):
            self._gpu_process.deleteLater()
            self._gpu_process=None
            self._set_capture_busy(False)

    @Slot(int,object)
    def _gpu_finished(self,code,_status):
        self.gpu_log.appendPlainText(
            "GPU学習終了: "+("成功（出力先と指標を確認してください）" if code==0
                         else f"失敗 exit={code}（出力ログを確認してください）"))
        if self._gpu_process is not None:
            self._gpu_process.deleteLater()
            self._gpu_process=None
        self._set_capture_busy(False)

    def closeEvent(self,event):
        if (self._gpu_process is not None and
                self._gpu_process.state()!=QProcess.ProcessState.NotRunning):
            event.ignore()
            self.gpu_log.appendPlainText("GPU再学習中です。学習終了後にウィンドウを閉じてください。")
            return
        if self._auto_thread is not None and self._auto_thread.isRunning():
            event.ignore()
            self.auto_progress.setText("解析中です。解析完了後に閉じてください")
            return
        self._stop()
        if self._thread is not None and self._thread.isRunning():
            if not self._thread.wait(5000):
                event.ignore()
                self.status.setText("収集停止を確認してから閉じてください")
                return
        super().closeEvent(event)
