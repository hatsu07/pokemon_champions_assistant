"""CLI helpers for strictly offline observer replay and calibration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import cv2

from .calibration import CalibrationLabels, OfflineReplayRunner, synthetic_frames_from_fixture
from .capture import ImageFileFrameSource, SyntheticFrameSource, VideoFrameSource
from .fsm import MatchController
from .observation_events import EventCertainty, EventPattern, ObservedEventKind, TextObservationParser
from .observer_pipeline import ObserverPipeline
from .ocr import TesseractJapaneseOcr
from .roi import RoiConfiguration
from .store import BattleStore
from .validation import ValidationDataset, evaluate_labelled_replay, sha256_sources
from .vision import DetectedUI, TemplateUiClassifier


def _load_patterns(path: Path | None) -> tuple[EventPattern, ...]:
    if path is None:
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        EventPattern(
            pattern_id=str(item["pattern_id"]),
            regex=str(item["regex"]),
            event_kind=ObservedEventKind(item["event_kind"]),
            certainty=EventCertainty(item.get("certainty", "CANDIDATE")),
            facts=item.get("facts", {}),
            effect_instance_template=item.get("effect_instance_template"),
            confirm_effect=bool(item.get("confirm_effect", False)),
            duration=item.get("duration"),
        )
        for item in payload.get("patterns", [])
    )


def _load_templates(path: Path | None) -> dict[DetectedUI, object]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[DetectedUI, object] = {}
    for name, filename in payload.get("templates", {}).items():
        image = cv2.imread(str((path.parent / filename).resolve()), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"cannot read UI template {filename}")
        result[DetectedUI(name)] = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return result


def add_observer_replay_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    replay = subparsers.add_parser("observer-replay", help="offline image/video/synthetic observer replay; never captures or controls a game")
    source = replay.add_mutually_exclusive_group(required=True)
    source.add_argument("--images", type=Path, nargs="+", help="ordered still images")
    source.add_argument("--video", type=Path, help="offline recording")
    source.add_argument("--synthetic-fixture", type=Path, help="synthetic RGB fixture JSON")
    replay.add_argument("--roi-config", type=Path, required=True)
    replay.add_argument("--db", type=Path, required=True)
    replay.add_argument("--master-dir", type=Path, default=Path("masters"))
    replay.add_argument("--match-id", default="offline-observer-replay")
    replay.add_argument("--labels", type=Path)
    replay.add_argument("--validation-dataset", type=Path, help="Phase 6 labelled replay dataset; synthetic and real evidence stay distinct")
    replay.add_argument("--event-patterns", type=Path, help="calibration-only OCR phrase rules")
    replay.add_argument("--ui-template-manifest", type=Path, help="calibration-only template paths")
    replay.add_argument("--stable-frames", type=int, default=2)


def run_observer_replay(args: argparse.Namespace) -> dict[str, object]:
    if args.images is not None:
        source = ImageFileFrameSource(args.images)
        source_kind = "IMAGE_FILES"
        evidence_paths = tuple(args.images)
    elif args.video is not None:
        source = VideoFrameSource(args.video)
        source_kind = "VIDEO_FILE"
        evidence_paths = (args.video,)
    else:
        source = SyntheticFrameSource(synthetic_frames_from_fixture(args.synthetic_fixture))
        source_kind = "SYNTHETIC_FIXTURE"
        evidence_paths = (args.synthetic_fixture,)
    store = BattleStore(args.db)
    try:
        session = MatchController(args.master_dir, store=store).start_match(args.match_id, now_ms=0)
        pipeline = ObserverPipeline(
            session=session,
            store=store,
            roi_config=RoiConfiguration.load(args.roi_config),
            ui_classifier=TemplateUiClassifier(_load_templates(args.ui_template_manifest)),
            ocr_engine=TesseractJapaneseOcr(),
            text_parser=TextObservationParser(_load_patterns(args.event_patterns)),
            stable_frames=args.stable_frames,
        )
        if args.labels is not None and args.validation_dataset is not None:
            raise ValueError("choose either --labels or --validation-dataset")
        dataset = None if args.validation_dataset is None else ValidationDataset.load(args.validation_dataset)
        labels = (
            dataset.calibration_labels()
            if dataset is not None
            else (None if args.labels is None else CalibrationLabels.load(args.labels))
        )
        observations, report = OfflineReplayRunner(pipeline).run(source, labels=labels)
        result: dict[str, object] = {
            "mode": "OFFLINE_OBSERVER_REPLAY",
            "source": source_kind,
            "real_game_validated": False,
            "network": "NOT_USED",
            "game_control": "NOT_IMPLEMENTED",
            "frames": len(observations),
            "watermark": None if not observations else observations[-1].watermark.__dict__,
            "calibration": report.as_dict(),
        }
        if dataset is not None:
            actual_hash = sha256_sources(evidence_paths)
            expected_hash = dataset.evidence.source_sha256
            hash_matches = None if expected_hash is None else actual_hash == expected_hash
            result["phase6_evaluation"] = evaluate_labelled_replay(
                observations,
                dataset,
                source_hash_matches=hash_matches,
            ).as_dict()
            result["phase6_evidence_source_sha256"] = actual_hash
        return result
    finally:
        store.close()
