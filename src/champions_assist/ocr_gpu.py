"""GPU-only PP-OCRv6 small backend. OCR text is untrusted observation data.

For source runs this module keeps one PaddleOCR pipeline in memory.
For PyInstaller Windows GUI builds a separately installed GPU Python runtime
can host the same engine over a persistent local stdio subprocess. No network.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Thread
from time import monotonic
from uuid import uuid4

import cv2
import numpy as np
from .ocr import OcrRead, OcrExecutionError

ENGINE_NAME="pp-ocrv6-small-gpu"
MODEL_DET="PP-OCRv6_small_det"
MODEL_REC="PP-OCRv6_small_rec"


def _verified_gpu():
    try:
        import paddle
        if not paddle.is_compiled_with_cuda() or paddle.device.cuda.device_count()<1:
            raise OcrExecutionError("GPU_OCR_CUDA_UNAVAILABLE")
    except ImportError as exc:
        raise OcrExecutionError("GPU_OCR_RUNTIME_MISSING") from exc


class PaddleV6SmallGpuOcr:
    """Persistent GPU-only pipeline; never downgrade to another model/CPU."""
    engine_name=ENGINE_NAME

    def __init__(self, *, pipeline_factory=None, gpu_check=None):
        self._factory=pipeline_factory
        self._gpu_check=gpu_check or _verified_gpu
        self._pipeline=None

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline
        self._gpu_check()
        factory=self._factory
        if factory is None:
            try:
                from paddleocr import PaddleOCR
            except ImportError as exc:
                raise OcrExecutionError("GPU_OCR_RUNTIME_MISSING") from exc
            factory=PaddleOCR
        try:
            self._pipeline=factory(
                ocr_version="PP-OCRv6",
                text_detection_model_name=MODEL_DET,
                text_recognition_model_name=MODEL_REC,
                device="gpu:0",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        except Exception as exc:
            raise OcrExecutionError("GPU_OCR_INITIALIZATION_FAILED") from exc
        return self._pipeline

    def read(self, image: np.ndarray, *, roi_name: str) -> OcrRead:
        if image.size==0 or image.ndim not in (2,3):
            raise OcrExecutionError("GPU_OCR_INVALID_IMAGE")
        pipeline=self._load()
        if image.dtype!=np.uint8:
            image=np.clip(image,0,255).astype(np.uint8)
        # Capture arrays are RGB; PaddleOCR numpy images follow OpenCV/BGR.
        if image.ndim==3 and image.shape[2]==3:
            image=cv2.cvtColor(image,cv2.COLOR_RGB2BGR)
        elif image.ndim==3 and image.shape[2]==4:
            image=cv2.cvtColor(image,cv2.COLOR_RGBA2BGR)
        try:
            results=pipeline.predict(np.ascontiguousarray(image))
            words=[]
            scores=[]
            for result in results:
                payload=result.json if hasattr(result,"json") else result
                if callable(payload):
                    payload=payload()
                if not isinstance(payload,dict):
                    raise OcrExecutionError("GPU_OCR_INVALID_RESPONSE")
                fields=payload.get("res",payload)
                readings=fields.get("rec_texts") or []
                confidences=fields.get("rec_scores") or []
                for i,word in enumerate(readings):
                    if str(word).strip():
                        words.append(str(word).strip())
                        if i<len(confidences) and isinstance(confidences[i],(int,float)):
                            scores.append(float(confidences[i]))
            return OcrRead(" ".join(words),sum(scores)/len(scores) if scores else None,ENGINE_NAME)
        except OcrExecutionError:
            raise
        except Exception as exc:
            raise OcrExecutionError("GPU_OCR_INFERENCE_FAILED") from exc


class PaddleGpuSidecar:
    """One persistent local GPU Python child for a Windows packaged GUI.

    CHAMPIONS_OCR_PYTHON points to a Python 3.11/3.12 venv with GPU
    PaddlePaddle, paddleocr and champions_assist installed. The child is
    started lazily in the OCR thread. A timeout invalidates the child rather
    than returning late output for a later frame.
    """
    engine_name=ENGINE_NAME

    def __init__(self, python_path: str, *, timeout_seconds: float=4.0):
        self.python_path=python_path
        self.timeout_seconds=timeout_seconds
        self._process=None
        self._answers=Queue()
        self._reader=None

    def _spawn(self):
        if self._process is not None and self._process.poll() is None:
            return
        if self._process is not None:
            self.stop()
        try:
            self._process=subprocess.Popen(
                [self.python_path,"-m","champions_assist.ocr_gpu_worker"],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,bufsize=1,
                text=True,encoding="utf-8",
                creationflags=(getattr(subprocess,"CREATE_NO_WINDOW",0)
                               if sys.platform=="win32" else 0))
        except OSError as exc:
            raise OcrExecutionError("GPU_OCR_SIDECAR_START_FAILED") from exc
        self._answers=Queue()
        pipe=self._process.stdout
        def read_lines():
            for line in pipe:
                try:
                    obj=json.loads(line)
                    if isinstance(obj,dict) and "request_id" in obj:
                        self._answers.put(obj)
                except (ValueError,TypeError):
                    continue
        self._reader=Thread(target=read_lines,daemon=True,name="ocr-gpu-stdio")
        self._reader.start()

    def read(self,image: np.ndarray,*,roi_name: str) -> OcrRead:
        self._spawn()
        ok,buf=cv2.imencode(".png",cv2.cvtColor(image,cv2.COLOR_RGB2BGR)
                            if image.ndim==3 and image.shape[2]==3 else image)
        if not ok:
            raise OcrExecutionError("GPU_OCR_PNG_FAILED")
        req=uuid4().hex
        try:
            self._process.stdin.write(json.dumps({
                "request_id":req,"roi":roi_name,
                "image":base64.b64encode(buf.tobytes()).decode("ascii")})+"\n")
            self._process.stdin.flush()
        except (OSError,BrokenPipeError) as exc:
            self.stop()
            raise OcrExecutionError("GPU_OCR_SIDECAR_DISCONNECTED") from exc
        deadline=monotonic()+self.timeout_seconds
        while monotonic()<deadline:
            try:
                message=self._answers.get(timeout=max(.001,deadline-monotonic()))
            except Empty:
                break
            if message.get("request_id")!=req:
                continue
            if message.get("error"):
                raise OcrExecutionError(str(message["error"]))
            return OcrRead(str(message.get("text") or ""),
                           message.get("confidence"),ENGINE_NAME)
        self.stop()
        raise OcrExecutionError("GPU_OCR_SIDECAR_TIMEOUT")

    def stop(self):
        process=self._process
        self._process=None
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass


def make_gpu_ocr():
    """Explicit GPU only. Packaged Windows builds use a separate GPU venv."""
    if getattr(sys,"frozen",False):
        python=os.environ.get("CHAMPIONS_OCR_PYTHON","").strip()
        if not python or not Path(python).is_file():
            raise OcrExecutionError("GPU_OCR_PYTHON_NOT_CONFIGURED")
        return PaddleGpuSidecar(python)
    return PaddleV6SmallGpuOcr()
