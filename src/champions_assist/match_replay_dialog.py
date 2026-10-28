"""Opt-in automatic read-only live capture and AI match-support GUI.

Frames come from a scoped game window/ADB and stay in memory. The player
explicitly starts/stops capture once per match; game inputs remain manual.
The four frozen match assets are loaded once, never re-read during battle.
"""
from __future__ import annotations

import json
import sys
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
    QCheckBox, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from .app_config import default_app_root
from .preload import MasterPreloader
from .siwc_auth import ChatGPTSignIn, ProtectedSessionStore, SignInError
from .match_replay import (new_replay, confirm_actual_selection, assume_advised_selection, run_replay_image, run_replay_prep, run_replay_batch, run_replay_text_action, run_replay_text_result, run_replay_screen_result, possible_result_message, _text_action_eligible)
from .turn_capture import TurnCaptureBuffer, AutomaticVisualProbe
from .screen_classifier import ClassifiedMenuGate, load_bundled_classifier
from .calibrated_screen_fields import ScreenFieldProfile
from .local_event_worker import AsyncBattleEventReader
from .battle_state_tracker import LocalBattleStateTracker
from .live_state_updates import apply_local_ocr
from .local_event_image_policy import event_image_plan
from .observation_events import ScreenLexicon
from .auto_match_capture import capture_for_chatgpt, FrameChangeGate, AutoCaptureUnavailable
from .match_debug_log import MatchDebugLog
from .live_diagnostics import current_state_digest
from .capture_settings import CapturePreviewService
from .app_config import CaptureConfiguration
from .background_training_capture import BackgroundTrainingCapture
from .screen_dataset import new_session_id


def prep_budget_before_battle(selection_started_at: float | None, *,
                              now: float, preview_seconds: float = 90.0,
                              input_reserve: float = 3.0,
                              prep_cap: float = 40.0) -> float:
    """Hard-cap provisional/confirmed PREP within the remaining preview.

    If selection-screen onset was not machine-confirmed, we cannot infer an
    accurate countdown; retain only the established 40s cap and interrupt on
    any verified battle event. This helper never implies game timer certainty.
    """
    if selection_started_at is None:
        return prep_cap
    elapsed=max(0.0,now-selection_started_at)
    return min(prep_cap,max(0.0,preview_seconds-elapsed-input_reserve))


# A fresh *verified* command candidate may replace stale frames, but an
# unclassified animation is not grounds to erase a completed turn decision.
_URGENT_SCREENS = frozenset(("TURN_ACTION", "SWITCH_SELECTION"))
# The in-game clock may start ~10 s before the first actionable classifier
# frame. Aim to finish within 30 s. Do not throw away a valid response just
# for missing this target: allow up to 32 s for the final usable proposal.
# 45 s nominal - ~10 s possible onset lag - 32 s = ~3 s for manual input.
# Neither limit is a measured in-game countdown; real testing is essential.
_TARGET_AI_TURN_SECONDS = 30.0
_CONSERVATIVE_AI_TURN_SECONDS = 32.0
_SCREEN_PRIORITY = {
    "TEAM_SELECTION": 1, "BATTLE_EVENT": 2,
    "TURN_ACTION": 3, "SWITCH_SELECTION": 3,
}


def _should_replace_inflight(inflight: str | None,
                             incoming: str | None) -> bool:
    """Replace only with a NEW explicit and different game screen category.

    Inflight None means a generic fallback observation, so a verified
    TURN_ACTION or SWITCH_SELECTION is stronger. Repeated/UNKNOWN frames never
    evict an action that is being prepared.
    """
    if incoming is None or incoming==inflight:
        return False
    if incoming=="TEAM_SELECTION" and inflight in (
            "TURN_ACTION","SWITCH_SELECTION","BATTLE_EVENT"):
        return False
    # A different explicit game scene is a stronger freshness signal than
    # any presumed importance ordering (a battle event can end an old turn).
    return incoming in _SCREEN_PRIORITY


def _supersedes_result(report: dict, incoming: str | None) -> bool:
    """Never cache stale recommendations that lack a validated screen.

    Genuine MOVE_MENU/PARTY_MENU decisions survive mere unclassified
    animations, but a model reply with no screen evidence must not overwrite
    battle state when a newer frame is already pending.
    """
    if not incoming:
        return report.get("screen") in (None, "", "UNKNOWN")
    screen=report.get("screen")
    if screen=="MOVE_MENU":
        kind="TURN_ACTION"
    elif screen=="PARTY_MENU":
        kind="SWITCH_SELECTION"
    elif screen in ("ACTION_RESOLUTION","RESULT_SCREEN"):
        kind="BATTLE_EVENT"
    elif screen=="TEAM_SELECTION":
        kind="TEAM_SELECTION"
    else:
        kind=None
    return _should_replace_inflight(kind,incoming)


