from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from champions_assist.__main__ import main
from champions_assist.adb_capture import AdbScreenshotFrameSource
from champions_assist.capture import CaptureUnavailable, Frame, LiveCaptureBackend, SyntheticFrameSource, create_live_frame_source
from champions_assist.capture_runtime import CaptureMetrics, CaptureWorker, LatencyLedger, compare_capture_reports
from champions_assist.observer_pipeline import FrameObservation, SyncWatermark
from champions_assist.validation import REQUIRED_PHASE6_SCENARIOS, ValidationDataset, evaluate_labelled_replay, run_capture_probe
from champions_assist.vision import DetectedUI, UiClassification
from champions_assist.windows_capture import WindowGeometry, WindowsNativeFrameSource


def test_frame(frame_id: str, captured_at_ms: int = 10) -> Frame:
    return Frame(
        frame_id=frame_id,
        capture_seq=1,
        captured_at_ms=captured_at_ms,
        image=np.zeros((4, 4, 3), dtype=np.uint8),
        source="SYNTHETIC_PHASE6",
    )


class _GeometryProvider:
    def __init__(self, values: list[WindowGeometry]) -> None:
        self.values = iter(values)

    def probe(self) -> WindowGeometry:
        return next(self.values)


class _UnavailableSource:
    def __iter__(self):
        raise CaptureUnavailable("WINDOW_MINIMIZED")
        yield  # pragma: no cover - keeps this a generator for the protocol


class _DesktopControllerProbe:
    def __init__(self) -> None:
        self.refresh_count = 0

    def clock_ms(self) -> int:
        return 130

    def refresh(self) -> None:
        self.refresh_count += 1


