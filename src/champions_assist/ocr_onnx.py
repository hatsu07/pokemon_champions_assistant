"""PP-OCRv6 small: local ONNX Runtime detection + CTC recognition.

The *same* ONNX Runtime installation as the screen classifier is used.
No PaddlePaddle, separately installed Python, remote inference, or OCR-based
battle-state interpretation is involved. The caller owns threading/lifecycle.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

from .ocr import OcrExecutionError, OcrRead

ENGINE_NAME = "pp-ocrv6-small-onnx"
MODEL_DET = "PP-OCRv6_small_det"
MODEL_REC = "PP-OCRv6_small_rec"
_DET_FILE = "pp-ocrv6-small-det.onnx"
_REC_FILE = "pp-ocrv6-small-rec.onnx"
_DICT_FILE = "ppocrv6_dict.txt"


def bundled_model_directory() -> Path:
    """Prefer bundled models; also support an explicit source-development stage."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    paths = (root / "ocr-v6",
             Path(__file__).resolve().parent / "data" / "ocr-v6")
    for candidate in paths:
        if all((candidate / name).is_file()
               for name in (_DET_FILE, _REC_FILE, _DICT_FILE)):
            return candidate
    raise OcrExecutionError("ONNX_OCR_MODELS_MISSING")


def _new_session(path: Path, *, factory, available, device: str) -> tuple[object, str]:
    if device not in ("auto", "gpu", "cpu"):
        raise OcrExecutionError("ONNX_OCR_DEVICE_INVALID")
    if device == "cpu":
        order = ["CPUExecutionProvider"]
    else:
        order = ["DmlExecutionProvider", "CUDAExecutionProvider"]
        if device == "auto":
            order.append("CPUExecutionProvider")
    last = None
    for provider in order:
        if provider not in available:
            continue
        options = None
        if provider == "DmlExecutionProvider":
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        providers = ([provider, "CPUExecutionProvider"] if
                     provider != "CPUExecutionProvider" else [provider])
        try:
            kwargs = {"providers": providers}
            if options is not None:
                kwargs["sess_options"] = options
            session = factory(str(path), **kwargs)
            actual = session.get_providers()
            if provider not in actual:
                last = "PROVIDER_NOT_ATTACHED"
                continue
            return session, provider
        except (RuntimeError, ValueError, OSError, TypeError) as exc:
            last = type(exc).__name__
    if device == "gpu":
        raise OcrExecutionError("ONNX_OCR_GPU_UNAVAILABLE")
    raise OcrExecutionError("ONNX_OCR_SESSION_FAILED")


def _rec_tensor(bgr: np.ndarray, width: int = 320, height: int = 48):
    """PaddleOCR RecResizeImg [3,48,320] with right-side zero padding."""
    h, w = bgr.shape[:2]
    ratio = w / max(1, h)
    valid_width = max(1, min(width, int(np.ceil(height * ratio))))
    scaled = cv2.resize(bgr, (valid_width, height))
    values = scaled.astype(np.float32) / 255.0
    values = (values - 0.5) / 0.5
    chw = values.transpose(2, 0, 1)
    output = np.zeros((1, 3, height, width), np.float32)
    output[0, :, :, :valid_width] = chw
    return output


def _ctc_decode(prediction: np.ndarray, characters: list[str]) -> tuple[str, float | None]:
    logits = np.asarray(prediction)
    if logits.ndim != 3 or logits.shape[0] != 1 or logits.shape[2] != len(characters) + 1:
        raise OcrExecutionError("ONNX_OCR_REC_OUTPUT_INVALID")
    matrix = logits[0]
    indices = np.argmax(matrix, axis=1)
    vals = matrix[np.arange(len(indices)), indices].astype(float)
    if np.any(vals < 0) or np.any(vals > 1):
        scaled = matrix - np.max(matrix, axis=1, keepdims=True)
        exp = np.exp(scaled)
        vals = (exp / np.maximum(exp.sum(axis=1, keepdims=True), 1e-30))[
            np.arange(len(indices)), indices]
    text, confidence, previous = [], [], -1
    for index, score in zip(indices, vals):
        index = int(index)
        if index != 0 and index != previous:
            text.append(characters[index - 1])
            confidence.append(float(score))
        previous = index
    return "".join(text), (sum(confidence) / len(confidence) if confidence else None)


