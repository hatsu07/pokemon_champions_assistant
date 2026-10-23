"""Opt-in screen dataset recording and offline human-labelled review.

- Save bounded unlabelled JPEGs to disk; do not retain entire matches in RAM.
- Fixed max disk quota and minimum free space prevent runaway recording.
- Review with 100-file pages and promote only human-approved files to PNG.
- No network calls or game control. Saved labelled training data is immutable.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event
from time import monotonic

import cv2
import numpy as np
from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QInputDialog, QPushButton,
    QScrollArea, QVBoxLayout, QWidget, QAbstractItemView,
)

from .app_config import AppSettingsStore, CaptureConfiguration, default_app_root
from .auto_match_capture import AutoCaptureUnavailable, capture_for_chatgpt
from .capture_settings import CapturePreviewService
from .screen_classifier import LABELS
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
        for item in (self.start_button,self.single_button,self.reload_button,
                     self.rebind_button,self.session_button,self.folder_button,
                     self.quota_box,self.fps_box,self.refresh_button,
                     self.save_button,self.discard_button,self.relabel_button):
            item.setEnabled(not busy)
        self.stop_button.setEnabled(busy)

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
        if self._thread is not None:return
        value=QFileDialog.getExistingDirectory(
            self,"保存先を選択",str(self.dataset.root.parent))
        if value:
            self.dataset=LabelledScreenDataset(value)
            self.staging=UnlabelledScreenStaging(value,
                       quota_bytes=int(self.quota_box.currentData())*1024**3)
            self.directory_label.setText("保存先: "+str(self.dataset.root))
            self._refresh_review_sessions()
            self._update_counts()

    def _new_session(self):
        if self._thread is not None:return
        self.session_id=new_session_id()
        self.session_label.setText("記録対象の対戦セッションID: "+self.session_id)
        self.capture_count.setText("今回の収集0枚")
        self.status.setText("新しい対戦セッション作成済み。過去画像は残っています")

    def _start(self, *, one_shot: bool):
        if self._thread is not None:return
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
        self._review_session=self.review_session_box.currentData()
        self._page=0
        self._reload_review_files()

    def _reload_review_files(self):
        self._current_files=(self.staging.session_files(self._review_session)
                             if self._review_session else [])
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
        if self._thread is not None:return
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
        if self._thread is not None:return
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
        if self._thread is not None:return
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

    def closeEvent(self,event):
        self._stop()
        if self._thread is not None and self._thread.isRunning():
            if not self._thread.wait(5000):
                event.ignore()
                self.status.setText("収集停止を確認してから閉じてください")
                return
        super().closeEvent(event)
