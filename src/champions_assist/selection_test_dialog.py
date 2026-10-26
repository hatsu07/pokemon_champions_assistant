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
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QWidget, QTabWidget, QScrollArea,
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
                 reasoning_effort, requested_at, prep_limit_seconds=87.0):
        super().__init__()
        self.mode=mode
        self.image=image
        self.assets=assets
        self.state=state
        self.app_root=Path(app_root)
        self.model=model
        self.reasoning_effort=reasoning_effort
        self.requested_at=requested_at
        self.prep_limit_seconds=prep_limit_seconds

    @Slot()
    def run(self):
        from .live_timing_test import prep_timing_test, turn_timing_test, switch_timing_test
        try:
            # Includes queued thread startup and auth in the strict budget.
            token=ChatGPTSignIn(ProtectedSessionStore(self.app_root)).access_token()
            kwargs=dict(state=self.state,assets=self.assets,access_token=token,
                        model=self.model,reasoning_effort=self.reasoning_effort,
                        started_at=self.requested_at)
            if self.mode=="PREP":
                updated, report=prep_timing_test(
                    **kwargs,limit_seconds=self.prep_limit_seconds)
            elif self.mode=="SWITCH":
                updated, report=switch_timing_test(image=self.image,**kwargs)
            elif self.mode=="EVENT":
                from .match_replay import run_replay_batch
                snapshot=dict(self.state)
                snapshot["phase"]="BATTLE"
                updated, raw=run_replay_batch(
                    [self.image],state=snapshot,assets=self.assets,
                    access_token=token,model=self.model,
                    reasoning_effort=self.reasoning_effort,
                    total_seconds=max(1.0,30.0-(perf_counter()-self.requested_at)))
                observation=raw.get("observation") or {}
                observed_event=observation.get("observed_event","NONE")
                messages=observation.get("messages") or []
                valid=(raw.get("status") in ("OBSERVED","MATCH_END","RESULT_UNCONFIRMED")
                       and raw.get("screen") not in ("TEAM_SELECTION","UNKNOWN",None)
                       and (observed_event!="NONE" or bool(messages)
                            or raw.get("status")=="MATCH_END"))
                report={
                    "status":"EVENT_READ_PASS" if valid else "EVENT_READ_FAIL",
                    "reason":"OBSERVED" if valid else raw.get("reason") or "NO_EVENT_OBSERVED",
                    "pass":valid,
                    "source_status":raw.get("status"),
                    "screen_observation":raw.get("screen"),
                    "event":observed_event,
                    "messages":messages[:8],
                    "match_result":observation.get("match_result","UNKNOWN"),
                    "state_phase":updated.get("phase"),
                }
                if not valid:
                    updated=self.state
            else:
                updated, report=turn_timing_test(image=self.image,**kwargs)
        except Exception as exc:
            limit=round(self.prep_limit_seconds*1000) if self.mode=="PREP" else 30000
            report={"status":self.mode+"_TIMING_FAIL",
                    "reason":type(exc).__name__,"pass":False,
                    "elapsed_ms":round((perf_counter()-self.requested_at)*1000),
                    "limit_ms":limit}
            updated=self.state
        self.finished.emit({"mode":self.mode,"state":updated,"report":report})


