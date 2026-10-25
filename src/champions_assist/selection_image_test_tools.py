"""Off-match preview of the actual JPEG payload, and controlled model/resolution A/B.

No pixels, data URIs, auth token or free-form model text are saved in JSONL.
Comparison requests are explicit user actions, sequential and ONLY use models
returned from the connected ChatGPT account (or its already selected model).
"""
from __future__ import annotations

from pathlib import Path
from time import perf_counter

import numpy as np
from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,QDialog,QFormLayout,QHBoxLayout,QLabel,QLineEdit,
    QMessageBox,QPushButton,QScrollArea,QTableWidget,QTableWidgetItem,
    QTabWidget,QVBoxLayout,QWidget,
)

from .selection_image_diagnostics import (
    payload_samples,payload_statistics,resize_same_image,
    run_recognition_comparison,truth_slots,
)
from .selection_vision import selection_image_content
from .siwc_auth import ChatGPTSignIn,ProtectedSessionStore


def preview_payload_report(image: np.ndarray, percent: int,
                           *, retry: bool = False) -> tuple[list[dict],dict]:
    source=resize_same_image(image,percent)
    items=payload_samples(source,include_full=retry)
    metadata=payload_statistics(
        source,selection_image_content(source,introduction="preview",
                                       include_full=retry))
    return items,metadata


