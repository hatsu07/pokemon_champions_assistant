"""Explicitly opt-in local JSONL worker; no GUI/SQLite/live-path import.

Requests are serialized; stale or out-of-order replies can never be adopted.
The worker cannot be restarted mid-match by this adapter. The caller must
freeze all four masters/rules/engine before creating a match-scoped instance.
"""
from __future__ import annotations

import json
from pathlib import Path
import queue
import subprocess
import threading
import time
from typing import Any, Mapping

from .models import (
    CapabilityStatus, DecisionWindow, PROTOCOL_VERSION,
    SimulationResult, canonical_json, fingerprint,
)

_MAX_BYTES = 1024 * 1024


class WorkerUnavailable(RuntimeError):
    pass


class LocalSimulatorWorker:
    def __init__(self, *, node_executable: str, worker_script: str,
                 showdown_root: str, simulator_sha: str) -> None:
        self._node = Path(node_executable)
        self._script = Path(worker_script)
        self._showdown = Path(showdown_root)
        self._sha = simulator_sha
        self._proc: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()
        self._stderr_tail: list[str] = []
        self._failed = False

    def start(self, *, timeout_seconds: float = 2.0) -> None:
        import os
        if self._proc or self._failed:
            raise WorkerUnavailable("worker already used or unavailable")
        if not (self._node.is_file() and self._script.is_file()
                and self._showdown.is_dir()):
            raise WorkerUnavailable("runtime not bundled or missing")
        env = {**os.environ, "SHOWDOWN_ROOT": str(self._showdown),
               "SHOWDOWN_EXPECTED_SHA": self._sha}
        try:
            self._proc = subprocess.Popen(
                [str(self._node), str(self._script)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, encoding="utf-8",
                errors="replace", bufsize=1, env=env)
        except OSError as exc:
            self._failed = True
            raise WorkerUnavailable("failed to start local worker") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        request = {
            "protocol": PROTOCOL_VERSION, "op": "HELLO",
            "request_id": "startup", "identity": {}, "payload": {},
        }
        try:
            reply = self._exchange(request, time.monotonic()+timeout_seconds)
            if reply.get("capability_status") != "PARTIAL" or not reply.get("ok"):
                raise WorkerUnavailable("pinned engine unavailable: "
                                        + str(reply.get("reason", "UNKNOWN")))
        except Exception:
            self.close()
            raise

    def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            for line in self._proc.stdout:
                if len(line.encode("utf-8")) > _MAX_BYTES:
                    self._responses.put(None)
                    break
                self._responses.put(line)
        finally:
            self._responses.put(None)

    def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        for line in self._proc.stderr:
            self._stderr_tail.append(line[:200].strip())
            del self._stderr_tail[:-20]

    def _exchange(self, request: Mapping[str, Any],
                  deadline_monotonic: float) -> dict[str, Any]:
        with self._lock:
            if self._failed or not self._proc or self._proc.poll() is not None:
                raise WorkerUnavailable("local worker not running")
            data = canonical_json(dict(request)) + "\n"
            if len(data.encode("utf-8")) > _MAX_BYTES:
                raise WorkerUnavailable("request too large")
            assert self._proc.stdin
            try:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except (OSError, BrokenPipeError) as exc:
                self._failed = True
                raise WorkerUnavailable("worker channel lost") from exc
            mismatches = 0
            while True:
                remaining = deadline_monotonic-time.monotonic()
                if remaining <= 0:
                    self._failed = True
                    raise WorkerUnavailable("worker timed out")
                try:
                    line = self._responses.get(timeout=remaining)
                except queue.Empty as exc:
                    self._failed = True
                    raise WorkerUnavailable("worker timed out") from exc
                if line is None:
                    self._failed = True
                    raise WorkerUnavailable("worker exited or oversized response")
                try:
                    resp = json.loads(line)
                except (json.JSONDecodeError, TypeError) as exc:
                    self._failed = True
                    raise WorkerUnavailable("malformed worker message") from exc
                if not isinstance(resp, dict):
                    self._failed = True
                    raise WorkerUnavailable("invalid worker response")
                if (resp.get("request_id") != request.get("request_id")
                        or resp.get("identity") != request.get("identity")
                        or resp.get("protocol") != PROTOCOL_VERSION
                        or resp.get("op") != request.get("op")):
                    mismatches += 1
                    if mismatches > 32:
                        self._failed = True
                        raise WorkerUnavailable("excessive stale replies")
                    continue
                return resp

    def simulate(self, window: DecisionWindow, battle_json: Mapping[str, Any],
                 own_choice: str, opponent_choice: str,
                 *, hypothesis_id: str = "") -> SimulationResult:
        """Only FULL_REPLAY_SYNTHETIC states. Always PARTIAL or UNSUPPORTED.

        No request or result is written to the evidence ledger.  Unknown
        real-game intermediate states cannot be reconstructed here.
        """
        if window.simulator_sha != self._sha:
            raise WorkerUnavailable("engine SHA changed within match")
        payload = {"source": "FULL_REPLAY_SYNTHETIC",
                   "battle": dict(battle_json), "p1_choice": own_choice,
                   "p2_choice": opponent_choice,
                   "hypothesis_id": hypothesis_id}
        snap_sha = fingerprint(payload)
        request = {"protocol": PROTOCOL_VERSION, "op": "SIMULATE",
                   "request_id": window.request_id, "identity": window.identity(),
                   "snapshot_sha256": snap_sha, "payload": payload}
        reply = self._exchange(request, window.deadline_monotonic)
        try:
            status = CapabilityStatus(reply.get("capability_status", "UNKNOWN"))
        except ValueError:
            status = CapabilityStatus.UNKNOWN
        # Even when the worker reports PARTIAL, it never certifies real
        # Champions mechanics or a recommendation.
        if status == CapabilityStatus.CONFIRMED:
            status = CapabilityStatus.UNKNOWN
        return SimulationResult.from_worker(
            window.request_id, window.identity(), snap_sha, hypothesis_id,
            status, reply.get("payload") or {},
            str(reply.get("reason") or ""))

    def close(self) -> None:
        self._failed = True
        proc = self._proc
        self._proc = None
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.communicate(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def __enter__(self) -> "LocalSimulatorWorker":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
