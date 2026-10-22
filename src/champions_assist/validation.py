"""Phase 6 calibration, labelled-replay evaluation and capture acceptance gates.

All reports distinguish synthetic/offline evidence from a live Windows probe.
They intentionally cannot assert that arbitrary captured pixels are Champions;
that requires labelled footage and an operator review on the target machine.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Sequence

import cv2

from .adb_capture import AdbScreenshotFrameSource
from .calibration import CalibrationLabels, FrameLabel
from .capture import LiveCaptureBackend, create_live_frame_source
from .capture_runtime import CaptureWorker, compare_capture_reports
from .observer_pipeline import FrameObservation
from .roi import NormalizedROI, ROI_NAMES, RoiConfiguration
from .vision import DetectedUI


EVIDENCE_KINDS = frozenset({"SYNTHETIC", "RECORDED_CHAMPIONS", "LIVE_WINDOWS_CHAMPIONS"})
REQUIRED_PHASE6_SCENARIOS = (
    "OPPONENT_TEAM_SELECTION",
    "NORMAL_COMMAND",
    "MOVE_MENU",
    "PARTY_MENU",
    "PIVOT_RESOLUTION",
    "FAINT_REPLACEMENT",
    "RANDOM_FORCED_SWITCH",
    "MOVE_AND_POKEMON_TEXT",
    "HP_STATUS_STAT_STAGES",
    "WEATHER_FIELD_WALL_HAZARD",
    "MEGA_ABILITY_ITEM",
    "TURN_AND_RESULT",
)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def sha256_sources(paths: Sequence[str | Path]) -> str:
    """Hash one source directly or an ordered set as a local replay manifest."""
    normalized = tuple(Path(path) for path in paths)
    if not normalized:
        raise ValueError("at least one evidence source is required")
    if len(normalized) == 1:
        return sha256_file(normalized[0])
    digest = hashlib.sha256()
    for path in normalized:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


@dataclass(frozen=True)
class ValidationEvidence:
    kind: str
    capture_backend: str | None
    source_sha256: str | None
    operator_attested: bool
    terms_reviewed: bool

    def __post_init__(self) -> None:
        if self.kind not in EVIDENCE_KINDS:
            raise ValueError(f"unsupported validation evidence kind: {self.kind}")
        if self.source_sha256 is not None and not self.source_sha256.startswith("sha256:"):
            raise ValueError("source_sha256 must use a sha256: prefix")

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "capture_backend": self.capture_backend,
            "source_sha256": self.source_sha256,
            "operator_attested": self.operator_attested,
            "terms_reviewed": self.terms_reviewed,
        }


@dataclass(frozen=True)
class ValidationFrameLabel:
    frame_id: str
    visual_ui: DetectedUI | None
    event_kinds: tuple[str, ...]
    ocr_text: dict[str, str]
    scenarios: tuple[str, ...]

    def as_calibration_label(self) -> FrameLabel:
        return FrameLabel(self.frame_id, self.visual_ui, self.event_kinds)


@dataclass(frozen=True)
class ValidationDataset:
    """Labels only; footage remains local and is never uploaded by this tool."""

    schema_version: str
    source_label: str
    evidence: ValidationEvidence
    frames: tuple[ValidationFrameLabel, ...]

    @classmethod
    def load(cls, path: str | Path) -> "ValidationDataset":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        evidence_raw = payload.get("evidence", {})
        evidence = ValidationEvidence(
            kind=str(evidence_raw.get("kind", "SYNTHETIC")),
            capture_backend=None if evidence_raw.get("capture_backend") is None else str(evidence_raw["capture_backend"]),
            source_sha256=None if evidence_raw.get("source_sha256") is None else str(evidence_raw["source_sha256"]),
            operator_attested=bool(evidence_raw.get("operator_attested", False)),
            terms_reviewed=bool(evidence_raw.get("terms_reviewed", False)),
        )
        labels: list[ValidationFrameLabel] = []
        frame_ids: set[str] = set()
        for item in payload.get("frames", []):
            frame_id = str(item["frame_id"])
            if frame_id in frame_ids:
                raise ValueError(f"duplicate validation frame_id: {frame_id}")
            frame_ids.add(frame_id)
            visual = item.get("visual_ui")
            ocr = item.get("ocr_text", {})
            scenarios = tuple(str(value) for value in item.get("scenarios", ()))
            unknown = set(scenarios) - set(REQUIRED_PHASE6_SCENARIOS)
            if unknown:
                raise ValueError(f"unknown Phase 6 scenarios: {sorted(unknown)}")
            if not isinstance(ocr, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in ocr.items()):
                raise ValueError("ocr_text must map ROI names to exact expected text")
            labels.append(
                ValidationFrameLabel(
                    frame_id=frame_id,
                    visual_ui=None if visual is None else DetectedUI(visual),
                    event_kinds=tuple(str(value) for value in item.get("event_kinds", ())),
                    ocr_text=dict(ocr),
                    scenarios=scenarios,
                )
            )
        return cls(
            schema_version=str(payload.get("schema_version", "phase6-validation-v1")),
            source_label=str(payload.get("source_label", "UNLABELLED")),
            evidence=evidence,
            frames=tuple(labels),
        )

    def calibration_labels(self) -> CalibrationLabels:
        return CalibrationLabels(self.source_label, tuple(frame.as_calibration_label() for frame in self.frames))


@dataclass(frozen=True)
class LabelledReplayEvaluation:
    evidence: ValidationEvidence
    labelled_frames: int
    ui_correct: int
    event_missed: int
    ocr_expected: int
    ocr_correct: int
    ocr_missed: int
    ocr_incorrect: int
    source_hash_matches: bool | None
    scenarios_passed: tuple[str, ...]
    scenarios_missing: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        recorded_real = self.evidence.kind in {"RECORDED_CHAMPIONS", "LIVE_WINDOWS_CHAMPIONS"}
        all_scenarios = not self.scenarios_missing
        admission_checks = {
            "labelled_real_footage": recorded_real,
            "source_hash_matches": self.source_hash_matches is True,
            "operator_attested": self.evidence.operator_attested,
            "terms_reviewed": self.evidence.terms_reviewed,
            "required_scenarios_passed": all_scenarios,
        }
        return {
            "evidence": self.evidence.as_dict(),
            "labelled_frames": self.labelled_frames,
            "ui_correct": self.ui_correct,
            "event_missed": self.event_missed,
            "ocr": {
                "expected": self.ocr_expected,
                "correct": self.ocr_correct,
                "missed": self.ocr_missed,
                "incorrect": self.ocr_incorrect,
            },
            "source_hash_matches": self.source_hash_matches,
            "scenarios_passed": list(self.scenarios_passed),
            "scenarios_missing": list(self.scenarios_missing),
            "phase7_admission": {
                "status": "READY_FOR_HUMAN_REVIEW" if all(admission_checks.values()) else "BLOCKED",
                "checks": admission_checks,
                "note": "This evaluates supplied labels only. Native Windows capture, packaged-app and MSI gates remain separate.",
            },
        }


def evaluate_labelled_replay(
    observations: Iterable[FrameObservation],
    dataset: ValidationDataset,
    *,
    source_hash_matches: bool | None = None,
) -> LabelledReplayEvaluation:
    by_id = {item.frame_id: item for item in observations}
    ui_correct = event_missed = ocr_expected = ocr_correct = ocr_missed = ocr_incorrect = 0
    passed_scenarios: set[str] = set()
    for expected in dataset.frames:
        observation = by_id.get(expected.frame_id)
        if observation is None:
            event_missed += len(expected.event_kinds)
            ocr_expected += len(expected.ocr_text)
            ocr_missed += len(expected.ocr_text)
            continue
        ui_ok = expected.visual_ui is None or observation.classification.visual_ui is expected.visual_ui
        if ui_ok and expected.visual_ui is not None:
            ui_correct += 1
        observed_events = {event.kind.value for event in observation.events}
        event_ok = set(expected.event_kinds).issubset(observed_events)
        event_missed += len(set(expected.event_kinds) - observed_events)
        ocr_ok = True
        for roi_name, text in expected.ocr_text.items():
            ocr_expected += 1
            observed_text = observation.ocr_text.get(roi_name)
            if observed_text is None:
                ocr_missed += 1
                ocr_ok = False
            elif observed_text == text:
                ocr_correct += 1
            else:
                ocr_incorrect += 1
                ocr_ok = False
        if ui_ok and event_ok and ocr_ok:
            passed_scenarios.update(expected.scenarios)
    missing = tuple(item for item in REQUIRED_PHASE6_SCENARIOS if item not in passed_scenarios)
    return LabelledReplayEvaluation(
        evidence=dataset.evidence,
        labelled_frames=len(dataset.frames),
        ui_correct=ui_correct,
        event_missed=event_missed,
        ocr_expected=ocr_expected,
        ocr_correct=ocr_correct,
        ocr_missed=ocr_missed,
        ocr_incorrect=ocr_incorrect,
        source_hash_matches=source_hash_matches,
        scenarios_passed=tuple(item for item in REQUIRED_PHASE6_SCENARIOS if item in passed_scenarios),
        scenarios_missing=missing,
    )


def _parse_roi(specification: str) -> tuple[str, NormalizedROI]:
    try:
        name, values = specification.split("=", 1)
        left, top, right, bottom = (float(value) for value in values.split(","))
    except ValueError as exc:
        raise ValueError("ROI must be NAME=left,top,right,bottom") from exc
    if name not in ROI_NAMES:
        raise ValueError(f"unsupported ROI name: {name}")
    return name, NormalizedROI(name, left, top, right, bottom)


def run_roi_calibration(args: argparse.Namespace) -> dict[str, object]:
    """Create a local ROI config and optional explicit crops from one image."""
    bgr = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"cannot read calibration image: {args.image}")
    image = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    height, width = image.shape[:2]
    rois = dict(RoiConfiguration.load(args.base_config).rois) if args.base_config else {}
    for specification in args.roi:
        name, roi = _parse_roi(specification)
        rois[name] = roi
    config = RoiConfiguration(
        version=args.roi_version,
        reference_resolution=(width, height),
        rois=rois,
        source_label=args.source_label,
    )
    config.save(args.output)
    written_templates: list[str] = []
    for specification in args.template:
        try:
            ui_name, roi_name, output = specification.split(",", 2)
            DetectedUI(ui_name)
        except ValueError as exc:
            raise ValueError("template must be UI_KIND,ROI_NAME,OUTPUT_PATH") from exc
        crop = config.crop(roi_name, image)
        if crop is None:
            raise ValueError(f"template refers to an undefined ROI: {roi_name}")
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(target), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)):
            raise RuntimeError(f"cannot write template: {target}")
        written_templates.append(str(target))
    return {
        "mode": "LOCAL_ROI_CALIBRATION",
        "input_sha256": sha256_file(args.image),
        "reference_resolution": [width, height],
        "roi_output": str(args.output),
        "template_outputs": written_templates,
        "source_label": args.source_label,
        "real_game_validated": False,
        "note": "Coordinates/templates require labelled replay and live-machine review before acceptance.",
    }


def run_capture_probe(args: argparse.Namespace) -> dict[str, object]:
    """Perform a caller-confirmed, read-only live capture probe."""
    if not args.confirm_read_only_capture:
        return {
            "mode": "LIVE_CAPTURE_PROBE",
            "status": "REFUSED_EXPLICIT_CONFIRMATION_REQUIRED",
            "network": "NOT_USED",
            "game_control": "NOT_IMPLEMENTED",
            "real_game_validated": False,
        }
    backend = LiveCaptureBackend(args.backend)
    if backend is LiveCaptureBackend.ADB_SCREENSHOT and not args.adb_serial:
        raise ValueError("--adb-serial is required for ADB_SCREENSHOT")
    source_factory = lambda: create_live_frame_source(
        backend,
        windows_bbox=None if args.windows_bbox is None else tuple(args.windows_bbox),
        windows_title=args.window_title,
        adb_serial=args.adb_serial,
    )
    adb_status = None
    if backend is LiveCaptureBackend.ADB_SCREENSHOT:
        adb_status = AdbScreenshotFrameSource(args.adb_serial).connection_status().as_dict()
    worker = CaptureWorker(
        source_factory,
        lambda _frame: None,
        expected_fps=args.expected_fps,
        retry_delay_ms=args.retry_delay_ms,
    )
    metrics = worker.run(frame_limit=args.frames, duration_ms=args.duration_ms)
    report = {
        "mode": "LIVE_CAPTURE_PROBE",
        "backend": backend.value,
        "status": "CAPTURED_READ_ONLY_UNVERIFIED_CONTENT" if metrics.frames else "CAPTURE_UNAVAILABLE",
        "metrics": metrics.as_dict(),
        "latency": worker.latency.as_dict(),
        "adb_connection": adb_status,
        "runtime_platform": os.name,
        "network": "NOT_USED",
        "game_control": "NOT_IMPLEMENTED",
        "real_game_validated": False,
        "content_verification": "UNVERIFIED; label footage and review it on the Windows/BlueStacks machine.",
    }
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report["report_output"] = str(args.output)
    return report


def run_capture_comparison(args: argparse.Namespace) -> dict[str, object]:
    reports: dict[str, dict[str, object]] = {}
    for path in args.report:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        backend = str(report.get("backend", ""))
        if backend in reports:
            raise ValueError(f"duplicate backend report: {backend}")
        reports[backend] = report
    return compare_capture_reports(reports)


def add_phase6_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    capture = subparsers.add_parser("capture-probe", help="explicit read-only Windows/ADB capture measurement; no game input")
    capture.add_argument("--backend", choices=[item.value for item in LiveCaptureBackend], required=True)
    capture.add_argument("--confirm-read-only-capture", action="store_true")
    capture.add_argument("--frames", type=int, default=120)
    capture.add_argument("--duration-ms", type=int, default=15_000)
    capture.add_argument("--expected-fps", type=float)
    capture.add_argument("--retry-delay-ms", type=int, default=250)
    capture.add_argument("--window-title")
    capture.add_argument("--windows-bbox", type=int, nargs=4, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    capture.add_argument("--adb-serial")
    capture.add_argument("--output", type=Path)

    calibration = subparsers.add_parser("roi-calibrate", help="write explicit ROI/template calibration artifacts from a local image")
    calibration.add_argument("--image", type=Path, required=True)
    calibration.add_argument("--output", type=Path, required=True)
    calibration.add_argument("--roi", action="append", default=[], metavar="NAME=L,T,R,B")
    calibration.add_argument("--template", action="append", default=[], metavar="UI_KIND,ROI_NAME,OUTPUT_PATH")
    calibration.add_argument("--base-config", type=Path)
    calibration.add_argument("--roi-version", default="phase6-local-1")
    calibration.add_argument("--source-label", default="UNCALIBRATED_LOCAL_IMAGE")

    comparison = subparsers.add_parser("capture-compare", help="compare saved read-only capture reports; never chooses a backend automatically")
    comparison.add_argument("--report", action="append", type=Path, required=True)
