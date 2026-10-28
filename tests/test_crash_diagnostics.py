"""Regression tests for local crash/error logging without exposing secrets."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from champions_assist import crash_diagnostics


class CrashDiagnosticsTests(unittest.TestCase):
    def test_sanitized_crash_records_never_contain_error_message_or_secret(self):
        with TemporaryDirectory() as directory:
            with patch.object(crash_diagnostics.faulthandler, "enable"):
                recorder = crash_diagnostics._Recorder(Path(directory))
            try:
                def exploding():
                    raise RuntimeError("SECRET_API_KEY confidential OCR words")
                try:
                    exploding()
                except RuntimeError as exc:
                    recorder.exception("UNIT_TEST_EXCEPTION", exc, stage="PYTHON")
                recorder.record("TEST_DEVICE", provider="DmlExecutionProvider",
                                device="directml", gpu_id=2,
                                component="secret raw image contents!", frame_seq=1)
                content=recorder.jsonl_path.read_text(encoding="utf-8")
                self.assertIn("UNIT_TEST_EXCEPTION", content)
                self.assertIn('"error_type":"RuntimeError"', content)
                self.assertIn('"provider":"DmlExecutionProvider"', content)
                self.assertIn('"component":"UNKNOWN"', content)
                self.assertNotIn("SECRET_API_KEY", content)
                self.assertNotIn("confidential", content)
                self.assertNotIn("raw image contents", content)
                entries=[json.loads(line) for line in content.splitlines()]
                record=next(row for row in entries if row["event"]=="UNIT_TEST_EXCEPTION")
                self.assertTrue(record["frames"])
                self.assertIn("file",record["frames"][-1])
                self.assertIn("line",record["frames"][-1])
            finally:
                os.close(recorder.fd)
                recorder.fatal_file.close()

    def test_failing_logger_does_not_affect_capture(self):
        original=crash_diagnostics._recorder
        try:
            class Faulty:
                def record(self,*args,**kwargs):
                    raise OSError("disk full secret")
                def exception(self,*args,**kwargs):
                    raise OSError("disk full secret")
            crash_diagnostics._recorder=Faulty()
            crash_diagnostics.record_event("ANY_EVENT")
            crash_diagnostics.record_exception("EX",ValueError("secret"))
        finally:
            crash_diagnostics._recorder=original


if __name__=="__main__":
    unittest.main()
