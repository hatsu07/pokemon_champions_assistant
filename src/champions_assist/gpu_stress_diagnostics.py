"""Opt-in offline DirectML stress diagnosis for the Windows frozen executable.

Four isolated cases: recognizer alone, screen classifier alone, both serial,
both parallel. Never reads game screenshots or calls an API. The supervisor
outlives a crashing native child and records exit code and last checkpoint.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from threading import Barrier, BrokenBarrierError, Lock, Thread
from time import perf_counter
from typing import Callable

MODES = ("rec", "classifier", "serial", "parallel")
WINDOWS_ACCESS_VIOLATION = 0xC0000005
_SAFE_ERROR = re.compile(r"^[A-Z0-9_]{1,100}$")


class DiagnosticFailure(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and _SAFE_ERROR.fullmatch(code) else "UNSPECIFIED"



def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _code(value: object) -> str:
    name = type(value).__name__
    return name if name.isidentifier() else "UNKNOWN"


class Checkpoints:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8", buffering=1)
        self._lock = Lock()

    def add(self, event: str, **details) -> None:
        # Callers only supply predefined stage names, indices and provider
        # codes; never exception messages or model paths.
        row = {"utc": _utc(), "event": event, **details}
        with self._lock:
            self._file.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._file.flush()
            os.fsync(self._file.fileno())

    def close(self) -> None:
        self._file.close()


def _choose_screen_model(override: str | None, app_root: Path):
    """Mirror the GUI's persisted model choice; do not invent trained weights."""
    from .screen_classifier import MODEL_FILE, MODEL_DIR
    if override:
        path = Path(override).expanduser()
        # Supplying a model explicitly is not permission to waive validation.
        return path, False
    choice = app_root / "screen_classifier_choice.json"
    if choice.is_file():
        data = json.loads(choice.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("SCREEN_CHOICE_INVALID")
        if isinstance(data.get("model_path"), str):
            return Path(data["model_path"]), data.get("trial_opt_in") is True
        if isinstance(data.get("directory"), str):
            return Path(data["directory"]) / MODEL_FILE, False
        raise ValueError("SCREEN_CHOICE_INVALID")
    return MODEL_DIR / MODEL_FILE, False


def _create_screen(model_file: Path, allow_trial: bool, gpu_id: int):
    from .screen_classifier import load_bundled_classifier
    classifier, status = load_bundled_classifier(
        model_path=model_file, device="directml", gpu_id=gpu_id,
        allow_unvalidated_for_trial=allow_trial,
    )
    if classifier is None:
        raise DiagnosticFailure("SCREEN_MODEL_NOT_READY_" + status)
    if classifier.provider != "DmlExecutionProvider":
        raise DiagnosticFailure("SCREEN_NOT_ON_DIRECTML")
    import numpy as np
    frame = np.full((240, 400, 3), 127, np.uint8)
    def run():
        prediction = classifier.predict(frame)
        if prediction.source != "ONNX_GPU_DML":
            raise RuntimeError("SCREEN_PROVIDER_CHANGED")
    return run


def _create_rec(gpu_id: int):
    import onnxruntime as ort
    import numpy as np
    from .ocr_onnx import (_REC_FILE, _verify_bundle, _new_session,
                           _rec_tensor, bundled_model_directory)
    folder = bundled_model_directory()
    _verify_bundle(folder)
    session, provider = _new_session(
        folder / _REC_FILE, factory=ort.InferenceSession,
        available=set(ort.get_available_providers()), device="directml",
        gpu_id=gpu_id,
    )
    if provider != "DmlExecutionProvider":
        raise RuntimeError("OCR_REC_NOT_ON_DIRECTML")
    synthetic_bgr = np.full((64, 128, 3), 255, dtype=np.uint8)
    tensor = _rec_tensor(synthetic_bgr)
    input_name = session.get_inputs()[0].name
    def run():
        output = session.run(None, {input_name: tensor})
        if not output or np.asarray(output[0]).ndim != 3:
            raise RuntimeError("OCR_REC_OUTPUT_INVALID")
    return run


def child(mode: str, iterations: int, gpu_id: int, progress: Path,
          model: str | None, trial: bool, app_root: Path) -> int:
    """This process MAY crash inside a native DLL; supervisor remains alive."""
    if mode not in MODES or not (1 <= iterations <= 1000) or not (0 <= gpu_id <= 7):
        return 2
    checkpoint = Checkpoints(progress)
    try:
        checkpoint.add("CHILD_STARTED", mode=mode, gpu_id=gpu_id)
        screen = rec = None
        if mode in ("rec", "serial", "parallel"):
            checkpoint.add("REC_SESSION_INIT_BEGIN")
            rec = _create_rec(gpu_id)
            checkpoint.add("REC_SESSION_INIT_COMPLETE", provider="DmlExecutionProvider")
        if mode in ("classifier", "serial", "parallel"):
            checkpoint.add("SCREEN_SESSION_INIT_BEGIN")
            model_path, allow_trial = _choose_screen_model(model, app_root)
            from hashlib import sha256
            checkpoint.add("SCREEN_MODEL_ID",
                           model_sha256=sha256(model_path.read_bytes()).hexdigest(),
                           trial_opt_in=bool(trial or allow_trial))
            screen = _create_screen(model_path, trial or allow_trial, gpu_id)
            checkpoint.add("SCREEN_SESSION_INIT_COMPLETE", provider="DmlExecutionProvider")
        checkpoint.add("SESSIONS_READY")
        for iteration in range(1, iterations + 1):
            begin = perf_counter()
            if mode == "parallel":
                # Distinct sessions, distinct threads; never simultaneous
                # Run() on the SAME session.
                barrier = Barrier(2)
                failures = []
                def worker(name: str, action: Callable):
                    try:
                        barrier.wait(timeout=30)
                        checkpoint.add("RUN_BEGIN", iteration=iteration, component=name)
                        # Confirm BOTH tasks reach the call boundary; without
                        # this, checkpoint fsync could serialize the runs.
                        barrier.wait(timeout=30)
                        action()
                        checkpoint.add("RUN_COMPLETE", iteration=iteration, component=name)
                    except Exception as exc:
                        failures.append(_code(exc))
                        checkpoint.add("RUN_ERROR", iteration=iteration,
                                       component=name, error_type=_code(exc),
                                       error_code=_error_code(exc))
                first = Thread(target=worker, args=("rec", rec), name="stress-rec")
                second = Thread(target=worker, args=("screen", screen), name="stress-screen")
                first.start()
                second.start()
                first.join(timeout=120)
                second.join(timeout=120)
                if first.is_alive() or second.is_alive():
                    checkpoint.add("PARALLEL_INFERENCE_TIMEOUT", iteration=iteration)
                    return 4
                if failures:
                    checkpoint.add("PARALLEL_INFERENCE_FAILED", iteration=iteration)
                    return 3
            else:
                actions = ([("rec", rec)] if mode == "rec" else
                           [("screen", screen)] if mode == "classifier" else
                           [("rec", rec), ("screen", screen)])
                for name, action in actions:
                    checkpoint.add("RUN_BEGIN", iteration=iteration, component=name)
                    try:
                        action()
                    except Exception as exc:
                        checkpoint.add("RUN_ERROR", iteration=iteration,
                                       component=name, error_type=_code(exc),
                                       error_code=_error_code(exc))
                        return 3
                    checkpoint.add("RUN_COMPLETE", iteration=iteration, component=name)
            checkpoint.add("ITERATION_COMPLETE", iteration=iteration,
                           elapsed_ms=round((perf_counter() - begin) * 1000, 3))
        checkpoint.add("CHILD_SUCCESS", iterations=iterations)
        return 0
    except Exception as exc:
        checkpoint.add("CHILD_ERROR", error_type=_code(exc),
                       error_code=_error_code(exc))
        return 3
    finally:
        checkpoint.close()


def _exit_status(code: int | None) -> str:
    if code is None:
        return "TIMEOUT"
    unsigned = code & 0xFFFFFFFF
    if unsigned == WINDOWS_ACCESS_VIOLATION:
        return "ACCESS_VIOLATION"
    return "SUCCESS" if code == 0 else "ERROR"


def _last_checkpoint(path: Path) -> dict | None:
    if not path.is_file():
        return None
    last = None
    with path.open("r", encoding="utf-8") as source:
        for line in source:
            try:
                last = json.loads(line)
            except ValueError:
                continue
    return last


def supervisor(executable: str, *, modes: tuple[str, ...], iterations: int,
               trials: int, gpu_id: int, app_root: Path, model: str | None = None,
               runner=None, timeout: int = 180) -> tuple[Path, dict]:
    """Isolate each case in a new EXE; never infer success from missing logs."""
    if (not modes or any(m not in MODES for m in modes)
            or not (1 <= iterations <= 1000) or not (1 <= trials <= 20)
            or not (0 <= gpu_id <= 7)):
        raise ValueError("INVALID_GPU_DIAGNOSTIC_OPTIONS")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = app_root / "diagnostics" / ("gpu-stress-" + stamp)
    output.mkdir(parents=True, exist_ok=False)
    try:
        model_path, _ = _choose_screen_model(model, app_root)
        screen_model_available = model_path.is_file()
    except (OSError, ValueError, TypeError):
        screen_model_available = False
    report = {"schema": 1, "status": "RUNNING", "gpu_id": gpu_id,
              "modes": list(modes), "iterations": iterations, "trials": trials,
              "isolated_processes": True, "network": "NOT_USED",
              "game_capture": "NOT_USED",
              "screen_model_available": screen_model_available, "cases": []}
    run_func = runner if runner is not None else subprocess.run
    def persist():
        tmp = output / "summary.tmp"
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(output / "summary.json")
    persist()
    for mode in modes:
        for trial in range(1, trials + 1):
            progress = output / f"{mode}-{trial:02d}.jsonl"
            command = [
                executable, "--gpu-diagnostic-child", mode,
                "--gpu-diagnostic-progress", str(progress),
                "--gpu-id", str(gpu_id), "--iterations", str(iterations),
            ]
            if model:
                command += ["--screen-model", model]
            started = perf_counter()
            case = {"mode": mode, "trial": trial, "status": "RUNNING",
                    "progress": progress.name}
            report["cases"].append(case)
            if mode != "rec" and not screen_model_available:
                case["status"] = "SKIPPED_NO_SCREEN_MODEL"
                persist()
                continue
            persist()
            try:
                completed = run_func(command, timeout=timeout, check=False)
                code = completed.returncode
            except subprocess.TimeoutExpired:
                code = None
            except OSError as exc:
                code = 3
                case["launch_error_type"] = _code(exc)
            case["exit_code_unsigned"] = None if code is None else code & 0xFFFFFFFF
            case["status"] = _exit_status(code)
            case["elapsed_ms"] = round((perf_counter() - started) * 1000)
            case["last_checkpoint"] = _last_checkpoint(progress)
            persist()
    report["status"] = "COMPLETE"
    report["success_count"] = sum(x["status"] == "SUCCESS" for x in report["cases"])
    report["crash_count"] = sum(x["status"] == "ACCESS_VIOLATION" for x in report["cases"])
    report["skipped_count"] = sum(x["status"].startswith("SKIPPED_") for x in report["cases"])
    persist()
    return output, report


def smoke(report_file: Path) -> int:
    """Frozen CLI smoke does not need a GPU, trained classifier or OCR session."""
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps({
        "status": "GPU_DIAGNOSTIC_CLI_SMOKE_OK",
        "modes": list(MODES), "child_isolation": True,
        "network": "NOT_USED", "gpu_inference_tested": False,
    }), encoding="utf-8")
    return 0
