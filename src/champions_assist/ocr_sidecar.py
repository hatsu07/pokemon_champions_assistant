"""Private, persistent OCR child process over length-prefixed local pipes.

No screenshots are saved and nothing is sent over a network. A child owns both
PP-OCRv6 det/rec DirectML sessions; the GUI process owns screen classification.
Frames stay in-memory and are only consumed on the existing OCR worker thread.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from threading import Lock, Thread, Timer
from typing import BinaryIO, Callable

from .ocr import OcrExecutionError, OcrRead

MAX_MESSAGE = 8 * 1024 * 1024
_MAX_PNG = 5 * 1024 * 1024
_READY_TIMEOUT = 45.0
_READ_TIMEOUT = 15.0


class ProtocolFailure(OcrExecutionError):
    pass


def _write(out: BinaryIO, item: dict) -> None:
    payload = json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MESSAGE:
        raise ProtocolFailure("OCR_SIDECAR_MESSAGE_TOO_LARGE")
    out.write(struct.pack("!I", len(payload)) + payload)
    out.flush()


def _read_exact(inp: BinaryIO, size: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < size:
        part = inp.read(size - len(buf))
        if not part:
            if not buf:
                return None
            raise ProtocolFailure("OCR_SIDECAR_INCOMPLETE_FRAME")
        buf.extend(part)
    return bytes(buf)


def _read(inp: BinaryIO) -> dict | None:
    header = _read_exact(inp, 4)
    if header is None:
        return None
    size = struct.unpack("!I", header)[0]
    if size < 2 or size > MAX_MESSAGE:
        raise ProtocolFailure("OCR_SIDECAR_INVALID_FRAME_SIZE")
    data = _read_exact(inp, size)
    if data is None:
        raise ProtocolFailure("OCR_SIDECAR_INCOMPLETE_FRAME")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise ProtocolFailure("OCR_SIDECAR_INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolFailure("OCR_SIDECAR_INVALID_JSON")
    return value


def _safe_code(exc: Exception) -> str:
    code = getattr(exc, "code", "")
    if isinstance(code, str) and code.startswith(("ONNX_OCR_", "OCR_")):
        return code[:96]
    return "OCR_SIDECAR_INFERENCE_FAILED"


def serve(*, device: str, gpu_id: int, factory: Callable | None = None,
          source: BinaryIO | None = None, target: BinaryIO | None = None) -> int:
    """The *only* runtime entry into OCR native ONNX sessions on GPU.

    In production this is executed by the frozen EXE's --ocr-sidecar branch,
    not from within the GUI Python process. An injected factory enables
    hardware-free protocol contract tests.
    """
    inp = source if source is not None else sys.stdin.buffer
    out = target if target is not None else sys.stdout.buffer
    try:
        if factory is None:
            from .ocr_onnx import make_onnx_ocr
            engine = make_onnx_ocr(device=device, gpu_id=gpu_id)
        else:
            engine = factory(device=device, gpu_id=gpu_id)
        engine.warmup()
        _write(out, {"type": "ready", "provider": str(engine.provider),
                     "det_provider": str(getattr(engine, "det_provider", engine.provider)),
                     "rec_provider": str(getattr(engine, "rec_provider", engine.provider))})
    except Exception as exc:
        _write(out, {"type": "fatal", "code": _safe_code(exc)})
        return 3

    import cv2
    import numpy as np
    while True:
        try:
            message = _read(inp)
            if message is None:
                break  # stdin closed when GUI dies; no persistent orphan
            if message.get("type") == "stop":
                break
            if message.get("type") != "read" or not isinstance(message.get("id"), int):
                raise ProtocolFailure("OCR_SIDECAR_INVALID_REQUEST")
            request_id = message["id"]
            roi = message.get("roi")
            payload = message.get("png")
            if not isinstance(roi, str) or len(roi) > 128 or not isinstance(payload, str):
                raise ProtocolFailure("OCR_SIDECAR_INVALID_REQUEST")
            if len(payload) > _MAX_PNG * 2:
                raise ProtocolFailure("OCR_SIDECAR_IMAGE_TOO_LARGE")
            png = base64.b64decode(payload, validate=True)
            if len(png) > _MAX_PNG:
                raise ProtocolFailure("OCR_SIDECAR_IMAGE_TOO_LARGE")
            image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None or image.size == 0:
                raise ProtocolFailure("OCR_SIDECAR_INVALID_IMAGE")
            reading = engine.read(image, roi_name=roi)
            _write(out, {"type": "result", "id": request_id, "text": reading.text,
                         "confidence": reading.confidence, "engine": reading.engine})
        except (ValueError, TypeError, OcrExecutionError) as exc:
            # A single malformed or failed ROI does not invent observations.
            # Do not include image bytes, OCR text, local file paths or stderr.
            _write(out, {"type": "error", "id": message.get("id") if isinstance(
                locals().get("message"), dict) else None, "code": _safe_code(exc)})
        except Exception as exc:
            _write(out, {"type": "error", "id": message.get("id") if isinstance(
                locals().get("message"), dict) else None, "code": _safe_code(exc)})
    return 0


class OcrSidecarClient:
    """OcrEngine-compatible proxy; owned by the existing asynchronous worker.

    stop() is called from the GUI thread. It must never join() or wait for a
    blocked inference; a watchdog kills a hung child and the next monitoring
    session can create a fresh one.
    """
    engine_name = "pp-ocrv6-small-onnx-sidecar"

    def __init__(self, *, device: str = "auto", gpu_id: int = 0,
                 command: tuple[str, ...] | None = None,
                 ready_timeout: float = _READY_TIMEOUT,
                 read_timeout: float = _READ_TIMEOUT):
        if device not in ("auto", "gpu", "cpu", "directml", "cuda") or not 0 <= gpu_id <= 7:
            raise OcrExecutionError("OCR_SIDECAR_INVALID_DEVICE")
        self.device, self.gpu_id = device, gpu_id
        self._command = command
        self._ready_timeout, self._read_timeout = ready_timeout, read_timeout
        self.provider = "NOT_INITIALIZED"
        self.det_provider = "NOT_INITIALIZED"
        self.rec_provider = "NOT_INITIALIZED"
        self._proc: subprocess.Popen | None = None
        self._lock = Lock()
        self._closed = False
        self._request_id = 0

    def _terminate(self, proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def _roundtrip(self, proc: subprocess.Popen, request: dict | None,
                   timeout: float) -> dict:
        if proc.stdin is None or proc.stdout is None:
            raise OcrExecutionError("OCR_SIDECAR_PIPE_UNAVAILABLE")
        watchdog = Timer(timeout, self._terminate, args=(proc,))
        watchdog.daemon = True
        watchdog.start()
        try:
            if request is not None:
                _write(proc.stdin, request)
            response = _read(proc.stdout)
            if response is None:
                raise OcrExecutionError("OCR_SIDECAR_EXITED")
            return response
        except (OSError, BrokenPipeError, ProtocolFailure) as exc:
            raise OcrExecutionError("OCR_SIDECAR_IPC_FAILED") from exc
        finally:
            watchdog.cancel()

    def warmup(self) -> None:
        with self._lock:
            if self._closed:
                raise OcrExecutionError("OCR_SIDECAR_STOPPED")
            if self._proc is not None:
                if self._proc.poll() is not None:
                    raise OcrExecutionError("OCR_SIDECAR_EXITED")
                return
            if self._command is not None:
                command = list(self._command)
            elif getattr(sys, "frozen", False):
                command = [sys.executable, "--ocr-sidecar",
                           "--ocr-device", self.device, "--ocr-gpu-id", str(self.gpu_id)]
            else:
                command = [sys.executable, "-u", "-m", "champions_assist.ocr_sidecar",
                           "--ocr-device", self.device, "--ocr-gpu-id", str(self.gpu_id)]
            try:
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                proc = subprocess.Popen(command, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        creationflags=flags)
            except OSError as exc:
                raise OcrExecutionError("OCR_SIDECAR_LAUNCH_FAILED") from exc
            self._proc = proc
        # Native GPU warmup may be expensive; never run this on the UI thread.
        try:
            response = self._roundtrip(proc, None, self._ready_timeout)
            if response.get("type") == "fatal":
                code = response.get("code")
                raise OcrExecutionError(code if isinstance(code, str)
                                        and code.startswith(("ONNX_OCR_", "OCR_"))
                                        else "OCR_SIDECAR_WARMUP_FAILED")
            if response.get("type") != "ready":
                raise OcrExecutionError("OCR_SIDECAR_INVALID_READY")
            provider = response.get("provider")
            expected_gpu = self.device in ("gpu", "directml", "cuda")
            if expected_gpu and provider not in ("DmlExecutionProvider", "CUDAExecutionProvider"):
                raise OcrExecutionError("OCR_SIDECAR_GPU_NOT_ACTIVE")
            if self.device == "directml" and provider != "DmlExecutionProvider":
                raise OcrExecutionError("OCR_SIDECAR_DML_NOT_ACTIVE")
            if self.device == "cuda" and provider != "CUDAExecutionProvider":
                raise OcrExecutionError("OCR_SIDECAR_CUDA_NOT_ACTIVE")
            self.provider = str(provider)
            self.det_provider = str(response.get("det_provider"))
            self.rec_provider = str(response.get("rec_provider"))
        except Exception:
            self._terminate(proc)
            raise

    def read(self, image, *, roi_name: str) -> OcrRead:
        self.warmup()
        import cv2
        import numpy as np
        if not isinstance(image, np.ndarray) or image.size == 0:
            raise OcrExecutionError("OCR_SIDECAR_INVALID_IMAGE")
        ok, encoded = cv2.imencode(".png", image)
        if not ok or encoded.nbytes > _MAX_PNG:
            raise OcrExecutionError("OCR_SIDECAR_IMAGE_TOO_LARGE")
        payload = base64.b64encode(encoded.tobytes()).decode("ascii")
        with self._lock:
            if self._closed or self._proc is None:
                raise OcrExecutionError("OCR_SIDECAR_STOPPED")
            self._request_id += 1
            request_id = self._request_id
            proc = self._proc
        response = self._roundtrip(proc, {"type": "read", "id": request_id,
                                          "roi": roi_name, "png": payload},
                                   self._read_timeout)
        if response.get("id") != request_id:
            self._terminate(proc)
            raise OcrExecutionError("OCR_SIDECAR_RESPONSE_MISMATCH")
        if response.get("type") == "error":
            code = response.get("code")
            raise OcrExecutionError(code if isinstance(code, str)
                                    and code.startswith(("ONNX_OCR_", "OCR_"))
                                    else "OCR_SIDECAR_READ_FAILED")
        if response.get("type") != "result" or not isinstance(response.get("text"), str):
            self._terminate(proc)
            raise OcrExecutionError("OCR_SIDECAR_INVALID_RESPONSE")
        conf = response.get("confidence")
        if conf is not None and (type(conf) not in (float, int) or
                                 not 0 <= conf <= 1):
            raise OcrExecutionError("OCR_SIDECAR_INVALID_CONFIDENCE")
        return OcrRead(response["text"], conf, str(response.get("engine", self.engine_name)))

    def stop(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            proc = self._proc
        if proc is not None:
            self._terminate(proc)
            # Reaping is asynchronous; GUI/turn actions NEVER block here.
            Thread(target=self._reap, args=(proc,), daemon=True,
                   name="ocr-sidecar-reaper").start()

    @staticmethod
    def _reap(proc: subprocess.Popen) -> None:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        finally:
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ocr-device", default="auto")
    parser.add_argument("--ocr-gpu-id", type=int, default=0)
    args = parser.parse_args()
    raise SystemExit(serve(device=args.ocr_device, gpu_id=args.ocr_gpu_id))