def _det_tensor(bgr: np.ndarray):
    """DB-det normalization from official PP-OCRv6_small_det inference.yml."""
    height, width = bgr.shape[:2]
    # Use an upper bound for tiny game ROI crops, not a full-page 960px image.
    ratio = min(1.0, 960.0 / max(height, width))
    dh = max(32, int(np.ceil(height * ratio / 32)) * 32)
    dw = max(32, int(np.ceil(width * ratio / 32)) * 32)
    image = cv2.resize(bgr, (dw, dh), interpolation=cv2.INTER_LINEAR)
    image = (image.astype(np.float32) / 255.0 -
             np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array(
                 [0.229, 0.224, 0.225], dtype=np.float32)
    return image.transpose(2, 0, 1)[None, ...].copy(), (width / dw, height / dh)


def _boxes(prediction: np.ndarray, width: int, height: int, scale):
    """DB-like polygon extraction for bounded, horizontal game-message ROIs.

    No interpretation of the text occurs here. Crops are passed to the model
    and only the raw recognized words are surfaced to ChatGPT.
    """
    score_map = np.asarray(prediction)
    if score_map.ndim == 4:
        score_map = score_map[0, 0]
    elif score_map.ndim == 3:
        score_map = score_map[0]
    if score_map.ndim != 2:
        raise OcrExecutionError("ONNX_OCR_DET_OUTPUT_INVALID")
    bitmap = (score_map > 0.20).astype(np.uint8)
    contours, _ = cv2.findContours(bitmap, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    sx, sy = scale
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:3000]:
        if cv2.contourArea(contour) < 4:
            continue
        box = cv2.minAreaRect(contour)
        if min(box[1]) < 2:
            continue
        mask = np.zeros(score_map.shape, np.uint8)
        cv2.drawContours(mask, [contour], -1, 1, -1)
        quality = float(cv2.mean(score_map, mask=mask)[0])
        if quality < 0.45:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        # Expand text boxes (DB unclip), then clamp to the original ROI.
        padx, pady = max(2, int(w * 0.20)), max(2, int(h * 0.20))
        x1 = max(0, int((x - padx) * sx))
        y1 = max(0, int((y - pady) * sy))
        x2 = min(width, int(np.ceil((x + w + padx) * sx)))
        y2 = min(height, int(np.ceil((y + h + pady) * sy)))
        if x2 - x1 >= 3 and y2 - y1 >= 3:
            boxes.append((x1, y1, x2, y2))
    # Most messages have one line; stable top-to-bottom, left-to-right order.
    boxes.sort(key=lambda box: (round(box[1] / 16), box[0], box[1]))
    return boxes


class PPocrV6SmallOnnx:
    engine_name = ENGINE_NAME

    def __init__(self, *, model_dir: str | Path | None = None,
                 device: str = "auto", session_factory=None,
                 available_providers=None):
        self.model_dir = Path(model_dir) if model_dir is not None else None
        self.device = device
        self._session_factory = session_factory
        self._available = available_providers
        self._det = None
        self._rec = None
        self.provider = "NOT_INITIALIZED"
        self._characters = []

    def warmup(self):
        if self._det is not None:
            return
        root = self.model_dir or bundled_model_directory()
        for name in (_DET_FILE, _REC_FILE, _DICT_FILE):
            if not (root / name).is_file():
                raise OcrExecutionError("ONNX_OCR_MODELS_MISSING")
        characters = (root / _DICT_FILE).read_text(encoding="utf-8-sig").splitlines()
        if len(characters) < 1000:
            raise OcrExecutionError("ONNX_OCR_DICTIONARY_INVALID")
        # Official PP-OCRv6 recognizer includes an additional space character.
        characters.append(" ")
        try:
            import onnxruntime as ort
            factory = self._session_factory or ort.InferenceSession
            available = set(self._available or ort.get_available_providers())
            det, det_provider = _new_session(
                root / _DET_FILE, factory=factory, available=available, device=self.device)
            rec, rec_provider = _new_session(
                root / _REC_FILE, factory=factory, available=available, device=self.device)

            def inference_probe(detector, recognizer):
                sample = np.full((64, 128, 3), 255, np.uint8)
                det_input, _ = _det_tensor(sample)
                detector.run(None, {detector.get_inputs()[0].name: det_input})
                output = recognizer.run(None, {
                    recognizer.get_inputs()[0].name: _rec_tensor(sample)})[0]
                if (np.asarray(output).ndim != 3 or
                        np.asarray(output).shape[2] != len(characters) + 1):
                    raise OcrExecutionError("ONNX_OCR_DICTIONARY_MISMATCH")

            try:
                inference_probe(det, rec)
            except Exception as exc:
                if self.device != "auto" or (
                        det_provider == rec_provider == "CPUExecutionProvider"):
                    raise OcrExecutionError("ONNX_OCR_INFERENCE_PROBE_FAILED") from exc
                # DML can attach but fail to execute an unsupported operator.
                # Fall back to the CPU *before* reporting readiness to the GUI.
                det, det_provider = _new_session(
                    root / _DET_FILE, factory=factory, available=available, device="cpu")
                rec, rec_provider = _new_session(
                    root / _REC_FILE, factory=factory, available=available, device="cpu")
                inference_probe(det, rec)
            self.provider = (det_provider if det_provider == rec_provider else
                             "MIXED:" + det_provider + "/" + rec_provider)
            self._det, self._rec, self._characters = det, rec, characters
        except OcrExecutionError:
            raise
        except Exception as exc:
            raise OcrExecutionError("ONNX_OCR_INITIALIZATION_FAILED") from exc

    def read(self, image: np.ndarray, *, roi_name: str) -> OcrRead:
        if image.ndim not in (2, 3) or image.size == 0:
            raise OcrExecutionError("ONNX_OCR_INVALID_IMAGE")
        self.warmup()
        if image.dtype != np.uint8:
            image = np.clip(image, 0, 255).astype(np.uint8)
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        elif image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_RGBA2BGR)
        elif image.shape[2] == 3:
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        else:
            raise OcrExecutionError("ONNX_OCR_INVALID_IMAGE")
        try:
            det_input, scale = _det_tensor(image)
            det_name = self._det.get_inputs()[0].name
            det_output = self._det.run(None, {det_name: det_input})[0]
            boxes = _boxes(det_output, image.shape[1], image.shape[0], scale)
            texts, scores = [], []
            rec_name = self._rec.get_inputs()[0].name
            for x1, y1, x2, y2 in boxes:
                crop = image[y1:y2, x1:x2]
                rec_output = self._rec.run(None, {rec_name: _rec_tensor(crop)})[0]
                line, confidence = _ctc_decode(rec_output, self._characters)
                if line:
                    texts.append(line)
                    if confidence is not None:
                        scores.append(confidence)
            return OcrRead(" ".join(texts),
                           sum(scores) / len(scores) if scores else None, ENGINE_NAME)
        except OcrExecutionError:
            raise
        except Exception as exc:
            raise OcrExecutionError("ONNX_OCR_INFERENCE_FAILED") from exc


def make_onnx_ocr(*, device: str = "auto") -> PPocrV6SmallOnnx:
    return PPocrV6SmallOnnx(device=device)
