"""GUI to manage the single local Windows OpenAI credential."""
from __future__ import annotations
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QMessageBox,QWidget
from .api_credentials import ApiKeyVault, CredentialStoreError


class ApiKeyDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, *, vault: ApiKeyVault | None = None):
        super().__init__(parent)
        self.setWindowTitle("OpenAI APIキー設定")
        self.setMinimumWidth(490)
        self.vault = vault
        if self.vault is None:
            try:
                self.vault = ApiKeyVault()
            except CredentialStoreError:
                pass
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel("キーはWindows資格情報マネージャーに保存します。\n"
                                "設定JSON・診断ログには保存しません。API通信はこの操作だけでは開始しません。"))
        self.status=QLabel()
        layout.addWidget(self.status)
        self.entry=QLineEdit()
        self.entry.setPlaceholderText("新しいOpenAI APIキーを入力")
        self.entry.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.entry)
        row=QHBoxLayout()
        self.save_button=QPushButton("登録・更新")
        self.remove_button=QPushButton("登録キーを削除")
        self.save_button.clicked.connect(self.save_key)
        self.remove_button.clicked.connect(self.delete_key)
        row.addWidget(self.save_button)
        row.addWidget(self.remove_button)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        if self.vault is None:
            self.status.setText("Windows資格情報マネージャーを利用できません")
            self.save_button.setEnabled(False)
            self.remove_button.setEnabled(False)
            return
        try:
            present=self.vault.is_configured()
            self.status.setText("登録済み（キーは表示しません）" if present else "未登録")
            self.remove_button.setEnabled(present)
        except CredentialStoreError:
            self.status.setText("資格情報にアクセスできません")
            self.save_button.setEnabled(False)
            self.remove_button.setEnabled(False)

    def save_key(self):
        if self.vault is None:
            return
        key=self.entry.text()
        try:
            self.vault.save(key)
            self.entry.clear()
            self.refresh()
        except CredentialStoreError as exc:
            self.entry.clear()
            QMessageBox.warning(self,"登録できません",str(exc))

    def delete_key(self):
        if self.vault is None:
            return
        response=QMessageBox.question(self,"削除確認","保存済みのAPIキーを削除しますか？",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if response!=QMessageBox.StandardButton.Yes:
            return
        try:
            self.vault.delete()
            self.entry.clear()
            self.refresh()
        except CredentialStoreError as exc:
            QMessageBox.warning(self,"削除できません",str(exc))
