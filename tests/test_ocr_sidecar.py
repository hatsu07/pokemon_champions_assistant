"""Hardware-free integration tests for the persistent OCR process protocol."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import struct
import sys
import threading
from time import monotonic, sleep
import unittest
from unittest.mock import patch

import numpy as np

from champions_assist.ocr import OcrExecutionError
from champions_assist import ocr_sidecar as sidecar


# Real Python subprocess; never loads ONNX, GPU, screenshot or external API.
_FAKE_CHILD = """
import os
import sys
from time import sleep
from champions_assist.ocr import OcrRead
from champions_assist.ocr_sidecar import serve

behavior=sys.argv[1]
class Fake:
    provider="DmlExecutionProvider"
    det_provider="DmlExecutionProvider"
    rec_provider="DmlExecutionProvider"
    def warmup(self):
        if behavior=="initcrash":
            os._exit(9)
    def read(self,image,*,roi_name):
        if behavior=="crash":
            os._exit(17)
        if behavior=="hang":
            sleep(30)
        return OcrRead("テスト:" + roi_name, 0.91, "offline-fake")
def factory(**kwargs):
    return Fake()
raise SystemExit(serve(device="directml",gpu_id=0,factory=factory))
"""


class OcrSidecarTests(unittest.TestCase):
    def make(self, behavior="ok"):
        return sidecar.OcrSidecarClient(
            device="directml", gpu_id=0, ready_timeout=8, read_timeout=3,
            command=(sys.executable, "-u", "-c", _FAKE_CHILD, behavior))

    def test_persistent_process_reads_multiple_rois_without_restarting(self):
        client=self.make()
        try:
            client.warmup()
            pid=client._proc.pid
            self.assertEqual(client.provider,"DmlExecutionProvider")
            sample=np.full((20,30,3),128,dtype=np.uint8)
            self.assertEqual(client.read(sample,roi_name="message").text,
                             "テスト:message")
            second=client.read(sample,roi_name="hp")
            self.assertEqual(second.text,"テスト:hp")
            self.assertAlmostEqual(second.confidence,0.91)
            self.assertEqual(client._proc.pid,pid)
        finally:
            client.stop()
        self.assertTrue(client._closed)

    def test_native_worker_exit_never_becomes_fabricated_ocr(self):
        client=self.make("crash")
        try:
            client.warmup()
            with self.assertRaises(OcrExecutionError):
                client.read(np.zeros((16,24,3),dtype=np.uint8),roi_name="message")
        finally:
            client.stop()

    def test_failed_warmup_is_reported_without_hanging(self):
        client=self.make("initcrash")
        try:
            with self.assertRaisesRegex(OcrExecutionError, "OCR_SIDECAR_EXITED"):
                client.warmup()
        finally:
            client.stop()

    def test_stop_does_not_wait_for_busy_native_child(self):
        client=self.make("hang")
        client.warmup()
        done=threading.Event()
        def work():
            try:
                client.read(np.zeros((16,24,3),dtype=np.uint8),roi_name="message")
            except OcrExecutionError:
                pass
            finally:
                done.set()
        worker=threading.Thread(target=work,daemon=True)
        worker.start()
        sleep(0.12)
        started=monotonic()
        client.stop()
        client.stop()  # idempotent
        self.assertLess(monotonic()-started,0.3)
        self.assertTrue(done.wait(4), "worker did not unblock after termination")

    def test_ipc_rejects_oversized_payload_without_consuming_bytes(self):
        data=BytesIO(struct.pack("!I",sidecar.MAX_MESSAGE+1))
        with self.assertRaises(sidecar.ProtocolFailure):
            sidecar._read(data)

    def test_ipc_rejects_bad_json_and_incomplete_frames(self):
        with self.assertRaises(sidecar.ProtocolFailure):
            sidecar._read(BytesIO(struct.pack("!I",4)+b"abcd"))
        with self.assertRaises(sidecar.ProtocolFailure):
            sidecar._read(BytesIO(struct.pack("!I",20)+b"{}"))

    def test_worker_factory_isolated_on_windows_without_inprocess_onnx(self):
        from champions_assist.local_event_worker import AsyncBattleEventReader
        from tests.test_local_event_worker import fixture
        profile, _, _ = fixture()
        class StubClient:
            provider="NOT_INITIALIZED"
            def warmup(self):
                self.provider="DmlExecutionProvider"
            def stop(self): pass
            def read(self, image, *, roi_name):
                from champions_assist.ocr import OcrRead
                return OcrRead("",None,"stub")
        with patch("champions_assist.ocr_sidecar.OcrSidecarClient",
                   return_value=StubClient()) as cls, \
             patch("champions_assist.ocr_onnx.make_onnx_ocr",
                   side_effect=AssertionError("in-process GPU is forbidden")):
            reader=AsyncBattleEventReader.with_onnx_ocr(
                profile,device="directml",gpu_id=0,isolate_process=True)
            try:
                cls.assert_called_once_with(device="directml",gpu_id=0)
                self.assertTrue(reader.enabled)
            finally:
                reader.stop()


if __name__=="__main__":
    unittest.main()
