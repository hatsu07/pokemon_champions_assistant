"""Offline replay labels and calibration metrics for synthetic or supplied footage."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from statistics import fmean
from typing import Iterable

from .capture_runtime import latency_summary
from .capture import Frame, FrameSource
from .observer_pipeline import FrameObservation, ObserverPipeline
from .vision import DetectedUI


@dataclass(frozen=True)
class FrameLabel:
    frame_id: str
    visual_ui: DetectedUI | None = None
    event_kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalibrationLabels:
    source_label: str
    frames: tuple[FrameLabel, ...]

    @classmethod
    def load(cls, path: str | Path) -> "CalibrationLabels":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        frames = tuple(
            FrameLabel(
                frame_id=str(item["frame_id"]),
                visual_ui=None if item.get("visual_ui") is None else DetectedUI(item["visual_ui"]),
                event_kinds=tuple(str(value) for value in item.get("event_kinds", [])),
            )
            for item in payload.get("frames", [])
        )
        return cls(str(payload.get("source_label", "UNLABELLED")), frames)


@dataclass(frozen=True)
class CalibrationReport:
    source_label: str
    frame_count: int
    labelled_frame_count: int
    ui_correct: int
    ui_missed: int
    ui_false_positive: int
    event_true_positive: int
    event_missed: int
    event_false_positive: int
    processing_ms_mean: float
    processing_ms_max: float
    processing_ms_p50: float | None
    processing_ms_p95: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "source_label": self.source_label,
            "frame_count": self.frame_count,
            "labelled_frame_count": self.labelled_frame_count,
            "ui": {
                "correct": self.ui_correct,
                "missed": self.ui_missed,
                "false_positive": self.ui_false_positive,
                "accuracy": None if not self.labelled_frame_count else self.ui_correct / self.labelled_frame_count,
            },
            "events": {
                "true_positive": self.event_true_positive,
                "missed": self.event_missed,
                "false_positive": self.event_false_positive,
            },
            "processing_ms": {
                "mean": self.processing_ms_mean,
                "p50": self.processing_ms_p50,
                "p95": self.processing_ms_p95,
                "max": self.processing_ms_max,
            },
            "real_game_validated": False,
        }


class OfflineReplayRunner:
    """Replay sources through the same observer pipeline used by future capture."""

    def __init__(self, pipeline: ObserverPipeline) -> None:
        self.pipeline = pipeline

    def run(self, source: FrameSource, *, labels: CalibrationLabels | None = None) -> tuple[tuple[FrameObservation, ...], CalibrationReport]:
        observations: list[FrameObservation] = []
        labels_by_frame = {} if labels is None else {item.frame_id: item for item in labels.frames}
        ui_correct = ui_missed = ui_false_positive = 0
        event_true_positive = event_missed = event_false_positive = 0
        latencies: list[float] = []
        for frame in source:
            observation = self.pipeline.process_frame(frame)
            observations.append(observation)
            latencies.append(observation.processing_ms)
            label = labels_by_frame.get(frame.frame_id)
            if label is None:
                continue
            predicted_ui = observation.classification.visual_ui
            if label.visual_ui == predicted_ui:
                ui_correct += 1
            else:
                ui_missed += 1
                if predicted_ui is not DetectedUI.UNKNOWN:
                    ui_false_positive += 1
            expected_events = set(label.event_kinds)
            observed_events = {event.kind.value for event in observation.events}
            event_true_positive += len(expected_events & observed_events)
            event_missed += len(expected_events - observed_events)
            event_false_positive += len(observed_events - expected_events)
        report = CalibrationReport(
            source_label="UNLABELLED" if labels is None else labels.source_label,
            frame_count=len(observations),
            labelled_frame_count=len(labels_by_frame),
            ui_correct=ui_correct,
            ui_missed=ui_missed,
            ui_false_positive=ui_false_positive,
            event_true_positive=event_true_positive,
            event_missed=event_missed,
            event_false_positive=event_false_positive,
            processing_ms_mean=0.0 if not latencies else fmean(latencies),
            processing_ms_max=0.0 if not latencies else max(latencies),
            processing_ms_p50=latency_summary(latencies)["p50_ms"],
            processing_ms_p95=latency_summary(latencies)["p95_ms"],
        )
        return tuple(observations), report


def synthetic_frames_from_fixture(path: str | Path) -> tuple[Frame, ...]:
    """Build plainly labelled test frames, never a representation of a real UI."""
    import numpy as np

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    width = int(payload.get("width", 96))
    height = int(payload.get("height", 64))
    frames: list[Frame] = []
    for index, item in enumerate(payload.get("frames", []), start=1):
        color = item.get("rgb", [0, 0, 0])
        if not isinstance(color, list) or len(color) != 3:
            raise ValueError("synthetic frame rgb must have three values")
        image = np.full((height, width, 3), color, dtype=np.uint8)
        for patch in item.get("patches", []):
            left, top, right, bottom = (int(patch[key]) for key in ("left", "top", "right", "bottom"))
            image[top:bottom, left:right] = patch["rgb"]
        frames.append(
            Frame(
                frame_id=str(item.get("frame_id", f"synthetic-{index}")),
                capture_seq=int(item.get("capture_seq", index)),
                captured_at_ms=int(item.get("captured_at_ms", index * 100)),
                image=image,
                source="SYNTHETIC_FIXTURE",
                reference=str(path),
            )
        )
    return tuple(frames)
