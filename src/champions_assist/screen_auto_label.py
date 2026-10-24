"""Offline-only screen dataset proposals: never create truth without human review.

This module uses an experimental ONNX model for *suggestions only*. It does not
change the production classifier gate, send data to networks, or touch labelled
PNG files. Frames are grouped only within a single recorded match/session.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

import cv2
import numpy as np

from .screen_classifier import LABELS, ScreenClassifier


@dataclass(frozen=True)
class AutoLabelFrame:
    """One frame's non-authoritative AI suggestion and review warnings."""
    path: Path
    predicted_label: str | None
    confidence: float
    margin: float
    review_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class AutoLabelGroup:
    files: tuple[Path, ...]
    candidate: str | None
    confidence: float
    margin: float
    representative: tuple[Path, ...]
    review_reason: str
    frame_details: tuple[AutoLabelFrame, ...] = ()

    @property
    def count(self) -> int:
        return len(self.files)


def load_suggestion_model(onnx: str | Path) -> ScreenClassifier:
    """Opt-in experimental use; runtime game/production paths stay validated-only."""
    model = Path(onnx)
    if model.name != "champions_screen_classifier.onnx":
        raise ValueError("EXPECTED_CHAMPIONS_SCREEN_CLASSIFIER_ONNX")
    manifest = model.with_suffix(".json")
    if not model.is_file() or not manifest.is_file():
        raise FileNotFoundError("MODEL_AND_SAME_DIRECTORY_MANIFEST_REQUIRED")
    return ScreenClassifier(model, manifest, device="cpu",
                            experimental_for_labelling=True)


def _visual_hash(rgb: np.ndarray) -> int:
    # dHash on the entire rendered ROI; boundaries are deliberately conservative.
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).ravel()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def _capture_time(path: Path) -> datetime | None:
    try:
        return datetime.strptime(path.stem.split("_", 1)[0], "%Y%m%dT%H%M%S%fZ")
    except ValueError:
        return None


def _representative(files: Sequence[Path]) -> tuple[Path, ...]:
    if len(files) < 3:
        return tuple(files)
    return files[0], files[len(files) // 2], files[-1]


def analyze_staged_session(files: Sequence[Path], *, classifier=None,
                           progress: Callable[[int, int], None] | None=None,
                           max_group: int=30, max_hash_distance: int=7,
                           max_color_distance: float=25.0
                           ) -> list[AutoLabelGroup]:
    """Compute *draft* groups; approval is explicitly out of scope.

    Continuity needs same parent, consecutive capture time, similarity, and
    consistent raw model prediction. Ambiguous predictions are not UNKNOWN truth.
    """
    if (not 1 <= max_group <= 100 or not 0 <= max_hash_distance <= 64
            or not 0 <= max_color_distance <= 255):
        raise ValueError("INVALID_AUTOLABEL_GROUP_SETTINGS")
    if not files:
        return []
    files = list(files)
    parents = {p.parent.resolve() for p in files}
    if len(parents) != 1 or any(p.suffix.lower() != ".jpg" or p.is_symlink() or
                                not p.is_file() for p in files):
        raise ValueError("INVALID_AUTOLABEL_SESSION_FILES")
    if files != sorted(files, key=lambda p: p.name):
        raise ValueError("AUTOLABEL_FILES_NOT_CHRONOLOGICAL")

    groups: list[AutoLabelGroup] = []
    active: list[Path] = []
    active_details: list[AutoLabelFrame] = []
    previous_hash: int | None = None
    previous_small: np.ndarray | None = None
    previous_time: datetime | None = None
    label: str | None = None
    min_conf = 1.0
    min_margin = 1.0

    def emit_group() -> None:
        if not active:
            return
        reason = ("AI候補・代表画像を確認" if label is not None else
                  "候補不明・人間がラベル選択")
        groups.append(AutoLabelGroup(tuple(active), label,
                                     min_conf if classifier else 0.0,
                                     min_margin if classifier else 0.0,
                                     _representative(active), reason,
                                     tuple(active_details)))

    for index, path in enumerate(files, start=1):
        # numpy.fromfile handles Windows paths containing Japanese characters.
        encoded = np.fromfile(path, dtype=np.uint8)
        bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError(f"UNREADABLE_STAGED_JPEG: {path.name}")
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        current_hash = _visual_hash(rgb)
        current_small = cv2.resize(rgb,(24,24),interpolation=cv2.INTER_AREA).astype(np.float32)
        color_change=(float(np.abs(current_small-previous_small).mean())
                      if previous_small is not None else 0.0)
        timestamp = _capture_time(path)
        current_label = None
        raw_label = None
        confidence, margin = 0.0, 0.0
        if classifier is not None:
            prediction = classifier.predict(rgb, raw_label=True)
            confidence, margin = prediction.confidence, prediction.margin
            raw_label = prediction.label
            if (prediction.label in LABELS and
                    confidence >= 0.90 and margin >= 0.20):
                current_label = prediction.label

        gap = ((timestamp - previous_time).total_seconds()
               if timestamp is not None and previous_time is not None else None)
        boundary = (not active or len(active) >= max_group or
                    (previous_hash is not None and
                     (previous_hash ^ current_hash).bit_count() > max_hash_distance) or
                    (previous_small is not None and color_change > max_color_distance) or
                    (gap is not None and not 0 <= gap <= 2.0) or
                    (classifier is not None and label != current_label))
        if boundary:
            emit_group()
            active = []
            active_details = []
            label = current_label
            min_conf, min_margin = 1.0, 1.0

        flags = []
        if classifier is None:
            flags.append("AI未設定")
        elif current_label is None:
            flags.append("AI予測の確信度不足")
        elif current_label == "OTHER":
            flags.append("OTHERは内容を確認")
        if previous_hash is not None and (previous_hash ^ current_hash).bit_count() >= 4:
            flags.append("前画像との形状差")
        if previous_small is not None and color_change >= 12.0:
            flags.append("前画像との色差")
        active.append(path)
        active_details.append(AutoLabelFrame(path, raw_label, confidence,
                                             margin, tuple(flags)))
        min_conf = min(min_conf, confidence)
        min_margin = min(min_margin, margin)
        previous_hash, previous_small, previous_time = current_hash, current_small, timestamp
        if progress is not None and (index == 1 or index % 25 == 0 or index == len(files)):
            progress(index, len(files))

    emit_group()
    return groups
