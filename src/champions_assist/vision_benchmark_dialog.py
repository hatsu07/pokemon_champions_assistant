"""Explicit ChatGPT-account vision benchmark, never automated game input."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Callable
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QComboBox,
    QPushButton,QFileDialog,QPlainTextEdit,QMessageBox,QWidget)
from .vision_api_benchmark import OpenAIVisionBenchmark,read_image,SUPPORTED_UI
from .siwc_auth import ChatGPTSignIn,ProtectedSessionStore,SignInError
from .app_config import default_app_root
from .vision_preferences import VisionPreferenceStore
from .reasoning_config import EFFORT_OPTIONS


class _Worker(QObject):
    finished = Signal(object)
    def __init__(self, job: str, store_root: str, path: str = "", model: str = "", expected=None, master_directory: str = "", team_path: str = "", reasoning_effort: str = "auto"):
        super().__init__()
        self.job,self.store_root,self.path,self.model,self.expected=job,store_root,path,model,expected
        self.master_directory,self.team_path=master_directory,team_path
        self.reasoning_effort=reasoning_effort
    @Slot()
    def run(self):
        try:
            auth=ChatGPTSignIn(ProtectedSessionStore(self.store_root))
            if self.job=="login":
                result={"operation":"login","status":"OK","account":auth.sign_in()}
            elif self.job=="models":
                result={"operation":"models","status":"OK","models":auth.models()}
            elif self.job=="signout":
                auth.sign_out()
                result={"operation":"signout","status":"OK"}
            elif self.job=="selection":
                from .selection_vision import run_selection_image
                from .preload import MasterPreloader
                if not self.master_directory or not self.team_path:
                    result={"operation":"selection","status":"BLOCKED","reason":"REGISTERED_TEAM_REQUIRED"}
                else:
                    assets=MasterPreloader(self.master_directory,team_source=self.team_path).load_for_new_match()
                    token=auth.access_token()
                    decision=run_selection_image(read_image(self.path),access_token=token,model=self.model,assets=assets,
                                                 reasoning_effort=self.reasoning_effort)
                    result={"operation":"selection",**decision.as_dict(),"registered_team":list(assets.own_team_names)}
            else:
                token=auth.access_token()
                image=read_image(self.path)
                report=OpenAIVisionBenchmark(model=self.model,reasoning_effort=self.reasoning_effort).run(
                    image,access_token=token).report(self.expected)
                result={"operation":"bench",**report}
        except Exception as exc:
            result={"operation":self.job,"status":"ERROR","error_type":type(exc).__name__,
                    "reason":str(exc) if isinstance(exc,SignInError) else "REQUEST_FAILED"}
        self.finished.emit(result)


class VisionBenchmarkPanel(QWidget):
    """Reusable inline ChatGPT settings/benchmark interface (no extra dialog)."""

    busy_changed = Signal(bool)

    def __init__(self,parent: QWidget|None=None,store_root: str|Path|None=None,master_directory: str|Path|None=None,team_path: str|Path|None=None,capture_config=None,context_provider: Callable|None=None):
        super().__init__(parent)
        self._context_provider=context_provider
        self.store_root=Path(store_root) if store_root else default_app_root()
        self.preferences=VisionPreferenceStore(self.store_root)
        self._pending_model_refresh=False
        self.master_directory=str(master_directory or "")
        self.team_path=str(team_path or "")
        self.capture_config=capture_config
        self._thread=None
        self._worker=None
        self._report=None
        form=QVBoxLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.addWidget(QLabel("ChatGPT Plus等の利用可能なモデルで画像認識を実測します。キー入力・従量APIキー不要。\n"
                              "ログインと画像送信は各ボタンの明示操作時のみ。プラン利用可否はログイン後に確認します。"))
        self.connection=QLabel("未接続")
        form.addWidget(self.connection)
        account_row=QHBoxLayout()
        self.login_button=QPushButton("ChatGPTでログイン")
        self.login_button.clicked.connect(lambda:self._start_job("login"))
        self.refresh_button=QPushButton("利用可能モデルを取得")
        self.refresh_button.clicked.connect(lambda:self._start_job("models"))
        self.logout_button=QPushButton("ログアウト")
        self.logout_button.clicked.connect(self._signout)
        for b in (self.login_button,self.refresh_button,self.logout_button):
            account_row.addWidget(b)
        form.addLayout(account_row)
        row=QHBoxLayout()
        self.path=QLineEdit();self.path.setReadOnly(True)
        pick=QPushButton("画像を選択");pick.clicked.connect(self._choose_image)
        row.addWidget(self.path,1);row.addWidget(pick);form.addLayout(row)
        row=QHBoxLayout()
        self.expected=QComboBox();self.expected.addItem("正解ラベルなし",None)
        for ui in SUPPORTED_UI:self.expected.addItem(ui,ui)
        self.model=QComboBox()
        self.model.addItem("ログイン後、モデル一覧を取得",None)
        self.model.currentIndexChanged.connect(self._remember_model)
        row.addWidget(QLabel("正解UI:"));row.addWidget(self.expected)
        row.addWidget(QLabel("モデル:"));row.addWidget(self.model,1);form.addLayout(row)
        effort_row=QHBoxLayout()
        self.reasoning_effort=QComboBox()
        for title,value in EFFORT_OPTIONS:
            self.reasoning_effort.addItem(title,value)
        chosen=self.reasoning_effort.findData(self.preferences.reasoning_effort())
        self.reasoning_effort.setCurrentIndex(max(0,chosen))
        self.reasoning_effort.currentIndexChanged.connect(self._remember_reasoning_effort)
        effort_row.addWidget(QLabel("AIの思考の深さ:"))
        effort_row.addWidget(self.reasoning_effort,1)
        form.addLayout(effort_row)
        hint=QLabel("軽量は応答速度を優先、高精度は思考時間が増えます。対応する強度はモデルにより異なります。\\n"
                    "『自動』はモデルの既定値。非対応ならエラーを表示し、勝手に切り替えません。")
        hint.setWordWrap(True);form.addWidget(hint)
        row=QHBoxLayout()
        self.send=QPushButton("この画像1枚を送信して実測")
        self.send.clicked.connect(self._start_benchmark)
        self.selection_button=QPushButton("選出画面から3匹＋初手を提案（35秒上限）")
        self.selection_button.clicked.connect(self._start_selection)
        self.save=QPushButton("診断JSONを保存");self.save.setEnabled(False)
        self.save.clicked.connect(self._save_report)
        row.addWidget(self.send);row.addWidget(self.save);form.addLayout(row)
        form.addWidget(self.selection_button)
        operational_hint=QLabel("対戦全体の自動キャプチャ支援はメイン画面の「自動キャプチャ対戦支援」から開始します。")
        operational_hint.setWordWrap(True)
        form.addWidget(operational_hint)
        self.output=QPlainTextEdit();self.output.setReadOnly(True);form.addWidget(self.output,1)
        if self._refresh_status():
            # Refresh the model list asynchronously after reopening the dialog.
            QTimer.singleShot(0, lambda: self._start_job("models"))
    @property
    def busy(self) -> bool:
        return self._thread is not None

    def _refresh_match_context(self) -> None:
        if self._context_provider is None:
            return
        master_directory, team_path, capture_config = self._context_provider()
        self.master_directory=str(master_directory or "")
        self.team_path=str(team_path or "")
        self.capture_config=capture_config

    def _auth(self):
        return ChatGPTSignIn(ProtectedSessionStore(self.store_root))
    def _refresh_status(self) -> bool:
        try:
            status=self._auth().status()
            connected=bool(status["connected"] and status["plan_enabled"])
            self.connection.setText("保存済みChatGPTセッションあり / 利用権限: "+("確認済み" if status["plan_enabled"] else "未確認")
                                    if status["connected"] else "ChatGPT未接続")
            return connected
        except Exception:
            self.connection.setText("接続状態を確認できません")
            return False

    def _remember_model(self, _index: int) -> None:
        model = self.model.currentData()
        if isinstance(model, str) and model:
            try:
                self.preferences.save_model(model)
            except (OSError, ValueError):
                self.connection.setText("モデル設定を保存できません")

    def _remember_reasoning_effort(self, _index: int) -> None:
        effort=self.reasoning_effort.currentData()
        try:
            self.preferences.save_reasoning_effort(str(effort))
        except (OSError,ValueError):
            self.connection.setText("思考強度の設定を保存できません")

    def _restore_model_options(self, models) -> None:
        # Refresh models for the current account, then restore the saved slug.
        preferred = self.preferences.selected_model()
        was_blocked = self.model.blockSignals(True)
        try:
            self.model.clear()
            for item in models:
                slug = item.get("slug") if isinstance(item, dict) else None
                if isinstance(slug, str) and slug:
                    self.model.addItem(str(item.get("display_name") or slug), slug)
            if self.model.count()==0:
                self.model.addItem("利用可能なモデルがありません", None)
            elif preferred is not None:
                index = self.model.findData(preferred)
                if index >= 0:
                    self.model.setCurrentIndex(index)
                else:
                    self.model.insertItem(0, "以前のモデルは利用不可：再選択してください", None)
                    self.model.setCurrentIndex(0)
            else:
                self.model.setCurrentIndex(0)
        finally:
            self.model.blockSignals(was_blocked)
        if preferred is None:
            self._remember_model(self.model.currentIndex())
    def _choose_image(self):
        path,_=QFileDialog.getOpenFileName(self,"ゲーム領域の保存画像","","Images (*.png *.jpg *.jpeg)")
        if path:self.path.setText(path)
    def _signout(self):
        if QMessageBox.question(self,"ログアウト確認","このアプリのChatGPTセッションを終了しますか？",
                                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes:
            self._start_job("signout")
    def _start_benchmark(self):
        if not self.path.text() or not Path(self.path.text()).is_file() or not self.model.currentData():
            QMessageBox.warning(self,"送信不可","保存画像とログイン後のモデルを選択してください。")
            return
        if QMessageBox.question(self,"画像送信の確認",
              "画像1枚を、接続したChatGPTアカウントの利用枠で送信します。実行しますか？",
              QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
              QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        self._start_job("bench")
    def _start_selection(self):
        self._refresh_match_context()
        if not self.path.text() or not Path(self.path.text()).is_file() or not self.model.currentData():
            QMessageBox.warning(self,"選出不可","画像とモデルを指定してください。")
            return
        if not self.master_directory or not self.team_path:
            QMessageBox.warning(self,"選出不可","設定画面で登録構築を「次戦に選択」してください。")
            return
        if QMessageBox.question(self,"選出支援の画像送信","選出画面1枚をChatGPTに送信し、画像認識と選出提案を行いますか？",
               QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
               QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        self._start_job("selection")
    def _start_job(self,operation):
        if self._thread is not None:return
        self.output.setPlainText("実行中: "+operation)
        self._set_busy(True)
        self._thread=QThread(self)
        self._worker=_Worker(operation,str(self.store_root),self.path.text(),
                             str(self.model.currentData() or ""),self.expected.currentData(),
                             self.master_directory,self.team_path,
                             str(self.reasoning_effort.currentData()))
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._complete)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()
    def _set_busy(self,busy):
        for widget in (self.login_button,self.refresh_button,self.logout_button,self.send,self.selection_button):
            widget.setEnabled(not busy)
        self.busy_changed.emit(busy)
    @Slot(object)
    def _complete(self,result):
        if result.get("operation") == "selection":
            from .selection_vision import selection_summary_ja
            self.output.setPlainText(
                selection_summary_ja(result) + "\n\n" +
                json.dumps(result, ensure_ascii=False, indent=2))
        else:
            self.output.setPlainText(json.dumps(result,ensure_ascii=False,indent=2))
        self._refresh_status()
        if result.get("operation")=="models" and result.get("status")=="OK":
            self._restore_model_options(result["models"])
            self.connection.setText("ChatGPTログイン有効 / モデル一覧更新済み")
        if result.get("operation")=="models" and result.get("status")!="OK":
            was_blocked=self.model.blockSignals(True)
            self.model.clear()
            self.model.addItem("ログインし直すかモデル一覧を再取得",None)
            self.model.blockSignals(was_blocked)
            self.connection.setText("モデル一覧取得に失敗：ログイン状態を確認してください")
        if result.get("operation")=="login" and result.get("status")=="OK":
            self._pending_model_refresh=True
        if result.get("operation")=="signout" and result.get("status")=="OK":
            was_blocked=self.model.blockSignals(True)
            self.model.clear()
            self.model.addItem("再ログインが必要",None)
            self.model.blockSignals(was_blocked)
        if result.get("operation") in ("bench","selection"):
            self._report=result
            self.save.setEnabled(True)
    @Slot()
    def _cleanup(self):
        self._thread=None;self._worker=None;self._set_busy(False)
        if self._pending_model_refresh:
            self._pending_model_refresh=False
            QTimer.singleShot(0, lambda: self._start_job("models"))
    def _save_report(self):
        if self._report is None:return
        path,_=QFileDialog.getSaveFileName(self,"診断JSONを保存","","JSON (*.json)")
        if not path:return
        try:
            with open(path,"x",encoding="utf-8") as out:
                json.dump(self._report,out,ensure_ascii=False,indent=2)
        except FileExistsError:
            QMessageBox.warning(self,"上書き不可","既存ファイルは上書きしません。")
        except OSError:
            QMessageBox.warning(self,"保存失敗","書き込みできません。")

class VisionBenchmarkDialog(QDialog):
    """Compatibility wrapper for callers explicitly wanting a standalone dialog.

    Settings now embeds VisionBenchmarkPanel directly and never opens this.
    """
    def __init__(self,parent: QWidget|None=None,store_root: str|Path|None=None,master_directory: str|Path|None=None,team_path: str|Path|None=None,capture_config=None):
        super().__init__(parent)
        self.setWindowTitle("ChatGPTでログイン・画像認識実測")
        self.setMinimumSize(760,540)
        form=QVBoxLayout(self)
        self.panel=VisionBenchmarkPanel(
            parent=self,store_root=store_root,master_directory=master_directory,
            team_path=team_path,capture_config=capture_config)
        form.addWidget(self.panel)

    def reject(self) -> None:
        if self.panel.busy:
            QMessageBox.information(self,"ChatGPT処理中","処理が終わってから閉じてください。")
            return
        super().reject()

    def closeEvent(self,event) -> None:
        if self.panel.busy:
            event.ignore()
            return
        super().closeEvent(event)
