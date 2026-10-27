"""Replaceable ROI OCR adapters.  OCR text remains evidence, never tactics."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import csv
from io import StringIO
import os
import tempfile
from pathlib import Path
import subprocess
import shutil
import sys
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


def discover_japanese_tesseract() -> tuple[str, str] | None:
    """Find both native executable and Japanese data; no subprocess or network.

    Portable builds stage OCR in _MEIPASS/ocr-runtime. A locally installed
    Tesseract with jpn.traineddata remains supported.
    """
    roots=[Path(getattr(sys,"_MEIPASS",Path(sys.executable).parent))/"ocr-runtime",
           Path(sys.executable).parent/"ocr-runtime"]
    if sys.platform=="win32":
        roots.extend([
            Path("C:/Program Files/Tesseract-OCR"),
            Path("C:/Program Files (x86)/Tesseract-OCR")])
    found=shutil.which("tesseract")
    if found:
        roots.append(Path(found).parent)
    for root in roots:
        binary=root/("tesseract.exe" if sys.platform=="win32" else "tesseract")
        data=root/"tessdata"
        if binary.is_file() and (data/"jpn.traineddata").is_file() and (
                data/"eng.traineddata").is_file():
            return str(binary),str(data)
    return None


class OcrExecutionError(RuntimeError):
    """Sanitized OCR failure. Never include raw stderr or private paths."""
    def __init__(self, code: str, *, status: int | None=None):
        super().__init__(code)
        self.code=code
        self.status=status


def classify_ocr_failure(stderr: str, status: int) -> str:
    message=str(stderr).casefold()
    if "error opening data file" in message or "failed loading language" in message:
        return "OCR_TESSDATA_LOAD_FAILED"
    if "couldn't load any languages" in message or "no languages could be loaded" in message:
        return "OCR_LANGUAGE_UNAVAILABLE"
    if "image" in message and ("read" in message or "decode" in message):
        return "OCR_IMAGE_READ_FAILED"
    return "TESSERACT_EXIT_"+str(status) if 0<=status<=255 else "OCR_NATIVE_FAILED"


class TesseractJapaneseOcr:
    """Japanese/English Tesseract adapter with an explicit bundled runtime."""

    engine_name = "tesseract-jpn+eng"

    def __init__(self, *, languages: str = "jpn+eng", config: str = "--psm 6",
                 timeout_seconds: float = 1.2) -> None:
        self.languages = languages
        self.config = config
        self.timeout_seconds = timeout_seconds
        runtime=discover_japanese_tesseract()
        # Offline synthetic/labelled replay can construct the adapter without
        # any native OCR executable. Only ACTUAL OCR requires the runtime.
        self.command,self.tessdata_dir=runtime if runtime is not None else (None,None)

    def read(self, image: np.ndarray, *, roi_name: str) -> OcrRead:
        """Execute the packaged binary with isolated tessdata and a real PNG.

        Avoid pytesseract's process-global binary path and config parsing.
        A Tesseract exit-1 is diagnosed without leaking user paths/text.
        """
        if self.command is None or self.tessdata_dir is None:
            raise OcrExecutionError("OCR_RUNTIME_MISSING")
        if image.size == 0 or image.ndim not in (2,3):
            raise ValueError("OCR_INVALID_IMAGE_SHAPE")
        if image.ndim==3 and image.shape[2] not in (1,3,4):
            raise ValueError("OCR_INVALID_IMAGE_CHANNELS")
        if image.dtype != np.uint8:
            image=np.clip(image,0,255).astype(np.uint8)
        if image.ndim==3 and image.shape[2]==1:
            image=image[:,:,0]
        image=np.ascontiguousarray(image)
        import cv2
        if image.ndim==3 and image.shape[2]==3:
            image=cv2.cvtColor(image,cv2.COLOR_RGB2BGR)
        elif image.ndim==3 and image.shape[2]==4:
            image=cv2.cvtColor(image,cv2.COLOR_RGBA2BGRA)
        # Calibrated Japanese popup text is most legible at 2x on the
        # user-provided 960x572 sample. Percent HUD is numeric-only and its
        # best offline result uses psm7/eng/whitelist. Other crops remain raw.
        if roi_name=="opponent_effect_popup":
            image=cv2.resize(image,None,fx=2,fy=2,
                             interpolation=cv2.INTER_CUBIC)
        numeric_percent=roi_name=="opponent_hp_percent"
        ok, buffer=cv2.imencode(".png",image)
        if not ok:
            raise OcrExecutionError("OCR_PNG_ENCODING_FAILED")
        environment=os.environ.copy()
        environment["TESSDATA_PREFIX"]=self.tessdata_dir
        with tempfile.TemporaryDirectory(prefix="champions-ocr-") as directory:
            path=Path(directory)/"roi.png"
            path.write_bytes(buffer.tobytes())
            try:
                flags=(["--psm","7","-c","tessedit_char_whitelist=0123456789/%"]
                       if numeric_percent else self.config.split())
                process=subprocess.run(
                    [self.command,str(path),"stdout","-l",
                     "eng" if numeric_percent else self.languages,
                     "--tessdata-dir",self.tessdata_dir,*flags,"tsv"],
                    capture_output=True,text=True,encoding="utf-8",
                    errors="replace",env=environment,
                    creationflags=(getattr(subprocess,"CREATE_NO_WINDOW",0)
                                   if sys.platform=="win32" else 0),
                    timeout=self.timeout_seconds,check=False)
            except subprocess.TimeoutExpired as exc:
                raise OcrExecutionError("OCR_PROCESS_TIMEOUT") from exc
            except OSError as exc:
                raise OcrExecutionError("OCR_PROCESS_START_FAILED") from exc
        if process.returncode != 0:
            raise OcrExecutionError(
                classify_ocr_failure(process.stderr,process.returncode),
                status=process.returncode)
        try:
            table=csv.DictReader(StringIO(process.stdout),delimiter="\t")
            # An exit-zero process without a valid TSV header did not deliver
            # OCR output. A legitimate empty detection still has TSV columns.
            if not {"text","conf"}.issubset(set(table.fieldnames or ())):
                raise OcrExecutionError("OCR_INVALID_TSV")
            rows=list(table)
            tokens=[r["text"].strip() for r in rows if r.get("text","").strip()]
            confs=[float(r["conf"]) for r in rows
                   if r.get("conf","").strip() not in ("","-1")]
        except (KeyError,ValueError,TypeError) as exc:
            raise OcrExecutionError("OCR_INVALID_TSV") from exc
        confidence=(sum(confs)/(100.0*len(confs))) if confs else None
        return OcrRead(" ".join(tokens),confidence,self.engine_name)

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