class _ReplayWorker(QObject):
    finished = Signal(object)
    progress = Signal(str)

    def __init__(self, *, app_root, selected_model, assets, state, job='auto_frame',
                 reasoning_effort='auto',
                 capture_config=None, capture_gate=None, capture_function=capture_for_chatgpt,
                 cancel_event=None, frames=None, screen_label=None, field_profile=None,
                 local_field_candidates=None,diagnostic_sink=None,
                 selection_seconds=55.0, prep_seconds=40.0,battle_seconds=30.0,
                 result_messages=None, result_screen=None):
        super().__init__()
        self.job=job
        self.frames=frames
        self.screen_label=screen_label
        self.field_profile=field_profile
        self.local_field_candidates=list(local_field_candidates or [])
        self.diagnostic_sink=diagnostic_sink
        self.app_root=app_root
        self.selected_model=selected_model
        self.reasoning_effort=reasoning_effort
        self.selection_seconds=float(selection_seconds)
        self.prep_seconds=float(prep_seconds)
        self.battle_seconds=float(battle_seconds)
        self.result_messages=list(result_messages or [])
        self.result_screen=dict(result_screen or {})
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
                if self.cancel_event is not None and self.cancel_event.is_set():
                    self.finished.emit({"state":self.state,"report":{"status":"CANCELLED"}})
                    return
                next_state, report=run_replay_prep(
                    state=self.state, assets=self.assets,
                    access_token=auth.access_token(),model=self.selected_model,
                    reasoning_effort=self.reasoning_effort,
                    limit_seconds=self.prep_seconds,
                )
                if self.cancel_event is not None and self.cancel_event.is_set():
                    self.finished.emit({"state":self.state,"report":{"status":"CANCELLED"}})
                    return
            elif self.job=="result":
                # Non-interactive, bounded TEXT-ONLY check of an actual OCR
                # result message. No battle screenshot is transmitted.
                token=auth.access_token()
                next_state,report=run_replay_text_result(
                    state=self.state,messages=self.result_messages,
                    access_token=token,model=self.selected_model,
                    reasoning_effort=self.reasoning_effort,
                    total_seconds=min(30.0,self.battle_seconds))
                self.finished.emit({"state":next_state,"report":report})
                return
            elif self.job=="result_screen":
                # No additional pixels: screen class was independently stable,
                # the ten most recent local OCR readings have source frame IDs.
                next_state,report=run_replay_screen_result(
                    state=self.state,
                    screen_frame=self.result_screen["frame"],
                    screen_time=self.result_screen["time"],
                    local_field_candidates=self.local_field_candidates,
                    access_token=auth.access_token(),model=self.selected_model,
                    reasoning_effort=self.reasoning_effort,
                    total_seconds=min(30.0,self.battle_seconds))
                self.finished.emit({"state":next_state,"report":report})
                return
            elif self.job=="batch":
                # OCR was performed asynchronously DURING event screens. The
                # 45-second command window never runs Tesseract or waits for
                # a late event recognition result.
                from time import perf_counter
                batch_started=perf_counter()
                if self.cancel_event is not None and self.cancel_event.is_set():
                    self.finished.emit({"state":self.state,"report":{"status":"CANCELLED"}})
                    return
                self.progress.emit("ChatGPT認証確認中")
                auth_started=perf_counter()
                token=auth.access_token()
                auth_ms=round((perf_counter()-auth_started)*1000)
                self.progress.emit(
                    "OCRテキストによるAI判断中（結果待ち）"
                    if _text_action_eligible(self.screen_label,self.state,self.assets)
                    else "必要情報不足のため画像確認中（結果待ち）")
                local_candidates=self.local_field_candidates
                if self.state.get("phase") in ("MATCHMAKING","SELECTION"):
                    # Selection: shared 55s including auth and all model calls.
                    remaining=self.selection_seconds-(perf_counter()-batch_started)
                    if remaining<=2.0:
                        next_state,report=self.state,{
                            "status":"DEADLINE_EXCEEDED",
                            "reason":"AUTH_OR_CAPTURE_BUDGET"}
                    else:
                        next_state,report=run_replay_image(
                            self.frames[-1],state=self.state,assets=self.assets,
                            access_token=token,model=self.selected_model,
                            reasoning_effort=self.reasoning_effort,
                            total_seconds=remaining,
                        )
                    report["transport"]="TWO_STAGE_SELECTION_MATRIX_FIRST"
                else:
                    # Leave at least 15 seconds from the game's nominal 45s
                    # input window for the human player. Tests use the same
                    # one-call replay path and strict 30s target.
                    remaining=self.battle_seconds-(perf_counter()-batch_started)
                    if remaining<=1.0:
                        next_state,report=self.state,{"status":"DEADLINE_EXCEEDED","reason":"AUTH_OR_AI_BUDGET"}
                    else:
                        if _text_action_eligible(self.screen_label,self.state,self.assets):
                            next_state,report=run_replay_text_action(
                                state=self.state,assets=self.assets,
                                access_token=token,model=self.selected_model,
                                reasoning_effort=self.reasoning_effort,
                                total_seconds=remaining,
                                expected_screen_label=self.screen_label,
                                local_field_candidates=local_candidates,
                                diagnostic_sink=self.diagnostic_sink)
                        else:
                            # One current image only when essential active
                            # identity is genuinely missing from local state.
                            next_state,report=run_replay_batch(
                                self.frames[-1:],state=self.state,assets=self.assets,
                                access_token=token,model=self.selected_model,
                                reasoning_effort=self.reasoning_effort,
                                total_seconds=remaining,
                                local_field_candidates=local_candidates,
                                expected_screen_label=self.screen_label,
                                diagnostic_sink=self.diagnostic_sink)
                            report["image_exception_reason"]="ACTIVE_IDENTITY_MISSING"
                report["auth_ms"]=auth_ms
                report["local_field_candidates_unverified"]=local_candidates
                report["screen_label_source"]=(
                    "SIX_CLASS_OR_USER_CALIBRATED" if self.screen_label
                    else "UNKNOWN"
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
                # Both capture and auth consume the selection budget.
                capture_and_auth_ms=round((perf_counter()-started)*1000)
                remaining=self.selection_seconds-(perf_counter()-started)
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
    live_clock_changed = Signal(str)
    shutdown_finished = Signal()

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
        self.matrix_source=None
        self.lookup_source=None
        self.live_assets_status="UNCONFIGURED"
        self.store_root=Path(store_root) if store_root else default_app_root()
        self.capture_config=capture_config
        self._capture_gate=FrameChangeGate()
        self._turn_buffer=TurnCaptureBuffer()
        self._event_frames=[]
        self._event_frame_ids=[]
        self._last_local_ocr_stats={}
        self._last_local_ocr_status="NOT_RUNNING"
        self._local_event_reader=None
        self._local_battle_tracker=LocalBattleStateTracker()
        self._ocr_capture_epochs={}
        self._active_state_revision=None
        self._active_request_id=None
        self._pending_result_messages=None
        self._result_check_sent_keys=set()
        self._result_ocr_candidate=None
        self._result_screen_candidate=None
        self._result_screen_sent=False
        self._revision_retry_keys=set()
        self._last_advised_switch=None
        self._automatic_probe=AutomaticVisualProbe(
            min_gap=8.0,matchmaking_recheck=float('inf'))
        self._screen_gate=ClassifiedMenuGate()
        # Missing/unvalidated training weights are not silently treated as
        # a working visual model. Default CPU is cheap and deterministic.
        self._screen_model_directory=None
        self._screen_model_path=None
        self._screen_model,self._screen_model_status=self._load_selected_screen_classifier()
        self._fallback_last_dispatched_at=None
        self._classifier_unknown_streak=0
        self._deferred_fallback_batch=None
        self._last_observed_ui=None
        self._command_menu_frame=None
        self._selection_clock_source=None
        self._turn_screen_seen_at=None
        self._turn_action_sent=False
        self._turn_event_after_action=False
        self._turn_epoch=0
        self._active_turn_epoch=None
        self._last_action_request_screen=None
        self._capture_seq=0
        self._last_capture_seq=0
        self._last_classifier_sample_at=None
        self._last_local_class=None
        self._pending_batch=None
        self._pending_screen_label=None
        self._field_profile=None
        self._manual_submission=False
        self._latest_image=None
        self._preview_last_at=None
        self._preview_last_clock=None
        self._capture_error_count=0
        self._capture_paused=False
        self._cancel_event=Event()
        self._shutdown_intent=None
        self._shutdown_ready=False
        self._auto_running=False
        self._dataset_session_id=None
        self._background_capture=None
        self._storage_status_timer=QTimer(self)
        self._storage_status_timer.setInterval(1500)
        self._storage_status_timer.timeout.connect(self._refresh_training_capture_status)
        self._poll_timer=QTimer(self)
        self._poll_timer.setInterval(300)
        self._poll_timer.timeout.connect(self._capture_next)
        self._status_timer=QTimer(self)
        self._status_timer.setInterval(250)
        self._status_timer.timeout.connect(self._refresh_live_status)
        self._status_timer.start()
        self.assets=None
        self.state=None
        self.entries=[]
        self._trace_origin=monotonic()
        self._timing_trace=[]
        self._debug_log=None
        self._active_request_started=None
        self._active_request_frame=None
        self._latest_recognition_screen=None
        self._last_action_text=""
        self._last_selection_text=""
        self._selection_screen_started=None
        self._prep_budget_seconds=40.0
        self._proposed_selection=None
        self._proposed_prep_key=None
        self._proposed_prep_payload=None
        self._restart_prep_after_cleanup=False
        self._thread=None
        self._worker=None
        self._active_job=None
        self._inflight_screen_label=None
        self._prep_thread=None
        self._prep_worker=None
        self._prep_cancel_event=Event()
        self._prep_timer=QTimer(self)
        self._prep_timer.setSingleShot(True)
        self._prep_timer.timeout.connect(self._prep_deadline)
        self.shutdown_finished.connect(self._on_shutdown_finished)
        self._ai_request_attempts=0
        self._ai_last_status="AI未送信（撮影・メニュー認識待ち）"
        panel=QVBoxLayout(self)
        intro=QLabel(
            "開始を一度押すと、設定したゲーム画面を自動キャプチャしてChatGPTに送信します。\\n"
            "学習用画像は明示同意ONの場合だけローカル保存します。停止はいつでも可能。ゲームの操作は行いません。"
        )
        intro.setWordWrap(True)
        panel.addWidget(intro)
        self.phase_banner=QLabel("現在の画面：対戦開始待ち")
        self.phase_banner.setObjectName("live_phase_banner")
        self.phase_banner.setStyleSheet(
            "QLabel#live_phase_banner {font-size: 17px; font-weight: 700;"
            "padding: 9px; background: #253449; color: white;}")
        self.phase_banner.setWordWrap(True)
        panel.addWidget(self.phase_banner)
        self.activity_banner=QLabel("アプリの作業：事前ロード待ち")
        self.activity_banner.setObjectName("live_activity_banner")
        self.activity_banner.setStyleSheet(
            "QLabel#live_activity_banner {font-size: 14px; font-weight: 600;"
            "padding: 7px; background: #334155; color: white;}")
        self.activity_banner.setWordWrap(True)
        panel.addWidget(self.activity_banner)
        self.deadline_banner=QLabel("時間：画面検出待ち（ゲーム内時計は取得しません）")
        self.deadline_banner.setObjectName("live_deadline_banner")
        self.deadline_banner.setStyleSheet(
            "QLabel#live_deadline_banner {font-size: 15px; font-weight: 700;"
            "padding: 8px; background: #164e63; color: white;}")
        self.deadline_banner.setWordWrap(True)
        panel.addWidget(self.deadline_banner)
        self.selection_banner=QLabel("選出：未提示")
        self.selection_banner.setObjectName("live_selection_banner")
        self.selection_banner.setStyleSheet(
            "QLabel#live_selection_banner {font-size: 20px; font-weight: 800;"
            "padding: 13px; background: #0f766e; color: white;}")
        self.selection_banner.setWordWrap(True)
        panel.addWidget(self.selection_banner)
        self.action_banner=QLabel("次の行動：画面判定待ち")
        self.action_banner.setObjectName("live_action_banner")
        self.action_banner.setStyleSheet(
            "QLabel#live_action_banner {font-size: 22px; font-weight: 800;"
            "padding: 12px; background: #1d4ed8; color: white;}")
        self.action_banner.setWordWrap(True)
        panel.addWidget(self.action_banner)
        result_controls=QHBoxLayout()
        self.confirm_win_button=QPushButton("対戦終了：勝利を手動確定")
        self.confirm_loss_button=QPushButton("対戦終了：敗北を手動確定")
        self.confirm_win_button.clicked.connect(
            lambda:self._finish_match_manually("WIN"))
        self.confirm_loss_button.clicked.connect(
            lambda:self._finish_match_manually("LOSS"))
        result_controls.addWidget(self.confirm_win_button)
        result_controls.addWidget(self.confirm_loss_button)
        panel.addLayout(result_controls)
        self.status=QLabel("未開始 / 自動送信は停止中")
        panel.addWidget(self.status)
        self.ai_status_label=QLabel(self._ai_last_status)
        self.ai_status_label.setObjectName("ai_dispatch_status")
        self.ai_status_label.setWordWrap(True)
        panel.addWidget(self.ai_status_label)
        self.debug_log_status=QLabel("自動デバッグログ: 対戦開始時に保存（画像・認証情報なし）")
        self.debug_log_status.setWordWrap(True)
        panel.addWidget(self.debug_log_status)
        self.menu_detection_label=QLabel("メニュー登録: なし")
        self.menu_detection_label.setWordWrap(True)
        panel.addWidget(self.menu_detection_label)
        self.local_classifier_label=QLabel(self._local_classifier_message())
        self.local_classifier_label.setWordWrap(True)
        panel.addWidget(self.local_classifier_label)
        self.classifier_choose_button=QPushButton("画面分類ONNXファイルを選択")
        self.classifier_choose_button.setObjectName("chooseScreenClassifier")
        self.classifier_choose_button.clicked.connect(self._choose_screen_classifier)
        panel.addWidget(self.classifier_choose_button)
        # Render the same frames that go to AI, without another acquisition.
        self.capture_preview_title=QLabel("ローカルキャプチャプレビュー（通常ターンは画像を送信しません）")
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
        self.training_capture_check=QCheckBox(
            "学習データ収集をON（対戦画面の未ラベルJPEGをローカル保存・最大2GB）"
        )
        self.training_capture_check.setObjectName("trainingCaptureOptIn")
        self.training_capture_check.setChecked(False)
        self.training_capture_check.toggled.connect(self._on_training_capture_toggle)
        panel.addWidget(self.training_capture_check)
        self.training_capture_status=QLabel(
            "学習データ収集: OFF（通常は画面を保存しません）"
        )
        self.training_capture_status.setObjectName("trainingCaptureStatus")
        self.training_capture_status.setWordWrap(True)
        panel.addWidget(self.training_capture_status)
        panel.addWidget(QLabel(
            "対戦中は画像保存のみ。正解ラベルの確認・PNG保存は対戦後に画面収集アプリで行います。"
        ))
        self.reconnect_button=QPushButton("撮影対象ウィンドウを再選択（ミラーリング対応）")
        self.reconnect_button.clicked.connect(self._reconnect_capture_window)
        panel.addWidget(self.reconnect_button)
        panel.addWidget(QLabel(
            "選出3匹＋初手を提案すると、自動的にPREPへ進みます。"
            "確定ボタンは不要です。実際の初手が異なれば画面から補正し、"
            "控え2匹は判明するまで提案前提として扱います。"
        ))
        # Legacy test/export entrypoints only; not part of the visible UI.
        # The player never needs to confirm a selection in the app.
        self.actual_three=QLineEdit()
        self.actual_lead=QLineEdit()
        self.confirm_button=QPushButton("legacy-hidden-confirm")
        self.actual_three.hide()
        self.actual_lead.hide()
        self.confirm_button.hide()
        # No manual calibration, screenshot upload or PREP retry controls.
        self.manual_batch_button=QPushButton("legacy-hidden-manual-submit",self)
        self.manual_batch_button.hide()
        self.prep_button=QPushButton("legacy-hidden-prep-retry",self)
        self.prep_button.hide()
        self.prep_banner=QLabel("PREP：選出待ち")
        self.prep_banner.setObjectName("live_prep_progress")
        self.prep_banner.setWordWrap(True)
        self.prep_banner.setStyleSheet(
            "QLabel#live_prep_progress {font-size: 17px; font-weight: 700;"
            "padding: 10px; background: #274060; color: white;}")
        panel.addWidget(self.prep_banner)
        panel.addWidget(QLabel("画像の認識・AI提案・進行ログ（現在の1試合）"))
        self.output=QPlainTextEdit()
        self.output.setReadOnly(True)
        panel.addWidget(self.output,1)
        self.export_button=QPushButton("テストログをJSON保存（画像本体・認証情報は含めない）")
        self.export_button.clicked.connect(self._export)
        panel.addWidget(self.export_button)
        self._render({"status":"NOT_STARTED"})

    def _new_match(self):
        if (self._shutdown_intent is not None or self._thread is not None
                or self._prep_thread is not None):
            return
        if self._auto_running:
            QMessageBox.warning(self, "停止が必要", "自動送信を停止してから新しい対戦を開始してください。")
            return
        if self._debug_log is not None:
            self._debug_log.write("PREVIOUS_SESSION_CLOSED")
            self._debug_log.close()
        new_id="capture-"+uuid4().hex
        self._debug_log=None
        try:
            self._debug_log=MatchDebugLog(self.store_root,match_id=new_id,
                                          async_mode=True)
            self.debug_log_status.setText("自動デバッグログ保存先: "+str(self._debug_log.path))
        except OSError:
            # Logging must NEVER prevent starting the match.
            self.debug_log_status.setText("自動デバッグログ: 保存先に書き込めません（支援は継続）")
        self._trace_event("MATCH_PRELOAD_START")
        try:
            if self.before_new_match is not None:
                self.before_new_match()
            if not self.master_directory or not self.team_path:
                raise ValueError("次戦用の構築を先に選択してください。")
            self.assets=MasterPreloader(
                self.master_directory, team_source=self.team_path,
                matrix_source=self.matrix_source,lookup_source=self.lookup_source,
            ).load_for_new_match()
            self.state=new_replay(self.assets)
            # Read the optional PRIVATE ROI profile once at match start.
            # Calibration does not bypass or replace the four-master load.
            private_profile=self.store_root / "calibration" / "screen_fields.json"
            try:
                self._field_profile=(
                    ScreenFieldProfile.from_json(private_profile)
                    if private_profile.is_file()
                    else ScreenFieldProfile.from_user_samples_960x572()
                )
            except (OSError, ValueError, TypeError, KeyError):
                # A broken optional profile cannot alter tactical facts.
                self._field_profile=None
            self._stop_local_event_reader()
            self._local_battle_tracker.clear()
            self._ocr_capture_epochs.clear()
            self._active_state_revision=None
            self._active_request_id=None
            self._pending_result_messages=None
            self._result_check_sent_keys.clear()
            self._result_ocr_candidate=None
            self._result_screen_candidate=None
            self._result_screen_sent=False
            self._revision_retry_keys.clear()
            self._last_advised_switch=None
            self.match_id=new_id
            self._selection_screen_started=None
            self._selection_clock_source=None
            self._deferred_fallback_batch=None
            self._classifier_unknown_streak=0
            self._last_observed_ui=None
            self._command_menu_frame=None
            self._turn_screen_seen_at=None
            self._turn_action_sent=False
            self._turn_event_after_action=False
            self._turn_epoch=0
            self._active_turn_epoch=None
            self._active_request_frame=None
            self._latest_recognition_screen=None
            self._last_action_text=""
            self._last_selection_text=""
            self._prep_budget_seconds=40.0
            self._proposed_selection=None
            self._proposed_prep_key=None
            self._proposed_prep_payload=None
            self._restart_prep_after_cleanup=False
            self._dataset_session_id=new_session_id()
            self.entries=[]
            self._trace_origin=monotonic()
            self._timing_trace=[]
            self.actual_three.clear()
            self.actual_lead.clear()
            self._capture_gate.reset()
            self._turn_buffer.reset_turn()
            self._automatic_probe.reset()
            self._screen_gate.reset()
            self._last_local_class=None
            self._pending_batch=None
            self._pending_screen_label=None
            self._ai_request_attempts=0
            self._set_ai_status("AI未送信（選出画面の登録／検出待ち）")
            self._update_menu_detection_label()
            self._trace_event("PRELOAD_4_FILES_COMPLETED",preload_count=4,
                              matrix_compatible=self.state.get("matrix_compatible",False),
                              matrix_provisional=self.state.get("matrix_provisional",False))
            self._render({"status":"MATCH_STARTED","message":"4ファイル事前読込完了。自動キャプチャ開始で進めます。"})
        except Exception as exc:
            self._trace_event("MATCH_PRELOAD_ERROR",reason=type(exc).__name__)
            self.assets=None
            self.state=None
            self.match_id=None
            QMessageBox.warning(self,"テスト開始不可",str(exc))

    def _reset(self):
        self.request_shutdown("reset")

    def request_shutdown(self, intent="close"):
        """Cancel new work now, complete reset/close after worker threads exit.

        QThread termination/deletion while executing an API request is unsafe.
        Cancellation is cooperative; the in-flight HTTP request may still need
        to reach its bounded timeout. Never make the user retry Stop/Reset/Close.
        """
        if intent not in ("reset","close"):
            raise ValueError("UNKNOWN_SHUTDOWN_INTENT")
        if self._shutdown_intent=="close":
            return
        self._shutdown_intent=intent
        self._shutdown_ready=False
        self._restart_prep_after_cleanup=False
        self._stop_auto()
        self._interrupt_prep()
        self._pending_batch=None
        self._pending_screen_label=None
        self._set_ai_status("停止要求済み／実行中のAI通信を破棄し終了処理へ移行")
        if self._debug_log is not None:
            self._trace_event("USER_REQUESTED_SHUTDOWN",
                              status="RESET" if intent=="reset" else "CLOSE")
        self.reset_button.setEnabled(False)
        self._maybe_finish_shutdown()

    def _maybe_finish_shutdown(self):
        if self._shutdown_intent is None:
            return
        if self._thread is not None or self._prep_thread is not None:
            return
        intent=self._shutdown_intent
        self._shutdown_intent=None
        if intent=="reset":
            self._perform_reset()
            self.reset_button.setEnabled(True)
        else:
            self._shutdown_ready=True
            # Queued emission avoids recursively closing the embedded panel
            # from inside Qt's existing closeEvent/finished slot.
            QTimer.singleShot(0,self.shutdown_finished.emit)

    @Slot()
    def _on_shutdown_finished(self):
        if self._shutdown_ready and not self.embedded:
            self.close()

    def _perform_reset(self):
        self._trace_event("MATCH_RESET")
        self._stop_auto()
        self._turn_buffer.reset_turn()
        self._event_frames.clear()
        self._event_frame_ids.clear()
        self._automatic_probe.reset()
        self._screen_gate.reset()
        self._last_local_class=None
        self._pending_batch=None
        self._pending_screen_label=None
        self._field_profile=None
        self._stop_local_event_reader()
        self._local_battle_tracker.clear()
        self._ocr_capture_epochs.clear()
        self._active_state_revision=None
        self._active_request_id=None
        self._pending_result_messages=None
        self._result_check_sent_keys.clear()
        self._result_ocr_candidate=None
        self._result_screen_candidate=None
        self._result_screen_sent=False
        self._revision_retry_keys.clear()
        self._last_advised_switch=None
        self.assets=None
        self.state=None
        self.entries=[]
        self._timing_trace=[]
        self._trace_origin=monotonic()
        self.actual_three.clear()
        self.actual_lead.clear()
        self._set_ai_status("AI未送信（対戦未開始）")
        self._render({"status":"RESET"})
        self.match_id=None
        if self._debug_log is not None:
            self._debug_log.close()
        self._debug_log=None

    def _diagnostic(self, event: str, detail: dict) -> None:
        """Bounded non-blocking evidence recording, no images or API bodies."""
        log=self._debug_log
        if log is None:
            return
        state=self.state or {}
        log.write_diagnostic(
            event,detail,
            turn_epoch=self._turn_epoch,
            frame_seq=self._last_capture_seq,
            state_revision=state.get("_live_state_revision",0),
            request_id=self._active_request_id or "")

    def _trace_event(self, event: str, **data) -> None:
        """Unverified UI-clock telemetry; no screenshot, disk or network use."""
        if len(self._timing_trace) >= 3000:
            return
        record={
            "at_ms":max(0,int((monotonic()-self._trace_origin)*1000)),
            "event":event,"phase":(self.state or {}).get("phase","UNKNOWN"),
            **data,
        }
        self._timing_trace.append(record)
        if self._debug_log is not None:
            self._debug_log.write(event,**{k:v for k,v in record.items() if k!="event"})

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
        storage=(" / 学習用JPEG保存ON" if self._background_capture is not None
                 else " / 学習用画像保存なし")
        self.capture_preview_info.setText(
            f"撮影成功 {self._preview_last_clock} / 送信範囲 {width}×{height} px / "
            "プレビューは最大2回/秒"+storage)

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
            self._event_frames.clear()
            self._event_frame_ids.clear()
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

    def _screen_gpu_settings(self):
        from .vision_preferences import VisionPreferenceStore
        prefs=VisionPreferenceStore(self.store_root)
        return prefs.screen_device(),prefs.screen_gpu_id()

    def _load_selected_screen_classifier(self):
        """Restore ONNX with the configured GPU preference; CPU fallback in auto."""
        device,gpu_id=self._screen_gpu_settings()
        self._loaded_screen_gpu_settings=(device,gpu_id)
        choice=self.store_root/"screen_classifier_choice.json"
        model_path=None
        allow_trial=False
        try:
            if choice.is_file():
                data=json.loads(choice.read_text(encoding="utf-8"))
                if not isinstance(data,dict):
                    return None,"MODEL_CONFIG_INVALID"
                if isinstance(data.get("model_path"),str):
                    model_path=Path(data["model_path"])
                    allow_trial=data.get("trial_opt_in") is True
                elif isinstance(data.get("directory"),str):
                    # Upgrade in place without changing the user's files.
                    from .screen_classifier import MODEL_FILE
                    model_path=Path(data["directory"])/MODEL_FILE
                else:
                    return None,"MODEL_CONFIG_INVALID"
        except (OSError,ValueError,TypeError):
            return None,"MODEL_CONFIG_INVALID"
        self._screen_model_path=model_path
        self._screen_model_directory=model_path.parent if model_path is not None else None
        if model_path is None:
            return load_bundled_classifier(device=device,gpu_id=gpu_id)
        return load_bundled_classifier(
            model_path=model_path,device=device,gpu_id=gpu_id,
            allow_unvalidated_for_trial=allow_trial)

    def _choose_screen_classifier(self):
        if self._auto_running or self._thread is not None or self._prep_thread is not None:
            QMessageBox.warning(self,"画面分類モデル","監視を停止してから設定してください。")
            return
        from .screen_classifier import MODEL_FILE
        initial=str(self._screen_model_path or
                    (self._screen_model_directory or self.store_root)/MODEL_FILE)
        selected,_=QFileDialog.getOpenFileName(
            self,"画面分類ONNXを選択（JSONはあれば自動読込）",
            initial,"ONNXモデル (*.onnx)")
        if not selected:
            return
        path=Path(selected)
        # Trial is explicit. No spoofed validated=true or checksum bypass.
        device,gpu_id=self._screen_gpu_settings()
        model,status=load_bundled_classifier(
            model_path=path,device=device,gpu_id=gpu_id,
            allow_unvalidated_for_trial=True)
        if model is None:
            reasons={
                "MODEL_LOAD_FAILED_SCREEN_MODEL_NOT_VALIDATED":
                    "未検証モデルは試験モードでのみ使えます。",
                "MODEL_LOAD_FAILED_SCREEN_MODEL_CHECKSUM_MISMATCH":
                    "ONNXとJSONのSHA-256が一致しません。正しいペアを選んでください。",
                "MODEL_LOAD_FAILED_INVALID_SCREEN_MODEL_LABELS":
                    "6分類のラベルと順序が現行仕様に対応していません。",
                "MODEL_LOAD_FAILED_INVALID_SCREEN_MODEL_MANIFEST":
                    "JSONまたは内蔵メタデータのschema_versionが3ではありません。",
                "MODEL_LOAD_FAILED_SCREEN_MODEL_METADATA_MISSING":
                    "対応するJSONもONNX内部の6分類メタデータもありません。",
                "MODEL_NOT_BUNDLED":"ONNXファイルが見つかりません。",
                "MODEL_PATH_NOT_ONNX":"ONNXファイル（.onnx）を選択してください。",
            }
            QMessageBox.warning(self,"画面分類モデルを読み込めません",
                "理由："+status+"\n"+
                reasons.get(status,"ONNX形式・設定情報・実行環境を確認してください。")+
                "\n対応するJSONがあれば自動照合し、無ければ"
                "ONNX内部のメタデータを使用します。")
            return
        trial=bool(getattr(model,"experimental",False))
        if trial:
            response=QMessageBox.warning(
                self,"未検証モデル：試験利用の確認",
                "このONNXは6分類に対応していますが、実機精度の検証基準を"
                "満たしていません。\n誤判定による不要なAI通信・行動判断の"
                "遅れが生じる可能性があります。\n未検証のまま試験利用しますか？",
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if response!=QMessageBox.StandardButton.Yes:
                return
        try:
            self.store_root.mkdir(parents=True,exist_ok=True)
            target=self.store_root/"screen_classifier_choice.json"
            temporary=target.with_suffix(".tmp")
            temporary.write_text(json.dumps({
                "model_path":str(path.resolve()),"trial_opt_in":trial
            },ensure_ascii=False),encoding="utf-8")
            temporary.replace(target)
        except OSError:
            QMessageBox.warning(self,"モデル設定の保存に失敗",
                                "ローカル設定フォルダーに書き込めません。")
            return
        self._screen_model_path=path
        self._loaded_screen_gpu_settings=(device,gpu_id)
        self._screen_model_directory=path.parent
        self._screen_model=model
        self._screen_model_status=status
        self._screen_gate.reset()
        self._last_local_class=None
        self.local_classifier_label.setText(self._local_classifier_message())
        self._trace_event("SCREEN_CLASSIFIER_CONFIGURED",
                          status=status,trial=trial)

    def _local_classifier_message(self) -> str:
        if self._screen_model is not None:
            source="指定したONNX" if self._screen_model_path else "同梱"
            if getattr(self._screen_model,"experimental",False):
                return ("画面判別: 【未検証・試験モード】5分類ONNX / "+
                        source+" / "+self._screen_model_status)
            return ("画面判別: 検証済み5分類ONNX利用中 / "+source+
                    " / "+self._screen_model_status)
        return ("画面判別: 未設定・利用不可（"+self._screen_model_status+
                "）。開始前に警告、低頻度の画像変化検出を使用します。")

    def _set_ai_status(self, status: str) -> None:
        """Status of the AI workflow, separate from local capture state."""
        self._ai_last_status=status
        self.ai_status_label.setText(status)
        self.ai_status_changed.emit(status)

    def _update_menu_detection_label(self) -> None:
        names={"TEAM_SELECTION":"選出画面",
               "TURN_ACTION":"技選択画面","SWITCH_SELECTION":"交代画面"}
        found=[names[k] for k in names if k in self._turn_buffer.templates]
        self.menu_detection_label.setText(
            "自動検出: 検証済みONNXがあれば選出・技選択・交代画面のみ送信。"
            "モデルなしは事前警告後に回数制限付きで確認。")

    def _eligible_menu(self, kind: str) -> bool:
        phase=(self.state or {}).get("phase")
        return (
            kind=="TEAM_SELECTION" and phase in ("MATCHMAKING","SELECTION")
            or kind in ("TURN_ACTION","SWITCH_SELECTION") and (
                phase in ("PREP","BATTLE") or
                phase=="SELECTION" and self._thread is not None
                and self._inflight_screen_label=="TEAM_SELECTION")
        )

    def _start_auto(self):
        if self._shutdown_intent is not None or self._thread is not None or self._auto_running:
            return
        if self.state is None or self.assets is None:
            QMessageBox.warning(self,"4ファイル事前読込が未完了",
                "先に対戦開始（4ファイル事前読込）を実行してください。")
            return
        if len(getattr(self.assets,"sha256",{}))!=4:
            QMessageBox.warning(self,"4ファイル事前読込が未完了",
                "4件すべての読み込み確認が必要です。再度対戦開始を行ってください。")
            return
        if not self.model or self.capture_config is None:
            QMessageBox.warning(self, "設定不足", "ChatGPTモデルとキャプチャ設定を確認してください。")
            return
        if not self.capture_config.game_viewport_normalized:
            QMessageBox.warning(self, "ROI未設定", "設定のゲーム画面範囲を指定してください。")
            return
        # Settings can change while the panel stays open. Reload before the
        # NEXT capture session, never mutate an in-flight inference session.
        if self._screen_gpu_settings()!=getattr(
                self,"_loaded_screen_gpu_settings",None):
            self._screen_model,self._screen_model_status=(
                self._load_selected_screen_classifier())
            self._screen_gate.reset()
            self.local_classifier_label.setText(
                self._local_classifier_message())
        if (self._screen_model is not None
                and getattr(self._screen_model,"experimental",False)):
            answer=QMessageBox.warning(
                self,"未検証ONNX：対戦での試験利用",
                "現在は未検証の5分類ONNXが選択されています。"
                "操作画面の誤判定や追加AI通信の可能性があります。"
                "この対戦で試験利用しますか？",
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            self._trace_event("TRIAL_CLASSIFIER_START_WARNING",
                              proceeded=answer==QMessageBox.StandardButton.Yes)
            if answer!=QMessageBox.StandardButton.Yes:
                return
        if self._screen_model is None:
            answer=QMessageBox.warning(
                self,"画面分類モデルが未設定・利用不可",
                "画面分類モデルが使えません（"+self._screen_model_status+"）。"
                "誤画面のAI確認や費用増加の可能性があります。"
                "画像変化検出を用いる低頻度モードで開始しますか？",
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            self._trace_event("MODEL_UNAVAILABLE_START_WARNING",
                              status=self._screen_model_status,
                              proceeded=answer==QMessageBox.StandardButton.Yes)
            if answer!=QMessageBox.StandardButton.Yes:
                return
        storage_note=(
            "学習用JPEGもローカルへ保存します（最大2GB、未ラベル、人手承認まで学習不可）。"
            if self.training_capture_check.isChecked()
            else "学習用画像は保存しません。"
        )
        if QMessageBox.question(self, "自動送信の確認",
                "指定したゲーム画面を継続監視します。分類モデルが有効なら"
                "選出画面のみ画像、ターンと交代は原則OCRテキストをAIへ送信します。"
                "未設定時は警告済みの低頻度モードを使用します。送信ごとの確認はありません。"
                +storage_note+"開始しますか？",
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        from .crash_diagnostics import record_event
        record_event("CAPTURE_MONITOR_START", component="MATCH_REPLAY")
        self._auto_running=True
        self._crash_capture_baseline=self._capture_seq
        self._start_local_event_reader()
        self._capture_error_count=0
        self._capture_paused=False
        self._poll_timer.setInterval(300)
        self._cancel_event.clear()
        self._capture_gate.reset()
        self._turn_buffer.reset_turn()
        self._event_frames.clear()
        self._event_frame_ids.clear()
        self._automatic_probe.reset()
        self._screen_gate.reset()
        self._last_local_class=None
        self._pending_batch=None
        self._deferred_fallback_batch=None
        self._classifier_unknown_streak=0
        self._fallback_last_dispatched_at=None
        self._last_observed_ui=None
        self._command_menu_frame=None
        self._turn_screen_seen_at=None
        self._turn_action_sent=False
        self._turn_event_after_action=False
        self._turn_epoch=0
        self._active_turn_epoch=None
        self._last_action_request_screen=None
        self._selection_screen_started=None
        self._selection_clock_source=None
        self._prep_budget_seconds=40.0
        self._clear_capture_preview("撮影開始済み・最初のフレームを取得中")
        if self.training_capture_check.isChecked():
            self._begin_background_collection()
        self._poll_timer.start()
        # Starting capture must update the status before a menu triggers AI.
        self._render(self.entries[-1] if self.entries else {"status":"AUTO_STARTED"})
        self._update_menu_detection_label()
        self._set_ai_status(
            "未検証5分類ONNXで試験監視中（誤判定に注意）"
            if self._screen_model is not None
            and getattr(self._screen_model,"experimental",False)
            else "5分類モデル監視中（操作画面のみAI送信）"
            if self._screen_model is not None
            else "分類モデルなし：画像変化を低頻度でAI確認（誤認識の可能性あり）")
        self._capture_next()

    def _stop_auto(self):
        self._stop_local_event_reader()
        self._interrupt_prep()
        self._end_background_collection()
        self._cancel_event.set()
        self._auto_running=False
        self._capture_paused=False
        self._capture_error_count=0
        self._poll_timer.stop()
        self._poll_timer.setInterval(300)
        self._pending_batch=None
        self._pending_screen_label=None
        self._pending_result_messages=None
        self._latest_image=None
        self._automatic_probe.reset()
        self._screen_gate.reset()
        self._clear_capture_preview("監視停止中")
        self._set_ai_status(
            "監視停止／通信中の結果は破棄・ワーカー終了処理中"
            if self._thread is not None or self._prep_thread is not None else
            "AI監視停止（新規画像送信なし）")
        # Previously the UI could still say "自動送信中" after stopping.
        if self.state is not None:
            self._render(self.entries[-1] if self.entries else {"status":"AUTO_STOPPED"})

    def _on_training_capture_toggle(self, enabled: bool) -> None:
        if enabled and self._auto_running and self.state is not None:
            self._begin_background_collection()
        elif not enabled:
            self._end_background_collection()
        elif enabled:
            self.training_capture_status.setText(
                "学習データ収集: ON（AI監視の開始後にローカル保存を開始）"
            )

    def _begin_background_collection(self) -> None:
        if self._background_capture is not None or not self.training_capture_check.isChecked():
            return
        if self._dataset_session_id is None:
            self._dataset_session_id=new_session_id()
        try:
            collector=BackgroundTrainingCapture(
                self.store_root/"private-training"/"screens",
                self._dataset_session_id,
            )
            collector.start()
        except (OSError, ValueError, RuntimeError) as exc:
            # Optional storage must never affect live recommendations.
            self.training_capture_status.setText(
                "学習データ収集開始不可: "+type(exc).__name__
            )
            return
        self._background_capture=collector
        self._storage_status_timer.start()
        self._refresh_training_capture_status()

    def _refresh_training_capture_status(self) -> None:
        collector=self._background_capture
        if collector is None:return
        stats=collector.stats()
        if stats.error:
            self.training_capture_status.setText(
                "学習画像保存停止: "+stats.error+
                f" / 保存{stats.saved}枚・収集断念{stats.dropped}枚"
            )
            self._end_background_collection(keep_status=True)
            return
        self.training_capture_status.setText(
            f"学習用JPEG保存中: {stats.saved}枚 / "
            f"混雑で見送り{stats.dropped}枚・同一画面省略{stats.duplicates}枚 / "
            "最大2GB・未ラベル"
        )

    def _end_background_collection(self, *, keep_status: bool = False) -> None:
        collector=self._background_capture
        self._background_capture=None
        self._storage_status_timer.stop()
        if collector is not None:
            collector.stop(wait=False)  # Never wait for JPEG fsync on live UI.
            if not keep_status:
                stats=collector.stats()
                self.training_capture_status.setText(
                    f"学習画像保存停止: {stats.saved}枚・"
                    f"混雑で見送り{stats.dropped}枚（未ラベルJPEGは保持）"
                )
        elif not keep_status and not self.training_capture_check.isChecked():
            self.training_capture_status.setText(
                "学習データ収集: OFF（通常は画面を保存しません）"
            )

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
                    self._queue_batch(self._turn_buffer.batch(image), screen_label=kind)
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

    def _begin_local_turn(self,screen_label: str):
        """Timestamp first confident action detection, never AI completion."""
        if screen_label not in _URGENT_SCREENS:
            return
        if self._turn_screen_seen_at is not None:
            return
        self._turn_epoch+=1
        # Never render the preceding turn's action in a new decision window.
        self._last_action_text=""
        self._last_advised_switch=None
        self._turn_screen_seen_at=monotonic()
        self._turn_action_sent=False
        self._turn_event_after_action=False
        self._trace_event("TURN_SCREEN_OBSERVED",
                          screen_label=screen_label,
                          clock_source="FIRST_LOCAL_DETECTION",
                          turn_epoch=self._turn_epoch,
                          frame_seq=self._last_capture_seq,
                          timeout_budget_ms=int(_CONSERVATIVE_AI_TURN_SECONDS*1000))

    def _note_battle_event(self):
        # Event ↔ OTHER oscillation is normal; rearm only once after
        # a turn's action request, without standalone paid AI processing.
        if self._turn_action_sent and not self._turn_event_after_action:
            self._turn_event_after_action=True
            self._turn_screen_seen_at=None
            self._last_action_text=""
            self._last_advised_switch=None
            self._screen_gate.rearm_after_resolution()
            self._trace_event("TURN_RESOLUTION_OBSERVED",
                              frame_seq=self._last_capture_seq,
                              turn_epoch=self._turn_epoch)

    def _queue_batch(self,frames, *, screen_label=None):
        if self._shutdown_intent is not None:
            return
        if not self._auto_running and not self._manual_submission:
            return
        if screen_label in _URGENT_SCREENS:
            # A forced party choice can follow an action without an
            # intervening classified BATTLE_EVENT. It is a NEW actionable
            # decision, not another use of the fainted member's move.
            if (screen_label=="SWITCH_SELECTION"
                    and self._last_advised_switch
                    and not (self.state or {}).get("awaiting_replacement")
                    and not self._turn_event_after_action
                    and self._last_advised_switch.get("turn_epoch")==self._turn_epoch
                    and (self.state or {}).get("our_active")!=
                        self._last_advised_switch.get("target")):
                # The player is FOLLOWING an already displayed normal hard
                # switch, not presenting another tactical decision.
                self._trace_event("PROPOSED_NORMAL_SWITCH_MENU_REUSED",
                                  turn_epoch=self._turn_epoch)
                display="次の行動："+str(self._last_advised_switch["target"])+"に交代。"
                self.action_banner.setText(display)
                self._diagnostic("SWITCH_REUSED",{
                    "target":self._last_advised_switch["target"],
                    "original_turn":self._last_advised_switch["turn_epoch"],
                    "display_turn":self._turn_epoch,
                    "our_active":(self.state or {}).get("our_active"),
                    "displayed":display,
                    "display_source":"CACHED_SWITCH_PROPOSAL"})
                return
            new_switch_window=(
                screen_label=="SWITCH_SELECTION"
                and self._turn_action_sent and not self._turn_event_after_action
                and getattr(self,"_last_action_request_screen",None)=="TURN_ACTION")
            if new_switch_window:
                self._turn_screen_seen_at=None
                self._turn_action_sent=False
                self._last_action_text=""
                self._trace_event("SWITCH_NEW_DECISION_WINDOW",
                                  previous_screen="TURN_ACTION",
                                  turn_epoch=self._turn_epoch)
                self.action_banner.setText(
                    "次の行動：交代先を確認中（直前の技提案は無効）")
            elif self._turn_action_sent and not self._turn_event_after_action:
                self._trace_event("SAME_TURN_AI_REQUEST_SUPPRESSED",
                                  screen_label=screen_label)
                return
            self._begin_local_turn(screen_label)
        # During selection the first outstanding recognition+tactical request
        # owns the screen. Subsequent animations, timer changes and fallback
        # frames must NOT enqueue another preview which would discard the
        # completed selection as SUPERSEDED. A true command/battle event still
        # preempts a stale preview through the existing pending mechanism.
        if (self._thread is not None
                and self._active_job=="batch"
                and self.state is not None
                and self.state.get("phase") in ("MATCHMAKING","SELECTION")
                and self._inflight_screen_label=="TEAM_SELECTION"
                and screen_label in (None,"TEAM_SELECTION")):
            self._trace_event("SELECTION_DUPLICATE_SUPPRESSED",
                              screen_label=screen_label or "UNCLASSIFIED")
            return
        if self._thread is not None and self._active_job=="batch":
            # A second TURN_ACTION belongs to a *different* decision window
            # after BATTLE_EVENT, even when its screen category is identical.
            newer_turn=(screen_label in _URGENT_SCREENS
                        and self._active_turn_epoch is not None
                        and self._turn_epoch>self._active_turn_epoch)
            if not newer_turn and not _should_replace_inflight(
                    self._inflight_screen_label,screen_label):
                self._trace_event("INFLIGHT_FRAME_SUPPRESSED",
                                  screen_label=screen_label or "UNCLASSIFIED")
                return
            if (not newer_turn and self._pending_batch is not None
                    and not _should_replace_inflight(self._pending_screen_label,
                                                     screen_label)):
                self._trace_event("PENDING_PRIORITY_PROTECTED",
                                  screen_label=screen_label or "UNCLASSIFIED")
                return
            if newer_turn:
                self._trace_event("NEW_TURN_QUEUED_BEHIND_INFLIGHT",
                                  turn_epoch=self._turn_epoch,
                                  active_turn_epoch=self._active_turn_epoch,
                                  screen_label=screen_label,
                                  frame_seq=self._last_capture_seq)
        if screen_label=="BATTLE_EVENT":
            self._note_battle_event()
        # PREP remains valid during screen changes. It is cancelled only
        # when the NEXT model request actually begins (_send_pending).
        # An unclassified image change is not proof that battle has started.
        # Let the visual AI observation confirm the opponent lead first.
        # At most one pending batch: new observed events replace stale ones.
        if (screen_label=="TEAM_SELECTION" and self._selection_screen_started is None
                and self.state is not None
                and self.state.get("phase") in ("MATCHMAKING","SELECTION")):
            self._selection_screen_started=monotonic()
            self._selection_clock_source="LOCAL_CANDIDATE"
            self._trace_event("SELECTION_CLOCK_STARTED",source="LOCAL_CANDIDATE")
        if screen_label in ("TEAM_SELECTION","TURN_ACTION","SWITCH_SELECTION"):
            self._trace_event("SCREEN_CLASSIFIER_CANDIDATE",
                              screen_label=screen_label,
                              confidence="UNVERIFIED")
        self._pending_batch=list(frames)
        self._pending_screen_label=screen_label
        if screen_label:
            self._latest_recognition_screen=screen_label
            self._refresh_live_status()
        self._send_pending()

    def _send_pending(self):
        if (self._shutdown_intent is not None or self._thread is not None
                or self._pending_batch is None):
            return
        if self.state is None or self.state.get("phase")=="ENDED":
            self._pending_batch=None
            return
        frames=self._pending_batch
        screen_label=self._pending_screen_label
        # Only the CURRENT screenshot is needed for the opponent-six preview;
        # old transition frames are unhelpful and cost capture/memory time.
        if screen_label in _URGENT_SCREENS:
            # Non-blocking GUI synchronization: allow the just-seen battle
            # message OCR a brief completion window before the ONE AI call.
            # Never spend more than 1.5 seconds or eat the action deadline.
            reader=self._local_event_reader
            if reader is not None:
                stats=reader.diagnostic_counts()
                outstanding=max(0,stats.get("ocr_jobs_enqueued",0)-
                                stats.get("ocr_jobs_completed",0))
                marker=(self._turn_epoch,screen_label)
                if getattr(self,"_ocr_sync_key",None)!=marker:
                    self._ocr_sync_key=marker
                    self._ocr_sync_started=monotonic()
                waited=monotonic()-self._ocr_sync_started
                remaining=(_CONSERVATIVE_AI_TURN_SECONDS-
                    (monotonic()-self._turn_screen_seen_at)
                    if self._turn_screen_seen_at else 32.0)
                if outstanding and waited<1.5 and remaining>5.0:
                    self._trace_event("OCR_AI_SYNC_WAIT",
                        outstanding=outstanding,
                        elapsed_ms=int(waited*1000))
                    QTimer.singleShot(100,self._send_pending)
                    return
                self._trace_event("OCR_AI_SYNC_FINISHED",
                    outstanding=outstanding,wait_ms=int(waited*1000),
                    reason="READY" if not outstanding else "TIME_BUDGET")
            self._ocr_sync_key=None
        # Image fallback after a failed/unknown screen classification also
        # receives the same already-acquired OCR delta. Never recapture or
        # re-run OCR synchronously during the tactical decision.
        local_candidates=(
            self._freeze_local_events_for_action(screen_label)
            if (screen_label in _URGENT_SCREENS or
                self.state.get("phase") in ("PREP","BATTLE")) else [])
        if frames and self.state.get("phase") in ("MATCHMAKING","SELECTION"):
            frames=[frames[-1]]
        elif frames and screen_label in _URGENT_SCREENS:
            # The normal path sends ZERO screenshots. A single current
            # frame remains only as a fallback if active identity is missing.
            frames=[frames[-1]]
            self._event_frames.clear()
            self._event_frame_ids.clear()
            self._trace_event("TEXT_FIRST_ACTION_FRAMES_NOT_TRANSMITTED",
                              screen_label=screen_label)
        elif frames and (
                screen_label=="BATTLE_EVENT"
                or (screen_label is None and self.state.get("phase")
                    in ("PREP","BATTLE"))):
            # Without a local classifier, one previous transition plus the
            # current screen carries useful event context at much lower
            # latency than repeatedly shipping four high-detail images.
            frames=frames[-2:]
        if self._thread is not None:
            # OCR found a possible result while snapshotting candidates;
            # verify the match result BEFORE sending another action.
            return
        self._pending_batch=None
        self._pending_screen_label=None
        self._turn_buffer.reset_turn()
        text_only=_text_action_eligible(screen_label,self.state,self.assets)
        planned_image_count=0 if text_only else len(frames)
        self._ai_request_attempts+=1
        self._active_request_started=monotonic()
        self._active_state_revision=self.state.get("_live_state_revision",0)
        self._active_request_id="req-"+str(self._ai_request_attempts)
        request_id=self._active_request_id
        turn_epoch=self._turn_epoch
        log=self._debug_log
        revision=self._active_state_revision
        def diagnostic_sink(event,detail):
            if log is not None:
                log.write_diagnostic(event,detail,request_id=request_id,
                                     turn_epoch=turn_epoch,
                                     state_revision=revision or 0)
        self._diagnostic("ACTION_REQUEST",{
            **current_state_digest(self.state),
            "request_id":request_id,
            "screen_kind":screen_label or "UNCLASSIFIED",
            "transport":"OCR_TEXT_ONLY" if text_only else "VISION_EXCEPTION",
            "ocr_candidate_count":len(local_candidates),
        })
        self._trace_event("ACTION_STATE_SNAPSHOT",
                          own_known=bool(self.state.get("our_active")),
                          opponent_known=bool(self.state.get("opponent_active")),
                          state_revision=self._active_state_revision)
        self._active_request_frame=frames[-1] if frames else None
        if self.state.get("phase") in ("MATCHMAKING","SELECTION"):
            elapsed_preview=(self._active_request_started-self._selection_screen_started
                             if self._selection_screen_started is not None else 0.0)
            selection_remaining=max(0.0,55.0-elapsed_preview)
        else:
            selection_remaining=35.0
        battle_remaining=_CONSERVATIVE_AI_TURN_SECONDS
        if (self.state.get("phase") in ("PREP","BATTLE")
                and self._turn_screen_seen_at is not None):
            battle_remaining=max(0.0,_CONSERVATIVE_AI_TURN_SECONDS-(
                monotonic()-self._turn_screen_seen_at))
        if (self.state.get("phase") in ("PREP","BATTLE")
                and battle_remaining<=1.0):
            self._trace_event("TURN_BUDGET_EXHAUSTED",
                              screen_label=screen_label or "UNKNOWN",
                              timeout_budget_ms=int(_CONSERVATIVE_AI_TURN_SECONDS*1000),
                              target_budget_ms=int(_TARGET_AI_TURN_SECONDS*1000))
            self._last_action_text=""
            self._render({"status":"DEADLINE_EXCEEDED",
                          "screen":screen_label,
                          "warning":"AI表示の最終期限を超過しました。ゲーム内時計は未取得。"})
            return
        # Only this successful new AI dispatch invalidates unfinished PREP.
        # No cancellation on soft 40s expiry, screen animations or a request
        # abandoned before it was actually sent.
        if self._prep_thread is not None:
            self._trace_event("PREP_SUPERSEDED_BY_NEW_AI_REQUEST",
                              screen_label=screen_label or "UNCLASSIFIED")
            self._interrupt_prep()
        if screen_label in _URGENT_SCREENS:
            self._turn_action_sent=True
            self._active_turn_epoch=self._turn_epoch
            self._trace_event("TURN_AI_REQUEST_STARTED",
                              screen_label=screen_label,
                              timeout_budget_ms=int(battle_remaining*1000),
                              turn_epoch=self._turn_epoch,
                              frame_seq=self._last_capture_seq,
                              ai_request_attempt=self._ai_request_attempts)
        self._trace_event(
            "AI_REQUEST_DISPATCH",
            ai_request_attempt=self._ai_request_attempts,
            frame_seq=self._last_capture_seq,
            turn_epoch=self._turn_epoch,
            image_count=planned_image_count,
            ai_transport="OCR_TEXT_ONLY" if text_only else "VISION_EXCEPTION",
            screen_label=screen_label or "UNCLASSIFIED",
            capture_to_enqueue_ms=(
                max(0,int((monotonic()-self._turn_screen_seen_at)*1000))
                if self._turn_screen_seen_at is not None
                and screen_label in _URGENT_SCREENS else 0))
        self._set_ai_status(
            f"AI処理を起動中 #{self._ai_request_attempts}（画像 {planned_image_count} 枚・送信は未確認）")
        request_report={
            "step":len(self.entries)+1, "status":"AI_REQUEST_QUEUED",
            "ai_request_attempt":self._ai_request_attempts,
            "image_count":planned_image_count,
            "ai_transport":"OCR_TEXT_ONLY" if text_only else "VISION_EXCEPTION",
            "screen_label_hint":screen_label,
            "message":"AI処理を起動しました。送信成功はまだ確認できていません。"}
        self.entries.append(request_report)
        self._render(request_report)
        self._set_busy(True)
        if screen_label in _URGENT_SCREENS:
            self._last_action_request_screen=screen_label
        self._active_job="batch"
        self._inflight_screen_label=screen_label
        self._thread=QThread(self)
        self._worker=_ReplayWorker(
            app_root=self.store_root,selected_model=self.model,
            reasoning_effort=self.reasoning_effort,
            assets=self.assets,state=self.state,job="batch",
            frames=frames,cancel_event=self._cancel_event,
            screen_label=screen_label,
            field_profile=self._field_profile,
            local_field_candidates=local_candidates,
            diagnostic_sink=diagnostic_sink,
            selection_seconds=selection_remaining,
            battle_seconds=battle_remaining,
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
        if progress.startswith("ChatGPT認証"):
            kind="AUTH_CHECK"
        elif progress.startswith("AI画像送信"):
            kind="IMAGE_REQUEST_WAIT"
        elif progress.startswith("ローカル文字認識"):
            kind="OPTIONAL_LOCAL_OCR"
        else:
            kind="WORKER_PROGRESS_OTHER"
        self._trace_event(kind,ai_request_attempt=self._ai_request_attempts)

    def _capture_next(self):
        from .crash_diagnostics import record_event, record_exception
        capture_base=getattr(self,"_crash_capture_baseline",0)
        if not self._auto_running:
            return
        if self.state is None or self.state.get("phase")=="ENDED":
            if self.state is not None and self.state.get("phase")=="ENDED":
                self._stop_auto()
            return
        if self._capture_seq == capture_base:
            record_event("FIRST_FRAME_CAPTURE_BEGIN")
        try:
            image=capture_for_chatgpt(self.capture_config)
        except AutoCaptureUnavailable as exc:
            record_exception("CAPTURE_UNAVAILABLE", exc, stage="CAPTURE")
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
        if self._capture_seq == capture_base:
            record_event("FIRST_FRAME_CAPTURE_COMPLETE")
        self._latest_image=image
        self._capture_seq+=1
        self._last_capture_seq=self._capture_seq
        if self._background_capture is not None:
            self._background_capture.offer(image)
        if self._last_capture_seq == capture_base+1:
            record_event("FIRST_FRAME_PREVIEW_BEGIN")
        self._show_capture_preview(image)
        if self._last_capture_seq == capture_base+1:
            record_event("FIRST_FRAME_PREVIEW_COMPLETE")
        # Take completed OCR before dispatching a tactical request. Never
        # wait on OCR, nor send these image pixels to the AI.
        if self._local_event_reader is not None:
            if self._last_capture_seq == capture_base+1:
                record_event("FIRST_FRAME_OCR_DRAIN_BEGIN")
            self._drain_event_candidates(include_snapshot=False)
            if self._last_capture_seq == capture_base+1:
                record_event("FIRST_FRAME_OCR_DRAIN_COMPLETE")
        if self._last_capture_seq == capture_base+1:
            record_event("FIRST_FRAME_MENU_OBSERVE_BEGIN")
        kind=self._turn_buffer.observe(image)
        if self._last_capture_seq == capture_base+1:
            record_event("FIRST_FRAME_MENU_OBSERVE_COMPLETE")
        phase=self.state.get("phase")
        if kind is not None and self._eligible_menu(kind):
            if kind in _URGENT_SCREENS:
                self._offer_event_for_local_ocr(image,label=kind)
            self._automatic_probe.acknowledge(image,phase)
            self._screen_gate.acknowledge(kind,phase)
            self._queue_batch(self._turn_buffer.batch(image), screen_label=kind)
            return

        if self._screen_model is not None:
            trace_classifier=self._last_capture_seq==capture_base+1 or self._last_capture_seq%20==0
            if trace_classifier:
                record_event("SCREEN_CLASSIFIER_PREDICT_BEGIN",
                             frame_seq=self._last_capture_seq,
                             provider=getattr(self._screen_model,"provider","UNKNOWN"))
            try:
                prediction=self._screen_model.predict(image)
            except (ValueError,RuntimeError,TypeError) as exc:
                record_exception("SCREEN_CLASSIFIER_ERROR", exc, stage="SCREEN_CLASSIFIER")
                # Do not disable capture or fabricate a classification.
                self._screen_model=None
                self._screen_model_status="MODEL_RUNTIME_ERROR_"+type(exc).__name__
                self.local_classifier_label.setText(self._local_classifier_message())
                self._automatic_probe.reset()
            else:
                if trace_classifier:
                    record_event("SCREEN_CLASSIFIER_PREDICT_COMPLETE",
                                 frame_seq=self._last_capture_seq,
                                 provider=getattr(self._screen_model,"provider","UNKNOWN"))
                if (self._result_screen_candidate is not None
                        and prediction.label in _URGENT_SCREENS
                        and prediction.confidence>=0.95
                        and prediction.margin>=0.50):
                    # A fresh real player action invalidates a stale/falsely
                    # identified end screen, even if the result GPT is busy.
                    self._trace_event("RESULT_SCREEN_STALE_ACTION_OBSERVED",
                                      frame_seq=self._last_capture_seq)
                    self._result_screen_candidate=None
                    self._result_screen_sent=False
                if (prediction.label in _URGENT_SCREENS
                        and prediction.confidence>=0.80
                        and phase in ("PREP","BATTLE")):
                    # Start OCR at first classifier observation, BEFORE the
                    # screen gate needs stable frames. Remains nonblocking.
                    self._offer_event_for_local_ocr(image,label=prediction.label)
                if prediction.label!=self._last_local_class:
                    self._last_local_class=prediction.label
                    self._trace_event("SCREEN_CLASSIFIER_CHANGED",
                                      frame_seq=self._last_capture_seq,
                                      screen_label=prediction.label,
                                      classifier_source=prediction.source,
                                      confidence=round(prediction.confidence,3),
                                      margin=round(prediction.margin,3),
                                      latency_ms=round(prediction.latency_ms,2))
                    self.local_classifier_label.setText(
                        f"ローカル画面判別: {prediction.label} / "
                        f"確信度 {prediction.confidence:.2f} / "
                        f"{prediction.latency_ms:.1f}ms / {prediction.source}")
                now=monotonic()
                if (self._last_classifier_sample_at is None
                        or now-self._last_classifier_sample_at>=3.0):
                    self._last_classifier_sample_at=now
                    self._trace_event("SCREEN_CLASSIFIER_SAMPLE",
                                      frame_seq=self._last_capture_seq,
                                      screen_label=prediction.label,
                                      classifier_source=prediction.source,
                                      confidence=round(prediction.confidence,3),
                                      margin=round(prediction.margin,3),
                                      latency_ms=round(prediction.latency_ms,2))
                if (prediction.label in _URGENT_SCREENS
                        and prediction.confidence>=0.95
                        and prediction.margin>=0.50
                        and phase in ("SELECTION","PREP","BATTLE")
                        and (not self._turn_action_sent
                             or self._turn_event_after_action)):
                    self._begin_local_turn(prediction.label)
                # RESULT_SCREEN is a gate for one OCR-grounded result check,
                # NEVER a direct WIN/LOSS determination or a new tactical call.
                if (prediction.label=="RESULT_SCREEN"
                        and prediction.confidence>=0.95
                        and prediction.margin>=0.50
                        and phase=="BATTLE"
                        and not getattr(self._screen_model,"experimental",False)
                        and not self._result_screen_sent):
                    self._offer_event_for_local_ocr(image,label="RESULT_SCREEN")
                classified=self._screen_gate.observe(prediction,phase)
                if classified=="RESULT_SCREEN":
                    if getattr(self._screen_model,"experimental",False):
                        self._trace_event("RESULT_SCREEN_TRIAL_REQUIRES_MANUAL_CONFIRMATION")
                    elif not self._result_screen_sent and phase=="BATTLE":
                        self._result_screen_candidate={
                            "frame":f"capture-{self._last_capture_seq}",
                            "time":monotonic(),"detected_at":monotonic()}
                        self._trace_event("RESULT_SCREEN_STABLE_CANDIDATE",
                            frame_id=self._result_screen_candidate["frame"],
                            stable_frames=2)
                        QTimer.singleShot(750,self._attempt_result_screen_verification)
                    classified=None
                if (prediction.label=="BATTLE_EVENT"
                        and prediction.confidence>=0.80
                        and phase in ("PREP","BATTLE")):
                    self._note_battle_event()
                if prediction.label=="BATTLE_EVENT" and phase in ("PREP","BATTLE"):
                    self._remember_event_frame(image)
                    self._offer_event_for_local_ocr(image)
                if classified=="BATTLE_EVENT":
                    # Non-interactive event animation: do not pay for an AI
                    # request until the next actionable screen appears.
                    self._trace_event("NON_ACTION_EVENT_NOT_SENT")
                    classified=None
                if (classified is not None and phase=="SELECTION"
                        and not (self._thread is not None
                                 and self._inflight_screen_label=="TEAM_SELECTION")):
                    classified=None
                if classified is not None:
                    if classified in _URGENT_SCREENS:
                        self._offer_event_for_local_ocr(image,label=classified)
                    self._trace_event("SCREEN_GATE_DISPATCH",
                                      frame_seq=self._last_capture_seq,
                                      screen_label=classified,
                                      confidence=round(prediction.confidence,3),
                                      margin=round(prediction.margin,3),
                                      turn_epoch=self._turn_epoch)
                    self._automatic_probe.acknowledge(image,phase)
                    self._queue_batch(self._turn_buffer.batch(image), screen_label=classified)
                # A loaded, validated ONNX remains authoritative. UNKNOWN
                # is abstention, not permission to send an unrelated frame.
                self._classifier_unknown_streak=(
                    self._classifier_unknown_streak+1
                    if prediction.label=="UNKNOWN" else 0)
                return

        self._observe_fallback_transition(image,phase)

    def _start_local_event_reader(self):
        self._stop_local_event_reader()
        lexicon=ScreenLexicon.from_frozen_assets(
            self.assets,opponent_names=tuple(getattr(self.assets,"matrix_index",{}).keys()))
        device,gpu_id=self._screen_gpu_settings()
        from .crash_diagnostics import record_event
        record_event("LOCAL_OCR_READER_START", device=device, gpu_id=gpu_id)
        self._local_event_reader=AsyncBattleEventReader.with_onnx_ocr(
            self._field_profile,lexicon=lexicon,device=device,gpu_id=gpu_id,
            isolate_process=(sys.platform=="win32" and device!="cpu"))
        self._last_ocr_reported_status=self._local_event_reader.reason
        record_event("LOCAL_OCR_READER_CREATED", component="MATCH_REPLAY")
        self._trace_event("LOCAL_OCR_STARTED",
                          status=self._local_event_reader.reason,
                          requested_device=device,requested_gpu_id=gpu_id,
                          timer_ocr=False)

    def _stop_local_event_reader(self):
        reader=getattr(self,"_local_event_reader",None)
        self._local_event_reader=None
        if reader is not None:
            reader.stop()

    def _offer_event_for_local_ocr(self,image,*,label="BATTLE_EVENT"):
        if self._local_event_reader is None:
            self._start_local_event_reader()
        # Persist already-finished OCR immediately DURING event animations.
        # Drain is nonblocking and does not perform OCR or an API call.
        self._drain_event_candidates(include_snapshot=False)
        reader=self._local_event_reader
        capture_id=f"capture-{self._last_capture_seq}"
        if reader.offer(image,label=label,frame_id=capture_id):
            self._ocr_capture_epochs[capture_id]=self._turn_epoch
            self._trace_event("LOCAL_OCR_EVENT_QUEUED",
                              frame_seq=self._last_capture_seq,
                              pending_overflow=reader.overflow)

    def _drain_event_candidates(self,*,include_snapshot=True):
        """Persist completed OCR only; never wait in the command-menu path."""
        reader=self._local_event_reader
        if reader is not None:
            status=getattr(reader,"reason",None)
            if status is not None and status!=getattr(self,"_last_ocr_reported_status",None):
                self._last_ocr_reported_status=status
                backend=getattr(reader,"_ocr",None)
                self._trace_event("LOCAL_OCR_PROVIDER",
                                  status=status,
                                  provider=getattr(backend,"provider","NOT_INITIALIZED"),
                                  requested_device=getattr(backend,"device","unknown"),
                                  requested_gpu_id=getattr(backend,"gpu_id",None))
            for result in reader.drain():
                captured_frame=getattr(result,"frame_id",None)
                if captured_frame is None and result.observations:
                    captured_frame=result.observations[0].frame_id
                capture_epoch=self._ocr_capture_epochs.pop(
                    captured_frame,self._turn_epoch)
                if (self._active_request_started is not None and
                        result.timestamp<=self._active_request_started):
                    self._trace_event("OCR_COMPLETED_AFTER_AI_INPUT_FREEZE",
                        frame_id=captured_frame,
                        capture_epoch=capture_epoch,
                        delay_ms=max(0,int((monotonic()-result.timestamp)*1000)),
                        active_request_id=self._active_request_id)
                if result.error:
                    self._trace_event("LOCAL_OCR_ERROR",reason=result.error,
                                      ocr_error_code=getattr(result,"error_code","UNKNOWN") or "UNKNOWN")
                if result.observations:
                    for row in result.observations:
                        self._diagnostic("OCR_RAW",{
                            "field":row.field,"roi":row.roi,
                            "frame_id":row.frame_id,
                            "raw_text":row.text,"confidence":row.confidence,
                            "captured_at":row.timestamp,
                            "processed_at":monotonic(),
                            "capture_epoch":capture_epoch,
                            "status":row.status,
                            "detected_pokemon":list(row.detected_pokemon),
                            "detected_moves":list(row.detected_moves),
                            "parsed":row.value if isinstance(row.value,dict)
                                     else {"evidence":str(row.value)[:160]},
                        })
                    # Battle messages are OCR candidates, not confirmed results.
                    # Request an independent text-only result judgment only
                    # for a narrow explicit match-result phrase.
                    # Require two high-confidence readings from distinct
                    # frames. A single OCR misread can NEVER start result AI.
                    for row in result.observations:
                        if (not row.field.endswith(("battle_message",
                                                   "system_message"))
                                or not row.field.startswith("screen_text.")):
                            continue
                        if (row.status!="CANDIDATE"
                                or row.confidence is None
                                or row.confidence<0.72):
                            continue
                        if self._screen_model is not None:
                            # Six-class RESULT_SCREEN gate owns result checks.
                            continue
                        if not possible_result_message(row.text):
                            continue
                        from hashlib import sha256
                        key=sha256("".join(row.text.split()).encode()).hexdigest()[:20]
                        old=self._result_ocr_candidate
                        if (old and old["key"]==key
                                and old["frame"]!=row.frame_id
                                and .15<=row.timestamp-old["time"]<=12.0):
                            self._result_ocr_candidate=None
                            self._request_result_from_ocr([row.text])
                        else:
                            self._result_ocr_candidate={
                                "key":key,"frame":row.frame_id,
                                "time":row.timestamp}
                    count,novel=self._local_battle_tracker.ingest(
                        result.observations,collect=True,
                        turn_epoch=capture_epoch)
                    if len(novel)<len(result.observations):
                        self._diagnostic("OCR_DEDUP",{
                            "dedup":len(result.observations)-len(novel),
                            "capture_epoch":capture_epoch,
                            "reason":"SAME_TURN_OR_NEAR_FRAME"})
                    # OCR is observation-only. Do not translate text into
                    # battle state locally. The tactical AI interprets raw
                    # candidates and the validated AI update is committed
                    # together with its same-turn action.
                    if novel:
                        self._trace_event("LOCAL_OCR_TEXT_PENDING_AI",
                                          accepted=count,
                                          raw_count=len(result.observations))
                    if count:
                        self._trace_event("LOCAL_OCR_CANDIDATES",
                                          accepted=count,
                                          raw_count=len(result.observations))
        if not include_snapshot:
            return []
        state=self.state or {}
        assets=self.assets
        snapshot=self._local_battle_tracker.snapshot(
            acknowledged_frames=set(state.get("_ai_ocr_ack_frames") or ()),
            own_active=state.get("our_active"),
            opponent_active=state.get("opponent_active"),
            own_names=tuple(m["name"] for m in assets.own_team) if assets else (),
            opponent_names=tuple(state.get("opponent_six") or ()),
        )
        return snapshot["local_observations"]

    def _freeze_local_events_for_action(self,screen_label):
        # Capture a completed-only snapshot before any tactical AI call.
        # Inflight OCR is cancelled without join(), preserving the 45s turn.
        records=self._drain_event_candidates()
        from .ocr_ai_evidence import select_ai_ocr_evidence
        records,selection=select_ai_ocr_evidence(records)
        self._diagnostic("OCR_AI_SELECTION",selection)
        if screen_label in _URGENT_SCREENS:
            reader=self._local_event_reader
            stats=reader.diagnostic_counts() if reader is not None else {}
            self._last_local_ocr_stats=dict(stats)
            self._last_local_ocr_status=getattr(reader,"reason","UNKNOWN") if reader else "NOT_RUNNING"
            # Once per actionable menu, rather than logging every ROI / frame.
            self._trace_event("LOCAL_OCR_TURN_SUMMARY",
                              frame_seq=self._last_capture_seq,
                              turn_epoch=self._turn_epoch,
                              ocr_status=(getattr(reader,"reason","UNKNOWN") if reader else "NOT_RUNNING"),
                              ocr_candidates_sent=len(records),
                              ocr_duplicates=self._local_battle_tracker.duplicates,
                              ocr_conflicts=self._local_battle_tracker.conflicts,
                              **stats)
            # Keep OCR alive through the next command. Old behavior cancelled
            # the worker and discarded newly read names/effects.
            if reader is not None:
                self._trace_event("LOCAL_OCR_CONTINUES_DURING_ACTION",
                                  unfinished=stats.get("ocr_queue_pending",0),
                                  overflow=reader.overflow,
                                  observed_candidates=len(records))
        return records

    def _remember_event_frame(self,image):
        """Buffer two distinct event images locally, without extra AI requests."""
        signature=AutomaticVisualProbe._small(image)
        if self._event_frames:
            last=AutomaticVisualProbe._small(self._event_frames[-1])
            if float(np.mean(np.abs(signature-last)))<0.025:
                return
        self._event_frames.append(image.copy())
        self._event_frame_ids.append(f"capture-{self._last_capture_seq}")
        self._event_frames=self._event_frames[-2:]
        self._event_frame_ids=self._event_frame_ids[-2:]
        self._trace_event("BATTLE_EVENT_FRAME_BUFFERED",
                          pending_count=len(self._event_frames))

    def _frames_for_action(self,command_image,local_candidates=None):
        """Send OCR text first; retain image fallback if ANY evidence is missing.

        Current actionable menu image is ALWAYS included to verify active and
        opponent state. Event screenshots can be skipped only when the saved
        ROI profile is validated on actual capture, OCR is complete and each
        buffered frame has confident message recognition.
        """
        plan=event_image_plan(
            self._event_frame_ids,
            local_candidates or [],
            reader_status=self._last_local_ocr_status,
            roi_validated=bool(self._field_profile
                               and self._field_profile.validated_on_game_capture),
            ocr_counters=self._last_local_ocr_stats)
        # When a faint and the prior actor were explicitly confirmed,
        # replaying the same event images to choose a replacement is wasted.
        # Without confirmed faint, retain image fallback on OCR uncertainty.
        confirmed_faint=bool((self.state or {}).get("awaiting_replacement"))
        include_events=plan.include_event_images and not confirmed_faint
        frames=(self._event_frames[-2:] if include_events else [])
        frames=list(frames)+[command_image.copy()]
        self._event_frames.clear()
        self._event_frame_ids.clear()
        self._trace_event("EVENT_IMAGE_TEXT_FIRST_DECISION",
                          image_count=len(frames),
                          fallback_reason=(
                              "CONFIRMED_FAINT_STATE_NO_EVENT_REPLAY"
                              if confirmed_faint else plan.reason),
                          event_images_buffered=plan.buffered,
                          event_images_recognized=plan.recognized,
                          image_fallback=include_events)
        self._trace_event("BATTLE_EVENT_FRAMES_INCLUDED",image_count=len(frames))
        return frames

    def _observe_fallback_transition(self,image,phase):
        """Model-free fallback is rate-limited; a scene change is NOT a menu."""
        if (self._fallback_last_dispatched_at is not None
                and monotonic()-self._fallback_last_dispatched_at<10.0):
            return
        if self._thread is not None and self._active_job=="batch":
            if self._automatic_probe.observe(image,phase):
                self._deferred_fallback_batch=self._turn_buffer.batch(image)[-2:]
                self._fallback_last_dispatched_at=monotonic()
                self._trace_event("FALLBACK_TRANSITION_DEFERRED")
            return
        if self._automatic_probe.observe(image,phase):
            if (self._last_observed_ui=="COMMAND_MENU"
                    and self._command_menu_frame is not None
                    and self.state is not None
                    and self.state.get("phase")=="BATTLE"):
                current=AutomaticVisualProbe._small(image)
                old=AutomaticVisualProbe._small(self._command_menu_frame)
                if float(np.mean(np.abs(current-old)))<0.12:
                    self._trace_event("COMMAND_MENU_DUPLICATE_SUPPRESSED")
                    return
            self._fallback_last_dispatched_at=monotonic()
            self._trace_event("FALLBACK_SCREEN_RECHECK",
                              source="MODEL_UNAVAILABLE_RATE_LIMITED")
            self._queue_batch(self._turn_buffer.batch(image))

    @Slot(object)
    def _completed(self, payload):
        if self._shutdown_intent is not None or self._cancel_event.is_set():
            # A stopped HTTP request can still return a result. Never apply
            # its stale action, advance the match, or dispatch more captures.
            return
        report=payload["report"]
        if (self._active_job=="result_screen"
                and self._result_screen_candidate is None):
            # Result classification was superseded by a real next-turn menu.
            # Ignore the old GPT conclusion; never overwrite current battle.
            self._trace_event("STALE_RESULT_SCREEN_ANSWER_DROPPED")
            report={"status":"SUPERSEDED","recommendation":"",
                    "warning":"RESULT_SCREEN_NO_LONGER_CURRENT"}
            payload={"state":self.state,"report":report}
        if (self._active_job=="batch" and
                self._active_state_revision is not None and
                (self.state or {}).get("_live_state_revision",0)!=self._active_state_revision and
                report.get("status") not in ("MATCH_END","ERROR",
                                            "CANCELLED","DEADLINE_EXCEEDED")):
            report={**report,"status":"SUPERSEDED","recommendation":"",
                    "warning":"OCR_BATTLE_STATE_CHANGED_WHILE_AI_WAS_DECIDING"}
            self._trace_event("OCR_NEW_STATE_INVALIDATED_OLD_AI",
                old_revision=self._active_state_revision,
                new_revision=self.state.get("_live_state_revision",0))
            self._turn_action_sent=False
            # OCR revision does not restart the game's real turn clock.
            self._screen_gate.rearm_after_resolution()
            # No dependency on a future classifier change: requeue the
            # actionable frame with the newly committed OCR revision.
            if (self.state and self.state.get("phase")=="BATTLE"
                    and self._auto_running
                    and self._inflight_screen_label in _URGENT_SCREENS
                    and self._active_request_frame is not None
                    and not self._turn_event_after_action):
                key=(self._turn_epoch,self.state["_live_state_revision"])
                if key not in self._revision_retry_keys:
                    self._revision_retry_keys.add(key)
                    if self._pending_batch is None:
                        self._pending_batch=[self._active_request_frame.copy()]
                        self._pending_screen_label=self._inflight_screen_label
                    self._trace_event("OCR_REVISION_RETRY_QUEUED",
                        state_revision=key[1],turn_epoch=key[0])
        if (self._active_job=="result" and report.get("status")=="MATCH_END"):
            # Verified OCR match result outranks any pending turn-screen frame.
            self._pending_batch=None
            self._pending_screen_label=None
            self._pending_result_messages=None
        if (self.state is not None and self.state.get("phase")=="ENDED"
                and report.get("status")!="MATCH_END"):
            # An earlier tactical answer may finish after a manual result.
            return
        if (self._active_job=="batch" and (
                self._pending_result_messages or self._result_screen_candidate)
                and report.get("status") in (
                    "ADVICE","PROVISIONAL_ADVICE","TEST_ONLY")):
            report={**report,"status":"SUPERSEDED",
                    "recommendation":"",
                    "warning":"POSSIBLE_MATCH_RESULT_OCR_PENDING"}
            self._trace_event("ACTION_WITH_RESULT_CHECK_PENDING_DROPPED")
        if (self._active_job=="batch"
                and report.get("screen")=="TEAM_SELECTION"
                and report.get("status") in (
                    "OK","PROVISIONAL_ADVICE","PARTIAL_ADVICE","TEST_ONLY")
                and self._auto_running and self._screen_model is not None
                and self._last_local_class not in (None,"TEAM_SELECTION","UNKNOWN")):
            # A different screen label alone is NOT a newer AI request:
            # preserve the finished selection as a historical suggestion.
            # Never treat it as a selection the player can still enter.
            report["selection_screen_unconfirmed"]=True
            report["recommendation"]=(
                "【選出画面の継続未確認・操作できるか要確認】"
                +str(report.get("recommendation") or ""))
            self._trace_event("SELECTION_RESULT_SCREEN_CHANGED",
                              screen_label=self._last_local_class,
                              ai_request_attempt=self._ai_request_attempts)
        if (self._active_job=="batch" and self._active_turn_epoch is not None
                and (self._active_turn_epoch!=self._turn_epoch
                     or self._turn_event_after_action)):
            report={**report,"status":"SUPERSEDED",
                    "recommendation":"",
                    "warning":"NEW_TURN_DETECTED_BEFORE_RESPONSE"}
            self._trace_event("STALE_TURN_ADVICE_DROPPED",
                              turn_epoch=self._turn_epoch,
                              active_turn_epoch=self._active_turn_epoch,
                              ai_request_attempt=self._ai_request_attempts)
        elif (self._active_job=="batch" and self._active_turn_epoch is not None
              and self._turn_screen_seen_at is not None
              and monotonic()-self._turn_screen_seen_at
                  >_CONSERVATIVE_AI_TURN_SECONDS
              and report.get("status") in (
                  "ADVICE","PROVISIONAL_ADVICE","TEST_ONLY")
              and report.get("recommendation")):
            # A local stopwatch is NOT authoritative evidence that the game's
            # current turn ended. A valid, same-epoch answer remains visible.
            # Truly superseded turns are rejected by the epoch/event guard above.
            elapsed=monotonic()-self._turn_screen_seen_at
            report["late_advice_presented"]=True
            report["warning"]=(str(report.get("warning") or "")+
                " 【32秒超過：現在のターンとゲーム内の残り時間を確認してください】")
            self._trace_event("LATE_TURN_ADVICE_PRESENTED",
                              turn_epoch=self._turn_epoch,
                              ai_request_attempt=self._ai_request_attempts,
                              elapsed_ms=round(elapsed*1000),
                              timeout_budget_ms=int(_CONSERVATIVE_AI_TURN_SECONDS*1000))
        elif (self._active_job=="batch"
              and self._active_turn_epoch is not None
              and self._turn_screen_seen_at is not None
              and monotonic()-self._turn_screen_seen_at>_TARGET_AI_TURN_SECONDS
              and report.get("status") in (
                  "ADVICE","PROVISIONAL_ADVICE","TEST_ONLY")
              and report.get("recommendation")):
            elapsed=monotonic()-self._turn_screen_seen_at
            report["warning"]=(str(report.get("warning") or "")+
                " 【応答遅延：ゲーム内の残り時間を確認して直ちに操作してください】")
            report["soft_target_exceeded"]=True
            self._trace_event("TURN_ADVICE_AFTER_SOFT_TARGET",
                              turn_epoch=self._turn_epoch,
                              ai_request_attempt=self._ai_request_attempts,
                              elapsed_ms=round(elapsed*1000),
                              target_budget_ms=int(_TARGET_AI_TURN_SECONDS*1000),
                              timeout_budget_ms=int(_CONSERVATIVE_AI_TURN_SECONDS*1000))
        if self._active_request_started is not None:
            report["queue_elapsed_ms"]=round((monotonic()-self._active_request_started)*1000)
            self._trace_event("AI_WORKER_FINISHED",queue_elapsed_ms=report["queue_elapsed_ms"],status=report.get("status","UNKNOWN"),api_total_ms=report.get("api_total_ms",0),api_first_output_ms=report.get("api_first_output_ms",0),image_prepare_ms=report.get("image_prepare_ms",0),validation_ms=report.get("validation_ms",0),auth_ms=report.get("auth_ms",0))
            self._active_request_started=None
        if self._active_job in ("auto_frame","batch") and not self._auto_running and self._cancel_event.is_set():
            return
        if report.get("status") == "UNCHANGED_FRAME":
            return
        # A correctly completed initial selection must not be thrown away
        # because its animated preview was automatically re-queued. Protect
        # the first legal selection only while we are still in selection phase;
        # real newer battle command screens remain eligible to supersede.
        if (self._active_job=="batch" and self._pending_batch is not None
                and self.state is not None
                and self.state.get("phase") in ("MATCHMAKING","SELECTION")
                and self._pending_screen_label in (None,"TEAM_SELECTION")
                and report.get("status") in ("OK","PROVISIONAL_ADVICE",
                                             "PARTIAL_ADVICE","TEST_ONLY")
                and len(report.get("selected_three") or ())==3
                and report.get("lead")):
            self._pending_batch=None
            self._pending_screen_label=None
            self._trace_event("SELECTION_RESULT_PROTECTED_FROM_DUPLICATE")
        # A genuinely NEW decision request supersedes an unanswered older
        # one. Duplicate preview images and local wall-clock expiry do NOT.
        # No new request can be sent until the current QThread finishes, so
        # a queued actionable frame is the preemption boundary.
        newer_selection_request_pending = (
            self._active_job=="batch"
            and report.get("screen")=="TEAM_SELECTION"
            and self._pending_batch is not None
            and self._pending_screen_label in ("TURN_ACTION","SWITCH_SELECTION"))
        superseded=(
            newer_selection_request_pending
            or (report.get("status")=="SUPERSEDED"
                and self._pending_batch is not None)
            or (self._active_job=="batch" and self._pending_batch is not None
                and _supersedes_result(report,self._pending_screen_label)))
        if newer_selection_request_pending:
            self._trace_event("STALE_SELECTION_ADVICE_DROPPED",
                              screen_label=self._pending_screen_label,
                              ai_request_attempt=self._ai_request_attempts)
        if self._pending_batch is not None and not superseded:
            # A reply beats another unclassified animation/duplicate. The
            # next new *observed* transition can still request a fresh action.
            self._trace_event("INFLIGHT_RESULT_PROTECTED",
                              status=report.get("status","UNKNOWN"))
            self._pending_batch=None
            self._pending_screen_label=None
        if superseded:
            report={**report,"status":"SUPERSEDED",
                    "recommendation":"","selected_three":[],"lead":None,
                    "warning":"NEWER_VERIFIED_SCREEN_PENDING"}
        if report.get("status") == "ERROR" and report.get("reason") == "AutoCaptureUnavailable":
            self._stop_auto()
        if (not superseded and payload.get("state", {}).get("phase")=="BATTLE"
                and self.state is not None and self.state.get("phase")=="PREP"):
            # An unclassified fallback frame was verified by vision. End PREP
            # without accepting any later stale PREP response.
            self._interrupt_prep()
        if report["status"] not in ("ERROR","DEADLINE_EXCEEDED",
                                    "CANCELLED","SUPERSEDED"):
            before=current_state_digest(self.state)
            after=current_state_digest(payload["state"])
            if before!=after:
                self._diagnostic("AI_STATE_UPDATE",{
                    "before":before,"after":after,
                    "source":"AI_IMAGE_OBSERVATION" if report.get(
                        "transport")!="OCR_TEXT_ONLY" else "TEXT_NO_NEW_IMAGE"})
                for side in ("our_active","opponent_active"):
                    if before.get(side)!=after.get(side):
                        self._diagnostic("ACTIVE_TRANSITION",{
                            "side":"own" if side=="our_active" else "opponent",
                            "previous":before.get(side),"current":after.get(side),
                            "source":"AI_IMAGE_OBSERVATION" if report.get(
                                "transport")!="OCR_TEXT_ONLY" else "TEXT_NO_NEW_IMAGE",
                            "verified":report.get("transport")!="OCR_TEXT_ONLY",
                            "last_confirmed_turn":self._turn_epoch})
            self.state=payload["state"]
            observed=report.get("screen")
            if observed and observed!="UNKNOWN":
                self._last_observed_ui=observed
                if observed=="COMMAND_MENU" and self._active_request_frame is not None:
                    self._command_menu_frame=self._active_request_frame.copy()
                elif observed in ("MOVE_MENU","PARTY_MENU","ACTION_RESOLUTION"):
                    self._command_menu_frame=None
        if report.get("status")=="ERROR":
            reason=report.get("reason","UNKNOWN")
            if (reason in ("ReadTimeout","APITimeoutError","TimeoutError")
                    and self.state is not None
                    and self.state.get("phase")=="MATCHMAKING"):
                self._set_ai_status("AI画像認識タイムアウト / 選出画面かは未確定・次の認識へ")
                report["phase_recognition_pending"]=True
                report["message"]=(
                    "ゲーム画面は選出に移っている可能性がありますが、"
                    "ChatGPTの応答が時間内に届かず、内部フェーズはMATCHMAKINGのままです。"
                    "相手6匹が確定するまで選出は出しません。")
            else:
                self._set_ai_status(f"AI処理エラー / {reason}")
        elif report.get("status")=="CANCELLED":
            self._set_ai_status("AI処理キャンセル")
        else:
            effort=report.get("live_reasoning_effort")
            speed_note=(" / 選出時モデル推論: "+str(effort)) if effort else ""
            self._set_ai_status(f"AI応答受信 / {report.get('status','UNKNOWN')}"+speed_note)
        if not superseded:
            self._trace_event("AI_RESULT_DISPLAYED",
                              status=report.get("status","UNKNOWN"),
                              ui=report.get("screen","UNKNOWN"),
                              ai_request_attempt=self._ai_request_attempts,
                              turn_epoch=self._turn_epoch,
                              queue_elapsed_ms=report.get("queue_elapsed_ms",0),
                              reason=report.get("reason","UNKNOWN"),
                              recommendation_present=bool(report.get("recommendation")),
                              selection_present=bool(report.get("selected_three")))
        entry={"step":len(self.entries)+1,**report}
        self.entries.append(entry)
        prefill_selection_prep=False
        if (report.get("screen")=="TEAM_SELECTION"
                and report.get("status") in (
                    "OK","PROVISIONAL_ADVICE","PARTIAL_ADVICE","TEST_ONLY")
                and len(report.get("selected_three") or ())==3 and report.get("lead")
                and not superseded
                and not report.get("selection_screen_unconfirmed")):
            self._proposed_selection=(tuple(report["selected_three"]),report["lead"])
            if self._selection_screen_started is None:
                self._selection_screen_started=(
                    monotonic()-report.get("queue_elapsed_ms",0)/1000.0)
                self._selection_clock_source="ESTIMATED_CAPTURE_SUBMISSION"
                self._trace_event("SELECTION_CLOCK_ESTIMATED_FROM_CAPTURE",
                                  source="ESTIMATED_CAPTURE_SUBMISSION")
            self.actual_three.setText(" / ".join(report["selected_three"]))
            self.actual_lead.setText(report.get("lead") or "")
            # The output is a recommendation, not proof of the player's
            # clicks. Adopt it *conditionally* to unlock cached PREP and the
            # 1st turn without an extra UI confirmation/action.
            try:
                self.state=assume_advised_selection(
                    self.state,self.assets,list(report["selected_three"]),
                    report["lead"])
            except (ValueError,KeyError) as exc:
                self._trace_event("AUTO_SELECTION_REJECTED",
                                  reason=type(exc).__name__)
            else:
                self._trace_event("AUTO_SELECTION_ASSUMED",
                                  selected_three_count=3,
                                  selection_assumed=True)
                # Seed the new PREP frame gate with the image which ACTUALLY
                # elicited selection (not the latest buffered animation).
                # Same selection screen cannot trigger a wasteful 30s repeat;
                # a genuine visual battle transition still triggers AI.
                if self._active_request_frame is not None:
                    self._automatic_probe.acknowledge(
                        self._active_request_frame,"PREP")
                prefill_selection_prep=True
        # Surface 3 + lead BEFORE even the local frozen-cache PREP step.
        self._render(entry)
        if prefill_selection_prep:
            self._prefill_cached_prep(
                roles={key:report.get(key) for key in (
                    "preserve","stopper","closer")})
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
            confirmed_key=(tuple(self.state["selected_three"]),self.state["lead"])
            self._trace_event("ACTUAL_SELECTION_CONFIRMED")
            # A completed provisional PREP is usable only if the three and
            # lead actually picked in the game match the AI recommendation.
            if self._proposed_prep_key==confirmed_key:
                if self._proposed_prep_payload is not None:
                    cache=self._proposed_prep_payload
                    if cache.get("report",{}).get("status") in (
                            "PREP_STRUCTURE_OK","PREP_PROVISIONAL","PREP_PARTIAL",
                            "PREP_CACHE_REUSED","PREP_CACHE_KNOWN_ONLY",
                            "PREP_CACHE_INCOMPLETE"):
                        self.state=cache["state"]
                        self._trace_event("PROVISIONAL_PREP_CONFIRMED")
                    else:
                        self.state["prep_status"]="INCOMPLETE"
                        self._proposed_prep_key=None
                        self._proposed_prep_payload=None
                elif self._prep_thread is not None and not self._prep_cancel_event.is_set():
                    self.state["prep_status"]="RUNNING"
                    self._trace_event("PROVISIONAL_PREP_AWAITING_RESULT")
                elif self._prep_thread is not None:
                    # Provisional worker has already timed out/cancelled;
                    # restart only after the old QThread has really exited.
                    self._proposed_prep_key=None
                    self._restart_prep_after_cleanup=True
            else:
                if self._prep_thread is not None:
                    self._interrupt_prep()
                    self._restart_prep_after_cleanup=True
                self._proposed_prep_key=None
                self._proposed_prep_payload=None
                self._trace_event("PROVISIONAL_PREP_SELECTION_DIFFERED")
            self.entries.append({"step":len(self.entries)+1,"operation":"CONFIRM_SELECTION",
                                 "selected_three":self.state["selected_three"],
                                 "lead":self.state["lead"],"status":"CONFIRMED"})
            self._render(self.entries[-1])
            # Prevent an unclassified fallback probe from redispatching the
            # final selection screen as if it were the opponent's first turn.
            if self._latest_image is not None:
                self._automatic_probe.acknowledge(self._latest_image,"PREP")
            if self.state.get("prep_status") not in (
                    "RUNNING","STRUCTURE_COMPLETE","PARTIAL_STRUCTURE_COMPLETE",
                    "CACHE_STRUCTURE_COMPLETE","CACHE_KNOWN_ONLY",
                    "CACHE_INCOMPLETE"):
                self._start_prep_auto()
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self,"実際の選出を確定できません",str(exc))

    def _prep(self):
        """Optional manual retry, never overlapping the automatic PREP."""
        if self.state is None or self.state.get("phase")!="PREP":
            QMessageBox.warning(self,"PREP不可","選出3匹と初手の確定後のみ実行できます。")
            return
        if QMessageBox.question(
            self,"PREP再実行の確認",
            "まだ対戦が始まっていない場合だけ40秒PREPを再実行しますか？",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )!=QMessageBox.StandardButton.Yes:return
        self._start_prep_auto()

    def _prefill_cached_prep(self, *, roles=None):
        """Immediate 18-pair cache + win roles from the SELECTION model call.

        A role is accepted only if it is one of the selected three. No second
        GPT request is needed when all roles and pre-evaluated matchups exist.
        Missing ratings/roles remain explicitly incomplete.
        """
        if (self.state is None or self.assets is None
                or self.state.get("phase")!="PREP"):
            return
        from .prep_cache import cached_pairs, cached_evidence_count
        known = tuple(name for name in self.state.get("opponent_six",[])
                      if name not in (self.state.get("visual_unknown_opponents") or []))
        selected = tuple(self.state.get("selected_three") or ())
        lead = self.state.get("lead")
        if len(selected)!=3 or not lead or len(known) not in (4,5,6):
            return
        pairs = cached_pairs(self.assets,selected,known,lead)
        if len(pairs)!=len(known)*3:
            return
        partial = len(known)!=6
        evaluated=cached_evidence_count(pairs)
        roles = roles or {}
        role_values = tuple(roles.get(k) for k in ("preserve","stopper","closer"))
        role_ready=all(isinstance(value,str) and value in selected
                       for value in role_values)
        role_count=3 if role_ready else sum(
            isinstance(value,str) and value in selected for value in role_values)
        all_cached=evaluated==len(pairs)
        # Cached matchups are the entire PREP deliverable. Win roles, when
        # already available from selection, are OPTIONAL; never call AI just
        # to fill preserve/stopper/closer before the player's first turn.
        ready=all_cached
        status=("PREP_CACHE_KNOWN_ONLY" if partial and ready else
                "PREP_CACHE_REUSED" if ready else
                "PREP_CACHE_INCOMPLETE")
        state_status=("CACHE_KNOWN_ONLY" if partial and ready else
                      "CACHE_STRUCTURE_COMPLETE" if ready else
                      "CACHE_INCOMPLETE")
        self.state["prep"] = {
            ("matchups_known" if partial else "matchups_18"):pairs,
            "source":"FROZEN_CACHE",
            "preserve":role_values[0] if role_ready else None,
            "stopper":role_values[1] if role_ready else None,
            "closer":role_values[2] if role_ready else None,
            "win_roles_status":"SELECTION_REUSED" if role_ready else "OPTIONAL_NOT_REQUESTED",
            "cached_matchups":evaluated,"missing_matchups":len(pairs)-evaluated,
            "unknown_opponent_slots":list(self.state.get("visual_unknown_opponents") or []),
            "unknown_pairs":[
                {"our_pokemon":member,"opponent_pokemon":opponent}
                for member in selected
                for opponent in (self.state.get("visual_unknown_opponents") or [])
            ],
            "note":"凍結済み対面評価を即時利用。勝ち筋3点の追加AI通信は行わない。"
                    "温存・切り返し・締めは実際の残り3匹に基づき各ターン判断。",
        }
        self.state["prep_status"]=state_status
        self._trace_event("PREP_CACHE_BASE_READY",
                          matchups_evaluated=evaluated,
                          known_opponents=len(known),missing_matchups=len(pairs)-evaluated,
                          win_conditions=role_count)
        if ready:
            # A full PREP is now complete without a separate API call. Emit
            # the normal PREP lifecycle markers so live test logs stay useful.
            self._trace_event("PREP_START",status="CACHED_FROM_SELECTION",
                              known_opponents=len(known),timeout_budget_ms=0)
            self._trace_event("PREP_RESULT",status=status,
                              matchups_evaluated=evaluated,win_conditions=role_count)
        self._render({"status":status,
                      "matchups_evaluated":evaluated,
                      "win_conditions":role_count,
                      "message":(
                          f"事前対面{evaluated}/{len(pairs)}を即時利用。"
                          "追加AI通信なし。勝ち筋は各ターン判断。"
                          if ready else
                          f"事前対面{evaluated}/{len(pairs)}を保持。"
                          "欠損箇所は未評価として保持。追加AI通信なし。")})


    def _attempt_result_screen_verification(self):
        """Only high-confidence, two-frame RESULT_SCREEN triggers this call.

        Wait a bounded time for already-queued asynchronous OCR; do not
        capture another image or run a second interpretation request.
        """
        candidate=self._result_screen_candidate
        if (candidate is None or self._result_screen_sent
                or self.state is None or self.state.get("phase")=="ENDED"
                or not self._auto_running or self._shutdown_intent is not None):
            return
        # An older action request cannot supersede a verified final screen.
        # _cleanup will prioritize the result over any pending action.
        if self._thread is not None:
            return
        evidence=self._drain_event_candidates(include_snapshot=True)
        # The result screen is not a 45-second action window. Prefer OCR
        # collected from the confirmed screen over racing the asynchronous
        # reader with only older battle messages.
        current_frame_ready=any(
            row.get("frame_id")==candidate["frame"] for row in evidence)
        if (not current_frame_ready
                and monotonic()-candidate["detected_at"]<1.5):
            QTimer.singleShot(200,self._attempt_result_screen_verification)
            return
        if not evidence:
            if monotonic()-candidate["detected_at"]<3.0:
                QTimer.singleShot(200,self._attempt_result_screen_verification)
            else:
                self._trace_event("RESULT_SCREEN_OCR_MISSING",
                                  frame_id=candidate["frame"])
            return
        self._result_screen_sent=True
        self._pending_result_messages=None
        self._pending_batch=None
        self._pending_screen_label=None
        self._active_job="result_screen"
        self._active_request_started=monotonic()
        self._trace_event("RESULT_SCREEN_GPT_REQUEST",images_sent=0,
                          frame_id=candidate["frame"],
                          ocr_candidate_count=len(evidence))
        self._thread=QThread(self)
        self._worker=_ReplayWorker(
            app_root=self.store_root,selected_model=self.model,
            reasoning_effort=self.reasoning_effort,
            assets=self.assets,state=self.state,job="result_screen",
            result_screen=candidate,
            local_field_candidates=evidence,
            cancel_event=self._cancel_event,battle_seconds=30.0)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._completed)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    def _request_result_from_ocr(self,messages):
        """Post-animation result check; image-free, one request per OCR phrase."""
        if self.state is None or self.state.get("phase")=="ENDED":
            return
        messages=[str(m)[:160] for m in messages if possible_result_message(m)]
        if not messages or not self._auto_running:
            return
        key=tuple(messages)
        if key in self._result_check_sent_keys:
            self._pending_result_messages=None
            return
        if self._thread is not None:
            self._pending_result_messages=messages
            self._trace_event("OCR_MATCH_RESULT_QUEUED_AFTER_INFLIGHT")
            return
        self._pending_result_messages=None
        self._result_check_sent_keys.add(key)
        self._active_job="result"
        self._active_request_started=monotonic()
        self._trace_event("OCR_MATCH_RESULT_TEXT_AI_SENT",images_sent=0,
                          message_count=len(messages))
        self._thread=QThread(self)
        self._worker=_ReplayWorker(
            app_root=self.store_root,selected_model=self.model,
            reasoning_effort=self.reasoning_effort,
            assets=self.assets,state=self.state,job="result",
            result_messages=messages,cancel_event=self._cancel_event,
            battle_seconds=30.0)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._completed)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    def _finish_match_manually(self, result):
        """Explicit human attestation when OCR is unavailable or ambiguous."""
        if result not in ("WIN","LOSS") or self.state is None:
            return
        if self.state.get("phase")=="ENDED":
            return
        answer=QMessageBox.question(
            self,"勝敗を確定",
            "ゲーム内の最終結果を確認しましたか？"
            " 確定すると対戦後記録・振り返りを開始します。",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer!=QMessageBox.StandardButton.Yes:
            return
        self._pending_batch=None
        self._pending_result_messages=None
        self._interrupt_prep()
        self.state["phase"]="ENDED"
        self.state["result"]=result
        self.state["last_event"]="PLAYER_CONFIRMED_MATCH_RESULT"
        self._trace_event("MANUAL_MATCH_RESULT_CONFIRMED",result=result)
        report={"status":"MATCH_END","screen":"USER_CONFIRMED_RESULT",
                "recommendation":"勝利。" if result=="WIN" else "敗北。",
                "result_source":"PLAYER_EXPLICIT_CONFIRMATION",
                "images_sent":0}
        self.entries.append({"step":len(self.entries)+1,**report})
        self._render(report)
        self._stop_auto()

    def _start_prep_auto(self, *, provisional=False):
        # No authentication, QThread, or API request for PREP. The six frozen
        # LIVE rows supply all available pairs. Missing cells stay unknown.
        if not provisional and self.state is not None and self.state.get("phase")=="PREP":
            self._prefill_cached_prep()
            return
        if provisional:
            return
        if (self.state is None or self._prep_thread is not None
                or self._thread is not None):
            return
        if provisional:
            if self.state.get("phase")!="SELECTION" or not self._proposed_selection:
                return
            try:
                prep_state=confirm_actual_selection(
                    self.state,self.assets,
                    list(self._proposed_selection[0]),self._proposed_selection[1])
            except (ValueError,KeyError):
                return
            self._proposed_prep_key=self._proposed_selection
        else:
            if self.state.get("phase")!="PREP":
                return
            prep_state=self.state
        if not (self.state.get("matrix_compatible") or self.state.get("matrix_provisional")):
            self.state["prep_status"]="UNAVAILABLE_MATRIX_MISMATCH"
            self._render({"status":"PREP_UNAVAILABLE",
                          "reason":"OWN_MATRIX_MISMATCH"})
            return
        # The selection countdown limits what we can promise to the player;
        # it must not kill a pending PREP request or discard a valid reply.
        # The next outgoing AI message is the only automatic supersession.
        self._prep_budget_seconds=prep_budget_before_battle(
            self._selection_screen_started,now=monotonic())
        if self._prep_budget_seconds<=2.0:
            self._trace_event("PREP_SOFT_BUDGET_EXPIRED_CONTINUING")
        self._prep_cancel_event.clear()
        if not provisional:
            self.state["prep_status"]="RUNNING"
        self._trace_event("PREP_START",timeout_budget_ms=int(self._prep_budget_seconds*1000),
                          status="PROVISIONAL" if provisional else "ACTUAL",
                          known_opponents=(
                              len(prep_state.get("opponent_six",[]))-
                              len(prep_state.get("visual_unknown_opponents") or [])),
                          unknown_opponents=len(prep_state.get("visual_unknown_opponents") or []))
        self._prep_thread=QThread(self)
        self._prep_worker=_ReplayWorker(
            app_root=self.store_root, selected_model=self.model,
            reasoning_effort=self.reasoning_effort,
            assets=self.assets, state=prep_state, job="prep",
            # Network failure still needs a finite socket timeout. It is NOT
            # the local game-clock target and late valid results are accepted.
            prep_seconds=max(120.0,self._prep_budget_seconds),
            cancel_event=self._prep_cancel_event,
        )
        self._prep_worker.moveToThread(self._prep_thread)
        self._prep_thread.started.connect(self._prep_worker.run)
        self._prep_worker.finished.connect(self._on_prep_result)
        self._prep_worker.finished.connect(self._prep_thread.quit)
        self._prep_thread.finished.connect(self._prep_worker.deleteLater)
        self._prep_thread.finished.connect(self._prep_thread.deleteLater)
        self._prep_thread.finished.connect(self._prep_cleanup)
        if self._prep_budget_seconds>0:
            self._prep_timer.start(int(self._prep_budget_seconds*1000))
        unknown_count=len(prep_state.get("visual_unknown_opponents") or [])
        known_count=6-unknown_count
        self._render({"status":"PREP_PROVISIONAL_RUNNING" if provisional else "PREP_RUNNING",
                      "message":(
                          f"既知{known_count}/6匹・{3*known_count}対面の"
                          "暫定PREPを実行中（未知の相手は未評価）。"
                          if unknown_count else
                          "提案選出に基づく18対面PREPを先行計算中。"
                          if provisional else "実際の選出で18対面PREPを計算中。")
                          +f"（目標残り {self._prep_budget_seconds:.1f}秒・"
                          "超過後も次のAI依頼まで継続）"})
        self._prep_thread.start()

    def _interrupt_prep(self):
        if self._prep_thread is None:
            return
        self._prep_timer.stop()
        self._prep_cancel_event.set()
        if self.state is not None and self.state.get("phase")=="PREP":
            self.state["prep_status"]="INTERRUPTED_BY_NEW_BATTLE_SCREEN"

    def _prep_deadline(self):
        if self._prep_thread is None:
            return
        # Advisory timer: never cancel or discard PREP merely for 40s expiry.
        self._trace_event("PREP_SOFT_DEADLINE_EXCEEDED_CONTINUING")
        if self.state is not None and self.state.get("phase")=="PREP":
            self._render({"status":"PREP_OVERTIME_RUNNING",
                          "message":"PREPの目標時間を超過しましたが、"
                          "次のAI依頼まで計算と有効回答の受付を継続します。"})

    @Slot(object)
    def _on_prep_result(self, payload):
        self._prep_timer.stop()
        if self._prep_cancel_event.is_set() or self.state is None:
            return
        result=payload["report"]
        if (self.state.get("phase")=="SELECTION"
                and self._proposed_prep_key is not None):
            # Preserve the result for actual confirmation, never mark it
            # LIVE-ready or silently make the suggested three actual.
            self._proposed_prep_payload=payload
            self._trace_event("PROVISIONAL_PREP_RESULT",
                              status=result.get("status","UNKNOWN"))
            self.entries.append({"step":len(self.entries)+1,
                                 "status":"PROVISIONAL_PREP_STORED",
                                 "message":"提案選出のPREP候補を保持。実際の3匹との一致確認待ち。",
                                 "matchups_evaluated":result.get("matchups_evaluated",0)})
            self._render(self.entries[-1])
            return
        if (self.state.get("phase")!="PREP"
                or self.state.get("prep_status")!="RUNNING"
                or self._prep_cancel_event.is_set()):
            return  # A new AI request or explicit stop supersedes PREP.
        self._trace_event("PREP_RESULT",status=result.get("status","UNKNOWN"))
        if result.get("status")=="CANCELLED":
            return
        if result.get("status") in ("ERROR", "DEADLINE_EXCEEDED"):
            self.state["prep_status"]=(
                "ERROR" if result["status"]=="ERROR" else "DEADLINE_EXCEEDED"
            )
            result={**result,"status":"PREP_ERROR" if result["status"]=="ERROR"
                    else "PREP_DEADLINE_EXCEEDED"}
        else:
            self.state=payload["state"]
        self.entries.append({"step":len(self.entries)+1, **result})
        self._render(self.entries[-1])

    @Slot()
    def _prep_cleanup(self):
        self._prep_thread=None
        self._prep_worker=None
        if self._shutdown_intent is not None:
            self._maybe_finish_shutdown()
            return
        if self._restart_prep_after_cleanup:
            self._restart_prep_after_cleanup=False
            if self.state is not None and self.state.get("phase")=="PREP":
                self._start_prep_auto()

    def _set_busy(self,busy):
        # Stop and Reset must remain reachable during network requests.
        for widget in (self.start_button,self.confirm_button,self.prep_button,
                       self.reconnect_button):
            widget.setEnabled(not busy)
        self.reset_button.setEnabled(self._shutdown_intent is None)
        self.auto_button.setEnabled(True)

    @Slot()
    def _cleanup(self):
        self._active_request_started=None
        self._active_request_frame=None
        self._active_turn_epoch=None
        self._active_state_revision=None
        self._active_request_id=None
        self._thread=None
        self._worker=None
        self._active_job=None
        self._inflight_screen_label=None
        self._set_busy(False)
        if self._shutdown_intent is not None:
            self._maybe_finish_shutdown()
            return
        if self._cancel_event.is_set():
            # Stop alone cancels the pending batch. Never restart PREP after
            # a stopped capture session.
            return
        if (self._result_screen_candidate is not None
                and not self._result_screen_sent and self._auto_running):
            self._attempt_result_screen_verification()
            if self._thread is not None:
                return
        if self._pending_result_messages is not None and self._auto_running:
            messages=self._pending_result_messages
            self._pending_result_messages=None
            key=tuple(messages)
            if key not in self._result_check_sent_keys:
                self._request_result_from_ocr(messages)
                return
        if self._pending_batch is not None and not self._cancel_event.is_set():
            self._send_pending()
        elif self._deferred_fallback_batch is not None and self._auto_running:
            frames=self._deferred_fallback_batch
            self._deferred_fallback_batch=None
            self._trace_event("FALLBACK_DEFERRED_DISPATCH")
            self._queue_batch(frames)
        elif (self.state is not None and self.state.get("phase")=="PREP"
              and self.state.get("prep_status")=="PENDING"
              and self._prep_thread is None):
            # An auto-adopted selection immediately starts PREP once its
            # selection-image QThread has really finished.
            self._start_prep_auto()
        # Provisional PREP no longer starts an additional AI request.

    def _refresh_live_status(self):
        """GUI-only update. Never captures or sends an AI request."""
        state=self.state or {}
        phase=state.get("phase","NOT_STARTED")
        prep=state.get("prep") or {}
        prep_status=state.get("prep_status","NOT_STARTED")
        expected=3*(6-len(state.get("visual_unknown_opponents") or []))
        done=prep.get("cached_matchups",0)
        if not isinstance(done,int):
            done=0
        roles=sum(
            isinstance(prep.get(k),str)
            and prep.get(k) in (state.get("selected_three") or ())
            for k in ("preserve","stopper","closer"))
        progress=(f"対面 {done}/{expected}・役割 {roles}/3（任意）"
                  if roles else f"対面 {done}/{expected}・役割は各ターンで判断")
        complete_statuses={
            "CACHE_STRUCTURE_COMPLETE","STRUCTURE_COMPLETE",
            "CACHE_KNOWN_ONLY","PARTIAL_STRUCTURE_COMPLETE"}
        incomplete_statuses={
            "CACHE_INCOMPLETE","INCOMPLETE","UNAVAILABLE_MATRIX_MISMATCH",
            "DEADLINE_EXCEEDED","ERROR","INTERRUPTED_BY_NEW_BATTLE_SCREEN"}
        if self._prep_thread is not None and not self._prep_cancel_event.is_set():
            prep_text="PREP：処理中（AI評価実行中）／"+progress
        elif prep_status in complete_statuses:
            prep_text="PREP：事前評価完了（構造確認）／"+progress
            if prep_status in ("CACHE_KNOWN_ONLY","PARTIAL_STRUCTURE_COMPLETE"):
                prep_text+="／未知相手あり・部分評価"
        elif prep_status in incomplete_statuses:
            prep_text="PREP：未完成・中断・エラー／"+progress+"／"+str(prep_status)
        elif phase in ("PREP","BATTLE"):
            prep_text="PREP：処理待ち・評価途中／"+progress
        else:
            prep_text="PREP：選出待ち（未開始）"
        if state.get("selection_assumed") and phase in ("PREP","BATTLE"):
            prep_text+="／実際の選出3匹は未確認"
        self.prep_banner.setText(prep_text)
        labels={
            "MATCHMAKING":"マッチング中・選出画面待ち",
            "SELECTION":"選出画面・3匹と初手を決める",
            "PREP":"選出提案から対戦準備中（実際の選出は画面照合）",
            "BATTLE":"対戦中・ターン判断",
            "ENDED":"対戦終了",
            "NOT_STARTED":"開始待ち",
        }
        observed=self._latest_recognition_screen
        indicator=(
            f"／画像候補：{observed}（AI未確認）"
            if observed and self._active_job=="batch" else "")
        self.phase_banner.setText(
            "現在のフェイズ："+labels.get(phase,phase)+indicator)
        if self._active_job=="batch" and self._active_request_started is not None:
            elapsed=monotonic()-self._active_request_started
            current=("OCRテキストから戦術判断中"
                     if self._inflight_screen_label in _URGENT_SCREENS
                     else "画像から相手選出を確認中")
            if phase in ("PREP","BATTLE") and self._turn_screen_seen_at is not None:
                remaining=max(0,int(_CONSERVATIVE_AI_TURN_SECONDS-
                                     (monotonic()-self._turn_screen_seen_at)))
                clock=(f"操作画面初回検知からAI表示最終期限まで約{remaining}秒"
                       "／目標30秒・ゲーム内時計は未取得・遅延約10秒を仮定")
            else:
                remaining=max(0,int(55-elapsed))
                clock=f"AI処理開始から {elapsed:.1f}秒／目標残り約{remaining}秒"
        elif self._prep_thread is not None and not self._prep_cancel_event.is_set():
            current="PREP：既存対面評価と勝ち筋を確認中"
            remaining=max(0,self._prep_timer.remainingTime()//1000)
            clock=f"PREP処理期限まで約{remaining}秒"
        else:
            if phase=="PREP" and state.get("prep_status") in (
                    "PENDING","CACHE_PREP_BASE_READY"):
                cache=state.get("prep") or {}
                current=(f"事前対面評価{cache.get('cached_matchups',0)}件を保持済み"
                         "／追加AI通信なし")
            else:
                current=("選出の画像認識・判断待ち" if phase=="SELECTION"
                         else "次の行動選択画面の検出待ち" if phase=="BATTLE"
                         else "ゲーム画面監視中" if self._auto_running
                         else "監視停止中")
            if (phase=="BATTLE" and self._turn_screen_seen_at is not None
                    and not self._turn_event_after_action):
                remaining=max(0,int(_CONSERVATIVE_AI_TURN_SECONDS-
                                     (monotonic()-self._turn_screen_seen_at)))
                clock=f"AI表示最終期限まで約{remaining}秒（目標30秒／ゲーム内時計未取得）"
            else:
                clock="次の画面の認識待ち／ゲーム内の残り時間は未取得"
        if phase in ("MATCHMAKING","SELECTION") and self._selection_screen_started is not None:
            reserve=max(0,87-(monotonic()-self._selection_screen_started))
            clock+=f"／選出→PREP推定余裕 {reserve:.0f}秒"
            clock+=("（ローカル分類候補起点）" if
                    self._selection_clock_source=="LOCAL_CANDIDATE"
                    else "（AI送信画像から逆算・未検証）")
        if self._active_request_started is not None and self._active_job=="batch":
            if remaining<=5:
                clock+="【期限間近】"
        if phase=="PREP" and state.get("selection_assumed"):
            current+="／実選出3匹は未確認"
        self.activity_banner.setText("アプリの作業："+current)
        self.deadline_banner.setText("時間："+clock+"（ローカル計測・ゲーム内時計とは別）")
        self.live_clock_changed.emit(clock)

    def _render(self, report):
        state=self.state or {}
        phase=state.get("phase","NOT_STARTED")
        compatible=state.get("matrix_compatible",False)
        suffix=("構築別LIVEマトリクス有効" if compatible
                else "登録構築専用・根拠限定AI判断（未検証）"
                if state.get("matrix_provisional")
                else "LIVEマトリクス不一致・機能試験のみ")
        sending = ("画面取得待機中（自動再試行）" if self._auto_running and self._capture_paused
                   else "自動送信中" if self._auto_running else "自動送信停止")
        if phase=="MATCHMAKING" and report.get("status")=="AI_REQUEST_QUEUED":
            if report.get("screen_label_hint")=="TEAM_SELECTION":
                display_phase="SELECTION候補（ローカル検出・AI確認待ち）"
            else:
                display_phase="画面判定中（内部MATCHMAKING・未確定）"
        elif phase=="MATCHMAKING" and report.get("phase_recognition_pending"):
            display_phase="画面判定失敗（内部MATCHMAKING・未更新）"
        else:
            display_phase=phase
        self.status.setText(
            f"段階：{display_phase} / {suffix} / {sending} / 記録 {len(self.entries)} 件")
        if report.get("screen") == "TEAM_SELECTION" and (
                report.get("selected_three") and report.get("lead")):
            selection=list(report["selected_three"])
            self._last_selection_text=(
                "選出（提案）："+ " / ".join(selection)
                +"   初手（提案）："+str(report["lead"]))
        elif state.get("selected_three") and state.get("lead"):
            is_proposal=bool(state.get("selection_assumed"))
            assumption="（提案）" if is_proposal else "（手動登録）"
            lead_label="初手（提案）：" if is_proposal else "初手："
            self._last_selection_text=(
                "選出"+assumption+"："+ " / ".join(state["selected_three"])
                +"   "+lead_label+str(state["lead"]))
        if report.get("status") in ("ADVICE","PROVISIONAL_ADVICE","TEST_ONLY") and (
                report.get("screen") in ("COMMAND_MENU","MOVE_MENU","PARTY_MENU")
                and report.get("recommendation")):
            self._last_action_text=str(report["recommendation"])
            # Remember a direct switch while the player opens the party menu.
            # A newly observed battle resolution/forced replacement invalidates it.
            if (report.get("action") or {}).get("kind")=="SWITCH":
                self._last_advised_switch={
                    "target":(report.get("action") or {}).get("target"),
                    "turn_epoch":self._turn_epoch}
            else:
                self._last_advised_switch=None
        if report.get("status") in ("MATCH_STARTED","RESET"):
            self._last_action_text=""
            self._last_selection_text=""
        if report.get("status") in (
                "NO_ACTION","BLOCKED","ERROR","CANCELLED","DEADLINE_EXCEEDED",
                "SUPERSEDED","MATCH_END"):
            # OCR text-only reports may omit screen. This must not resurrect
            # a prior action when a fresh turn produced no legal advice.
            self._last_action_text=""
            self._last_advised_switch=None
        elif (report.get("screen") in (
                "COMMAND_MENU","PARTY_MENU","DETAIL_OVERLAY",
                "ACTION_RESOLUTION","RESULT_SCREEN")
                and report.get("status")=="OBSERVED"):
            self._last_action_text=""
        self.selection_banner.setText(
            self._last_selection_text+"（実選出未確認）"
            if self._last_selection_text and state.get("selection_assumed")
            else self._last_selection_text or "選出：未提示")
        if state.get("phase")=="ENDED":
            action_text="対戦終了："+str(state.get("result","UNKNOWN"))
        elif report.get("status")=="AI_REQUEST_QUEUED":
            action_text=("交代先を判定中（倒れたポケモンの技は無効）"
                         if report.get("screen_label_hint")=="SWITCH_SELECTION"
                         else "現在の対面から技4つ・交代候補を比較中")
        elif report.get("status") in ("BLOCKED","NO_ACTION","ERROR","CANCELLED"):
            action_text="今回のAI提案なし（前ターンの指示は無効）"
        elif report.get("status")=="DEADLINE_EXCEEDED":
            action_text="AI表示最終期限超過：このターンの新しい提案なし"
        elif report.get("status")=="SUPERSEDED":
            action_text="前ターンの提案を破棄（新しい画面を確認中）"
        elif report.get("screen")=="COMMAND_MENU" and (
                report.get("status") not in ("ADVICE","PROVISIONAL_ADVICE","TEST_ONLY")):
            action_text="技4つ・交代先の判定未成立（AIによる提案なし）"
        elif (self._latest_recognition_screen=="SWITCH_SELECTION"
              and report.get("status") not in (
                  "ADVICE","PROVISIONAL_ADVICE","TEST_ONLY")):
            action_text="交代画面を検出。次に出すポケモンは未確定（旧技提案は無効）"
        elif self._last_action_text and state.get("phase")=="BATTLE":
            action_text=self._last_action_text
        else:
            action_text="技・交代先の画像判定待ち（まだ行動提案なし）"
        displayed="次の行動："+action_text
        self.action_banner.setText(displayed)
        if report.get("status") in ("ADVICE","PROVISIONAL_ADVICE","TEST_ONLY",
                                   "SUPERSEDED","NO_ACTION","BLOCKED",
                                   "AI_REQUEST_QUEUED"):
            self._diagnostic("GUI_DISPLAY",{
                "displayed":displayed,
                "recommendation":report.get("recommendation") or "",
                "display_source":(
                    "NEW_AI_RESPONSE" if report.get("status") in (
                        "ADVICE","PROVISIONAL_ADVICE","TEST_ONLY")
                    else "QUEUE_OR_BLOCKED"),
                "status":report.get("status"),
                "our_active":state.get("our_active"),
                "opponent_active":state.get("opponent_active"),
                "state_revision":state.get("_live_state_revision",0)})
        if report.get("status") in ("ADVICE","PROVISIONAL_ADVICE","TEST_ONLY"):
            self._trace_event("ACTION_GUI_RENDERED",
                status=report.get("status"),
                target_own=state.get("our_active",""),
                target_opponent=state.get("opponent_active",""),
                turn_epoch=self._turn_epoch,
                state_revision=state.get("_live_state_revision",0),
                recommendation_present=bool(report.get("recommendation")))
        report["display_phase"]=display_phase
        self._refresh_live_status()
        rec=report.get("recommendation","") or report.get("message","")
        if report.get("status")=="TEST_ONLY":
            rec="【機能試験のみ・戦術的な妥当性未検証】"+rec
        elif report.get("status")=="PROVISIONAL_ADVICE" and not rec.startswith("【暫定"):
            rec="【暫定AI推奨・対面未認証】"+rec
        line=(rec+"\n\n") if rec else ""
        self.output.setPlainText(
            line+json.dumps({"latest":report,"replay_state":state,
                             "timeline":self.entries},ensure_ascii=False,indent=2))
        self.output.verticalScrollBar().setValue(0)
        if self._debug_log is not None:
            self._debug_log.report(report)
        # Only observed UI reports; no hidden controller state is inferred.
        self.state_changed.emit(dict(state), dict(report))

    def _export(self):
        if self.state is None:return
        dest,_=QFileDialog.getSaveFileName(self,"対戦テスト結果を保存","","JSON (*.json)")
        if not dest:return
        report={"scope":"OPT_IN_AUTO_CAPTURE_MATCH_SUPPORT","model":self.model,
                "training_frames":"local_unlabelled_opt_in_not_exported",
                "reasoning_effort":self.reasoning_effort,
                "matrix_compatible":self.state["matrix_compatible"],
                "state":self.state,"timeline":self.entries,
                "timing_observations":{
                    "schema":"champions-local-ui-timing-candidates-v1",
                    "source":"UNVERIFIED_LOCAL_GUI_MONOTONIC_CLOCK",
                    "automatically_certified":False,
                    "events":list(self._timing_trace),
                    "warning":"SCREEN_CANDIDATE_NOT_VERIFIED_GAME_START; "
                              "USER_VIDEO_REVIEW_REQUIRED_FOR_E2E",
                }}
        try:
            with open(dest,"x",encoding="utf-8") as output:
                json.dump(report,output,ensure_ascii=False,indent=2)
        except FileExistsError:
            QMessageBox.warning(self,"既存ファイル","上書きしません。別の名前を選んでください。")
        except OSError:
            QMessageBox.warning(self,"保存不可","指定の場所に書き込めません。")

    def closeEvent(self, event):
        if not self._shutdown_ready and (
                self._thread is not None or self._prep_thread is not None):
            self.request_shutdown("close")
            event.ignore()
            return
        self._stop_auto()
        if self._debug_log is not None:
            self._debug_log.write("WINDOW_CLOSED")
            self._debug_log.close()
            self._debug_log=None
        super().closeEvent(event)
