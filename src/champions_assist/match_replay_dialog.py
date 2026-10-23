"""Opt-in automatic read-only live capture and AI match-support GUI.

Frames come from a scoped game window/ADB and stay in memory. The player
explicitly starts/stops capture once per match; game inputs remain manual.
The four frozen match assets are loaded once, never re-read during battle.
"""
from __future__ import annotations

import json
from dataclasses import replace as dataclass_replace
from datetime import datetime
from pathlib import Path
from threading import Event
from time import monotonic
from uuid import uuid4

import numpy as np
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from .app_config import default_app_root
from .preload import MasterPreloader
from .siwc_auth import ChatGPTSignIn, ProtectedSessionStore, SignInError
from .match_replay import new_replay, confirm_actual_selection, run_replay_image, run_replay_prep, run_replay_batch
from .turn_capture import TurnCaptureBuffer, AutomaticVisualProbe
from .screen_classifier import ClassifiedMenuGate, load_bundled_classifier
from .auto_match_capture import capture_for_chatgpt, FrameChangeGate, AutoCaptureUnavailable
from .capture_settings import CapturePreviewService
from .app_config import CaptureConfiguration


class _ReplayWorker(QObject):
    finished = Signal(object)
    progress = Signal(str)

    def __init__(self, *, app_root, selected_model, assets, state, job='auto_frame',
                 reasoning_effort='auto',
                 capture_config=None, capture_gate=None, capture_function=capture_for_chatgpt,
                 cancel_event=None, frames=None):
        super().__init__()
        self.job=job
        self.frames=frames
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
            elif self.job=="batch":
                if self.cancel_event is not None and self.cancel_event.is_set():
                    self.finished.emit({"state":self.state,"report":{"status":"CANCELLED"}})
                    return
                self.progress.emit("ChatGPT認証確認中")
                token=auth.access_token()
                self.progress.emit("AI画像送信処理中（結果待ち）")
                if self.state.get("phase") in ("MATCHMAKING","SELECTION"):
                    # Team preview needs the exact six LIVE matrix rows AFTER
                    # reading the opponent icons; do not guess selection in
                    # a single blind request. Only battle actions are batched.
                    next_state,report=run_replay_image(
                        self.frames[-1],state=self.state,assets=self.assets,
                        access_token=token,model=self.selected_model,
                        reasoning_effort=self.reasoning_effort,
                    )
                    report["transport"]="TWO_STAGE_SELECTION_MATRIX_FIRST"
                else:
                    next_state,report=run_replay_batch(
                        self.frames, state=self.state, assets=self.assets,
                        access_token=token,model=self.selected_model,
                        reasoning_effort=self.reasoning_effort,
                    )
                self.finished.emit({"state":next_state,"report":report})
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
    """One live-session engine and UI; also embeddable in the main window.

    This widget owns the ONLY active visual replay state and its 4-file preload.
    The main window observes state_changed and never constructs another FSM.
    """
    state_changed = Signal(object, object)
    ai_status_changed = Signal(str)

    def __init__(self, *, model: str, master_directory: str, team_path: str,
                 capture_config: CaptureConfiguration | None = None,
                 store_root: str | Path | None = None, parent: QWidget | None = None,
                 reasoning_effort: str = "auto", embedded: bool = False,
                 before_new_match=None, on_capture_reconnected=None):
        super().__init__(parent)
        self.embedded = embedded
        self.before_new_match = before_new_match
        self.on_capture_reconnected = on_capture_reconnected
        self.match_id = None
        if embedded:
            # A QDialog ordinarily becomes a separate native window even with
            # a parent. Qt.Widget keeps the existing tested controls inline.
            self.setWindowFlags(Qt.WindowType.Widget)
        else:
            self.setWindowTitle("マッチング～対戦終了 自動キャプチャ・AI支援（ゲーム操作なし）")
            self.setMinimumSize(790,620)
        self.model=model
        self.reasoning_effort=reasoning_effort
        self.master_directory=master_directory
        self.team_path=team_path
        self.store_root=Path(store_root) if store_root else default_app_root()
        self.capture_config=capture_config
        self._capture_gate=FrameChangeGate()
        self._turn_buffer=TurnCaptureBuffer()
        self._automatic_probe=AutomaticVisualProbe()
        self._screen_gate=ClassifiedMenuGate()
        # Missing/unvalidated training weights are not silently treated as
        # a working visual model. Default CPU is cheap and deterministic.
        self._screen_model,self._screen_model_status=load_bundled_classifier(device="cpu")
        self._last_local_class=None
        self._pending_batch=None
        self._manual_submission=False
        self._latest_image=None
        self._preview_last_at=None
        self._preview_last_clock=None
        self._capture_error_count=0
        self._capture_paused=False
        self._cancel_event=Event()
        self._auto_running=False
        self._poll_timer=QTimer(self)
        self._poll_timer.setInterval(300)
        self._poll_timer.timeout.connect(self._capture_next)
        self.assets=None
        self.state=None
        self.entries=[]
        self._thread=None
        self._worker=None
        self._active_job=None
        self._ai_request_attempts=0
        self._ai_last_status="AI未送信（撮影・メニュー認識待ち）"
        panel=QVBoxLayout(self)
        intro=QLabel(
            "開始を一度押すと、設定したゲーム画面を自動キャプチャしてChatGPTに送信します。\\n"
            "画像ファイルは保存しません。停止はいつでも可能。ゲームの操作は行いません。"
        )
        intro.setWordWrap(True)
        panel.addWidget(intro)
        self.status=QLabel("未開始 / 自動送信は停止中")
        panel.addWidget(self.status)
        self.ai_status_label=QLabel(self._ai_last_status)
        self.ai_status_label.setObjectName("ai_dispatch_status")
        self.ai_status_label.setWordWrap(True)
        panel.addWidget(self.ai_status_label)
        self.menu_detection_label=QLabel("メニュー登録: なし")
        self.menu_detection_label.setWordWrap(True)
        panel.addWidget(self.menu_detection_label)
        self.local_classifier_label=QLabel(self._local_classifier_message())
        self.local_classifier_label.setWordWrap(True)
        panel.addWidget(self.local_classifier_label)
        # Render the same frames that go to AI, without another acquisition.
        self.capture_preview_title=QLabel("キャプチャプレビュー（ChatGPT送信対象のゲーム領域）")
        panel.addWidget(self.capture_preview_title)
        self.capture_preview=QLabel("監視開始後にここへ撮影画像を表示します")
        self.capture_preview.setObjectName("live_capture_preview")
        self.capture_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.capture_preview.setMinimumHeight(110)
        self.capture_preview.setMaximumHeight(220)
        self.capture_preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.capture_preview.setStyleSheet(
            "QLabel#live_capture_preview { background-color: #171c24; color: #e8e8e8; border: 1px solid #727880; }")
        panel.addWidget(self.capture_preview)
        self.capture_preview_info=QLabel("撮影待機中 / 画像は保存されません")
        panel.addWidget(self.capture_preview_info)
        row=QHBoxLayout()
        self.start_button=QPushButton("対戦開始（4ファイル事前読込）")
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
        self.reconnect_button=QPushButton("撮影対象ウィンドウを再選択（ミラーリング対応）")
        self.reconnect_button.clicked.connect(self._reconnect_capture_window)
        panel.addWidget(self.reconnect_button)
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
        calibration=QHBoxLayout()
        for kind,label in (("TEAM_SELECTION","選出画面を登録"),
                           ("MOVE_MENU","技選択画面を登録"),
                           ("PARTY_MENU","交代画面を登録")):
            button=QPushButton(label)
            button.clicked.connect(lambda _checked=False, k=kind:self._calibrate(k))
            calibration.addWidget(button)
        panel.addLayout(calibration)
        self.manual_batch_button=QPushButton("現在までの重要画像をまとめてAIへ送信（手動保険）")
        self.manual_batch_button.clicked.connect(self._manual_batch)
        panel.addWidget(self.manual_batch_button)
        panel.addWidget(QLabel("自動送信は登録した選択画面への遷移時のみ。登録前は手動送信を使用してください。"))
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
            if self.before_new_match is not None:
                self.before_new_match()
            if not self.master_directory or not self.team_path:
                raise ValueError("次戦用の構築を先に選択してください。")
            self.assets=MasterPreloader(self.master_directory,
                                        team_source=self.team_path).load_for_new_match()
            self.state=new_replay(self.assets)
            self.match_id="capture-"+uuid4().hex
            self.entries=[]
            self.actual_three.clear()
            self.actual_lead.clear()
            self._capture_gate.reset()
            self._turn_buffer.reset_turn()
            self._automatic_probe.reset()
            self._screen_gate.reset()
            self._last_local_class=None
            self._pending_batch=None
            self._ai_request_attempts=0
            self._set_ai_status("AI未送信（選出画面の登録／検出待ち）")
            self._update_menu_detection_label()
            self._render({"status":"MATCH_STARTED","message":"4ファイル事前読込完了。自動キャプチャ開始で進めます。"})
        except Exception as exc:
            self.assets=None
            self.state=None
            self.match_id=None
            QMessageBox.warning(self,"テスト開始不可",str(exc))

    def _reset(self):
        if self._thread is not None:return
        self._stop_auto()
        self._turn_buffer.reset_turn()
        self._automatic_probe.reset()
        self._screen_gate.reset()
        self._last_local_class=None
        self._pending_batch=None
        self.assets=None
        self.state=None
        self.entries=[]
        self.actual_three.clear()
        self.actual_lead.clear()
        self._set_ai_status("AI未送信（対戦未開始）")
        self._render({"status":"RESET"})
        self.match_id=None

    def _show_capture_preview(self, image: np.ndarray, *, force: bool = False) -> None:
        """Render an already captured RGB frame; no new screenshot or API call."""
        now=monotonic()
        if not force and self._preview_last_at is not None and now-self._preview_last_at<0.5:
            return
        rgb=np.ascontiguousarray(image)
        height,width=rgb.shape[:2]
        rendered=QImage(rgb.data,width,height,rgb.strides[0],QImage.Format.Format_RGB888).copy()
        size=self.capture_preview.size()
        pixmap=QPixmap.fromImage(rendered).scaled(
            max(1,size.width()-8),max(1,size.height()-8),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.capture_preview.setPixmap(pixmap)
        self._preview_last_at=now
        self._preview_last_clock=datetime.now().strftime("%H:%M:%S")
        self.capture_preview_info.setText(
            f"撮影成功 {self._preview_last_clock} / 送信範囲 {width}×{height} px / "
            "プレビューは最大2回/秒・画像ファイル保存なし")

    def _clear_capture_preview(self, message: str) -> None:
        self._preview_last_at=None
        self._preview_last_clock=None
        self.capture_preview.clear()
        self.capture_preview.setText(message)
        self.capture_preview_info.setText("取得画像なし / 画像ファイル保存なし")

    def _reconnect_capture_window(self) -> None:
        """Explicitly select and validate the CURRENT window, no auto-retarget."""
        if self._thread is not None:
            QMessageBox.information(self,"AI処理中","画像認識処理が終わってから撮影対象を変更してください。")
            return
        if self.capture_config is None or self.capture_config.backend!="WINDOWS_NATIVE":
            QMessageBox.warning(self,"撮影方式が違います","Windowsネイティブ方式の対象ウィンドウを選択してください。")
            return
        try:
            candidates=CapturePreviewService().list_windows()
            if not candidates:
                raise AutoCaptureUnavailable("WINDOW_LIST_EMPTY")
            labels=[candidate.display_name() for candidate in candidates]
            current=self.capture_config.selected_window_identity
            selected_index=next((i for i,item in enumerate(candidates)
                                 if item.identity()==current),0)
            picked,ok=QInputDialog.getItem(
                self,"撮影対象の再接続",
                "現在表示されているスマホのミラーリング画面を明示的に選択してください。\n"
                "選択後はそのウィンドウだけを撮影確認し、別対象へ自動切替しません。",
                labels,selected_index,False)
            if not ok:
                return
            candidate=candidates[labels.index(picked)]
            config=dataclass_replace(
                self.capture_config,selected_window_identity=candidate.identity())
            # Identical capture path to the running loop.
            frame=capture_for_chatgpt(config)
            if self.on_capture_reconnected is not None:
                self.on_capture_reconnected(config)
            self._stop_auto()
            self.capture_config=config
            self._capture_gate.reset()
            # Recalibrate command screens if target/window content changed.
            self._turn_buffer=TurnCaptureBuffer()
            self._automatic_probe.reset()
            self._screen_gate.reset()
            self._last_local_class=None
            self._update_menu_detection_label()
            self._set_ai_status("AI未送信（再接続後、画面登録をやり直してください）")
            self._show_capture_preview(frame,force=True)
            self.capture_preview_info.setText(
                self.capture_preview_info.text()+" / 撮影対象の再接続済み・監視は停止中")
            report={"step":len(self.entries)+1,"status":"CAPTURE_TARGET_RECONNECTED",
                    "message":"選択したウィンドウの撮影に成功しました。"
                              "必要な画面登録をし直し、明示的に自動キャプチャを再開してください。"}
            self.entries.append(report)
            self._render(report)
        except (AutoCaptureUnavailable,OSError,ValueError) as exc:
            QMessageBox.warning(self,"撮影対象の再接続に失敗",str(exc))

    def _local_classifier_message(self) -> str:
        if self._screen_model is not None:
            return "画面判別: 検証用ONNX分類器 / CPU / 画面登録不要"
        return ("画面判別: 学習済みモデル未配置（"+self._screen_model_status+
                "）。CPU画像変化検出 → ChatGPT確認。精度未検証")

    def _set_ai_status(self, status: str) -> None:
        """Status of the AI workflow, separate from local capture state."""
        self._ai_last_status=status
        self.ai_status_label.setText(status)
        self.ai_status_changed.emit(status)

    def _update_menu_detection_label(self) -> None:
        names={"TEAM_SELECTION":"選出画面",
               "MOVE_MENU":"技選択画面","PARTY_MENU":"交代画面"}
        found=[names[k] for k in names if k in self._turn_buffer.templates]
        self.menu_detection_label.setText(
            "標準自動認識: 有効 / 補助登録: "+
            (" / ".join(found) if found else "なし（登録不要）"))

    def _eligible_menu(self, kind: str) -> bool:
        phase=(self.state or {}).get("phase")
        return (
            kind=="TEAM_SELECTION" and phase in ("MATCHMAKING","SELECTION")
            or kind in ("MOVE_MENU","PARTY_MENU") and phase in ("PREP","BATTLE")
        )

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
                "指定したゲーム画面をローカルで継続監視します。登録した選択画面を検出した時のみ、"
                "直近の重要画像をまとめてChatGPTへ送信します。送信ごとの確認は行いません。"
                "画像はファイル保存しません。開始しますか？",
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._auto_running=True
        self._capture_error_count=0
        self._capture_paused=False
        self._poll_timer.setInterval(300)
        self._cancel_event.clear()
        self._capture_gate.reset()
        self._turn_buffer.reset_turn()
        self._automatic_probe.reset()
        self._screen_gate.reset()
        self._last_local_class=None
        self._pending_batch=None
        self._clear_capture_preview("撮影開始済み・最初のフレームを取得中")
        self._poll_timer.start()
        # Starting capture must update the status before a menu triggers AI.
        self._render(self.entries[-1] if self.entries else {"status":"AUTO_STARTED"})
        self._update_menu_detection_label()
        self._set_ai_status("標準自動判定有効（最初の画面からAI確認を開始）")
        self._capture_next()

    def _stop_auto(self):
        self._cancel_event.set()
        self._auto_running=False
        self._capture_paused=False
        self._capture_error_count=0
        self._poll_timer.stop()
        self._poll_timer.setInterval(300)
        self._pending_batch=None
        self._latest_image=None
        self._automatic_probe.reset()
        self._screen_gate.reset()
        self._clear_capture_preview("監視停止中")
        if self._thread is None:
            self._set_ai_status("AI監視停止（新規画像送信なし）")
        # Previously the UI could still say "自動送信中" after stopping.
        if self.state is not None:
            self._render(self.entries[-1] if self.entries else {"status":"AUTO_STOPPED"})

    def _calibrate(self, kind):
        if self.capture_config is None:
            QMessageBox.warning(self,"ROI未設定","先に画面キャプチャを設定してください。")
            return
        try:
            image=capture_for_chatgpt(self.capture_config)
            if self._auto_running and self._thread is None and self._eligible_menu(kind):
                self._turn_buffer.acknowledge_calibrated_current(kind,image)
                self._update_menu_detection_label()
                self._render({"status":"MENU_CALIBRATED","kind":kind})
                if not self._automatic_probe.recently_submitted(image):
                    self._automatic_probe.acknowledge(image,self.state["phase"])
                    self._screen_gate.acknowledge(kind,self.state["phase"])
                    self._queue_batch(self._turn_buffer.batch(image))
            else:
                self._turn_buffer.calibrate(kind,image)
                self._update_menu_detection_label()
                self._render({"status":"MENU_CALIBRATED","kind":kind})
                if self._auto_running and self._thread is None:
                    self._set_ai_status("AI未送信（現在のフェーズでは登録画面を送信しません）")
        except (AutoCaptureUnavailable,ValueError) as exc:
            QMessageBox.warning(self,"画面登録不可",str(exc))

    def _manual_batch(self):
        if self.capture_config is None or self.state is None:
            QMessageBox.warning(self,"開始前","対戦とキャプチャの設定が必要です。")
            return
        try:
            image=capture_for_chatgpt(self.capture_config)
            self._latest_image=image
            # A deliberate manual resend can restart a previously stopped session.
            self._cancel_event.clear()
            self._manual_submission=True
            try:
                self._queue_batch(self._turn_buffer.batch(image))
            finally:
                self._manual_submission=False
        except AutoCaptureUnavailable as exc:
            QMessageBox.warning(self,"撮影失敗",str(exc))

    def _queue_batch(self,frames):
        if not self._auto_running and not self._manual_submission:
            return
        # At most one pending batch: new observed events replace stale ones.
        self._pending_batch=list(frames)
        self._send_pending()

    def _send_pending(self):
        if self._thread is not None or self._pending_batch is None:
            return
        if self.state is None or self.state.get("phase")=="ENDED":
            self._pending_batch=None
            return
        frames=self._pending_batch
        self._pending_batch=None
        self._turn_buffer.reset_turn()
        self._ai_request_attempts+=1
        self._set_ai_status(
            f"AI処理を起動中 #{self._ai_request_attempts}（画像 {len(frames)} 枚・送信は未確認）")
        request_report={
            "step":len(self.entries)+1, "status":"AI_REQUEST_QUEUED",
            "ai_request_attempt":self._ai_request_attempts,
            "image_count":len(frames),
            "message":"AI処理を起動しました。送信成功はまだ確認できていません。"}
        self.entries.append(request_report)
        self._render(request_report)
        self._set_busy(True)
        self._active_job="batch"
        self._thread=QThread(self)
        self._worker=_ReplayWorker(
            app_root=self.store_root,selected_model=self.model,
            reasoning_effort=self.reasoning_effort,
            assets=self.assets,state=self.state,job="batch",
            frames=frames,cancel_event=self._cancel_event,
        )
        self._worker.moveToThread(self._thread)
        self._worker.progress.connect(self._on_ai_progress)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._completed)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    @Slot(str)
    def _on_ai_progress(self, progress: str) -> None:
        self._set_ai_status(f"{progress} / 試行 #{self._ai_request_attempts}")

    def _capture_next(self):
        if not self._auto_running:
            return
        if self.state is None or self.state.get("phase")=="ENDED":
            if self.state is not None and self.state.get("phase")=="ENDED":
                self._stop_auto()
            return
        try:
            image=capture_for_chatgpt(self.capture_config)
        except AutoCaptureUnavailable as exc:
            self._capture_error_count+=1
            if self._capture_error_count>=3 and not self._capture_paused:
                # A transient BlueStacks/Win32/ADB miss must not silently
                # disable user-approved monitoring. Remain opted-in and
                # retry slowly until the window can be captured again.
                self._capture_paused=True
                self._poll_timer.setInterval(2000)
                report={"step":len(self.entries)+1,"status":"CAPTURE_PAUSED",
                        "reason":str(exc),"consecutive_failures":self._capture_error_count,
                        "message":"画面取得に失敗しました。自動送信は停止せず、2秒間隔で再試行します。"}
                self.entries.append(report)
                self._render(report)
                stale=("最後に取得した画像 "+self._preview_last_clock
                       if self._preview_last_clock else "取得できた画像なし")
                self.capture_preview_info.setText(
                    f"画面取得失敗: {exc} / {stale} / 2秒間隔で再試行")
            return
        self._capture_error_count=0
        if self._capture_paused:
            self._capture_paused=False
            self._poll_timer.setInterval(300)
            report={"step":len(self.entries)+1,"status":"CAPTURE_RESUMED",
                    "message":"画面の取得が復旧しました。自動送信を継続します。"}
            self.entries.append(report)
            self._render(report)
        self._latest_image=image
        self._show_capture_preview(image)
        kind=self._turn_buffer.observe(image)
        phase=self.state.get("phase")
        if kind is not None and self._eligible_menu(kind):
            self._automatic_probe.acknowledge(image,phase)
            self._screen_gate.acknowledge(kind,phase)
            self._queue_batch(self._turn_buffer.batch(image))
            return

        if self._screen_model is not None:
            try:
                prediction=self._screen_model.predict(image)
            except (ValueError,RuntimeError,TypeError) as exc:
                # Do not disable capture or fabricate a classification.
                self._screen_model=None
                self._screen_model_status="MODEL_RUNTIME_ERROR_"+type(exc).__name__
                self.local_classifier_label.setText(self._local_classifier_message())
                self._automatic_probe.reset()
            else:
                if prediction.label!=self._last_local_class:
                    self._last_local_class=prediction.label
                    self.local_classifier_label.setText(
                        f"ローカル画面判別: {prediction.label} / "
                        f"確信度 {prediction.confidence:.2f} / "
                        f"{prediction.latency_ms:.1f}ms / {prediction.source}")
                classified=self._screen_gate.observe(prediction,phase)
                if classified is not None:
                    self._automatic_probe.acknowledge(image,phase)
                    self._queue_batch(self._turn_buffer.batch(image))
                # A validated installed model is the default trigger. Do not
                # also fire an unrelated image-change gate for UNKNOWN frames.
                return

        if self._automatic_probe.observe(image,phase):
            # No bundled trained model: bounded fallback only. ChatGPT must
            # classify the screenshot before generating any tactical choice.
            self._queue_batch(self._turn_buffer.batch(image))

    @Slot(object)
    def _completed(self, payload):
        report=payload["report"]
        if self._active_job in ("auto_frame","batch") and not self._auto_running and self._cancel_event.is_set():
            return
        if report.get("status") == "UNCHANGED_FRAME":
            return
        if self._active_job=="batch" and self._pending_batch is not None:
            # A newer command screen was observed while this AI call ran.
            # Never display an older turn's action as the current advice.
            report={**report,"status":"SUPERSEDED",
                    "recommendation":"","warning":"NEWER_COMMAND_SCREEN_PENDING"}
        if report.get("status") == "ERROR" and report.get("reason") == "AutoCaptureUnavailable":
            self._stop_auto()
        if report["status"] not in ("ERROR","DEADLINE_EXCEEDED"):
            self.state=payload["state"]
        if report.get("status")=="ERROR":
            self._set_ai_status(f"AI処理エラー / {report.get('reason','UNKNOWN')}")
        elif report.get("status")=="CANCELLED":
            self._set_ai_status("AI処理キャンセル")
        else:
            self._set_ai_status(f"AI応答受信 / {report.get('status','UNKNOWN')}")
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
        for widget in (self.start_button,self.reset_button,self.confirm_button,self.prep_button,self.auto_button,self.reconnect_button):
            widget.setEnabled(not busy)

    @Slot()
    def _cleanup(self):
        self._thread=None
        self._worker=None
        self._active_job=None
        self._set_busy(False)
        if self._pending_batch is not None and not self._cancel_event.is_set():
            self._send_pending()

    def _render(self, report):
        state=self.state or {}
        phase=state.get("phase","NOT_STARTED")
        compatible=state.get("matrix_compatible",False)
        suffix="構築別LIVEマトリクス有効" if compatible else "LIVEマトリクス不一致・機能試験のみ"
        sending = ("画面取得待機中（自動再試行）" if self._auto_running and self._capture_paused
                   else "自動送信中" if self._auto_running else "自動送信停止")
        self.status.setText(f"段階：{phase} / {suffix} / {sending} / 記録 {len(self.entries)} 件")
        rec=report.get("recommendation","") or report.get("message","")
        if report.get("status")=="TEST_ONLY":
            rec="【機能試験のみ・戦術的な妥当性未検証】"+rec
        line=(rec+"\n\n") if rec else ""
        self.output.setPlainText(
            line+json.dumps({"latest":report,"replay_state":state,
                             "timeline":self.entries},ensure_ascii=False,indent=2))
        self.output.verticalScrollBar().setValue(0)
        # Only observed UI reports; no hidden controller state is inferred.
        self.state_changed.emit(dict(state), dict(report))

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
