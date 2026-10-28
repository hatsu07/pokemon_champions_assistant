"""Phase 1 only: two *processes* perform overlapping DirectML inference.

This module is an opt-in offline diagnostic. It does not run during battle,
modify the OCR worker, capture screens, or access any network. Each child
owns exactly one ONNX Runtime session, initialized inside that child.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
from time import monotonic, perf_counter, perf_counter_ns, sleep
from typing import Callable

from .gpu_stress_diagnostics import (
    Checkpoints, _choose_screen_model, _code, _create_rec, _create_screen,
    _error_code, _exit_status, _last_checkpoint,
)

ROLES = ("rec", "screen")
MODE = "multiprocess"
POLL_SECONDS = 0.002


def _signal(folder: Path, kind: str, role: str, iteration: int) -> Path:
    # All components are trusted fixed tokens, never user-supplied filenames.
    return folder / f"{kind}-{role}-{iteration:04d}"


def _wait_for_go(folder: Path, iteration: int, *, deadline_seconds: float) -> None:
    target = folder / f"go-{iteration:04d}"
    deadline = monotonic() + deadline_seconds
    while monotonic() < deadline:
        if target.exists():
            return
        if (folder / "abort").exists():
            raise RuntimeError("MULTIPROCESS_SUPERVISOR_ABORTED")
        sleep(POLL_SECONDS)
    raise TimeoutError("MULTIPROCESS_GO_TIMEOUT")


def process_child(role: str, iterations: int, gpu_id: int, progress: Path,
                  sync_dir: Path, model: str | None, app_root: Path,
                  action_factory: Callable | None = None) -> int:
    """Actual role process; caller is responsible for creating sync_dir."""
    if (role not in ROLES or not 1 <= iterations <= 1000
            or not 0 <= gpu_id <= 7 or not sync_dir.is_dir()):
        return 2
    checkpoints = Checkpoints(progress)
    try:
        checkpoints.add("CHILD_STARTED", mode=MODE, role=role, gpu_id=gpu_id)
        if action_factory is not None:
            # Dependency-injected tests never create ONNX sessions.
            action = action_factory(role, gpu_id, model, app_root)
        elif role == "rec":
            checkpoints.add("REC_SESSION_INIT_BEGIN")
            action = _create_rec(gpu_id)
            checkpoints.add("REC_SESSION_INIT_COMPLETE",
                            provider="DmlExecutionProvider")
        else:
            checkpoints.add("SCREEN_SESSION_INIT_BEGIN")
            path, consent = _choose_screen_model(model, app_root)
            checkpoints.add("SCREEN_MODEL_ID",
                            model_sha256=sha256(path.read_bytes()).hexdigest(),
                            trial_opt_in=consent)
            action = _create_screen(path, consent, gpu_id)
            checkpoints.add("SCREEN_SESSION_INIT_COMPLETE",
                            provider="DmlExecutionProvider")
        checkpoints.add("SESSION_READY", role=role)
        _signal(sync_dir, "ready", role, 0).touch()
        for iteration in range(1, iterations + 1):
            checkpoints.add("RUN_ARMED", iteration=iteration, component=role)
            _signal(sync_dir, "armed", role, iteration).touch()
            _wait_for_go(sync_dir, iteration, deadline_seconds=60)
            start = perf_counter_ns()
            try:
                action()
            except Exception as exc:
                checkpoints.add("RUN_ERROR", iteration=iteration, component=role,
                                error_type=_code(exc), error_code=_error_code(exc))
                return 3
            end = perf_counter_ns()
            checkpoints.add("RUN_COMPLETE", iteration=iteration, component=role,
                            run_start_ns=start, run_end_ns=end,
                            elapsed_ms=round((end - start) / 1_000_000, 3))
            _signal(sync_dir, "done", role, iteration).touch()
        checkpoints.add("CHILD_SUCCESS", iterations=iterations, role=role)
        return 0
    except Exception as exc:
        checkpoints.add("CHILD_ERROR", error_type=_code(exc),
                        error_code=_error_code(exc), role=role)
        return 3
    finally:
        checkpoints.close()


def _completed_intervals(progress: Path) -> dict[int, tuple[int, int]]:
    intervals = {}
    if not progress.is_file():
        return intervals
    for line in progress.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
            if entry.get("event") == "RUN_COMPLETE":
                start, end = entry["run_start_ns"], entry["run_end_ns"]
                if isinstance(start, int) and isinstance(end, int) and end >= start:
                    intervals[entry["iteration"]] = (start, end)
        except (ValueError, KeyError, TypeError):
            continue
    return intervals


def _overlap(rec: Path, screen: Path) -> tuple[int, float, int]:
    a, b = _completed_intervals(rec), _completed_intervals(screen)
    overlaps = []
    for iteration in a.keys() & b.keys():
        start = max(a[iteration][0], b[iteration][0])
        end = min(a[iteration][1], b[iteration][1])
        overlaps.append(max(0, end - start))
    return sum(n > 0 for n in overlaps), round(sum(overlaps) / 1_000_000, 3), len(overlaps)


def run_case(executable: str, *, output: Path, trial: int, iterations: int,
             gpu_id: int, app_root: Path, model: str | None = None,
             timeout: float = 180, command_factory: Callable | None = None,
             popen_factory: Callable = subprocess.Popen) -> dict:
    """Supervise both EXEs, require actual measured Run() overlap to pass.

    If either child fails, terminate the other. Never leave orphan GPU work
    after a native crash, timeout, failed init, or parent exception.
    """
    if not (1 <= trial <= 20 and 1 <= iterations <= 1000 and 0 <= gpu_id <= 7
            and timeout > 0):
        raise ValueError("INVALID_MULTIPROCESS_DIAGNOSTIC_OPTIONS")
    sync = output / f".multiprocess-{trial:02d}-sync"
    sync.mkdir(parents=True, exist_ok=False)
    paths = {role: output / f"multiprocess-{trial:02d}-{role}.jsonl"
             for role in ROLES}
    case = {"mode": MODE, "trial": trial, "status": "RUNNING",
            "progress": {role: path.name for role, path in paths.items()},
            "workers": {}, "overlap_iterations": 0,
            "overlap_ms": 0.0, "measured_iterations": 0}
    processes = {}
    started = perf_counter()
    deadline = monotonic() + timeout
    timed_out = False
    launch_error = None
    try:
        for role in ROLES:
            command = (
                command_factory(role, paths[role], sync) if command_factory else
                [executable, "--gpu-diagnostic-process-child", role,
                 "--gpu-diagnostic-progress", str(paths[role]),
                 "--gpu-diagnostic-sync-dir", str(sync),
                 "--gpu-id", str(gpu_id), "--iterations", str(iterations)]
                + (["--screen-model", model] if model else [])
            )
            processes[role] = popen_factory(command, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
        # Each iteration is coordinated AFTER both processes have independently
        # loaded their own sessions. The single go marker releases both.
        for iteration in range(1, iterations + 1):
            targets = [_signal(sync, "armed", role, iteration) for role in ROLES]
            while not all(target.exists() for target in targets):
                if any(proc.poll() is not None for proc in processes.values()):
                    break
                if monotonic() >= deadline:
                    timed_out = True
                    break
                sleep(POLL_SECONDS)
            if timed_out or not all(target.exists() for target in targets):
                break
            (sync / f"go-{iteration:04d}").touch()
            done = [_signal(sync, "done", role, iteration) for role in ROLES]
            while not all(path.exists() for path in done):
                # One fast child may finish its final iteration and exit 0
                # while the other is still running: that is not a failure.
                if any(proc.poll() is not None and proc.returncode != 0
                       for proc in processes.values()):
                    break
                if monotonic() >= deadline:
                    timed_out = True
                    break
                sleep(POLL_SECONDS)
            if timed_out or not all(path.exists() for path in done):
                break
        else:
            for proc in processes.values():
                remaining = max(0.1, deadline - monotonic())
                try:
                    proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    timed_out = True
    except OSError as exc:
        launch_error = _code(exc)
    finally:
        # Release anyone waiting for the other process, then reap all children.
        (sync / "abort").touch()
        for proc in processes.values():
            if proc.poll() is None:
                proc.terminate()
        for proc in processes.values():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        for role in ROLES:
            proc = processes.get(role)
            code = proc.returncode if proc is not None else None
            case["workers"][role] = {
                "status": _exit_status(code) if code is not None else "NOT_LAUNCHED",
                "exit_code_unsigned": code & 0xFFFFFFFF if code is not None else None,
                "last_checkpoint": _last_checkpoint(paths[role]),
                "progress": paths[role].name,
            }
        count, overlap_ms, measured = _overlap(paths["rec"], paths["screen"])
        case.update(overlap_iterations=count, overlap_ms=overlap_ms,
                    measured_iterations=measured,
                    elapsed_ms=round((perf_counter() - started) * 1000))
        statuses = [v["status"] for v in case["workers"].values()]
        if launch_error:
            case["launch_error_type"] = launch_error
        if "ACCESS_VIOLATION" in statuses:
            case["status"] = "ACCESS_VIOLATION"
        elif timed_out:
            case["status"] = "TIMEOUT"
        elif launch_error or statuses != ["SUCCESS", "SUCCESS"]:
            case["status"] = "ERROR"
        elif measured != iterations:
            case["status"] = "INCOMPLETE"
        elif count == 0:
            case["status"] = "NO_MEASURED_OVERLAP"
        else:
            case["status"] = "SUCCESS"
        shutil.rmtree(sync)
    return case
