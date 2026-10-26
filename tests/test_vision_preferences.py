"""Offline GUI tests: login state, model choice, and persistence survive reopen."""
from __future__ import annotations
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent
from champions_assist.vision_preferences import VisionPreferenceStore
from champions_assist.vision_benchmark_dialog import VisionBenchmarkDialog
from champions_assist.desktop import ensure_application

MODELS = [
    {"slug":"gpt-6.1-sol","display_name":"GPT-6.1-Sol"},
    {"slug":"gpt-5.6-terra","display_name":"GPT-5.6-Terra"},
]

class FakeDisconnectedAuth:
    def status(self):
        return {"connected":False,"plan_enabled":False}

class VisionPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ensure_application()

    @classmethod
    def tearDownClass(cls):
        # QApplication is shared by GUI test modules.  Do not quit it here:
        # doing so deletes shared Qt state while PySide wrappers still exist.
        cls.app.closeAllWindows()
        cls.app.processEvents()

    def _dispose_dialog(self, dialog):
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_model_preference_survives_disk_reload_without_storing_secrets(self):
        with tempfile.TemporaryDirectory() as root:
            first = VisionPreferenceStore(root)
            self.assertIsNone(first.selected_model())
            first.save_model("gpt-5.6-terra")
            self.assertEqual(VisionPreferenceStore(root).selected_model(),"gpt-5.6-terra")
            contents = first.path.read_text(encoding="utf-8")
            self.assertNotIn("access_token",contents)
            self.assertNotIn("refresh_token",contents)
            with self.assertRaises(ValueError):
                first.save_model("unsafe\nmodel")

    def test_screen_gpu_preference_defaults_to_auto_and_persists_adapter(self):
        with tempfile.TemporaryDirectory() as root:
            prefs=VisionPreferenceStore(root)
            self.assertEqual(prefs.screen_device(),"auto")
            self.assertEqual(prefs.screen_gpu_id(),0)
            prefs.save_model("gpt-5.6-terra")
            prefs.save_screen_device("directml",2)
            restored=VisionPreferenceStore(root)
            self.assertEqual(restored.screen_device(),"directml")
            self.assertEqual(restored.screen_gpu_id(),2)
            self.assertEqual(restored.selected_model(),"gpt-5.6-terra")
            with self.assertRaises(ValueError):
                prefs.save_screen_device("not-a-provider",0)
            with self.assertRaises(ValueError):
                prefs.save_screen_device("cuda",99)
            with self.assertRaises(ValueError):
                prefs.save_screen_device("cuda",True)

    def test_model_combobox_selection_is_restored_on_new_dialog(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(VisionBenchmarkDialog, "_auth",return_value=FakeDisconnectedAuth()):
                first=VisionBenchmarkDialog(store_root=root)
                try:
                    first._restore_model_options(MODELS)
                    first.model.setCurrentIndex(first.model.findData("gpt-5.6-terra"))
                    self.assertEqual(first.preferences.selected_model(),"gpt-5.6-terra")
                finally:
                    self._dispose_dialog(first)
                later=VisionBenchmarkDialog(store_root=root)
                try:
                    later._restore_model_options(list(reversed(MODELS)))
                    self.assertEqual(later.model.currentData(),"gpt-5.6-terra")
                    # Never silently fall back to a different model if removed.
                    later._restore_model_options([MODELS[0]])
                    self.assertIsNone(later.model.currentData())
                    self.assertEqual(later.preferences.selected_model(),"gpt-5.6-terra")
                finally:
                    self._dispose_dialog(later)

if __name__=="__main__":
    unittest.main()
