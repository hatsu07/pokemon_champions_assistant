"""Replaceable ROI OCR adapters.  OCR text remains evidence, never tactics."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import subprocess
from typing import Iterable, Mapping, Protocol

import numpy as np


@dataclass(frozen=True)
class OcrRead:
    text: str
    confidence: float | None
    engine: str


class OcrEngine(Protocol):
    def read(self, image: np.ndarray, *, roi_name: str) -> OcrRead: ...


def image_digest(image: np.ndarray) -> str:
    return "sha256:" + hashlib.sha256(image.tobytes()).hexdigest()


class MappingOcrEngine:
    """Deterministic fixture/calibration adapter keyed by an ROI image digest."""

    def __init__(self, readings: Mapping[tuple[str, str], OcrRead]) -> None:
        self.readings = dict(readings)
        self.calls: list[str] = []

    @classmethod
    def for_images(cls, readings: Iterable[tuple[str, np.ndarray, OcrRead]]) -> "MappingOcrEngine":
        return cls({(name, image_digest(image)): value for name, image, value in readings})

    def read(self, image: np.ndarray, *, roi_name: str) -> OcrRead:
        self.calls.append(roi_name)
        return self.readings.get((roi_name, image_digest(image)), OcrRead("", None, "MAPPING_OCR"))


class TesseractJapaneseOcr:
    """Japanese/English Tesseract adapter, used only when explicitly configured."""

    engine_name = "tesseract-jpn+eng"

    def __init__(self, *, languages: str = "jpn+eng", config: str = "--psm 6") -> None:
        self.languages = languages
        self.config = config

    def read(self, image: np.ndarray, *, roi_name: str) -> OcrRead:
        import pytesseract

        data = pytesseract.image_to_data(image, lang=self.languages, config=self.config, output_type=pytesseract.Output.DICT)
        tokens = [str(token).strip() for token in data["text"] if str(token).strip()]
        confidences = [float(value) for value in data["conf"] if str(value) not in {"", "-1"}]
        confidence = None if not confidences else sum(confidences) / (100.0 * len(confidences))
        return OcrRead(" ".join(tokens), confidence, self.engine_name)

    @staticmethod
    def runtime_manifest() -> dict[str, object]:
        """Return package-size/provenance data without copying a model into source."""
        version = "UNAVAILABLE"
        try:
            version = subprocess.run(["tesseract", "--version"], check=True, capture_output=True, text=True, timeout=2).stdout.splitlines()[0]
        except (OSError, subprocess.SubprocessError):
            pass
        candidates = tuple(Path("/usr/share/tesseract-ocr/5/tessdata").glob("jpn.traineddata"))
        traineddata = candidates[0] if candidates else None
        return {
            "engine": "Tesseract OCR",
            "engine_license": "Apache-2.0",
            "language_data": "jpn.traineddata (distro package; not vendored)",
            "language_data_bytes": None if traineddata is None else traineddata.stat().st_size,
            "engine_version": version,
            "msi_release_action": "verify packaged binary, traineddata size, and distro notices before Windows release",
        }
