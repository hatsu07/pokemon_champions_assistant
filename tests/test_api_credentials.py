"""API credential GUI and vault tests; no real Windows secrets accessed."""
from __future__ import annotations

import os
import unittest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from champions_assist.api_credentials import ApiKeyVault, CredentialStoreError, get_api_key
from champions_assist.settings import ModelSettings


class InMemoryCredential:
    def __init__(self):
        self.secret=None
    def read(self):
        return self.secret
    def write(self,key):
        self.secret=key
    def delete(self):
        existed=self.secret is not None
        self.secret=None
        return existed


class CredentialTests(unittest.TestCase):
    def test_write_read_delete_and_environment_fallback(self):
        backing=InMemoryCredential()
        vault=ApiKeyVault(backing)
        vault.save("sk-example-for-test")
        self.assertTrue(vault.is_configured())
        self.assertEqual(get_api_key(vault=vault,environ={"CHAMPIONS_ASSIST_OPENAI_API_KEY":"sk-env"}), "sk-example-for-test")
        self.assertTrue(vault.delete())
        self.assertEqual(get_api_key(vault=vault,environ={"CHAMPIONS_ASSIST_OPENAI_API_KEY":"sk-env"}), "sk-env")

    def test_empty_secret_is_not_saved(self):
        backing=InMemoryCredential()
        vault=ApiKeyVault(backing)
        for invalid in ("", "   ", "a\nb", " sk-example"):
            with self.assertRaises(CredentialStoreError):
                vault.save(invalid)
        self.assertIsNone(backing.secret)

    def test_model_env_injection_still_deterministic(self):
        settings=ModelSettings.from_environment({"CHAMPIONS_ASSIST_OPENAI_API_KEY":"sk-test"})
        self.assertEqual(settings.api_key,"sk-test")

    def test_gui_never_echoes_existing_key(self):
        from champions_assist.desktop import ensure_application
        from champions_assist.api_key_dialog import ApiKeyDialog
        from PySide6.QtWidgets import QLineEdit
        _app=ensure_application()
        backend=InMemoryCredential()
        backend.secret="sk-secret-not-to-display"
        dialog=ApiKeyDialog(vault=ApiKeyVault(backend))
        try:
            self.assertEqual(dialog.entry.echoMode(),QLineEdit.EchoMode.Password)
            self.assertEqual(dialog.entry.text(),"")
            self.assertNotIn(backend.secret,dialog.status.text())
            dialog.entry.setText("sk-replaced")
            dialog.save_key()
            self.assertEqual(backend.secret,"sk-replaced")
            self.assertEqual(dialog.entry.text(),"")
        finally:
            dialog.close()
