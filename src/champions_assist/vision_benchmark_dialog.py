"""Explicit single-image OpenAI vision benchmark dialog, offline until button press."""
from __future__ import annotations
import json
import os
from pathlib import Path
from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QFileDialog, QPlainTextEdit, QMessageBox, QWidget)
from .vision_api_benchmark import OpenAIVisionBenchmark, read_image, SUPPORTED_UI
from .api_credentials import get_api_key, CredentialStoreError


class _VisionWorker(QObject):
    finished = Signal(object)

    def __init__(self, image_path: str, model: str, key: str, expected: str | None):
        super().__init__()
        self.image_path, self.model, self.key, self.expected = image_path, model, key, expected

    @Slot()
    def run(self):
        try:
            image = read_image(self.image_path)
            report = OpenAIVisionBenchmark(model=self.model).run(image, api_key=self.key).report(self.expected)
        except Exception as exc:
            report = {"status": "ERROR", "error_type": type(exc).__name__, "scope": "OFFLINE_SINGLE_IMAGE_ONLY"}
        self.finished.emit(report)


class VisionBenchmarkDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("OpenAI画像認識 実測テスト（明示送信のみ）")
        self.setMinimumSize(740, 510)
        self._thread: QThread | None = None
        self._worker: _VisionWorker | None = None
        self._report: dict | None = None
        form = QVBoxLayout(self)
        form.addWidget(QLabel("PNG/JPEGを1枚だけ明示送信します。画像内容が外部APIに送信され、利用料金が発生します。対戦支援には反映しません。"))
        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        choose = QPushButton("保存画像を選択")
        choose.clicked.connect(self.choose_image)
        row.addWidget(self.path, 1)
        row.addWidget(choose)
        form.addLayout(row)
        row = QHBoxLayout()
        self.expected = QComboBox()
        self.expected.addItem("正解ラベルなし", None)
        for ui in SUPPORTED_UI:
            self.expected.addItem(ui, ui)
        self.model = QLineEdit(os.environ.get("CHAMPIONS_ASSIST_OPENAI_MODEL", "gpt-4.1-mini"))
        row.addWidget(QLabel("正解UI:"))
        row.addWidget(self.expected)
        row.addWidget(QLabel("モデル:"))
        row.addWidget(self.model)
        form.addLayout(row)
        form.addWidget(QLabel("APIキーは設定 → OpenAI APIから登録可能です。キーは設定JSONに保存しません。"))
        row = QHBoxLayout()
        self.send = QPushButton("この画像をOpenAIに送信して実測")
        self.send.clicked.connect(self.start)
        self.save = QPushButton("診断JSONを保存")
        self.save.setEnabled(False)
        self.save.clicked.connect(self.save_report)
        row.addWidget(self.send)
        row.addWidget(self.save)
        form.addLayout(row)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        form.addWidget(self.output, 1)

    def choose_image(self):
        p, _ = QFileDialog.getOpenFileName(self, "テスト画像を選択", "", "Images (*.png *.jpg *.jpeg)")
        if p:
            self.path.setText(p)
            self._report = None
            self.save.setEnabled(False)

    def start(self):
        if self._thread is not None:
            return
        path = self.path.text()
        try:
            key = get_api_key() or ""
        except CredentialStoreError:
            QMessageBox.warning(self, "実測できません", "Windows資格情報を読み取れません。")
            return
        model = self.model.text().strip()
        if not path or not Path(path).is_file() or not key or not model:
            QMessageBox.warning(self, "実測できません", "画像、モデル、登録済みAPIキーを確認してください。")
            return
        confirmed = QMessageBox.question(
            self, "外部API送信の確認", "指定した画像1枚をOpenAI APIへ送信します。API利用料金が発生します。実行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self.send.setEnabled(False)
        self.output.setPlainText("API応答を計測中（30秒タイムアウト、SDK再試行なし）")
        self._thread = QThread(self)
        self._worker = _VisionWorker(path, model, key, self.expected.currentData())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._complete)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    @Slot(object)
    def _complete(self, report):
        self._report = report
        self.output.setPlainText(json.dumps(report, ensure_ascii=False, indent=2))
        self.save.setEnabled(True)

    @Slot()
    def _cleanup(self):
        self._thread = None
        self._worker = None
        self.send.setEnabled(True)

    def save_report(self):
        if self._report is None:
            return
        p, _ = QFileDialog.getSaveFileName(self, "診断JSONを保存", "", "JSON (*.json)")
        if not p:
            return
        try:
            with open(p, "x", encoding="utf-8") as f:
                json.dump(self._report, f, ensure_ascii=False, indent=2)
        except FileExistsError:
            QMessageBox.warning(self, "上書き不可", "既存ファイルは上書きしません。")
        except OSError as exc:
            QMessageBox.warning(self, "保存できません", type(exc).__name__)