class SelectionTestDialog(QDialog):
    """Local reproducible opponent six → team-three+lead diagnostic UI."""

    def __init__(self, *, assets, model: str, reasoning_effort: str,
                 app_root: str | Path, parent=None, master_directory=None,
                 team_path=None, capture_config=None,
                 matrix_source=None, lookup_source=None):
        super().__init__(parent)
        self.setWindowTitle("テスト全般（個別画像テスト・実ゲーム統合テスト）")
        self.resize(1050,760)
        self.assets=assets
        self.model=model
        self.reasoning_effort=reasoning_effort
        self.app_root=Path(app_root)
        self.master_directory=master_directory
        self.team_path=team_path
        self.capture_config=capture_config
        self.matrix_source=matrix_source
        self.lookup_source=lookup_source
        self.live_panel=None
        self._close_after_workers=False
        self.live_test_counts={"selection":0,"prep":0,"turns":0,"switches":0,
                               "events":0,"result":0}
        self.live_last_phase=""
        self.image=None
        self.turn_image=None
        self.switch_image=None
        self.event_image=None
        self.test_state=None
        self.timing_results=[]
        self.timing_requested_at=None
        self.selection_started_at=None
        self.selection_display_elapsed_ms=None
        self.auto_prep_pending=False
        self.prep_completed=False
        self.prep_attempted=False
        self.prep_limit_seconds=0.0
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
        outer=QVBoxLayout(self)
        self.tabs=QTabWidget(self)
        outer.addWidget(self.tabs)
        self.selection_page=QWidget(self.tabs)
        self.selection_scroll=QScrollArea(self.tabs)
        self.selection_scroll.setWidgetResizable(True)
        self.selection_scroll.setWidget(self.selection_page)
        self.tabs.addTab(self.selection_scroll,"選出画面テスト")
        layout=QVBoxLayout(self.selection_page)
        heading=QLabel(
            "選出を表示した直後、同じ相手6匹・選出3匹でPREPを自動開始します。"
            " 選出表示＋初手6／控え12対面＋勝ち筋3点の完了を合計90秒以内"
            " （PREPは残り時間を使用・ゲーム操作余裕3秒）で検証します。"
            "ターン30秒は別画像で検証します。実ゲームの操作はしません。"
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
        # PREP is mandatory and automatic immediately after the selection
        # recommendation is displayed; no separate manual test button.
        self.turn_choose_button=QPushButton("ターン画面PNG/JPEGを選択")
        self.turn_choose_button.setEnabled(False)
        self.turn_choose_button.clicked.connect(self.choose_turn_image)
        timing.addWidget(self.turn_choose_button)
        self.turn_run_button=QPushButton("ターン30秒テスト")
        self.turn_run_button.setEnabled(False)
        self.turn_run_button.clicked.connect(self.run_turn_test)
        timing.addWidget(self.turn_run_button)
        self._timing_row=timing
        self.timing_status=QLabel(
            "選出後のPREPは自動で始まります（選出＋PREP計90秒）。"
            "ターン30秒テストには『技選択／交代選択』画面の画像が必要です。"
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

        self.turn_page=QWidget(self.tabs)
        self.tabs.addTab(self.turn_page,"ターン画面テスト")
        turn_layout=QVBoxLayout(self.turn_page)
        turn_layout.addWidget(QLabel(
            "選出タブで選出＋PREPを実行後、技選択画面のPNG/JPEGを読み取り、"
            "画像認識・合法手・30秒目標を検証します。"))
        turn_layout.addLayout(self._timing_row)
        self.turn_status=QLabel("選出＋PREP後にターン画像を指定してください。")
        self.turn_status.setWordWrap(True)
        self.turn_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        turn_layout.addWidget(self.turn_status)
        turn_layout.addStretch(1)

        self.switch_page=QWidget(self.tabs)
        self.tabs.addTab(self.switch_page,"交代先選択画面テスト")
        switch_layout=QVBoxLayout(self.switch_page)
        switch_layout.addWidget(QLabel(
            "交代先選択画面（PARTY_MENU）と、選出済み3匹から合法な交代先を"
            "返せるかを独立検証します。候補が画面に無ければ合格しません。"))
        row=QHBoxLayout()
        self.switch_choose_button=QPushButton("交代先選択画面PNG/JPEGを選択")
        self.switch_choose_button.clicked.connect(self.choose_switch_image)
        self.switch_run_button=QPushButton("交代先選択テスト")
        self.switch_run_button.clicked.connect(self.run_switch_test)
        self.switch_run_button.setEnabled(False)
        row.addWidget(self.switch_choose_button)
        row.addWidget(self.switch_run_button)
        switch_layout.addLayout(row)
        self.switch_status=QLabel("選出＋PREP後、交代先選択画面を指定してください。")
        self.switch_status.setWordWrap(True)
        self.switch_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        switch_layout.addWidget(self.switch_status)
        switch_layout.addStretch(1)

        self.event_page=QWidget(self.tabs)
        self.tabs.addTab(self.event_page,"バトルイベント読み取りテスト")
        event_layout=QVBoxLayout(self.event_page)
        event_layout.addWidget(QLabel(
            "対戦中のイベント・HP変化・判明技・結果画面のPNG/JPEGを"
            "認識し、既存バトル状態へ観測情報だけを反映します。"
            "勝敗や行動の実行を推測しません。"))
        row=QHBoxLayout()
        self.event_choose_button=QPushButton("バトルイベント画面PNG/JPEGを選択")
        self.event_choose_button.clicked.connect(self.choose_event_image)
        self.event_run_button=QPushButton("イベント読み取りテスト")
        self.event_run_button.clicked.connect(self.run_event_test)
        self.event_run_button.setEnabled(False)
        row.addWidget(self.event_choose_button)
        row.addWidget(self.event_run_button)
        event_layout.addLayout(row)
        self.event_status=QLabel("選出＋PREP後、イベント画面を指定してください。")
        self.event_status.setWordWrap(True)
        self.event_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        event_layout.addWidget(self.event_status)
        event_layout.addStretch(1)

        self.integration_page=QWidget(self.tabs)
        self.tabs.addTab(self.integration_page,"1対戦統合テスト（実ゲーム画面）")
        self.integration_layout=QVBoxLayout(self.integration_page)
        self.integration_intro=QLabel(
            "保存画像ではなく設定済みの実ゲームウィンドウ／ADB画面を"
            "読み取る統合テストです。対戦開始を押して4ファイルをロードし、"
            "キャプチャを開始してください。選出→PREP→ターン→交代→"
            "イベント→決着まで状態を継続して検証します。"
            "ゲーム操作・自動キー入力はしません。"
            "選出確定はプレイヤーが確認します。")
        self.integration_intro.setWordWrap(True)
        self.integration_layout.addWidget(self.integration_intro)
        self.integration_status=QLabel("ライブテストは未開始")
        self.integration_status.setWordWrap(True)
        self.integration_layout.addWidget(self.integration_status)
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self,index):
        if self.tabs.widget(index) is self.integration_page:
            self._ensure_live_test()

    def _ensure_live_test(self):
        if self.live_panel is not None:
            return
        if (self.master_directory is None or self.team_path is None
                or self.capture_config is None):
            self.integration_status.setText(
                "実ゲーム画面の検証には設定済みのウィンドウまたはADB、"
                "ゲーム領域、登録済みチームが必要です。設定画面を確認してください。")
            return
        # The exact same live read-only support FSM/vision/capture panel is
        # embedded here, not simulated from saved screenshots.
        from .match_replay_dialog import MatchReplayDialog
        panel=MatchReplayDialog(
            model=self.model, master_directory=str(self.master_directory),
            team_path=str(self.team_path), capture_config=self.capture_config,
            store_root=self.app_root, reasoning_effort=self.reasoning_effort,
            parent=self.integration_page, embedded=True)
        panel.matrix_source=(str(self.matrix_source)
                             if self.matrix_source is not None else None)
        panel.lookup_source=(str(self.lookup_source)
                             if self.lookup_source is not None else None)
        self.live_panel=panel
        panel.state_changed.connect(self._on_live_state)
        panel.shutdown_finished.connect(self._retry_close_after_workers)
        self.integration_status.setText(
            "実ゲームキャプチャの支援エンジンを接続しました。"
            "4ファイル事前ロード→監視開始で統合テストします。")
        scroll=QScrollArea(self.integration_page)
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        self.integration_layout.addWidget(scroll)

    @Slot(object,object)
    def _on_live_state(self,state,report):
        """Live-only integration scoreboard, based on delivered observations.

        A test run never claims a win until a RESULT_SCREEN was actually
        observed and the underlying replay engine changed to ENDED.
        """
        stage=str(state.get("phase","UNKNOWN"))
        status=str(report.get("status","UNKNOWN"))
        screen=str(report.get("screen","UNKNOWN"))
        if status=="NEW_MATCH" or (
                stage=="MATCHMAKING" and self.live_last_phase=="ENDED"):
            self.live_test_counts={k:0 for k in self.live_test_counts}
        if screen=="TEAM_SELECTION" and status in (
                "OK","PROVISIONAL_ADVICE","PARTIAL_ADVICE","TEST_ONLY"):
            self.live_test_counts["selection"]+=1
        if status.startswith("PREP_") and (
                state.get("prep") is not None
                or status in ("PREP_CACHE_REUSED","PREP_CACHE_KNOWN_ONLY")):
            self.live_test_counts["prep"]+=1
        if screen=="MOVE_MENU" and report.get("action",{}).get("kind") in (
                "MOVE","PIVOT"):
            self.live_test_counts["turns"]+=1
        if screen=="PARTY_MENU" and report.get("action",{}).get("kind")=="SWITCH":
            self.live_test_counts["switches"]+=1
        if status in ("OBSERVED","MATCH_END") and screen in (
                "ACTION_RESOLUTION","COMMAND_MENU","RESULT_SCREEN"):
            self.live_test_counts["events"]+=1
        if stage=="ENDED" and state.get("result") in ("WIN","LOSS"):
            self.live_test_counts["result"]=1
        self.live_last_phase=stage
        c=self.live_test_counts
        ended=stage=="ENDED" and bool(c["result"])
        self.integration_status.setText(
            f"統合テスト {'決着確認' if ended else '進行中'}"
            f"／段階 {stage}／直近 {screen}:{status}"
            f"／選出 {c['selection']}／PREP {c['prep']}"
            f"／技判断 {c['turns']}／交代 {c['switches']}"
            f"／イベント {c['events']}"
            f"／勝敗 {state.get('result','UNKNOWN')}"
            "。戦術的な正確性とゲーム操作成功を保証しません。")

    @Slot()
    def _retry_close_after_workers(self):
        if (self._close_after_workers and self.thread is None
                and (self.live_panel is None or
                     (self.live_panel._thread is None
                      and self.live_panel._prep_thread is None))):
            QTimer.singleShot(0,self.close)

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
        self.auto_prep_pending=False
        self.prep_completed=False
        self.selection_started_at=None
        self.selection_display_elapsed_ms=None
        self.turn_image=None
        self.switch_image=None
        self.event_image=None
        self.turn_choose_button.setEnabled(False)
        self.turn_run_button.setEnabled(False)
        self.switch_run_button.setEnabled(False)
        self.event_run_button.setEnabled(False)
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
        self.turn_choose_button.setEnabled(False)
        self.turn_run_button.setEnabled(False)
        self.auto_prep_pending=False
        self.prep_completed=False
        self.prep_attempted=False
        self.test_state=None
        self.selection_started_at=perf_counter()
        self.selection_display_elapsed_ms=None
        self.status.setText("選出画像認識・選出3匹＋初手を解析中。表示後すぐ自動PREPへ進みます（合計90秒）")
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
            +"\n"+("未検証理由" if status in ("PROVISIONAL_ADVICE","PARTIAL_ADVICE") else "失敗理由")
            +": "+str(report.get("reason","なし"))
        )
        icons=(observations.get("opponent_type_icons") if isinstance(observations,dict)
               else None) or report.get("observed_type_icons")
        if isinstance(icons,list) and len(icons)==6:
            result+=("\n視認タイプ（AI読取・未確定）: "
                     + " / ".join("+".join(v) if isinstance(v,list) and v
                                  else "不明" for v in icons))
        conflict=report.get("type_mismatch_slots") or []
        if conflict:
            result+="\nタイプ矛盾枠（要確認）: "+", ".join(
                str(n)+"番" for n in conflict)
        unmatched=report.get("unmatched_opponent_slots") or []
        if unmatched and status in ("BLOCKED","PARTIAL_ADVICE"):
            formatted=" / ".join(
                f"{entry.get('slot','?')}番目: {entry.get('label','?')}"
                for entry in unmatched if isinstance(entry,dict))
            if formatted:
                result+=(
                    "\n相手の照合・再確認対象: "+formatted+
                    "\n※画像認識候補であり、実際のポケモンではない可能性があります。"
                    " 元の対面AI評価データは削除されていません。")
        if "matrix_ai_hint_rows" in report:
            result += (
                f"\n構築別マトリクスAI評価: {report['matrix_ai_hint_rows']}/6匹、"
                f"{report['matrix_ai_hint_pairs']}/36対面"
                f"（構造化v2: {report['matrix_ai_structured_rows']}/6匹）"
                + ("\nAI評価案が未生成: " + " / ".join(report["matrix_ai_missing_opponents"])
                   if report["matrix_ai_missing_opponents"] else "")
            )
        self.details.setText(result)
        self.selection_display_elapsed_ms=(
            round((perf_counter()-self.selection_started_at)*1000)
            if self.selection_started_at is not None else None)
        self.test_state=None
        if (self.selection_started_at is not None
                and status in ("OK","PROVISIONAL_ADVICE","PARTIAL_ADVICE")):
            try:
                from .live_timing_test import selection_test_prep_state
                self.test_state=selection_test_prep_state(self.assets,report)
            except (KeyError,ValueError,TypeError):
                pass
        self.auto_prep_pending=self.test_state is not None
        if self.auto_prep_pending:
            partial=status=="PARTIAL_ADVICE"
            self.timing_status.setText(
                f"選出を{self.selection_display_elapsed_ms/1000:.3f}秒で表示しました。"
                + ("相手未判明枠は推測せず、既知の相手への暫定PREPを自動実行します…"
                   if partial else
                   " 同じ6匹と選出3匹でPREP18対面を自動実行します…"))
        else:
            self.timing_status.setText(
                "未判明の相手があるため、既知の相手から暫定選出しました。"
                "PREP18対面の完了は未達として扱います。"
                if status=="PARTIAL_ADVICE" else
                "選出が確定できず、自動PREPは実行しません。")
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

    def run_turn_test(self):
        self._start_timing_test("TURN")

    def _pick_diagnostic_image(self, title):
        if self.thread is not None:
            return None
        source,_=QFileDialog.getOpenFileName(
            self,title,"","画像 (*.png *.jpg *.jpeg)")
        if not source:
            return None
        try:
            return load_test_image(source)
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,"テスト画像を読み込めません",str(exc))
            return None

    def choose_switch_image(self):
        image=self._pick_diagnostic_image("交代先選択画面")
        if image is not None:
            self.switch_image=image
            self.switch_status.setText("交代画面を読込済み。合法な交代先が表示されているか検証します。")
            self.switch_run_button.setEnabled(
                self.test_state is not None and self.prep_attempted)

    def choose_event_image(self):
        image=self._pick_diagnostic_image("バトルイベント画面")
        if image is not None:
            self.event_image=image
            self.event_status.setText("イベント画像を読込済み。戦闘状態への観測反映を検証します。")
            self.event_run_button.setEnabled(
                self.test_state is not None and self.prep_attempted)

    def run_switch_test(self):
        self._start_timing_test("SWITCH")

    def run_event_test(self):
        self._start_timing_test("EVENT")

    def _start_timing_test(self, mode):
        if self.thread is not None or self.test_state is None:
            return
        if mode=="PREP" and self.selection_started_at is None:
            return
        if mode in ("TURN","SWITCH","EVENT") and not self.prep_attempted:
            return
        if mode=="TURN" and self.turn_image is None:
            return
        if mode=="SWITCH" and self.switch_image is None:
            return
        if mode=="EVENT" and self.event_image is None:
            return
        self.select_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.compare_button.setEnabled(False)
        self.turn_choose_button.setEnabled(False)
        self.turn_run_button.setEnabled(False)
        self.switch_choose_button.setEnabled(False)
        self.switch_run_button.setEnabled(False)
        self.event_choose_button.setEnabled(False)
        self.event_run_button.setEnabled(False)
        self.timing_status.setText(
            "PREP：選出開始から87秒以内に初手6＋控え12＋勝ち筋3点を検査中…"
            if mode=="PREP" else
            "画像認識・判明イベントの反映中…" if mode=="EVENT" else
            "交代先の画面判別・合法手検査中…" if mode=="SWITCH" else
            "ターン30秒：画像認識＋AI判断＋合法手検査中…")
        self.timing_requested_at=perf_counter()
        prep_limit=87.0
        if mode=="PREP":
            # The test begins at the selection button click (not after AI
            # recognition). Reserve three seconds for human game input.
            prep_limit=max(0.0,87.0-(self.timing_requested_at-self.selection_started_at))
            if prep_limit<=1.0:
                self._timing_complete({
                    "mode":"PREP","state":self.test_state,
                    "report":{"status":"PREP_TIMING_FAIL",
                              "reason":"INSUFFICIENT_90S_BUDGET","pass":False,
                              "lead_evaluated":0,"bench_evaluated":0,
                              "win_conditions":0}})
                self._clear_worker()
                return
        self.prep_limit_seconds=prep_limit if mode=="PREP" else 0.0
        if self.log:
            self.log.write(mode+"_TIMING_TEST_START",phase=mode,
                           time_limit_ms=round(prep_limit*1000) if mode=="PREP" else 30000,
                           combined_limit_ms=90000 if mode=="PREP" else 0,
                           selection_ui_elapsed_ms=self.selection_display_elapsed_ms if mode=="PREP" else None,
                           model=self.model)
        self.thread=QThread(self)
        self.worker=_TimingTestWorker(
            mode=mode,image=(
                self.turn_image if mode=="TURN" else
                self.switch_image if mode=="SWITCH" else
                self.event_image if mode=="EVENT" else None),
            assets=self.assets,state=self.test_state,app_root=self.app_root,
            model=self.model,reasoning_effort=self.reasoning_effort,
            requested_at=self.timing_requested_at,
            prep_limit_seconds=prep_limit)
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
        limit=round(self.prep_limit_seconds*1000) if mode=="PREP" else 30000
        report["ui_delivery_elapsed_ms"]=wall
        total=round((perf_counter()-self.selection_started_at)*1000) if (
            mode=="PREP" and self.selection_started_at is not None) else 0
        if mode=="PREP":
            report["selection_ui_elapsed_ms"]=self.selection_display_elapsed_ms
            report["selection_prep_total_ms"]=total
            report["combined_limit_ms"]=90000
            report["human_input_reserve_ms"]=3000
        if wall>limit or (mode=="PREP" and total>87000):
            report["status"]=mode+"_TIMING_FAIL"
            report["reason"]=(
                "COMBINED_90S_BUDGET_EXCEEDED"
                if mode=="PREP" and total>87000
                else "UI_DELIVERY_DEADLINE_EXCEEDED")
            report["pass"]=False
            # UI delivery expired: a model-side partial result is NOT usable
            # inside the game clock, regardless of its structure.
            report["partial_ready"]=False
            report["recommendation"]=""
        if mode=="PREP":
            self.prep_attempted=True
            self.prep_completed=bool(report.get("pass"))
            if report.get("pass") or (
                    report.get("status")=="PREP_TIMING_PARTIAL"
                    and report.get("partial_ready")) or (
                    report.get("status")=="PREP_TIMING_CACHE_INCOMPLETE"
                    and report.get("cache_ready")):
                self.test_state=payload["state"]
            details=(
                f"選出＋自動PREP90秒 "
                f"{'合格' if report.get('pass') else '暫定PREP実行' if report.get('partial_ready') else 'キャッシュ一部未評価' if report.get('cache_ready') else '未達'} "
                f"／選出表示 {self.selection_display_elapsed_ms/1000:.3f}秒"
                f"／PREP表示まで {wall/1000:.3f}秒"
                f"／合計 {total/1000:.3f}秒"
                f"（ゲーム入力用3秒確保）"
                f"／初手 {report.get('lead_evaluated',0)}/6"
                f"／控え {report.get('bench_evaluated',0)}/12"
                f"／勝ち筋 {report.get('win_conditions',0)}/3"
                + (f"／キャッシュ活用 {report.get('cached_matchups',0)}対面"
                   f"／評価不足 {report.get('missing_matchups',0)}対面"
                   f"／役割AI {report.get('source_model_elapsed_ms',0) or 0}ms"
                   if report.get('source_status','').startswith('PREP_CACHE')
                   else "")
                + (f"／既知の相手 {report.get('known_opponents',0)}/6"
                   f"／未評価 {report.get('unknown_matchups',0)}対面"
                   if report.get('partial_ready') else "")
                + f"／理由: {report.get('reason','UNKNOWN')}"
                f"／AI元結果: {report.get('source_status','UNKNOWN')}"
                f"／AI元理由: {report.get('source_reason','-')}"
                "。構造と時間の検証であり、戦術的正確性は未検証。")
        elif mode=="EVENT":
            valid=bool(report.get("pass"))
            if valid:
                self.test_state=payload["state"]
            details=(
                f"バトルイベント読取 {'成功' if valid else '失敗'}"
                f"／画面: {report.get('screen_observation','UNKNOWN')}"
                f"／イベント: {report.get('event','NONE')}"
                f"／結果: {report.get('match_result','UNKNOWN')}"
                f"／状態: {report.get('state_phase','UNKNOWN')}"
                f"／読み取ったメッセージ: {' / '.join(report.get('messages') or []) or 'なし'}"
                f"／所要時間: {wall/1000:.3f}秒"
                f"／理由: {report.get('reason','UNKNOWN')}")
            self.event_status.setText(details)
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
        if mode=="TURN":
            self.turn_status.setText(details)
        elif mode=="SWITCH":
            self.switch_status.setText(details)
        if self.log:
            safe={k:v for k,v in report.items()
                  if k not in ("state","observation","screen_image")}
            self.log.report(safe,event=mode+"_TIMING_TEST_RESULT")

    @Slot()
    def _clear_worker(self):
        self.thread=None
        self.worker=None
        if self._close_after_workers:
            self.auto_prep_pending=False
            self._retry_close_after_workers()
            return
        self.select_button.setEnabled(True)
        self.run_button.setEnabled(self.image is not None)
        self.preview_button.setEnabled(self.image is not None)
        self.compare_button.setEnabled(self.image is not None)
        if self.auto_prep_pending:
            self.auto_prep_pending=False
            # Qt's previous worker thread has fully ended. Immediately start
            # PREP using the frozen selection and the ORIGINAL 90s clock.
            self._start_timing_test("PREP")
            return
        self.turn_choose_button.setEnabled(
            self.prep_attempted and self.test_state is not None)
        self.turn_run_button.setEnabled(
            self.prep_attempted and self.test_state is not None
            and self.turn_image is not None)
        self.switch_choose_button.setEnabled(True)
        self.event_choose_button.setEnabled(True)
        self.switch_run_button.setEnabled(
            self.prep_attempted and self.test_state is not None
            and self.switch_image is not None)
        self.event_run_button.setEnabled(
            self.prep_attempted and self.test_state is not None
            and self.event_image is not None)

    def closeEvent(self,event):
        self._close_after_workers=True
        # Close is a one-shot request, not a dialog asking the user to keep
        # retrying. Cancellation can still await an in-flight HTTP timeout.
        self.auto_prep_pending=False
        if self.live_panel is not None:
            if not self.live_panel._shutdown_ready:
                self.live_panel.request_shutdown("close")
        if (self.thread is not None or
                (self.live_panel is not None and (
                 self.live_panel._thread is not None or
                 self.live_panel._prep_thread is not None))):
            self.integration_status.setText(
                "終了要求を受け付けました。新規AI送信を停止し、"
                "実行中の通信終了後にこの画面を自動的に閉じます。")
            event.ignore()
            return
        if self.log:
            self.log.write("SELECTION_TEST_CLOSED")
        super().closeEvent(event)
