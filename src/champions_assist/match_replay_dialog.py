"""Opt-in automatic read-only live capture and AI match-support GUI.

Frames come from a scoped game window/ADB and stay in memory. The player
explicitly starts/stops capture once per match; game inputs remain manual.
The four frozen match assets are loaded once, never re-read during battle.
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from .app_config import default_app_root
from .preload import MasterPreloader
from .siwc_auth import ChatGPTSignIn, ProtectedSessionStore, SignInError
from .match_replay import new_replay, confirm_actual_selection, run_replay_image, run_replay_prep
from .auto_match_capture import capture_for_chatgpt, FrameChangeGate, AutoCaptureUnavailable
from .app_config import CaptureConfiguration


class _ReplayWorker(QObject):
    finished = Signal(object)

    def __init__(self, *, app_root, selected_model, assets, state, job='auto_frame',
                 reasoning_effort='auto',
                 capture_config=None, capture_gate=None, capture_function=capture_for_chatgpt,
                 cancel_event=None):
        super().__init__()
        self.job=job
        self.app_root=app_root
        self.selected_model=selected_model
        self.reasoning_effort=reasoning_effort
        self.capture_config=capture_config
        self.capture_gate=capture_gate
        self.capture_function=capture_function
        self.cancel_event=cancel_event
        self.assets=assets
        self.state=state

    @Slot()
    def run(self):
        try:
            auth=ChatGPTSignIn(ProtectedSessionStore(self.app_root))
            if self.job=="prep":
                next_state, report=run_replay_prep(
                    state=self.state, assets=self.assets,
                    access_token=auth.access_token(),model=self.selected_model,
                    reasoning_effort=self.reasoning_effort,
                )
            elif self.job=="auto_frame":
                # Capture and model transport run on this worker thread.
                # Do not materialize the RGB data on disk, ever.
                from time import perf_counter
                started=perf_counter()
                image=self.capture_function(self.capture_config)
                if self.cancel_event is not None and self.cancel_event.is_set():
                    self.finished.emit({"state":self.state,
                                        "report":{"status":"CANCELLED"}})
                    return
                if not self.capture_gate.accept(image):
                    self.finished.emit({"state":self.state,
                                        "report":{"status":"UNCHANGED_FRAME"}})
                    return
                token=auth.access_token()
                # Both capture and credential refresh consume the 35s budget.
                capture_and_auth_ms=round((perf_counter()-started)*1000)
                remaining=35.0-(perf_counter()-started)
                if self.cancel_event is not None and self.cancel_event.is_set():
                    next_state,report=self.state,{"status":"CANCELLED"}
                elif remaining <= 1.0:
                    next_state,report=self.state,{"status":"DEADLINE_EXCEEDED"}
                else:
                    next_state,report=run_replay_image(
                        image, state=self.state, assets=self.assets,
                        access_token=token,model=self.selected_model,
                        reasoning_effort=self.reasoning_effort,
                        total_seconds=remaining,
                    )
                report["capture_and_auth_ms"]=capture_and_auth_ms
                report["elapsed_ms"]=round((perf_counter()-started)*1000)
                self.finished.emit({"state":next_state,"report":report})
            else:
                raise ValueError("UNKNOWN_REPLAY_JOB")
        except Exception as exc:
            self.finished.emit({"state":self.state,"report":{
                "status":"ERROR","reason":str(exc) if isinstance(exc,SignInError)
                else type(exc).__name__}})


class MatchReplayDialog(QDialog):
    def __init__(self, *, model: str, master_directory: str, team_path: str,
                 capture_config: CaptureConfiguration | None = None,
                 store_root: str | Path | None = None, parent: QWidget | None = None,
                 reasoning_effort: str = "auto"):
        super().__init__(parent)
        self.setWindowTitle("マッチング～対戦終了 自動キャプチャ・AI支援（ゲーム操作なし）")
        self.setMinimumSize(790,620)
        self.model=model
        self.reasoning_effort=reasoning_effort
        self.master_directory=master_directory
        self.team_path=team_path
        self.store_root=Path(store_root) if store_root else default_app_root()
        self.capture_config=capture_config
        self._capture_gate=FrameChangeGate()
        self._cancel_event=Event()
        self._auto_running=False
        self._poll_timer=QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._capture_next)
        self.assets=None
        self.state=None
        self.entries=[]
        self._thread=None
        self._worker=None
        self._active_job=None
        panel=QVBoxLayout(self)
        intro=QLabel(
            "開始を一度押すと、設定したゲーム画面を自動キャプチャしてChatGPTに送信します。\\n"
            "画像ファイルは保存しません。停止はいつでも可能。ゲームの操作は行いません。"
        )
        intro.setWordWrap(True)
        panel.addWidget(intro)
        self.status=QLabel("未開始 / 自動送信は停止中")
        panel.addWidget(self.status)
        row=QHBoxLayout()
        self.start_button=QPushButton("新しいテスト対戦を開始（4ファイル事前読込）")
        self.start_button.clicked.connect(self._new_match)
        self.reset_button=QPushButton("テスト終了・リセット")
        self.reset_button.clicked.connect(self._reset)
        row.addWidget(self.start_button)
        row.addWidget(self.reset_button)
        panel.addLayout(row)
        row=QHBoxLayout()
        self.auto_button=QPushButton("ゲーム画面を自動キャプチャしてAI支援開始")
        self.auto_button.clicked.connect(self._start_auto)
        self.stop_button=QPushButton("自動送信を停止")
        self.stop_button.clicked.connect(self._stop_auto)
        row.addWidget(self.auto_button)
        row.addWidget(self.stop_button)
        panel.addLayout(row)
        panel.addWidget(QLabel("実際に使った3匹と初手（選出提案を確認後、手動で確定）"))
        row=QHBoxLayout()
        self.actual_three=QLineEdit()
        self.actual_three.setPlaceholderText("3匹を / で区切る（例：ピカチュウ / マリルリ / ギャラドス）")
        self.actual_lead=QLineEdit()
        self.actual_lead.setPlaceholderText("実際の初手")
        self.confirm_button=QPushButton("実際の選出を確定")
        self.confirm_button.clicked.connect(self._confirm_selection)
        row.addWidget(self.actual_three,2)
        row.addWidget(self.actual_lead,1)
        row.addWidget(self.confirm_button)
        panel.addLayout(row)
        self.prep_button=QPushButton("選出後の40秒PREPをテスト（18対面）")
        self.prep_button.clicked.connect(self._prep)
        panel.addWidget(self.prep_button)
        panel.addWidget(QLabel("画像の認識・AI提案・進行ログ（現在の1試合）"))
        self.output=QPlainTextEdit()
        self.output.setReadOnly(True)
        panel.addWidget(self.output,1)
        self.export_button=QPushButton("テストログをJSON保存（画像本体・認証情報は含めない）")
        self.export_button.clicked.connect(self._export)
        panel.addWidget(self.export_button)
        self._render({"status":"NOT_STARTED"})

    def _new_match(self):
        if self._thread is not None:return
        if self._auto_running:
            QMessageBox.warning(self, "停止が必要", "自動送信を停止してから新しい対戦を開始してください。")
            return
        try:
            if not self.master_directory or not self.team_path:
                raise ValueError("次戦用の構築を先に選択してください。")
            self.assets=MasterPreloader(self.master_directory,
                                        team_source=self.team_path).load_for_new_match()
            self.state=new_replay(self.assets)
            self.entries=[]
            self.actual_three.clear()
            self.actual_lead.clear()
            self._capture_gate.reset()
            self._render({"status":"MATCH_STARTED","message":"4ファイル事前読込完了。自動キャプチャ開始で進めます。"})
        except Exception as exc:
            self.assets=None
            self.state=None
            QMessageBox.warning(self,"テスト開始不可",str(exc))

    def _reset(self):
        if self._thread is not None:return
        self._stop_auto()
        self.assets=None
        self.state=None
        self.entries=[]
        self.actual_three.clear()
        self.actual_lead.clear()
        self._render({"status":"RESET"})

    def _start_auto(self):
        if self._thread is not None or self._auto_running:
            return
        if self.state is None or self.assets is None:
            QMessageBox.warning(self, "未開始", "先に新しい対戦を開始してください。")
            return
        if not self.model or self.capture_config is None:
            QMessageBox.warning(self, "設定不足", "ChatGPTモデルとキャプチャ設定を確認してください。")
            return
        if not self.capture_config.game_viewport_normalized:
            QMessageBox.warning(self, "ROI未設定", "設定のゲーム画面範囲を指定してください。")
            return
        if QMessageBox.question(self, "自動送信の確認",
                "指定したゲーム画面を継続的に読み取り、変化した画像をChatGPTに自動送信します。"
                "送信のたびの確認は行いません。画像はローカル保存しません。開始しますか？",
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._auto_running=True
        self._cancel_event.clear()
        self._capture_gate.reset()
        self._poll_timer.start()
        self._capture_next()

    def _stop_auto(self):
        self._cancel_event.set()
        self._auto_running=False
        self._poll_timer.stop()

    def _capture_next(self):
        if not self._auto_running or self._thread is not None:
            return
        if self.state is None or self.state.get("phase") in ("ENDED", "SELECTION"):
            if self.state is not None and self.state.get("phase")=="ENDED":
                self._stop_auto()
            return
        self._set_busy(True)
        self._active_job="auto_frame"
        self._thread=QThread(self)
        self._worker=_ReplayWorker(
            app_root=self.store_root, selected_model=self.model,
            reasoning_effort=self.reasoning_effort,
            assets=self.assets,state=self.state,job="auto_frame",
            capture_config=self.capture_config,capture_gate=self._capture_gate,
            cancel_event=self._cancel_event,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._completed)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    @Slot(object)
    def _completed(self, payload):
        report=payload["report"]
        if self._active_job=="auto_frame" and not self._auto_running:
            return
        if report.get("status") == "UNCHANGED_FRAME":
            return
        if report.get("status") == "ERROR" and report.get("reason") == "AutoCaptureUnavailable":
            self._stop_auto()
        if report["status"] not in ("ERROR","DEADLINE_EXCEEDED"):
            self.state=payload["state"]
        entry={"step":len(self.entries)+1,**report}
        self.entries.append(entry)
        if report.get("screen")=="TEAM_SELECTION" and report.get("selected_three"):
            self.actual_three.setText(" / ".join(report["selected_three"]))
            self.actual_lead.setText(report.get("lead") or "")
        self._render(entry)
        if self.state is not None and self.state.get("phase")=="ENDED":
            self._stop_auto()

    def _confirm_selection(self):
        if self._thread is not None:return
        if self.state is None or self.assets is None:
            QMessageBox.warning(self,"未開始","最初にテスト対戦を開始してください。")
            return
        try:
            three=[x.strip() for x in self.actual_three.text().split("/") if x.strip()]
            self.state=confirm_actual_selection(self.state,self.assets,three,self.actual_lead.text().strip())
            self.entries.append({"step":len(self.entries)+1,"operation":"CONFIRM_SELECTION",
                                 "selected_three":self.state["selected_three"],
                                 "lead":self.state["lead"],"status":"CONFIRMED"})
            self._render(self.entries[-1])
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self,"実際の選出を確定できません",str(exc))

    def _prep(self):
        if self._thread is not None:return
        if self.state is None or self.state["phase"]!="PREP":
            QMessageBox.warning(self,"PREP不可","まず実際の選出3匹と初手を確定してください。")
            return
        if QMessageBox.question(
            self,"PREPテストの確認",
            "40秒のPREP試験を実行しますか？構築専用のLIVEマトリクスがなければ評価を行いません。",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )!=QMessageBox.StandardButton.Yes:return
        self._set_busy(True)
        self._active_job="prep"
        self._thread=QThread(self)
        self._worker=_ReplayWorker(
            app_root=self.store_root, selected_model=self.model,
            reasoning_effort=self.reasoning_effort,
            assets=self.assets, state=self.state, job="prep")
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._completed)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    def _set_busy(self,busy):
        for widget in (self.start_button,self.reset_button,self.confirm_button,self.prep_button,self.auto_button):
            widget.setEnabled(not busy)

    @Slot()
    def _cleanup(self):
        self._thread=None
        self._worker=None
        self._active_job=None
        self._set_busy(False)

    def _render(self, report):
        state=self.state or {}
        phase=state.get("phase","NOT_STARTED")
        compatible=state.get("matrix_compatible",False)
        suffix="構築別LIVEマトリクス有効" if compatible else "LIVEマトリクス不一致・機能試験のみ"
        sending = "自動送信中" if self._auto_running else "自動送信停止"
        self.status.setText(f"段階：{phase} / {suffix} / {sending} / 記録 {len(self.entries)} 件")
        rec=report.get("recommendation","") or report.get("message","")
        if report.get("status")=="TEST_ONLY":
            rec="【機能試験のみ・戦術的な妥当性未検証】"+rec
        line=(rec+"\n\n") if rec else ""
        self.output.setPlainText(
            line+json.dumps({"latest":report,"replay_state":state,
                             "timeline":self.entries},ensure_ascii=False,indent=2))
        self.output.verticalScrollBar().setValue(0)

    def _export(self):
        if self.state is None:return
        dest,_=QFileDialog.getSaveFileName(self,"対戦テスト結果を保存","","JSON (*.json)")
        if not dest:return
        report={"scope":"OPT_IN_AUTO_CAPTURE_MATCH_SUPPORT_NO_IMAGE_STORAGE","model":self.model,
                "reasoning_effort":self.reasoning_effort,
                "matrix_compatible":self.state["matrix_compatible"],
                "state":self.state,"timeline":self.entries}
        try:
            with open(dest,"x",encoding="utf-8") as output:
                json.dump(report,output,ensure_ascii=False,indent=2)
        except FileExistsError:
            QMessageBox.warning(self,"既存ファイル","上書きしません。別の名前を選んでください。")
        except OSError:
            QMessageBox.warning(self,"保存不可","指定の場所に書き込めません。")

    def closeEvent(self, event):
        self._stop_auto()
        if self._thread is not None:
            # Never destroy a QThread while a network request is still
            # running. Closing will be possible after it completes.
            event.ignore()
            return
        super().closeEvent(event)
