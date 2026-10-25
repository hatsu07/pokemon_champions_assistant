"""Offline privacy, bounded disk use and reproducible test-selection GUI checks."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from champions_assist.desktop import ensure_application
from champions_assist.match_debug_log import MatchDebugLog,KEEP_FILES
from champions_assist.selection_test_dialog import (
    SelectionTestDialog, _SelectionTestWorker, load_test_image)
from tests.test_match_replay import fixture_assets


class AutoDebugLogTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)

    def test_automatic_jsonl_filters_secrets_and_long_messages(self):
        logger=MatchDebugLog(self.root,match_id="capture-"+"a"*32,clock=lambda:5.0)
        logger.write("AI_RESULT",status="ERROR",reason="ReadTimeout",elapsed_ms=18002,
                     token="sk-secret",api_key="secret",
                     prompt="untrusted prompt",image_bytes="pixel raw",image_count=2,
                     warning="malformed secret=somevalue",status_detail="body private",
                     model="gpt-5.6-terra",visual_image_count=1,
                     visual_first_attempt_elapsed_ms=12341,
                     visual_transport_retry_elapsed_ms=7780)
        logger.report({"status":"PROVISIONAL_ADVICE","screen":"TEAM_SELECTION",
                       "opponent_six":["ガブリアス","ピカチュウ"],
                       "observation":{"opponent_six":["ピカチュウ","ガブリアス"],
                                      "messages":["private user message"]},
                       "request_stage":"SELECTION_TACTICS"},
                      event="AI_REPLAY_RESULT")
        lines=[json.loads(x) for x in logger.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(lines),3)
        self.assertEqual(lines[1]["reason"],"ReadTimeout")
        self.assertEqual(lines[1]["elapsed_ms"],18002)
        self.assertEqual(lines[1]["visual_image_count"],1)
        self.assertEqual(lines[1]["visual_first_attempt_elapsed_ms"],12341)
        self.assertEqual(lines[1]["visual_transport_retry_elapsed_ms"],7780)
        self.assertEqual(lines[2]["opponent_six_candidate"],["ピカチュウ","ガブリアス"])
        self.assertEqual(lines[2]["opponent_six_validated"],["ガブリアス","ピカチュウ"])
        raw=logger.path.read_text(encoding="utf-8")
        for secret in ("sk-secret","private user message","pixel raw","untrusted prompt",
                       "malformed secret","body private"):
            self.assertNotIn(secret,raw)
        self.assertNotIn("our_six",raw)

    def test_rotates_old_sessions_without_deleting_unrelated_files(self):
        root=self.root/"debug-logs"
        root.mkdir()
        unrelated=root/"keep_this.txt"
        unrelated.write_text("keep",encoding="utf-8")
        for n in range(KEEP_FILES+3):
            MatchDebugLog(self.root,match_id="capture-"+f"{n:032x}")
        items=list(root.glob("capture-*.jsonl"))
        self.assertLessEqual(len(items),KEEP_FILES)
        self.assertTrue(unrelated.exists())

    def test_log_failure_does_not_raise_and_invalid_session_is_not_path(self):
        root=self.root/"debug-logs"
        root.write_text("block directory",encoding="utf-8")
        with self.assertRaises(OSError):
            MatchDebugLog(self.root)
        root.unlink()
        logger=MatchDebugLog(self.root,match_id="../../secrets")
        self.assertEqual(logger.path.parent.name,"debug-logs")
        self.assertTrue(logger.path.name.startswith("capture-"))


class OfflineSelectionTestWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=ensure_application()

    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.assets=fixture_assets()
        self.dialog=SelectionTestDialog(
            assets=self.assets,model="gpt-5.6-terra",reasoning_effort="high",
            app_root=self.root)
        self.addCleanup(self.dialog.close)

    def test_can_load_synthetic_capture_without_live_capture_or_api(self):
        path=self.root/"試験用画像.png"
        frame=np.zeros((624,1048,3),dtype=np.uint8)
        encoded=cv2.imencode(".png",frame)[1].tobytes()
        path.write_bytes(encoded)
        loaded=load_test_image(path)
        self.assertEqual(loaded.shape,(624,1048,3))
        self.assertFalse(self.dialog.run_button.isEnabled())
        self.dialog.set_image(loaded)
        self.assertTrue(self.dialog.run_button.isEnabled())
        self.assertIsNotNone(self.dialog.image_label.pixmap())
        self.assertEqual(self.dialog.status.text().startswith("画像読込済み"),True)
        self.assertTrue(self.dialog.log.path.is_file())

    def test_worker_invokes_exact_live_selection_function_once(self):
        frame=np.zeros((624,1048,3),dtype=np.uint8)
        worker=_SelectionTestWorker(
            image=frame,assets=self.assets,app_root=self.root,
            model="mock",reasoning_effort="high")
        result=[]
        worker.finished.connect(result.append)
        with patch("champions_assist.selection_test_dialog.ChatGPTSignIn") as auth, \
             patch("champions_assist.selection_test_dialog.run_replay_image") as live:
            auth.return_value.access_token.return_value="fake"
            live.return_value=({},{
                "status":"PROVISIONAL_ADVICE","opponent_six":["ガブリアス"],
                "selected_three":["ピカチュウ","ドドゲザン","ガブリアス"],
                "lead":"ピカチュウ","opponent_recognition_attempts":2})
            worker.run()
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]["status"],"PROVISIONAL_ADVICE")
        self.assertIn("test_total_elapsed_ms",result[0])
        self.assertEqual(live.call_count,1)
        kwargs=live.call_args.kwargs
        self.assertEqual(kwargs["model"],"mock")
        self.assertEqual(kwargs["reasoning_effort"],"high")
        self.assertEqual(kwargs["state"]["phase"],"MATCHMAKING")
        self.assertLessEqual(kwargs["total_seconds"],55)

    def test_result_ui_includes_timing_roster_and_persistent_debug(self):
        self.dialog.set_image(np.zeros((624,1048,3),dtype=np.uint8))
        self.dialog._complete({
            "status":"PROVISIONAL_ADVICE",
            "observation":{"opponent_six":["ガブリアス","アシレーヌ"]},
            "selected_three":["ピカチュウ","ドドゲザン","ガブリアス"],
            "lead":"ピカチュウ",
            "test_total_elapsed_ms":12000,
            "opponent_recognition_attempts":2})
        self.assertIn("12000 ms",self.dialog.details.text())
        self.assertIn("ガブリアス",self.dialog.details.text())
        self.assertIn("ドドゲザン",self.dialog.details.text())
        self.assertIn("認識回数: 2",self.dialog.details.text())
        lines=[json.loads(x) for x in self.dialog.log.path.read_text(
            encoding="utf-8").splitlines()]
        self.assertTrue(any(x["event"]=="SELECTION_TEST_RESULT" for x in lines))
        self.assertTrue(any(x["event"]=="SELECTION_TEST_DONE" for x in lines))
        self.assertFalse(any("image" in x and "bytes" in x for x in lines))


if __name__=="__main__":
    unittest.main()
