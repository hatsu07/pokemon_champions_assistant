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
    # Live capture and JPEG encoder consume RGB uint8, not OpenCV BGR.
    return cv2.cvtColor(image,cv2.COLOR_BGR2RGB)


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
        # Diagnostic-only cached six-row coverage; no filesystem/network IO.
        try:
            from .live_context import selection_hint_coverage
            six=tuple(report.get("opponent_six") or ())
            if len(six)==6:
                report.update(selection_hint_coverage(
                    six,self.assets.matrix_rows_for(six)))
        except (KeyError,ValueError,TypeError,AttributeError):
            pass
        # Test-only instrumentation: measure the JPEG/data URI bytes produced
        # by the same shared image builder, without modifying live replay.
        # A diagnostics failure must never override the test result.
        try:
            from .selection_vision import selection_image_content
            from .selection_image_diagnostics import payload_statistics
            content=selection_image_content(
                self.image,introduction="Selection test image byte metrics")
            report.update(payload_statistics(self.image,content))
        except Exception:
            pass
        report["test_total_elapsed_ms"]=round((perf_counter()-started)*1000)
        self.finished.emit(report)


class _TimingTestWorker(QObject):
    """Off-match diagnostic only; no actual game observations are confirmed."""
    finished=Signal(object)

    def __init__(self, *, mode, image, assets, state, app_root, model,
                 reasoning_effort, requested_at):
        super().__init__()
        self.mode=mode
        self.image=image
        self.assets=assets
        self.state=state
        self.app_root=Path(app_root)
        self.model=model
        self.reasoning_effort=reasoning_effort
        self.requested_at=requested_at

    @Slot()
    def run(self):
        from .live_timing_test import prep_timing_test, turn_timing_test
        try:
            # Includes queued thread startup and auth in the strict budget.
            token=ChatGPTSignIn(ProtectedSessionStore(self.app_root)).access_token()
            kwargs=dict(state=self.state,assets=self.assets,access_token=token,
                        model=self.model,reasoning_effort=self.reasoning_effort,
                        started_at=self.requested_at)
            if self.mode=="PREP":
                updated, report=prep_timing_test(**kwargs)
            else:
                updated, report=turn_timing_test(image=self.image,**kwargs)
        except Exception as exc:
            limit=40000 if self.mode=="PREP" else 30000
            report={"status":self.mode+"_TIMING_FAIL",
                    "reason":type(exc).__name__,"pass":False,
                    "elapsed_ms":round((perf_counter()-self.requested_at)*1000),
                    "limit_ms":limit}
            updated=self.state
        self.finished.emit({"mode":self.mode,"state":updated,"report":report})


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
        self.turn_image=None
        self.test_state=None
        self.timing_results=[]
        self.timing_requested_at=None
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
        timing=QHBoxLayout()
        self.prep_test_button=QPushButton("PREP 40秒・18対面検証")
        self.prep_test_button.setEnabled(False)
        self.prep_test_button.clicked.connect(self.run_prep_test)
        timing.addWidget(self.prep_test_button)
        self.turn_choose_button=QPushButton("ターン画面PNG/JPEGを選択")
        self.turn_choose_button.setEnabled(False)
        self.turn_choose_button.clicked.connect(self.choose_turn_image)
        timing.addWidget(self.turn_choose_button)
        self.turn_run_button=QPushButton("ターン30秒テスト")
        self.turn_run_button.setEnabled(False)
        self.turn_run_button.clicked.connect(self.run_turn_test)
        timing.addWidget(self.turn_run_button)
        layout.addLayout(timing)
        self.timing_status=QLabel(
            "選出テスト成功後、PREP 40秒とターン30秒を個別検証できます。"
            "ターンは実際の『技選択／交代選択』画面の画像が必要です。"
            " AI案の実行・実戦結果は推測しません。")
        self.timing_status.setWordWrap(True)
        self.timing_status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.timing_status)
        extra=QHBoxLayout()
        from PySide6.QtWidgets import QComboBox
        self.preview_percent=QComboBox()
        self.preview_percent.addItem("元画像100%（実戦と同じ）",100)
        self.preview_percent.addItem("50%に縮小（比較条件）",50)
        extra.addWidget(self.preview_percent)
        self.preview_button=QPushButton("ChatGPTに送信する画像をプレビュー")
        self.preview_button.setEnabled(False)
        self.preview_button.clicked.connect(self.show_payload_preview)
        extra.addWidget(self.preview_button)
        self.compare_button=QPushButton("解像度×AIモデル比較")
        self.compare_button.setEnabled(False)
        self.compare_button.clicked.connect(self.show_comparison)
        extra.addWidget(self.compare_button)
        layout.addLayout(extra)
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
        # An old selection must never silently drive a new preview's PREP/turn.
        self.test_state=None
        self.turn_image=None
        self.prep_test_button.setEnabled(False)
        self.turn_choose_button.setEnabled(False)
        self.turn_run_button.setEnabled(False)
        h,w=self.image.shape[:2]
        frame=QImage(self.image.data,w,h,self.image.strides[0],QImage.Format.Format_RGB888)
        self.image_label.setPixmap(
            QPixmap.fromImage(frame.copy()).scaled(
                710,310,Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        self.status.setText(f"画像読込済み: {w}×{h} / 実戦と同じ処理で検証")
        self.run_button.setEnabled(True)
        self.preview_button.setEnabled(True)
        self.compare_button.setEnabled(True)
        if self.log:
            self.log.write("TEST_IMAGE_READY",image_count=1)

    def show_payload_preview(self):
        if self.image is None or self.thread is not None:
            return
        from .selection_image_test_tools import OutboundImagePreviewDialog
        dialog=OutboundImagePreviewDialog(
            image=self.image,percent=self.preview_percent.currentData(),parent=self)
        dialog.exec()

    def show_comparison(self):
        if self.image is None or self.thread is not None:
            return
        from .selection_image_test_tools import OpponentImageComparisonDialog
        dialog=OpponentImageComparisonDialog(
            image=self.image,assets=self.assets,root=self.app_root,
            current_model=self.model,logger=self.log,parent=self)
        dialog.exec()

    def run_test(self):
        if self.thread is not None or self.image is None:
            return
        self.run_button.setEnabled(False)
        self.select_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.compare_button.setEnabled(False)
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
            +"\n"+("未検証理由" if status=="PROVISIONAL_ADVICE" else "失敗理由")
            +": "+str(report.get("reason","なし"))
        )
        if "matrix_ai_hint_rows" in report:
            result += (
                f"\n構築別マトリクスAI評価: {report['matrix_ai_hint_rows']}/6匹、"
                f"{report['matrix_ai_hint_pairs']}/36対面"
                f"（構造化v2: {report['matrix_ai_structured_rows']}/6匹）"
                + ("\nAI評価案が未生成: " + " / ".join(report["matrix_ai_missing_opponents"])
                   if report["matrix_ai_missing_opponents"] else "")
            )
        self.details.setText(result)
        self.test_state=None
        if status in ("OK","PROVISIONAL_ADVICE"):
            try:
                from .live_timing_test import selection_test_prep_state
                self.test_state=selection_test_prep_state(self.assets,report)
            except (KeyError,ValueError,TypeError):
                pass
        if self.test_state is not None:
            self.timing_status.setText(
                "AIが選んだ3匹を仮選出として保存（実ゲームの確定操作なし）。"
                "PREP40秒とターン30秒を別々にテストできます。")
        self.prep_test_button.setEnabled(self.test_state is not None)
        self.turn_choose_button.setEnabled(self.test_state is not None)
        self.turn_run_button.setEnabled(
            self.test_state is not None and self.turn_image is not None)
        if self.log:
            self.log.report(report,event="SELECTION_TEST_RESULT")
            self.log.write("SELECTION_TEST_DONE",status=status,
                           elapsed_ms=elapsed if type(elapsed) is int else -1,
                           selection_present=len(chosen)==3 and bool(report.get("lead")))

    def choose_turn_image(self):
        if self.thread is not None or self.test_state is None:
            return
        source,_=QFileDialog.getOpenFileName(
            self,"ターンの技選択または交代選択画面","","画像 (*.png *.jpg *.jpeg)")
        if not source:
            return
        try:
            self.turn_image=load_test_image(source)
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,"ターン画面を読み込めません",str(exc))
            return
        self.turn_run_button.setEnabled(True)
        self.timing_status.setText(
            "ターン画像を読み込みました。30秒テストは画像認識から合法手検査まで"
            "実際の対戦処理を使用します。再テストは何回でも可能です。")

    def run_prep_test(self):
        self._start_timing_test("PREP")

    def run_turn_test(self):
        self._start_timing_test("TURN")

    def _start_timing_test(self, mode):
        if self.thread is not None or self.test_state is None:
            return
        if mode=="TURN" and self.turn_image is None:
            return
        self.select_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.compare_button.setEnabled(False)
        self.prep_test_button.setEnabled(False)
        self.turn_choose_button.setEnabled(False)
        self.turn_run_button.setEnabled(False)
        self.timing_status.setText(
            "PREP 40秒：初手6＋控え12＋勝ち筋3点を検査中…"
            if mode=="PREP" else
            "ターン30秒：画像認識＋AI判断＋合法手検査中…")
        self.timing_requested_at=perf_counter()
        if self.log:
            self.log.write(mode+"_TIMING_TEST_START",phase=mode,
                           time_limit_ms=40000 if mode=="PREP" else 30000,
                           model=self.model)
        self.thread=QThread(self)
        self.worker=_TimingTestWorker(
            mode=mode,image=self.turn_image if mode=="TURN" else None,
            assets=self.assets,state=self.test_state,app_root=self.app_root,
            model=self.model,reasoning_effort=self.reasoning_effort,
            requested_at=self.timing_requested_at)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._timing_complete)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._clear_worker)
        self.thread.start()

    @Slot(object)
    def _timing_complete(self,payload):
        mode=payload["mode"]
        report=dict(payload["report"])
        # The displayed answer, not merely the model response, must arrive
        # within the hard time budget. Qt event dispatch delay counts too.
        wall=round((perf_counter()-self.timing_requested_at)*1000)
        limit=40000 if mode=="PREP" else 30000
        report["ui_delivery_elapsed_ms"]=wall
        if wall>limit:
            report["status"]=mode+"_TIMING_FAIL"
            report["reason"]="UI_DELIVERY_DEADLINE_EXCEEDED"
            report["pass"]=False
            report["recommendation"]=""
        if mode=="PREP":
            if report.get("pass"):
                self.test_state=payload["state"]
            details=(
                f"PREP40秒 {'合格' if report.get('pass') else '未達'} "
                f"／画面表示まで {wall/1000:.3f}秒"
                f"／初手 {report.get('lead_evaluated',0)}/6"
                f"／控え {report.get('bench_evaluated',0)}/12"
                f"／勝ち筋 {report.get('win_conditions',0)}/3"
                f"／理由: {report.get('reason','UNKNOWN')}"
                "。構造と時間の検証であり、戦術的正確性は未検証。")
        else:
            self.timing_results.append({
                "pass":bool(report.get("pass")), "elapsed_ms":wall,
                "status":report.get("status")})
            count=len(self.timing_results)
            passes=sum(row["pass"] for row in self.timing_results)
            values=sorted(r["elapsed_ms"] for r in self.timing_results)
            # Inclusive nearest-rank p95; no network or clock inference.
            from math import ceil
            p95=values[max(0,ceil(count*0.95)-1)]
            details=(
                f"ターン30秒 {'合格' if report.get('pass') else '未達'} "
                f"／画面表示まで {wall/1000:.3f}秒"
                f"／推奨: {report.get('recommendation') or 'なし'}"
                f"／理由: {report.get('reason','UNKNOWN')}"
                f"。累計 {passes}/{count}件合格"
                f"、最大 {max(values)/1000:.3f}秒"
                f"、p95 {p95/1000:.3f}秒"
                f"（全件30秒以下が目標）。")
        self.timing_status.setText(details)
        if self.log:
            safe={k:v for k,v in report.items()
                  if k not in ("state","observation","screen_image")}
            self.log.report(safe,event=mode+"_TIMING_TEST_RESULT")

    @Slot()
    def _clear_worker(self):
        self.thread=None
        self.worker=None
        self.select_button.setEnabled(True)
        self.run_button.setEnabled(self.image is not None)
        self.preview_button.setEnabled(self.image is not None)
        self.compare_button.setEnabled(self.image is not None)
        self.prep_test_button.setEnabled(self.test_state is not None)
        self.turn_choose_button.setEnabled(self.test_state is not None)
        self.turn_run_button.setEnabled(
            self.test_state is not None and self.turn_image is not None)

    def closeEvent(self,event):
        if self.thread is not None:
            QMessageBox.information(self,"実行中","テスト通信の終了後に閉じてください")
            event.ignore()
            return
        if self.log:
            self.log.write("SELECTION_TEST_CLOSED")
        super().closeEvent(event)
