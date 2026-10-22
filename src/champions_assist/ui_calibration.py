"""Explicitly labelled, locally stored screenshot UI calibration.

A calibration probe is not real-game validation or a match decision.
Only user-selected UI fragments are persisted; source screenshots never upload.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

import cv2
import numpy as np

from .roi import NormalizedROI
from .vision import DetectedUI


SUPPORTED_LABELS = frozenset(item.value for item in DetectedUI if item is not DetectedUI.UNKNOWN)


@dataclass(frozen=True)
class CalibrationPrediction:
    status: str
    visual_ui: str
    confidence: float | None
    scores: dict[str, float]
    template_count: int

    def as_dict(self) -> dict[str, object]:
        return {"status": self.status, "visual_ui": self.visual_ui, "confidence": self.confidence,
                "scores": self.scores, "template_count": self.template_count,
                "calibration_only": True, "real_game_validated": False}


class LocalUiCalibration:
    """Anchor-local OpenCV templates. Scores are similarities, not probabilities."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.manifest = self.directory / "manifest.json"

    def list_templates(self) -> list[dict]:
        if not self.manifest.exists():
            return []
        data = json.loads(self.manifest.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or not isinstance(data.get("templates"), list):
            raise ValueError("unsupported UI calibration manifest")
        return [dict(item) for item in data["templates"]]

    @staticmethod
    def _sha(image: np.ndarray) -> str:
        return hashlib.sha256(image.tobytes()).hexdigest()

    @staticmethod
    def crop_viewport(image: np.ndarray, roi: tuple[float, float, float, float] | None) -> np.ndarray:
        if roi is None:
            raise ValueError("set the game viewport ROI before calibration")
        return NormalizedROI("GAME_VIEWPORT", *roi).crop(image)

    @staticmethod
    def read_local_png(path: str | Path) -> np.ndarray:
        encoded = np.fromfile(str(path), dtype=np.uint8)
        bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError("image file cannot be read")
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    def add_template(self, image: np.ndarray, *, label: str, anchor: tuple[float, float, float, float]) -> dict:
        if label not in SUPPORTED_LABELS:
            raise ValueError("unsupported UI label")
        if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
            raise ValueError("expected RGB uint8 screenshot")
        patch = NormalizedROI("CALIBRATION_ANCHOR", *anchor).crop(image)
        if min(patch.shape[:2]) < 12:
            raise ValueError("anchor region must be at least 12 x 12 pixels")
        identifier = uuid.uuid4().hex
        filename = f"anchor-{identifier}.png"
        entry = {"id": identifier, "label": label, "anchor": list(anchor), "file": filename,
                 "reference_size": [image.shape[1], image.shape[0]],
                 "source_sha256": self._sha(image),
                 "captured_at_utc": datetime.now(timezone.utc).isoformat()}
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / filename
        ok, encoded = cv2.imencode(".png", cv2.cvtColor(patch, cv2.COLOR_RGB2BGR))
        if not ok:
            raise ValueError("anchor encoding failed")
        with path.open("xb") as out:
            out.write(encoded.tobytes())
        try:
            records = self.list_templates() + [entry]
            temp = self.manifest.with_suffix(".tmp")
            try:
                temp.write_text(json.dumps({"schema_version": 1, "templates": records}, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(temp, self.manifest)
            finally:
                temp.unlink(missing_ok=True)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return entry

    def classify(self, image: np.ndarray, *, min_score: float = 0.82, min_margin: float = 0.03) -> CalibrationPrediction:
        if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
            raise ValueError("expected RGB uint8 screenshot")
        records = self.list_templates()
        if not records:
            return CalibrationPrediction("NO_TEMPLATES", "UNKNOWN", None, {}, 0)
        if any(record.get("source_sha256") == self._sha(image) for record in records):
            return CalibrationPrediction("TRAINING_FRAME_REUSED", "UNKNOWN", None, {}, len(records))
        h, w = image.shape[:2]
        grayscale = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        scores: dict[str, float] = {}
        usable = 0
        for record in records:
            label = record.get("label")
            anchor, reference = record.get("anchor"), record.get("reference_size")
            if label not in SUPPORTED_LABELS or not isinstance(anchor, list) or len(anchor) != 4:
                continue
            if not isinstance(reference, list) or len(reference) != 2 or min(reference) <= 0:
                continue
            if abs(w/h - reference[0]/reference[1]) > 0.06:
                continue
            filename = str(record.get("file", ""))
            if not filename.startswith("anchor-") or "/" in filename or "\\" in filename:
                continue
            patch = cv2.imread(str(self.directory / filename), cv2.IMREAD_GRAYSCALE)
            if patch is None:
                continue
            l, t, r, b = map(float, anchor)
            pw, ph = round((r-l)*w), round((b-t)*h)
            if pw < 12 or ph < 12:
                continue
            if patch.shape[:2] != (ph, pw):
                patch = cv2.resize(patch, (pw, ph), interpolation=cv2.INTER_AREA)
            px, py = round(w*.025), round(h*.025)
            x0, y0 = max(0, round(l*w)-px), max(0, round(t*h)-py)
            x1, y1 = min(w, round(r*w)+px), min(h, round(b*h)+py)
            search = grayscale[y0:y1, x0:x1]
            if search.shape[0] < ph or search.shape[1] < pw:
                continue
            result = cv2.matchTemplate(search, patch, cv2.TM_SQDIFF_NORMED)
            score = max(0.0, min(1.0, 1.0-float(np.min(result))))
            scores[label] = max(scores.get(label, 0.0), score)
            usable += 1
        if not scores:
            return CalibrationPrediction("NO_COMPATIBLE_TEMPLATES", "UNKNOWN", None, {}, len(records))
        ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        best_label, best_score = ordered[0]
        if best_score < min_score:
            status, choice = "LOW_CONFIDENCE", "UNKNOWN"
        elif len(ordered) > 1 and best_score - ordered[1][1] < min_margin:
            status, choice = "AMBIGUOUS", "UNKNOWN"
        else:
            status, choice = "CANDIDATE_ONLY", best_label
        return CalibrationPrediction(status, choice, round(best_score, 4),
                                     {name: round(value, 4) for name, value in sorted(scores.items())}, usable)

    @staticmethod
    def run_ocr_probe(image: np.ndarray, *, anchor: tuple[float, float, float, float]) -> dict[str, object]:
        """Read-only diagnostic; explicit failure when Windows Tesseract is absent."""
        if shutil.which("tesseract") is None:
            return {"status": "OCR_UNAVAILABLE", "reason": "TESSERACT_NOT_FOUND"}
        try:
            import pytesseract
            if not {"jpn", "eng"}.issubset(set(pytesseract.get_languages(config=""))):
                return {"status": "OCR_UNAVAILABLE", "reason": "JPN_ENG_LANGUAGE_DATA_MISSING"}
            from .ocr import TesseractJapaneseOcr
            patch = NormalizedROI("OCR_ANCHOR", *anchor).crop(image)
            result = TesseractJapaneseOcr().read(patch, roi_name="OCR_ANCHOR")
            return {"status": "OCR_CANDIDATE_ONLY", "text": result.text,
                    "confidence": result.confidence, "engine": result.engine}
        except (OSError, RuntimeError, ValueError, ImportError) as exc:
            return {"status": "OCR_UNAVAILABLE", "reason": type(exc).__name__}
