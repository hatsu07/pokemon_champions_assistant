"""Explicit OFF-MATCH image replay using the same live selection pipeline.

This diagnostic window never captures a live game or presses any game buttons.
It accepts a user-selected local team-selection screenshot, freezes the current
registered team via all four masters, and calls the real opponent-recognition +
selection path once. The screenshot stays in memory and is never logged.
"""
from __future__ import annotations

from pathlib import Path
from time import perf_counter
from uuid import uuid4

import cv2
import numpy as np
from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout,
)

from .match_debug_log import MatchDebugLog
from .match_replay import new_replay, run_replay_image
from .siwc_auth import ChatGPTSignIn, ProtectedSessionStore


def load_test_image(path: str | Path) -> np.ndarray:
    """Read a user-chosen PNG/JPEG with a bounded size and Unicode filename."""
    source=Path(path)
    if (not source.is_file() or source.suffix.lower() not in (".png",".jpg",".jpeg")
            or source.stat().st_size>15*1024*1024 or source.stat().st_size<100):
        raise ValueError("15 MB以下の選出画面PNG/JPEGを指定してください")
    image=cv2.imdecode(np.frombuffer(source.read_bytes(),dtype=np.uint8),
                       cv2.IMREAD_COLOR)
    if (image is None or image.ndim!=3 or image.shape[0]<200 or
            image.shape[1]<300 or image.shape[0]*image.shape[1]>12000000):
        raise ValueError("選出画面画像を正しく読み取れません")
    return image


class _SelectionTestWorker(QObject):
    finished=Signal(object)

    def __init__(self, *, image, assets, app_root, model, reasoning_effort):
        super().__init__()
        self.image=image
        self.assets=assets
        self.app_root=Path(app_root)
        self.model=model
        self.reasoning_effort=reasoning_effort

    @Slot()
    def run(self):
        started=perf_counter()
        try:
            token=ChatGPTSignIn(ProtectedSessionStore(self.app_root)).access_token()
            remaining=55.0-(perf_counter()-started)
            if remaining<=1.5:
                report={"status":"DEADLINE_EXCEEDED","reason":"AUTH_BUDGET"}
            else:
                _state,report=run_replay_image(
                    self.image,state=new_replay(self.assets),assets=self.assets,
                    access_token=token,model=self.model,
                    reasoning_effort=self.reasoning_effort,
                    total_seconds=remaining,
                )
        except Exception as exc:
            report={"status":"ERROR","reason":type(exc).__name__}
        report["test_total_elapsed_ms"]=round((perf_counter()-started)*1000)
        self.finished.emit(report)