class OutboundImagePreviewDialog(QDialog):
    """Renders bytes decoded from the EXACT JPEG data URI builder."""

    def __init__(self, *, image: np.ndarray, percent: int=100, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ChatGPTに送信する画像をプレビュー（保存・通信なし）")
        self.resize(780,620)
        outer=QVBoxLayout(self)
        self.tabs=QTabWidget()
        outer.addWidget(self.tabs)
        for title,retry in (("初回認識",False),("再認識時",True)):
            tab=QWidget()
            lay=QVBoxLayout(tab)
            items,meta=preview_payload_report(image,percent,retry=retry)
            label=QLabel(
                f"元画像 {meta['visual_source_width']}×{meta['visual_source_height']} px / "
                f"右側切り出し X={meta['visual_crop_x']} px "
                f"{meta['visual_crop_width']}×{meta['visual_crop_height']} px / "
                f"送信 {meta['visual_image_count']}画像 / "
                f"JPEG計 {meta['visual_total_jpeg_bytes']:,} B / "
                f"Data URI計 {meta['visual_total_data_uri_bytes']:,} B"
            )
            label.setWordWrap(True)
            lay.addWidget(label)
            for idx,item in enumerate(items,1):
                lab=QLabel(
                    f"画像{idx}：{item['width']}×{item['height']}px / "
                    f"{item['jpeg_bytes']:,} JPEG bytes / detail={item['detail']}"
                )
                lay.addWidget(lab)
                pic=QPixmap()
                if not pic.loadFromData(item["jpeg"],"JPG"):
                    raise ValueError("JPEG_PREVIEW_FAILED")
                view=QLabel()
                view.setAlignment(Qt.AlignmentFlag.AlignCenter)
                view.setPixmap(pic.scaled(710,400,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation))
                lay.addWidget(view)
            scroll=QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(tab)
            self.tabs.addTab(scroll,title)
        info=QLabel("画像は現在のアプリと同じJPEG生成関数で作成しています。"
                    "画面表示上は縮小されますが、上のピクセル数・バイト数は実際の送信画像です。"
                    "再認識画像は、初回結果の検証に失敗した場合だけ送ります。")
        info.setWordWrap(True)
        outer.addWidget(info)


class _ModelListWorker(QObject):
    finished=Signal(object)

    def __init__(self, root: Path):
        super().__init__()
        self.root=root

    @Slot()
    def run(self):
        try:
            models=ChatGPTSignIn(ProtectedSessionStore(self.root)).models()
            self.finished.emit({"models":models})
        except Exception as exc:
            self.finished.emit({"error":type(exc).__name__})


class _CompareWorker(QObject):
    progress=Signal(object)
    finished=Signal(object)

    def __init__(self, *, source: np.ndarray, assets, root: Path, models: list[str],
                 reference: tuple[str,...] | None):
        super().__init__()
        self.source=source.copy()
        self.assets=assets
        self.root=root
        self.models=models[:2]
        self.reference=reference

    @Slot()
    def run(self):
        try:
            token=ChatGPTSignIn(ProtectedSessionStore(self.root)).access_token()
        except Exception as exc:
            self.finished.emit({"status":"ERROR","reason":type(exc).__name__})
            return
        started=perf_counter()
        # Model is held fixed when testing resolution; every combination
        # is called independently, never blending or reusing a model output.
        for model in self.models:
            for percent in (100,50):
                image=resize_same_image(self.source,percent)
                result=run_recognition_comparison(
                    image,access_token=token,model=model,assets=self.assets,
                    correct=self.reference,timeout_seconds=40.0)
                result["comparison_percent"]=percent
                self.progress.emit(result)
        self.finished.emit({"status":"DONE",
                            "elapsed_ms":round((perf_counter()-started)*1000)})


class OpponentImageComparisonDialog(QDialog):
    """Manual test harness, never active during a match and never sends a choice."""

    def __init__(self, *, image: np.ndarray, assets, root: str | Path,
                 current_model: str, logger=None,parent=None):
        super().__init__(parent)
        self.setWindowTitle("同一画像・解像度×AIモデル比較（試合外）")
        self.resize(1020,620)
        self.image=image.copy()
        self.assets=assets
        self.root=Path(root)
        self.logger=logger
        self.thread=None
        self.worker=None
        self.available_models={current_model}
        outer=QVBoxLayout(self)
        description=QLabel(
            "同じ元画像を100%と50%に縮小し、選んだモデルごとに相手6匹の認識だけを比較します。"
            "最大4件のChatGPT通信を順次実行します（各回最大40秒の読取期限）。"
            "戦術モデルの選出評価は実行しません。正解6匹の入力がない場合は正解率を表示しません。"
        )
        description.setWordWrap(True)
        outer.addWidget(description)
        form=QFormLayout()
        self.model_a=QComboBox()
        self.model_a.addItem(current_model,current_model)
        self.model_b=QComboBox()
        self.model_b.addItem("比較モデルなし（解像度だけを比較）",None)
        self.refresh_button=QPushButton("ChatGPT利用可能モデルを取得")
        self.refresh_button.clicked.connect(self.refresh_models)
        form.addRow("モデルA",self.model_a)
        form.addRow("モデルB（任意）",self.model_b)
        form.addRow("",self.refresh_button)
        self.reference=QLineEdit()
        self.reference.setPlaceholderText(
            "任意：正解6匹を上から順に / 区切り（例：マスカーニャ / サーナイト / ...）")
        form.addRow("相手の正解6匹",self.reference)
        outer.addLayout(form)
        actions=QHBoxLayout()
        self.run_button=QPushButton("同一画像の解像度×モデル比較を実行")
        self.run_button.clicked.connect(self.run_comparison)
        actions.addWidget(self.run_button)
        self.status=QLabel("未実行")
        actions.addWidget(self.status)
        outer.addLayout(actions)
        self.table=QTableWidget(0,7)
        self.table.setHorizontalHeaderLabels([
            "モデル","元画像解像度","送信切り出し","JPEG / Base64サイズ",
            "認識6匹（未検証）","通信時間","判定 / 正解位置数"
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setWordWrap(True)
        outer.addWidget(self.table)
        self.log_path=QLabel(
            "自動ログ: "+str(logger.path) if logger else "自動ログ: 保存不可")
        self.log_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.log_path)

    def _set_busy(self,busy: bool):
        self.run_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.model_a.setEnabled(not busy)
        self.model_b.setEnabled(not busy)
        self.reference.setEnabled(not busy)

    def _start_worker(self,worker):
        self._set_busy(True)
        self.thread=QThread(self)
        self.worker=worker
        worker.moveToThread(self.thread)
        self.thread.started.connect(worker.run)
        worker.finished.connect(self._finished)
        worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._cleanup)
        self.thread.start()

    def refresh_models(self):
        if self.thread is not None:return
        self.status.setText("利用可能なモデルを確認中")
        self._start_worker(_ModelListWorker(self.root))

    @Slot(object)
    def _finished(self,result):
        if "models" in result:
            models=[v for v in result["models"]
                    if isinstance(v,dict) and isinstance(v.get("slug"),str)
                    and v["slug"]]
            if not models:
                self.status.setText("利用可能なモデルを取得できません")
                return
            self.available_models={v["slug"] for v in models}
            current=self.model_a.currentData()
            self.model_a.clear()
            self.model_b.clear()
            self.model_b.addItem("比較モデルなし（解像度だけを比較）",None)
            for v in models:
                name=v.get("display_name") or v["slug"]
                self.model_a.addItem(name,v["slug"])
                self.model_b.addItem(name,v["slug"])
            ix=self.model_a.findData(current)
            if ix>=0:self.model_a.setCurrentIndex(ix)
            self.status.setText(f"利用可能モデル {len(models)}件")
        elif result.get("status")=="DONE":
            self.status.setText(
                f"比較完了 / 合計 {result.get('elapsed_ms',0)} ms")
            if self.logger:self.logger.write(
                "COMPARISON_DONE",elapsed_ms=result.get("elapsed_ms",0))
        else:
            self.status.setText("比較に失敗: "+str(result.get("reason","UNKNOWN")))
            if self.logger:self.logger.write(
                "COMPARISON_ERROR",reason=result.get("reason","UNKNOWN"))

    def run_comparison(self):
        if self.thread is not None:return
        models=[self.model_a.currentData()]
        other=self.model_b.currentData()
        if other and other not in models:models.append(other)
        if (not models[0] or any(model not in self.available_models
                                 for model in models)):
            QMessageBox.warning(self,"モデル未確認",
                                "接続中のChatGPTで利用可能なモデルだけを選んでください")
            return
        reference=None
        if self.reference.text().strip():
            try:
                reference=truth_slots(self.reference.text())
            except ValueError as exc:
                QMessageBox.warning(self,"正解6匹の入力",str(exc))
                return
        self.table.setRowCount(0)
        self.status.setText(f"比較中（{len(models)*2}条件）")
        if self.logger:
            self.logger.write("COMPARISON_STARTED",
                              image_count=len(models)*2,
                              visual_source_width=self.image.shape[1],
                              visual_source_height=self.image.shape[0])
        worker=_CompareWorker(
            source=self.image,assets=self.assets,root=self.root,
            models=models,reference=reference)
        worker.progress.connect(self._progress)
        self._start_worker(worker)

    @Slot(object)
    def _progress(self,result):
        row=self.table.rowCount()
        self.table.insertRow(row)
        correct=result.get("correct_slots",-1)
        accuracy=(f"{correct}/6" if type(correct) is int and correct>=0
                  else "正解未入力")
        fields=[
            result.get("model",""),
            f"{result.get('visual_source_width',0)}×{result.get('visual_source_height',0)} "
            f"({result.get('comparison_percent',100)}%)",
            f"{result.get('visual_crop_width',0)}×{result.get('visual_crop_height',0)}",
            f"{result.get('visual_total_jpeg_bytes',0):,} / "
            f"{result.get('visual_total_data_uri_bytes',0):,} B",
            " / ".join(result.get("opponent_six") or []) or "取得不可",
            str(result.get("comparison_elapsed_ms",0))+" ms",
            str(result.get("status","UNKNOWN"))+" / "+accuracy,
        ]
        for col,value in enumerate(fields):
            self.table.setItem(row,col,QTableWidgetItem(str(value)))
        self.table.resizeRowsToContents()
        self.status.setText(f"{row+1}条件の結果を取得")
        if self.logger:
            self.logger.report(result,event="COMPARISON_RESULT")

    @Slot()
    def _cleanup(self):
        self.thread=None
        self.worker=None
        self._set_busy(False)

    def closeEvent(self,event):
        if self.thread is not None:
            QMessageBox.information(self,"実行中","通信終了後に閉じてください")
            event.ignore()
            return
        super().closeEvent(event)
