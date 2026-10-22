"""Standalone saved-screenshot match-replay GUI, deliberately read-only.

The only outgoing image is the file selected by the player at each click.
The frozen four match masters are loaded once at "new replay", never midbattle.
"""
from __future__ import annotations

import json
from pathlib import Path
from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from .app_config import default_app_root
from .preload import MasterPreloader
from .siwc_auth import ChatGPTSignIn, ProtectedSessionStore, SignInError
from .match_replay import new_replay, confirm_actual_selection, run_replay_image
from .vision_api_benchmark import read_image


class _ReplayWorker(QObject):
    finished = Signal(object)

    def __init__(self, *, app_root, selected_model, image_path, assets, state):
        super().__init__()
        self.app_root=app_root
        self.selected_model=selected_model
        self.image_path=image_path
        self.assets=assets
        self.state=state

    @Slot()
    def run(self):
        try:
            auth=ChatGPTSignIn(ProtectedSessionStore(self.app_root))
            next_state, report=run_replay_image(
                read_image(self.image_path), state=self.state, assets=self.assets,
                access_token=auth.access_token(),model=self.selected_model,
            )
            self.finished.emit({"state":next_state,"report":report})
        except Exception as exc:
            self.finished.emit({"state":self.state,"report":{
                "status":"ERROR","reason":str(exc) if isinstance(exc,SignInError)
                else type(exc).__name__}})


class MatchReplayDialog(QDialog):
    def __init__(self, *, model: str, master_directory: str, team_path: str,
                 store_root: str | Path | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("対戦全体の画像・AIやり取りテスト（ゲーム操作なし）")
        self.setMinimumSize(790,620)
        self.model=model
        self.master_directory=master_directory
        self.team_path=team_path
        self.store_root=Path(store_root) if store_root else default_app_root()
        self.assets=None
        self.state=None
        self.entries=[]
        self._thread=None
        self._worker=None
        panel=QVBoxLayout(self)
        intro=QLabel(
            "マッチング→選出→技選択→交代→行動結果→勝敗まで、画像を1枚ずつ送信します。\n"
            "マッチング中・戦闘中は登録マスターを再読込しません。AIの提案はゲーム操作に反映されません。"
        )
        intro.setWordWrap(True)
        panel.addWidget(intro)
        self.status=QLabel("未開始 / 保存画像だけで検証")
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
        self.path=QLineEdit()
        self.path.setReadOnly(True)
        select=QPushButton("次の画像を選択")
        select.clicked.connect(self._pick)
        self.next_button=QPushButton("この画像を送信してAI判断")
        self.next_button.clicked.connect(self._next)
        row.addWidget(self.path,1)
        row.addWidget(select)
        row.addWidget(self.next_button)
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
        try:
            if not self.master_directory or not self.team_path:
                raise ValueError("次戦用の構築を先に選択してください。")
            self.assets=MasterPreloader(self.master_directory,
                                        team_source=self.team_path).load_for_new_match()
            self.state=new_replay(self.assets)
            self.entries=[]
            self.actual_three.clear()
            self.actual_lead.clear()
            self._render({"status":"MATCH_STARTED","message":"4ファイルの事前読込完了。画像を追加してください。"})
        except Exception as exc:
            self.assets=None
            self.state=None
            QMessageBox.warning(self,"テスト開始不可",str(exc))

    def _reset(self):
        if self._thread is not None:return
        self.assets=None
        self.state=None
        self.entries=[]
        self.actual_three.clear()
        self.actual_lead.clear()
        self.path.clear()
        self._render({"status":"RESET"})

    def _pick(self):
        path,_=QFileDialog.getOpenFileName(self,"次の対戦画面","","Images (*.png *.jpg *.jpeg)")
        if path:self.path.setText(path)

    def _next(self):
        if self._thread is not None:return
        if self.assets is None or self.state is None:
            QMessageBox.warning(self,"未開始","「新しいテスト対戦を開始」を押してください。")
            return
        if self.state["phase"]=="ENDED":
            QMessageBox.warning(self,"対戦終了済み","新しいテスト対戦を開始してください。")
            return
        if not self.path.text() or not Path(self.path.text()).is_file():
            QMessageBox.warning(self,"画像未選択","次の画像を選択してください。")
            return
        if not self.model:
            QMessageBox.warning(self,"モデル未選択","ChatGPTのモデルを選択してください。")
            return
        if QMessageBox.question(
            self,"送信確認","選んだ保存画像1枚をChatGPTへ送信して、認識と判断を実行しますか？",
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )!=QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        self._thread=QThread(self)
        self._worker=_ReplayWorker(
            app_root=self.store_root, selected_model=self.model,
            image_path=self.path.text(),assets=self.assets,state=self.state,
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
        if report["status"] not in ("ERROR","DEADLINE_EXCEEDED"):
            self.state=payload["state"]
        entry={"step":len(self.entries)+1,**report}
        self.entries.append(entry)
        if report.get("screen")=="TEAM_SELECTION" and report.get("selected_three"):
            self.actual_three.setText(" / ".join(report["selected_three"]))
            self.actual_lead.setText(report.get("lead") or "")
        self._render(entry)

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

    def _set_busy(self,busy):
        for widget in (self.start_button,self.next_button,self.reset_button,self.confirm_button):
            widget.setEnabled(not busy)

    @Slot()
    def _cleanup(self):
        self._thread=None
        self._worker=None
        self._set_busy(False)

    def _render(self, report):
        state=self.state or {}
        phase=state.get("phase","NOT_STARTED")
        compatible=state.get("matrix_compatible",False)
        suffix="構築別LIVEマトリクス有効" if compatible else "LIVEマトリクス不一致・機能試験のみ"
        self.status.setText(f"段階：{phase} / {suffix} / 記録 {len(self.entries)} 件")
        rec=report.get("recommendation","")
        line=(rec+"\n\n") if rec else ""
        self.output.setPlainText(
            line+json.dumps({"latest":report,"replay_state":state,
                             "timeline":self.entries},ensure_ascii=False,indent=2))
        self.output.verticalScrollBar().setValue(0)

    def _export(self):
        if self.state is None:return
        dest,_=QFileDialog.getSaveFileName(self,"対戦テスト結果を保存","","JSON (*.json)")
        if not dest:return
        report={"scope":"OFFLINE_SAVED_IMAGE_MATCH_REPLAY_ONLY","model":self.model,
                "matrix_compatible":self.state["matrix_compatible"],
                "state":self.state,"timeline":self.entries}
        try:
            with open(dest,"x",encoding="utf-8") as output:
                json.dump(report,output,ensure_ascii=False,indent=2)
        except FileExistsError:
            QMessageBox.warning(self,"既存ファイル","上書きしません。別の名前を選んでください。")
        except OSError:
            QMessageBox.warning(self,"保存不可","指定の場所に書き込めません。")