class Phase6TestCase(unittest.TestCase):
    def test_window_source_uses_fresh_client_geometry_and_refuses_minimized_window(self) -> None:
        provider = _GeometryProvider(
            [
                WindowGeometry("AVAILABLE", title="BlueStacks App Player", hwnd=1, client_bbox=(10, 20, 110, 220), dpi=144),
                WindowGeometry("AVAILABLE", title="BlueStacks App Player", hwnd=1, client_bbox=(20, 30, 220, 330), dpi=192),
            ]
        )
        bboxes: list[tuple[int, int, int, int] | None] = []

        def grab(bbox: tuple[int, int, int, int] | None) -> Image.Image:
            bboxes.append(bbox)
            return Image.fromarray(np.zeros((3, 5, 3), dtype=np.uint8))

        source = WindowsNativeFrameSource(
            window_title="BlueStacks",
            max_frames=2,
            geometry_provider=provider,
            image_grabber=grab,
            clock_ms=lambda: 123,
            is_windows=False,
        )
        iterator = iter(source)
        first = next(iterator)
        self.assertEqual(first.captured_at_ms, 123)
        self.assertEqual(first.reference, "BlueStacks App Player")
        self.assertEqual(bboxes, [(10, 20, 110, 220)])
        second = next(iterator)
        self.assertEqual(second.reference, "BlueStacks App Player")
        self.assertEqual(bboxes, [(10, 20, 110, 220), (20, 30, 220, 330)])
        self.assertEqual(source.last_geometry.dpi if source.last_geometry else None, 192)
        minimized = WindowsNativeFrameSource(
            window_title="BlueStacks",
            max_frames=1,
            geometry_provider=_GeometryProvider([WindowGeometry("WINDOW_MINIMIZED", title="BlueStacks App Player", hwnd=1)]),
            image_grabber=grab,
            is_windows=False,
        )
        with self.assertRaisesRegex(CaptureUnavailable, "WINDOW_MINIMIZED"):
            next(iter(minimized))

    def test_capture_worker_recovers_after_unavailable_source_without_gui_or_sqlite_sharing(self) -> None:
        sources = iter((_UnavailableSource(), SyntheticFrameSource((test_frame("recovered", 15),))))
        clock_values = iter((0, 10, 25))
        received: list[str] = []
        worker = CaptureWorker(
            lambda: next(sources),
            lambda frame: received.append(frame.frame_id),
            retry_delay_ms=0,
            clock_ms=lambda: next(clock_values),
        )
        metrics = worker.run(frame_limit=1)
        self.assertEqual(received, ["recovered"])
        self.assertEqual(metrics.frames, 1)
        self.assertEqual(metrics.as_dict()["failure_reasons"], ["WINDOW_MINIMIZED"])
        self.assertEqual(metrics.as_dict()["recovery"]["samples"], 1)

    def test_latency_metrics_include_p50_p95_max_and_drop_estimate(self) -> None:
        metrics = CaptureMetrics(expected_fps=10.0)
        metrics.record_frame(test_frame("one", 95), arrived_at_ms=100)
        metrics.record_frame(test_frame("two", 195), arrived_at_ms=200)
        metrics.record_frame(test_frame("three", 395), arrived_at_ms=400)
        report = metrics.as_dict()
        self.assertEqual(report["frames"], 3)
        self.assertEqual(report["frame_interval"]["p50_ms"], 150.0)
        self.assertEqual(report["frame_interval"]["p95_ms"], 195.0)
        self.assertEqual(report["estimated_dropped_frames"], 1)
        ledger = LatencyLedger()
        ledger.captured("one", 100)
        ledger.observer_completed("one", 115)
        ledger.gui_presented("one", 130)
        self.assertEqual(ledger.as_dict()["capture_to_gui"]["max_ms"], 30.0)

    def test_labelled_replay_requires_every_scenario_and_never_auto_approves(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dataset.json"
            frames = [
                {
                    "frame_id": scenario,
                    "visual_ui": "COMMAND_MENU",
                    "scenarios": [scenario],
                    "ocr_text": {"ROI_STATUS": scenario},
                }
                for scenario in REQUIRED_PHASE6_SCENARIOS
            ]
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "phase6-validation-v1",
                        "source_label": "LOCAL_WINDOWS_LABELS",
                        "evidence": {
                            "kind": "LIVE_WINDOWS_CHAMPIONS",
                            "capture_backend": "WINDOWS_NATIVE",
                            "source_sha256": "sha256:" + "0" * 64,
                            "operator_attested": True,
                            "terms_reviewed": True,
                        },
                        "frames": frames,
                    }
                ),
                encoding="utf-8",
            )
            dataset = ValidationDataset.load(path)
        observations = tuple(
            FrameObservation(
                frame_id=scenario,
                raw_observation_id=scenario,
                classification=UiClassification(DetectedUI.COMMAND_MENU, 1.0, {}),
                stable_visual_ui=DetectedUI.COMMAND_MENU,
                events=(),
                watermark=SyncWatermark(1, 0, 0),
                processing_ms=1.0,
                ocr_text={"ROI_STATUS": scenario},
            )
            for scenario in REQUIRED_PHASE6_SCENARIOS
        )
        report = evaluate_labelled_replay(observations, dataset, source_hash_matches=True).as_dict()
        self.assertEqual(report["scenarios_missing"], [])
        self.assertEqual(report["phase7_admission"]["status"], "READY_FOR_HUMAN_REVIEW")
        self.assertNotEqual(report["phase7_admission"]["status"], "APPROVED")

    def test_synthetic_or_incomplete_labels_remain_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.json"
            path.write_text(
                json.dumps(
                    {
                        "evidence": {"kind": "SYNTHETIC"},
                        "frames": [{"frame_id": "one", "visual_ui": "COMMAND_MENU", "scenarios": ["NORMAL_COMMAND"]}],
                    }
                ),
                encoding="utf-8",
            )
            dataset = ValidationDataset.load(path)
        observation = FrameObservation(
            frame_id="one",
            raw_observation_id="one",
            classification=UiClassification(DetectedUI.COMMAND_MENU, 1.0, {}),
            stable_visual_ui=DetectedUI.COMMAND_MENU,
            events=(),
            watermark=SyncWatermark(1, 0, 0),
            processing_ms=1.0,
            ocr_text={},
        )
        report = evaluate_labelled_replay((observation,), dataset).as_dict()
        self.assertIn("PIVOT_RESOLUTION", report["scenarios_missing"])
        self.assertEqual(report["phase7_admission"]["status"], "BLOCKED")

    def test_calibration_and_probe_cli_are_explicit_and_offline_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "screen.png"
            config = root / "roi.json"
            template = root / "command.png"
            self.assertTrue(cv2.imwrite(str(image), np.full((20, 40, 3), 90, dtype=np.uint8)))
            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "roi-calibrate",
                        "--image",
                        str(image),
                        "--output",
                        str(config),
                        "--roi",
                        "ROI_UI_CONTROLS=0,0,1,1",
                        "--template",
                        f"COMMAND_MENU,ROI_UI_CONTROLS,{template}",
                    ]
                )
            calibrated = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(config.exists(), calibrated)
            self.assertTrue(template.exists())
            self.assertFalse(calibrated["real_game_validated"])
            output = StringIO()
            with redirect_stdout(output):
                code = main(["capture-probe", "--backend", "WINDOWS_NATIVE"])
            refused = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(refused["status"], "REFUSED_EXPLICIT_CONFIRMATION_REQUIRED")
        self.assertFalse(refused["real_game_validated"])

    def test_labelled_synthetic_replay_cli_reaches_sqlite_fsm_but_stays_non_real(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "frames.json"
            roi = root / "roi.json"
            labels = root / "labels.json"
            database = root / "replay.sqlite3"
            fixture.write_text(json.dumps({"width": 8, "height": 8, "frames": [{"frame_id": "one", "rgb": [0, 0, 0]}]}), encoding="utf-8")
            roi.write_text(json.dumps({"version": "test", "reference_resolution": None, "rois": {}}), encoding="utf-8")
            labels.write_text(
                json.dumps(
                    {
                        "evidence": {"kind": "SYNTHETIC"},
                        "frames": [{"frame_id": "one", "scenarios": ["NORMAL_COMMAND"]}],
                    }
                ),
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "observer-replay",
                        "--synthetic-fixture",
                        str(fixture),
                        "--roi-config",
                        str(roi),
                        "--validation-dataset",
                        str(labels),
                        "--db",
                        str(database),
                        "--master-dir",
                        "masters",
                    ]
                )
            report = json.loads(output.getvalue())
            database_exists = database.exists()
        self.assertEqual(code, 0)
        self.assertTrue(database_exists)
        self.assertEqual(report["phase6_evaluation"]["evidence"]["kind"], "SYNTHETIC")
        self.assertEqual(report["phase6_evaluation"]["phase7_admission"]["status"], "BLOCKED")

    def test_capture_comparison_requires_both_measured_backends_and_never_selects_one(self) -> None:
        comparison = compare_capture_reports(
            {
                "WINDOWS_NATIVE": {"metrics": {"frames": 3, "capture_lag": {"p95_ms": 10.0}}},
                "ADB_SCREENSHOT": {"metrics": {"frames": 3, "capture_lag": {"p95_ms": 20.0}}},
            }
        )
        self.assertEqual(comparison["comparison_status"], "MEASURED_REVIEW_REQUIRED")
        self.assertIsNone(comparison["automatic_recommendation"])
        incomplete = compare_capture_reports({"WINDOWS_NATIVE": {"metrics": {"frames": 0}}})
        self.assertEqual(incomplete["comparison_status"], "INCONCLUSIVE")

    def test_adb_probe_is_get_state_only_and_windows_probe_does_not_false_pass_in_docker(self) -> None:
        completed = type("Completed", (), {"returncode": 0, "stdout": b"device\n", "stderr": b""})()
        source = AdbScreenshotFrameSource("emulator-5555")
        with patch("champions_assist.adb_capture.subprocess.run", return_value=completed) as run:
            self.assertEqual(source.connection_status().status, "ADB_DEVICE_READY")
        command = run.call_args.args[0]
        self.assertEqual(command[-1], "get-state")
        self.assertNotIn("input", command)
        custom_path_source = create_live_frame_source(
            LiveCaptureBackend.ADB_SCREENSHOT,
            adb_serial="emulator-5555",
            adb_path="C:/trusted/HD-Adb.exe",
        )
        self.assertEqual(custom_path_source.adb_path, "C:/trusted/HD-Adb.exe")
        output = StringIO()
        with redirect_stdout(output):
            code = main(
                [
                    "capture-probe",
                    "--backend",
                    "WINDOWS_NATIVE",
                    "--confirm-read-only-capture",
                    "--window-title",
                    "BlueStacks",
                    "--frames",
                    "1",
                    "--duration-ms",
                    "1",
                    "--retry-delay-ms",
                    "0",
                ]
            )
        report = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "CAPTURE_UNAVAILABLE")
        self.assertFalse(report["real_game_validated"])

    def test_capture_probe_records_image_size_client_pixels_and_win32_dpi_without_claiming_game_content(self) -> None:
        provider = _GeometryProvider(
            [WindowGeometry("AVAILABLE", title="BlueStacks App Player", hwnd=1, client_bbox=(10, 20, 110, 220), dpi=144)]
        )
        source = WindowsNativeFrameSource(
            window_title="BlueStacks",
            max_frames=1,
            geometry_provider=provider,
            image_grabber=lambda _bbox: Image.fromarray(np.zeros((100, 50, 3), dtype=np.uint8)),
            is_windows=False,
        )
        args = type(
            "CaptureArgs",
            (),
            {
                "backend": "WINDOWS_NATIVE",
                "confirm_read_only_capture": True,
                "adb_serial": None,
                "adb_path": "adb",
                "windows_bbox": None,
                "window_title": "BlueStacks",
                "expected_fps": None,
                "retry_delay_ms": 0,
                "frames": 1,
                "duration_ms": 100,
                "output": None,
                "save_first_frame": None,
            },
        )()
        with patch("champions_assist.validation.create_live_frame_source", return_value=source):
            report = run_capture_probe(args)
        measurement = report["frame_measurement"]
        self.assertEqual(report["status"], "CAPTURED_READ_ONLY_UNVERIFIED_CONTENT")
        self.assertEqual(measurement["image_size_px"]["unique_values"], [[50, 100]])
        self.assertEqual(measurement["window_client_size_screen_px"]["unique_values"], [[100, 200]])
        self.assertEqual(measurement["image_to_client_pixel_scale"]["x"]["p50"], 0.5)
        self.assertEqual(measurement["win32_window_dpi"]["unique_values"], [144])
        self.assertEqual(measurement["win32_window_dpi"]["scale_relative_to_96_dpi"]["p50"], 1.5)
        self.assertIsInstance(report["capture_timing"]["probe_elapsed_ms"], int)
        self.assertIsNotNone(report["capture_timing"]["first_frame_acquisition_ms"])
        self.assertFalse(report["real_game_validated"])

    def test_capture_probe_saves_only_the_first_frame_to_a_new_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "first.png"
            args = type(
                "CaptureArgs",
                (),
                {
                    "backend": "WINDOWS_NATIVE",
                    "confirm_read_only_capture": True,
                    "adb_serial": None,
                    "adb_path": "adb",
                    "windows_bbox": None,
                    "window_title": "BlueStacks",
                    "expected_fps": None,
                    "retry_delay_ms": 0,
                    "frames": 1,
                    "duration_ms": 100,
                    "output": None,
                    "save_first_frame": output_path,
                },
            )()
            with patch("champions_assist.validation.create_live_frame_source", return_value=SyntheticFrameSource((test_frame("saved"),))):
                report = run_capture_probe(args)
            self.assertTrue(output_path.exists())
            self.assertEqual(report["saved_first_frame"]["path"], str(output_path))
            self.assertEqual(report["saved_first_frame"]["image_size_px"], [4, 4])

    def test_windows_probe_refuses_untargeted_desktop_capture(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            with self.assertRaisesRegex(ValueError, "--window-title"):
                main(["capture-probe", "--backend", "WINDOWS_NATIVE", "--confirm-read-only-capture"])

    def test_desktop_observer_bridge_only_refreshes_and_records_gui_latency(self) -> None:
        from champions_assist.desktop import DesktopObserverBridge

        controller = _DesktopControllerProbe()
        ledger = LatencyLedger()
        ledger.captured("frame-1", 100)
        bridge = DesktopObserverBridge(controller, latency_ledger=ledger)  # type: ignore[arg-type]
        bridge.publish_observation("frame-1")
        self.assertEqual(controller.refresh_count, 1)
        self.assertEqual(ledger.as_dict()["capture_to_gui"]["max_ms"], 30.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