class SelectionTestDialog(QDialog):
    """Local reproducible opponent six → team-three+lead diagnostic UI."""

    def __init__(self, *, assets, model: str, reasoning_effort: str,
                 app_root: str | Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("選出画面テスト（試合外・ゲーム操作なし）")
        self.resize(750,575)
        self.assets=assets
        self.model=model
        self.reasoning_effort=reasoning_effort
        self.app_root=Path(app_root)
        self.image=None
        self.thread=None
        self.worker=None
        self.log=None
        try:
            self.log=MatchDebugLog(self.app_root,
                                   match_id="capture-"+uuid4().hex)
            self.log.write("SELECTION_TEST_OPEN",
                           model=self.model,phase="MATCHMAKING",preload_count=4)
        except OSError:
            pass
        layout=QVBoxLayout(self)
        heading=QLabel(
            "登録済み自軍6匹を使用します。画像からは相手6匹をChatGPTで認識し、"
            "選出90秒のうち約55秒を上限に選出結果を返す処理を1回実行します。"
            "試合中の使用・自動操作はしません。"
        )
        heading.setWordWrap(True)
        layout.addWidget(heading)
        actions=QHBoxLayout()
        self.select_button=QPushButton("選出画面PNG/JPEGを選択")
        self.select_button.clicked.connect(self.choose_image)
        actions.addWidget(self.select_button)
        self.run_button=QPushButton("画像から相手6匹を認識・選出テスト")
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self.run_test)
        actions.addWidget(self.run_button)
        layout.addLayout(actions)
        self.image_label=QLabel("テスト画像を選択してください")
        self.image_label.setMinimumHeight(240)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.image_label)
        self.status=QLabel("未開始")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.details=QLabel("")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.details)
        self.log_label=QLabel(
            "自動ログ: "+str(self.log.path) if self.log else "ログ保存不可")
        self.log_label.setWordWrap(True)
        self.log_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.log_label)

    def choose_image(self):
        if self.thread is not None:
            return
        source,_=QFileDialog.getOpenFileName(
            self,"テストする選出画面を指定","","画像 (*.png *.jpg *.jpeg)")
        if not source:
            return
        try:
            image=load_test_image(source)
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,"選出画面画像を読み込めません",str(exc))
            return
        self.set_image(image)

    def set_image(self,image: np.ndarray):
        """Also permits offline synthetic Qt tests without invoking a file dialog."""
        if self.thread is not None:
            return
        self.image=image.copy()
        h,w=self.image.shape[:2]
        frame=QImage(self.image.data,w,h,self.image.strides[0],QImage.Format.Format_BGR888)
        self.image_label.setPixmap(
            QPixmap.fromImage(frame.copy()).scaled(
                710,310,Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        self.status.setText(f"画像読込済み: {w}×{h} / 実戦と同じ処理で検証")
        self.run_button.setEnabled(True)
        if self.log:
            self.log.write("TEST_IMAGE_READY",image_count=1)

    def run_test(self):
        if self.thread is not None or self.image is None:
            return
        self.run_button.setEnabled(False)
        self.select_button.setEnabled(False)
        self.status.setText("ChatGPT画像認識・相手6匹確認・選出判断を実行中（選出約55秒上限）")
        self.details.setText("")
        if self.log:
            self.log.write("TEST_API_QUEUED",phase="MATCHMAKING",image_count=1,
                           model=self.model)
        self.thread=QThread(self)
        self.worker=_SelectionTestWorker(
            image=self.image,assets=self.assets,app_root=self.app_root,
            model=self.model,reasoning_effort=self.reasoning_effort)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._complete)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._clear_worker)
        self.thread.start()

    @Slot(object)
    def _complete(self, report: dict):
        status=str(report.get("status","UNKNOWN"))
        self.status.setText("選出テスト結果: "+status)
        observations=report.get("observation")
        opponents=(observations.get("opponent_six") if isinstance(observations,dict)
                   else []) or report.get("opponent_six") or []
        chosen=report.get("selected_three") or []
        elapsed=report.get("test_total_elapsed_ms",report.get("elapsed_ms","不明"))
        result=(
            "相手6匹（AI認識・未実機確認）: "+(" / ".join(opponents) if opponents else "取得不可")
            +"\n選出3匹: "+(" / ".join(chosen) if chosen else "なし")
            +"\n初手: "+str(report.get("lead") or "なし")
            +"\n所要時間: "+str(elapsed)+" ms"
            +"\n認識回数: "+str(report.get("opponent_recognition_attempts","不明"))
            +"\n失敗理由: "+str(report.get("reason","なし"))
        )
        self.details.setText(result)
        if self.log:
            self.log.report(report,event="SELECTION_TEST_RESULT")
            self.log.write("SELECTION_TEST_DONE",status=status,
                           elapsed_ms=elapsed if type(elapsed) is int else -1,
                           selection_present=len(chosen)==3 and bool(report.get("lead")))

    @Slot()
    def _clear_worker(self):
        self.thread=None
        self.worker=None
        self.select_button.setEnabled(True)
        self.run_button.setEnabled(self.image is not None)

    def closeEvent(self,event):
        if self.thread is not None:
            QMessageBox.information(self,"実行中","テスト通信の終了後に閉じてください")
            event.ignore()
            return
        if self.log:
            self.log.write("SELECTION_TEST_CLOSED")
        super().closeEvent(event)
