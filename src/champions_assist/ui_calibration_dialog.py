"""Private, explicit manual calibration dialog; no model or game control."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QPushButton,
    QPlainTextEdit,QMessageBox,QFileDialog,QWidget)
from .settings_dialog import RoiPreviewLabel
from .ui_calibration import LocalUiCalibration, SUPPORTED_LABELS
from .vision import DetectedUI


class UiCalibrationDialog(QDialog):
    """Train and validate using different game-viewport frames, never same-frame accuracy."""

    def __init__(self, *, storage_root: str | Path, initial_image: np.ndarray | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("認識較正（実機診断・オフライン）")
        self.setMinimumSize(900, 720)
        self.calibration = LocalUiCalibration(Path(storage_root) / "ui-calibration")
        self.image: np.ndarray | None = None
        self._last: dict | None = None
        layout = QVBoxLayout(self)
        self.help = QLabel("ゲーム領域を表示し、変化の少ないUIの一部分だけをドラッグ指定してください。\n"
                           "学習に使った画像では精度を評価できません。別フレームを開いて「分類を試す」を実行します。")
        self.help.setWordWrap(True)
        layout.addWidget(self.help)
        self.preview = RoiPreviewLabel()
        layout.addWidget(self.preview, 1)
        controls = QHBoxLayout()
        self.label = QComboBox()
        for kind in DetectedUI:
            if kind is not DetectedUI.UNKNOWN:
                self.label.addItem(kind.value)
        controls.addWidget(self.label)
        for caption, func in (("このUIを登録",self.add_sample),("画像を開く",self.open_image),
                              ("分類を試す",self.classify),("選択部分OCR",self.ocr_probe),
                              ("診断JSONを保存",self.export_report)):
            button = QPushButton(caption)
            button.clicked.connect(func)
            controls.addWidget(button)
        layout.addLayout(controls)
        self.status = QLabel()
        layout.addWidget(self.status)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output)
        if initial_image is not None:
            self.set_image(initial_image)
        self.refresh_status()

    def refresh_status(self) -> None:
        try:
            count = len(self.calibration.list_templates())
        except (OSError, ValueError):
            count = 0
        self.status.setText(f"登録テンプレート: {count} / ローカルのみ / 自動操作・API通信なし")

    def set_image(self, image: np.ndarray) -> None:
        self.image = image.copy()
        self.preview.set_frame(self.image)
        self.preview.set_normalized_roi(None)
        self._last = None

    def open_image(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self,"ゲーム領域PNGを開く", "", "Images (*.png *.jpg *.jpeg)")
        if filename:
            try:
                self.set_image(self.calibration.read_local_png(filename))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self,"画像を開けません",str(exc))

    def add_sample(self) -> None:
        if self.image is None or self.preview.normalized_roi is None:
            QMessageBox.information(self,"範囲が必要","画像内で特徴部分を選択してください。")
            return
        try:
            row = self.calibration.add_template(self.image,label=self.label.currentText(),
                                                anchor=self.preview.normalized_roi)
            self.output.setPlainText(f"登録: {row['label']} / id={row['id']}\n別フレームで分類を試してください。")
            self.refresh_status()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self,"登録できません",str(exc))

    def classify(self) -> None:
        if self.image is None:
            return
        try:
            result = self.calibration.classify(self.image).as_dict()
            expected = self.label.currentText()
            result["expected_label"] = expected
            result["matches_expected"] = result["visual_ui"] == expected if result["status"] == "CANDIDATE_ONLY" else None
            self._last = result
            self.output.setPlainText(json.dumps(result,ensure_ascii=False,indent=2))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self,"分類できません",str(exc))

    def ocr_probe(self) -> None:
        if self.image is None or self.preview.normalized_roi is None:
            QMessageBox.information(self,"範囲が必要","文字のある部分をドラッグ指定してください。")
            return
        result = self.calibration.run_ocr_probe(self.image,anchor=self.preview.normalized_roi)
        self.output.setPlainText(json.dumps(result,ensure_ascii=False,indent=2))

    def export_report(self) -> None:
        if self._last is None:
            QMessageBox.information(self,"診断が必要","先に別フレームで分類を試してください。")
            return
        path, _ = QFileDialog.getSaveFileName(self,"診断JSONを保存","","JSON (*.json)")
        if path:
            target = Path(path)
            if target.exists():
                QMessageBox.warning(self,"上書きできません","既存ファイルは上書きしません。")
                return
            try:
                with target.open("x",encoding="utf-8") as out:
                    json.dump(self._last,out,ensure_ascii=False,indent=2)
            except OSError as exc:
                QMessageBox.warning(self,"保存できません",type(exc).__name__)
