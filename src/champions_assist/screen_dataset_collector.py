"""Standalone, offline GUI for creating human-labelled Champions UI screen datasets.

Only a user-selected read-only viewport is captured. Frames remain in bounded
RAM until a human selects the correct label and confirms saving locally.
There is no ChatGPT/OpenAI transport or gameplay control in this module.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event
from time import monotonic

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
from .screen_dataset import ReviewBuffer, LabelledScreenDataset

SCREEN_LABELS={
    "MATCHMAKING":"マッチング待機・準備",
    "TEAM_SELECTION":"3匹を選ぶ選出画面",
    "MOVE_MENU":"技選択画面",
    "PARTY_MENU":"交代先選択画面",
    "BATTLE_EVENT":"攻撃・交代・メッセージ等の演出",
    "RESULT":"勝敗・対戦終了",
    "UNKNOWN":"その他・判別不能",
}


class _ReadOnlyCollectorWorker(QObject):
    frame=Signal(object)
    error=Signal(str)
    finished=Signal()

    def __init__(self, config: CaptureConfiguration, *, one_shot=False,
                 cadence_seconds=1.0, capture=capture_for_chatgpt):
        super().__init__()
        self.config=config
        self.one_shot=one_shot
        self.cadence_seconds=cadence_seconds
        self.capture=capture
        self._stop=Event()

    def stop(self):
        self._stop.set()

    @Slot()
    def run(self):
        try:
            while not self._stop.is_set():
                started=monotonic()
                try:
                    frame=self.capture(self.config)
                    if isinstance(frame,np.ndarray):
                        self.frame.emit(frame)
                    else:
                        self.error.emit("CAPTURE_RESULT_INVALID")
                except AutoCaptureUnavailable as exc:
                    self.error.emit(str(exc))
                except (OSError,ValueError,RuntimeError) as exc:
                    self.error.emit(type(exc).__name__)
                if self.one_shot:
                    break
                delay=max(0.0,self.cadence_seconds-(monotonic()-started))
                self._stop.wait(delay)
        finally:
            self.finished.emit()


class ScreenDatasetCollector(QMainWindow):
    """Human-in-the-loop sampler. Never stores unattended screenshots."""

    def __init__(self, *, config_path: str|Path|None=None,
                 dataset_root: str|Path|None=None, capture=capture_for_chatgpt,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pokémon Champions — 学習データ収集・ラベル付け（開発用）")
        self.resize(920,770)
        self._settings_store=AppSettingsStore(
            config_path or default_app_root()/"config"/"settings.json")
        self.capture_config=self._settings_store.load().settings.capture
        self.dataset=LabelledScreenDataset(
            dataset_root or default_app_root()/"private-training"/"screens")
        self.buffer=ReviewBuffer(max_frames=150,max_edge=960)
        self._capture=capture
        self._thread=None
        self._worker=None
        self._last_sample_at=None
        self._build()
        self._update_source_label()
        self._update_counts()

    def _build(self):
        scroll=QScrollArea(self)
        scroll.setWidgetResizable(True)
        body=QWidget()
        layout=QVBoxLayout(body)
        intro=QLabel(
            "開発用：スマホのミラーリング画面を読み取り専用で収集します。"
            "撮影候補は最大150枚をメモリに保持し、正解ラベルを確認して保存を押すまで画像ファイルを作りません。"
            "クラウド送信・ゲーム操作はありません。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.source_label=QLabel()
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)
        row=QHBoxLayout()
        self.reload_button=QPushButton("メインアプリのキャプチャ設定を再読込")
        self.reload_button.clicked.connect(self._reload_capture)
        row.addWidget(self.reload_button)
        self.rebind_button=QPushButton("ミラーリングウィンドウを選び直す")
        self.rebind_button.clicked.connect(self._rebind_window)
        row.addWidget(self.rebind_button)
        layout.addLayout(row)
        self.directory_label=QLabel("学習データ保存先: "+str(self.dataset.root))
        self.directory_label.setWordWrap(True)
        layout.addWidget(self.directory_label)
        self.folder_button=QPushButton("学習データの保存先を変更")
        self.folder_button.clicked.connect(self._choose_folder)
        layout.addWidget(self.folder_button)
        self.session_label=QLabel("対戦セッションID: "+self.buffer.session_id)
        layout.addWidget(self.session_label)
        self.session_button=QPushButton("次の対戦セッションを作成（現在の候補は破棄）")
        self.session_button.clicked.connect(self._new_session)
        layout.addWidget(self.session_button)

        self.consent=QCheckBox("このゲーム画面のローカル収集を明示的に許可する")
        layout.addWidget(self.consent)
        controls=QHBoxLayout()
        self.start_button=QPushButton("自動収集開始（1秒ごと・メモリのみ）")
        self.start_button.clicked.connect(lambda:self._start(one_shot=False))
        controls.addWidget(self.start_button)
        self.single_button=QPushButton("1枚撮影")
        self.single_button.clicked.connect(lambda:self._start(one_shot=True))
        controls.addWidget(self.single_button)
        self.stop_button=QPushButton("収集停止")
        self.stop_button.clicked.connect(self._stop)
        controls.addWidget(self.stop_button)
        layout.addLayout(controls)
        self.status=QLabel("待機中 / 画像は未保存 / AI送信なし")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.preview=QLabel("収集するとここにゲーム画面が表示されます")
        self.preview.setMinimumHeight(240)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet(
            "QLabel { background-color:#181b22;color:white;border:1px solid #666; }")
        layout.addWidget(self.preview)
        self.review_count=QLabel("候補0枚 / 上限150枚")
        layout.addWidget(self.review_count)
        self.review_list=QListWidget()
        self.review_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.review_list.setMinimumHeight(135)
        self.review_list.currentItemChanged.connect(self._show_current_frame)
        layout.addWidget(self.review_list)
        layout.addWidget(QLabel("正解ラベルを人間が選択（複数候補を選んで一括保存可）"))
        self.label_box=QComboBox()
        self.label_box.addItem("正解ラベルを選択してください",None)
        for label in LABELS:
            self.label_box.addItem(f"{label} — {SCREEN_LABELS[label]}",label)
        layout.addWidget(self.label_box)
        save_row=QHBoxLayout()
        self.save_button=QPushButton("選択候補をこのラベルでローカル保存")
        self.save_button.clicked.connect(self._save_selected)
        save_row.addWidget(self.save_button)
        self.discard_button=QPushButton("選択候補を破棄")
        self.discard_button.clicked.connect(self._discard_selected)
        save_row.addWidget(self.discard_button)
        layout.addLayout(save_row)
        self.relabel_button=QPushButton("保存済み画像の誤ラベルを訂正")
        self.relabel_button.clicked.connect(self._relabel_saved)
        layout.addWidget(self.relabel_button)
        self.audit_label=QLabel()
        self.audit_label.setWordWrap(True)
        layout.addWidget(self.audit_label)
        self.audit_button=QPushButton("データセット件数・セッション数を再集計")
        self.audit_button.clicked.connect(self._update_counts)
        layout.addWidget(self.audit_button)
        note=QLabel(
            "保存形式: screens/<LABEL>/<match-ID>__<image-ID>.png。"
            "同一対戦の類似画像が学習と検証へ跨らないよう、対戦ごとにセッションを新規作成してください。"
            "画像は勝手に共有・学習・アップロードされません。")
        note.setWordWrap(True)
        layout.addWidget(note)
        scroll.setWidget(body)
        self.setCentralWidget(scroll)
        self._set_capture_busy(False)

    def _set_capture_busy(self, busy):
        self.start_button.setEnabled(not busy)
        self.single_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.reload_button.setEnabled(not busy)
        self.rebind_button.setEnabled(not busy)
        self.session_button.setEnabled(not busy)

    def _update_source_label(self):
        config=self.capture_config
        target=config.selected_window_identity or {}
        target_name=target.get("title") or ("ADB: "+str(config.adb_serial) if config.adb_serial else "未指定")
        roi=config.game_viewport_normalized
        self.source_label.setText(
            f"撮影方式: {config.backend} / 対象: {target_name} / ROI: {roi if roi else '未設定'}")

    def _reload_capture(self):
        if self._thread is not None:
            return
        self.capture_config=self._settings_store.load().settings.capture
        self._update_source_label()
        self.status.setText("キャプチャ設定を再読込しました（画像収集は停止中）")

    def _rebind_window(self):
        if self._thread is not None or self.capture_config.backend!="WINDOWS_NATIVE":
            self.status.setText("Windowsネイティブ方式かつ収集停止中のみウィンドウ選択できます")
            return
        candidates=CapturePreviewService().list_windows()
        if not candidates:
            self.status.setText("撮影対象ウィンドウが見つかりません")
            return
        names=[c.display_name() for c in candidates]
        picked,accepted=QInputDialog.getItem(self,"ミラーリング対象を選択",
                                              "現在表示されている実際の画面を選択してください",
                                              names,0,False)
        if not accepted:return
        chosen=candidates[names.index(picked)]
        new_config=replace(self.capture_config,selected_window_identity=chosen.identity())
        from .app_config import AppSettingsStore
        from dataclasses import replace as replace_settings
        old=self._settings_store.load().settings
        self._settings_store.save(replace_settings(old,capture=new_config))
        self.capture_config=new_config
        self._update_source_label()
        self.status.setText("対象ウィンドウを更新しました。撮影して画像を確認してください")

    def _choose_folder(self):
        if self._thread is not None:return
        value=QFileDialog.getExistingDirectory(self,"学習画像の保存先",str(self.dataset.root.parent))
        if value:
            self.dataset=LabelledScreenDataset(value)
            self.directory_label.setText("学習データ保存先: "+str(self.dataset.root))
            self._update_counts()

    def _new_session(self):
        if self._thread is not None:return
        if self.buffer.frames and QMessageBox.question(
            self,"未保存候補の破棄","未保存の候補画像を破棄して新しい対戦セッションを開始しますか？",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No
            )!=QMessageBox.StandardButton.Yes:
            return
        self.buffer.new_session()
        self.review_list.clear()
        self.preview.clear()
        self.preview.setText("新しい対戦の画面を収集してください")
        self.session_label.setText("対戦セッションID: "+self.buffer.session_id)
        self._update_buffer_label()
        self.status.setText("新規対戦セッション作成済み（前対戦の未保存候補は破棄）")

    def _start(self, *, one_shot: bool):
        if self._thread is not None:return
        if not self.consent.isChecked():
            self.status.setText("収集には明示的な許可チェックが必要です")
            return
        if self.capture_config.game_viewport_normalized is None:
            self.status.setText("ゲームROIが未設定です。メインアプリのキャプチャ設定を確認してください")
            return
        if self.capture_config.backend=="WINDOWS_NATIVE" and not self.capture_config.selected_window_identity:
            self.status.setText("対象ウィンドウを選択してください")
            return
        if len(self.buffer.frames)>=self.buffer.max_frames:
            self.status.setText("候補が上限です。ラベル保存または破棄してから再開してください")
            return
        thread=QThread(self)
        worker=_ReadOnlyCollectorWorker(self.capture_config,one_shot=one_shot,
                                        capture=self._capture)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.frame.connect(self._on_frame)
        worker.error.connect(self._on_capture_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_worker_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread=thread
        self._worker=worker
        self._set_capture_busy(True)
        self.status.setText("1枚撮影中" if one_shot else "画像候補をローカル収集中（保存・送信なし）")
        thread.start()

    def _stop(self):
        if self._worker is not None:
            self._worker.stop()
        self.stop_button.setEnabled(False)
        if self._thread is not None:
            self.status.setText("収集停止処理中（候補はメモリに保持）")

    @Slot()
    def _on_worker_finished(self):
        self._thread=None
        self._worker=None
        self._set_capture_busy(False)
        self.status.setText("収集停止 / 候補画像は未保存 / AI送信なし")

    @Slot(str)
    def _on_capture_error(self, reason):
        self.status.setText(f"画面取得失敗: {reason} / 保存なし。設定とミラーリング状態を確認")

    @Slot(object)
    def _on_frame(self, image):
        try:
            sample=self.buffer.append(image)
        except (BufferError,ValueError) as exc:
            self.status.setText(str(exc)+" / 保存されていません")
            self._stop()
            return
        item=QListWidgetItem(f"{sample.timestamp}  |  {sample.id}")
        item.setData(Qt.ItemDataRole.UserRole,sample.id)
        self.review_list.addItem(item)
        self.review_list.setCurrentItem(item)
        self._update_buffer_label()
        self.status.setText(f"メモリ内候補 {len(self.buffer.frames)} 枚 / AI送信・ディスク保存なし")

    @Slot(object,object)
    def _show_current_frame(self, current, _previous):
        if current is None:
            self.preview.clear()
            self.preview.setText("表示する候補なし")
            return
        image_id=current.data(Qt.ItemDataRole.UserRole)
        sample=next((f for f in self.buffer.frames if f.id==image_id),None)
        if sample is None:return
        height,width=sample.image.shape[:2]
        rgb=np.ascontiguousarray(sample.image)
        qimg=QImage(rgb.data,width,height,rgb.strides[0],QImage.Format.Format_RGB888).copy()
        pix=QPixmap.fromImage(qimg).scaled(
            max(1,self.preview.width()-8),max(1,self.preview.height()-8),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(pix)

    def _selected_frame_ids(self):
        items=self.review_list.selectedItems()
        if not items and self.review_list.currentItem() is not None:
            items=[self.review_list.currentItem()]
        return {item.data(Qt.ItemDataRole.UserRole) for item in items}

    def _remove_candidates(self, ids):
        self.buffer.remove(ids)
        for index in reversed(range(self.review_list.count())):
            item=self.review_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) in ids:
                self.review_list.takeItem(index)
        self._update_buffer_label()

    def _save_selected(self):
        label=self.label_box.currentData()
        ids=self._selected_frame_ids()
        if not ids or label not in LABELS:
            self.status.setText("候補画像と正解ラベルを選択してください")
            return
        answer=QMessageBox.question(
            self,"保存する画像と正解ラベルの確認",
            f"選択した {len(ids)} 枚を「{label}」として、ローカルに保存しますか？",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        if answer!=QMessageBox.StandardButton.Yes:return
        saved=set()
        try:
            for sample in list(self.buffer.frames):
                if sample.id in ids:
                    self.dataset.save(sample,label)
                    saved.add(sample.id)
        except (OSError,ValueError) as exc:
            self.status.setText(f"保存エラー: {type(exc).__name__} / 成功分 {len(saved)} 枚")
        else:
            self.status.setText(f"{len(saved)} 枚を {label} に保存（クラウド送信なし）")
        finally:
            self._remove_candidates(saved)
            self._update_counts()

    def _discard_selected(self):
        ids=self._selected_frame_ids()
        self._remove_candidates(ids)
        self.status.setText(f"未保存候補 {len(ids)} 枚を破棄しました")

    def _relabel_saved(self):
        path,_=QFileDialog.getOpenFileName(
            self,"ラベルを訂正する保存済み画像を選択",str(self.dataset.root),
            "画像 (*.png)")
        if not path:return
        chosen,ok=QInputDialog.getItem(
            self,"正解ラベルへ移動","確認した正しいラベルを選択してください",list(LABELS),0,False)
        if not ok:return
        try:
            self.dataset.relabel(path,chosen)
        except (ValueError,OSError) as exc:
            self.status.setText("ラベル変更失敗: "+type(exc).__name__)
        else:
            self.status.setText("保存済み画像を "+chosen+" に訂正しました")
        self._update_counts()

    def _update_buffer_label(self):
        self.review_count.setText(
            f"未保存候補 {len(self.buffer.frames)}/{self.buffer.max_frames} 枚"
            "（上限で自動停止・画像は失われません）")

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
                self.status.setText("撮影スレッド終了待ち。撮影停止後に閉じてください")
                return
        super().closeEvent(event)
