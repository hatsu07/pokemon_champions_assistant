"""Opt-in, isolated postmatch pipeline diagnostics. No game capture or real DB writes."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QPlainTextEdit, QVBoxLayout, QWidget,
)

from .postmatch_review_e2e_test import run_postmatch_review_e2e


class _ReviewE2EWorker(QThread):
    stage_changed = Signal(str)
    result_ready = Signal(object)

    def __init__(self, *, app_root: Path, model: str, effort: str,
                 use_api: bool, parent=None):
        super().__init__(parent)
        self.app_root = app_root
        self.model = model
        self.effort = effort
        self.use_api = use_api

    def run(self) -> None:
        try:
            token = None
            if self.use_api:
                from .siwc_auth import ChatGPTSignIn, ProtectedSessionStore
                token = ChatGPTSignIn(ProtectedSessionStore(self.app_root)).access_token()
            result = run_postmatch_review_e2e(
                model=self.model, reasoning_effort=self.effort,
                access_token=token, use_api=self.use_api,
                on_stage=self.stage_changed.emit)
        except Exception as exc:
            # Never put credentials, prompts, or raw API error text into UI.
            from .team_ai_analysis import safe_ai_error
            result = {
                "status": "FAILED", "stage": "WORKER_FAILED",
                "mode": "API" if self.use_api else "OFFLINE",
                "error": safe_ai_error(exc), "error_type": type(exc).__name__,
                "review_saved": False,
            }
        self.result_ready.emit(result)


class PostmatchReviewTestPage(QWidget):
    """Each button creates its own temporary SQLite and synthetic ENDED/WIN."""

    idle = Signal()
    _STAGES = {
        "NOT_STARTED": "未開始",
        "AUTH_REQUIRED": "認証不足",
        "RECORD_END": "記録保存",
        "REVIEW_REQUEST": "AIレビュー中",
        "READBACK": "SQLite保存確認",
        "COMPLETE": "完了",
        "WORKER_FAILED": "失敗",
    }

    def __init__(self, *, app_root: Path, model: str, effort: str, parent=None):
        super().__init__(parent)
        self.app_root = Path(app_root)
        self.model = model
        self.effort = effort
        self._worker: _ReviewE2EWorker | None = None
        self._closing = False
        layout = QVBoxLayout(self)
        intro = QLabel(
            "TEST_ONLY / SYNTHETIC_TEST：実戦の勝敗検知・戦術評価・画面取得は検証しません。"
            "合成 ENDED/WIN を一時SQLiteに記録し、本番と同じ record_finished → "
            "request_review → SQLite読戻しを検査します。実戦の対戦履歴・学習DBは変更しません。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        api_warning = QLabel(
            "実APIレビュー試験はログイン済みモデルに送信し、通信とAPI料金が発生します。"
            "押下しない限り実API通信しません。通信タイムアウトは最大90秒です。"
        )
        api_warning.setWordWrap(True)
        layout.addWidget(api_warning)
        buttons = QHBoxLayout()
        self.offline_button = QPushButton("オフライン模擬試験")
        self.api_button = QPushButton("実APIレビュー試験（有料通信）")
        buttons.addWidget(self.offline_button)
        buttons.addWidget(self.api_button)
        layout.addLayout(buttons)
        self.offline_button.clicked.connect(lambda: self.start(False))
        self.api_button.clicked.connect(lambda: self.start(True))
        self.stage_label = QLabel("未開始（TEST_ONLY）")
        self.stage_label.setWordWrap(True)
        layout.addWidget(self.stage_label)
        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setPlainText("一時SQLite（試験終了時に自動削除）／実ゲーム未検証")
        layout.addWidget(self.report_view, 1)

    @property
    def has_active_worker(self) -> bool:
        return self._worker is not None

    def start(self, use_api: bool) -> None:
        if self._closing or self._worker is not None:
            return
        if use_api and not self.model:
            self.stage_label.setText("失敗：モデル未設定（実APIは呼び出していません）")
            return
        self.offline_button.setEnabled(False)
        self.api_button.setEnabled(False)
        self.stage_label.setText("記録保存へ移行中（TEST_ONLY）")
        self.report_view.setPlainText(
            ("実API" if use_api else "オフライン") +
            "／一時SQLiteのみ／実戦データは使用しません"
        )
        worker = _ReviewE2EWorker(
            app_root=self.app_root, model=self.model, effort=self.effort,
            use_api=use_api, parent=self)
        self._worker = worker
        worker.stage_changed.connect(self._on_stage)
        worker.result_ready.connect(self._on_result)
        worker.finished.connect(self._on_finished)
        worker.start()

    @Slot(str)
    def _on_stage(self, stage: str) -> None:
        self.stage_label.setText(self._STAGES.get(stage, "失敗：" + stage) + "（TEST_ONLY）")

    @Slot(object)
    def _on_result(self, report: dict) -> None:
        ok = report.get("status") == "PASS"
        self.stage_label.setText(("完了・PASS" if ok else "失敗・FAIL") + "（TEST_ONLY）")
        fields = [
            "種別: " + str(report.get("mode", "UNKNOWN")),
            "試合ID: " + str(report.get("match_id", "—")),
            "最終段階: " + str(report.get("stage", "UNKNOWN")),
            "試合記録: " + str(bool(report.get("record_saved"))),
            "レビューSQLite保存: " + str(bool(report.get("review_saved"))),
            "レビュー状態: " + str(report.get("review_status", "UNKNOWN")),
            "保存件数: " + str(report.get("findings", 0)),
            "保存先: 隔離一時SQLite（試験終了で削除）",
            "実ゲーム検証: なし",
        ]
        if not ok:
            fields.append("失敗理由: " + str(report.get("error_type") or report.get("stage")))
        if ok and report.get("mode") == "API":
            fields.append("AI要約（未検証）: " + str(report.get("next_match_notes", "")))
        self.report_view.setPlainText("\n".join(fields))

    @Slot()
    def _on_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        if not self._closing:
            self.offline_button.setEnabled(True)
            self.api_button.setEnabled(True)
        self.idle.emit()

    def request_close(self) -> None:
        # QThread cannot interrupt a running HTTP request safely. Do not
        # destroy it; prevent new work and let the bounded request finish.
        self._closing = True
        self.offline_button.setEnabled(False)
        self.api_button.setEnabled(False)
