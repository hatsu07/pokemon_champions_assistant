"""Explicitly opt-in local JSONL worker; no GUI/SQLite/live-path import.

Requests are serialized; stale or out-of-order replies can never be adopted.
The worker cannot be restarted mid-match by this adapter. The caller must
freeze all four masters/rules/engine before creating a match-scoped instance.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import json
import hashlib
from pathlib import Path
import queue
import subprocess
import threading
import time
from uuid import uuid4
from typing import Any, Mapping

from .models import (
    CapabilityStatus, FrozenBattleState, PROTOCOL_VERSION,
    SimulationResult, canonical_json, fingerprint,
)
from .legal_actions import generate_candidates, showdown_choice_for_candidate

_MAX_BYTES = 1024 * 1024
_P2_COMMAND = re.compile(r"(?:move [1-4]|switch [1-3])\Z")

@dataclass(frozen=True)
class EngineP2CandidateSet:
    status: str
    reason: str
    commands: tuple[str, ...] = ()
    battle_sha256: str = ""
    request_sha256: str = ""
    scope: str = ""
    # Intentionally not a certificate of exhaustive real-game legality.
    exhaustive_legal_choices_verified: bool = False


def _verified_roster_binding(snapshot: FrozenBattleState, assets: Any) -> list[dict[str, Any]]:
    """Tie Showdown's p1 order and moves to the actual match-preloaded bytes.

    The Champions CSV does not universally contain simulator IDs. Refuse
    simulation until each selected member's *explicit, verified* Showdown IDs
    are present; Japanese move names must never be guessed/romanized here.
    """
    from ..preload import FrozenMatchAssets, REQUIRED_MASTER_FILES, _parse_team

    w = snapshot.window
    if not isinstance(assets, FrozenMatchAssets):
        raise WorkerUnavailable("match-preloaded FrozenMatchAssets required")
    try:
        expected = tuple((key, assets.sha256[key]) for key in REQUIRED_MASTER_FILES)
        if (expected != w.four_master_sha256
                or expected[-1][1] != w.team_sha256
                or any(len(digest) != 64 for _, digest in expected)):
            raise WorkerUnavailable("frozen master or team SHA differs from decision window")
        for name, digest in expected:
            body = assets.contents[name]
            raw = body.encode("utf-8")
            # The match preloader decodes UTF-8-SIG; original bytes may or
            # may not have a BOM. Accept only those two exact encodings.
            if (hashlib.sha256(raw).hexdigest() != digest
                    and hashlib.sha256(bytes.fromhex("efbbbf") + raw).hexdigest() != digest):
                raise WorkerUnavailable("preloaded master bytes do not match SHA manifest")
        # CSV index must be derived from the very same frozen team bytes.
        parsed = _parse_team(assets.contents[REQUIRED_MASTER_FILES[3]])
        if parsed != assets.own_team:
            raise WorkerUnavailable("preloaded team rows differ from frozen CSV")
        state = snapshot.state
        selected, order = state.get("selected_three"), state.get("showdown_party_order")
        if (not isinstance(selected, list) or len(selected) != 3
                or not isinstance(order, list) or len(order) != 3
                or not all(isinstance(name, str) and name for name in order)
                or len(set(order)) != 3 or set(order) != set(selected)):
            raise WorkerUnavailable("current Showdown selected-three order not proven")
        registered = {row["name"]: row for row in parsed}
        if len(registered) != len(parsed):
            raise WorkerUnavailable("ambiguous registered team member names")
        roster = []
        for name in order:
            row = registered.get(name)
            if row is None:
                raise WorkerUnavailable("selected member not in frozen registered team")
            species_id = row.get("showdown_species_id", "")
            moves = []
            if (not isinstance(species_id, str)
                    or not species_id.isascii() or not species_id.isalnum()
                    or species_id != species_id.lower()):
                raise WorkerUnavailable("verified Showdown species ID mapping missing")
            for index in range(1, 5):
                move = row.get(f"move{index}", "")
                move_id = row.get(f"showdown_move{index}_id", "")
                if move:
                    if (not isinstance(move_id, str) or not move_id.isascii()
                            or not move_id.isalnum() or move_id != move_id.lower()):
                        raise WorkerUnavailable("verified Showdown move ID mapping missing")
                    moves.append(move_id)
                elif move_id:
                    raise WorkerUnavailable("unexpected mapped move in empty slot")
            if len(set(moves)) != len(moves) or not moves:
                raise WorkerUnavailable("ambiguous Showdown move slots")
            roster.append({"name": name, "species_id": species_id, "moves": moves})
        if len({r["species_id"] for r in roster}) != len(roster):
            raise WorkerUnavailable("ambiguous duplicate Showdown species mapping")
        if (w.active_pokemon and w.active_pokemon not in order):
            raise WorkerUnavailable("active Pokémon not in frozen Showdown team")
        return roster
    except WorkerUnavailable:
        raise
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise WorkerUnavailable("invalid or incomplete preloaded Showdown binding") from exc



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
            # A child process may remain alive but stop draining stdin.
            # Pipe write/flush can block before the reply deadline is checked.
            # Keep ALL blocking stdin operations in a daemon writer and
            # terminate the worker on a deadline breach; never wait for that
            # writer on the GUI/decision thread.
            assert self._proc.stdin
            if time.monotonic() >= deadline_monotonic:
                self._failed = True
                raise WorkerUnavailable("worker deadline before send")
            sent: queue.Queue[BaseException | None] = queue.Queue(maxsize=1)
            stdin = self._proc.stdin

            def write_request() -> None:
                try:
                    stdin.write(data)
                    stdin.flush()
                    sent.put_nowait(None)
                except (OSError, ValueError, BrokenPipeError) as exc:
                    sent.put_nowait(exc)

            threading.Thread(target=write_request, daemon=True).start()
            try:
                remaining = max(0.0, deadline_monotonic - time.monotonic())
                failure = sent.get(timeout=remaining)
            except queue.Empty as exc:
                self._failed = True
                # Killing the child releases a blocked OS pipe writer.
                try:
                    self._proc.kill()
                except OSError:
                    pass
                raise WorkerUnavailable("worker stdin write timed out") from exc
            if failure is not None:
                self._failed = True
                try:
                    self._proc.kill()
                except OSError:
                    pass
                raise WorkerUnavailable("worker channel lost") from failure
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
                if (resp.get("snapshot_sha256") != request.get("snapshot_sha256")
                        or resp.get("request_id") != request.get("request_id")
                        or resp.get("identity") != request.get("identity")
                        or resp.get("protocol") != PROTOCOL_VERSION
                        or resp.get("op") != request.get("op")
                        or (request.get("op") in ("SIMULATE", "ENUMERATE_P2") and (
                            resp.get("branch_request_id") != request.get("branch_request_id")
                            or resp.get("hypothesis_id") != request["payload"]["hypothesis_id"]
                            or resp.get("branch_input_sha256") != request["payload"]["branch_input_sha256"]
                            or (request.get("op") == "SIMULATE" and
                                resp.get("candidate_action_id") != request["payload"]["candidate_action_id"])))):
                    mismatches += 1
                    if mismatches > 32:
                        self._failed = True
                        raise WorkerUnavailable("excessive stale replies")
                    continue
                return resp

    def simulate(self, snapshot: FrozenBattleState, battle_json: Mapping[str, Any],
                 own_choice: str, opponent_choice: str,
                 *, hypothesis_id: str = "",
                 candidate_action_id: str = "", assets: Any = None) -> SimulationResult:
        """Only FULL_REPLAY_SYNTHETIC states. Always PARTIAL or UNSUPPORTED.

        No request or result is written to the evidence ledger.  Unknown
        real-game intermediate states cannot be reconstructed here.
        """
        window = snapshot.window
        # Never silently bind a branch to a nonexistent hypothesis or a
        # Showdown choice that has no explicit local action identity.
        if not candidate_action_id or candidate_action_id not in snapshot.verified_action_ids:
            raise WorkerUnavailable("branch candidate is not an allowed action")
        expected_roster = _verified_roster_binding(snapshot, assets)
        matching = [a for a in generate_candidates(snapshot, assets)
                    if a.action_id == candidate_action_id]
        if len(matching) != 1:
            raise WorkerUnavailable("branch action is ambiguous or unavailable")
        try:
            expected_choice = showdown_choice_for_candidate(
                matching[0], snapshot, assets)
        except (ValueError, TypeError, KeyError) as exc:
            raise WorkerUnavailable("branch action has no verified Showdown slot") from exc
        if own_choice != expected_choice:
            raise WorkerUnavailable("branch choice does not match candidate action")
        hypothesis_ids = {h.hypothesis_id for h in snapshot.hypotheses}
        if (hypothesis_id not in hypothesis_ids if hypothesis_ids else bool(hypothesis_id)):
            raise WorkerUnavailable("branch hypothesis is not registered")
        if window.simulator_sha != self._sha:
            raise WorkerUnavailable("engine SHA changed within match")
        payload = {"source": "FULL_REPLAY_SYNTHETIC",
                   "battle": dict(battle_json), "p1_choice": own_choice,
                   "p2_choice": opponent_choice,
                   "hypothesis_id": hypothesis_id,
                   "candidate_action_id": candidate_action_id,
                   "expected_p1_roster": expected_roster,
                   "expected_p1_active": window.active_pokemon}
        snap_sha = snapshot.snapshot_sha256
        payload["branch_input_sha256"] = fingerprint(payload)
        # Never reuse a branch token, even for identical replay inputs.
        branch_request_id = uuid4().hex
        request = {"protocol": PROTOCOL_VERSION, "op": "SIMULATE",
                   "request_id": window.request_id, "branch_request_id": branch_request_id,
                   "identity": window.identity(),
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
            reply["request_id"], reply["identity"], reply["snapshot_sha256"],
            reply["hypothesis_id"], status, reply.get("payload") or {},
            str(reply.get("reason") or ""),
            branch_request_id=reply["branch_request_id"],
            branch_input_sha256=reply["branch_input_sha256"],
            candidate_action_id=reply["candidate_action_id"],
            # Capture the actual request only *after* _exchange has matched
            # the worker echo to this branch nonce, hash and action.
            submitted_request=request)

    def enumerate_p2_from_replay(
        self, snapshot: FrozenBattleState, battle_json: Mapping[str, Any],
        *, hypothesis_id: str = "", assets: Any = None,
    ) -> EngineP2CandidateSet:
        """Only for full synthetic/replay Battle bytes, never partial game OCR.

        The worker takes its request from the engine's own p2.activeRequest.
        This wrapper verifies echo identity and returned candidate structure,
        but never claims exhaustive Champions/legal choice coverage.
        """
        window = snapshot.window
        if window.simulator_sha != self._sha:
            raise WorkerUnavailable("engine SHA changed within match")
        roster = _verified_roster_binding(snapshot, assets)
        from .models import joint_hypothesis_ids
        try:
            known = joint_hypothesis_ids(snapshot.hypotheses)
        except ValueError as exc:
            raise WorkerUnavailable("invalid opponent joint hypothesis IDs") from exc
        if hypothesis_id not in known:
            raise WorkerUnavailable("opponent hypothesis is not registered")
        if not isinstance(battle_json, Mapping):
            raise WorkerUnavailable("full synthetic Battle JSON mapping required")
        battle = dict(battle_json)
        nonce = uuid4().hex
        payload = {
            "source": "FULL_REPLAY_SYNTHETIC", "battle": battle,
            "expected_p1_roster": roster,
            "expected_p1_active": window.active_pokemon,
            "hypothesis_id": hypothesis_id,
        }
        payload["branch_input_sha256"] = fingerprint(payload)
        request = {
            "protocol": PROTOCOL_VERSION, "op": "ENUMERATE_P2",
            "request_id": window.request_id,
            "branch_request_id": nonce,
            "identity": window.identity(),
            "snapshot_sha256": snapshot.snapshot_sha256,
            "payload": payload,
        }
        response = self._exchange(request, window.deadline_monotonic)
        if response.get("capability_status") != "PARTIAL" or response.get("ok") is not True:
            return EngineP2CandidateSet("NO_CHOICES", str(response.get("reason") or "ENGINE_P2_UNSUPPORTED"))
        body = response.get("payload")
        if not isinstance(body, dict):
            raise WorkerUnavailable("invalid P2 engine choice payload")
        commands = body.get("commands")
        token = body.get("request_sha256")
        battle_sha = body.get("battle_sha256")
        if (body.get("source") != "FULL_REPLAY_SYNTHETIC"
                or body.get("hypothesis_id") != hypothesis_id
                or body.get("branch_input_sha256") != payload["branch_input_sha256"]
                or body.get("status") != "ENGINE_CHECKED_SIMPLE_CHOICES"
                or body.get("scope") != "PINNED_SHOWDOWN_SIMPLE_SINGLES_ONLY"
                or body.get("exhaustive_legal_choices_verified") is not False
                or not isinstance(commands, list) or not 1 <= len(commands) <= 24
                or any(not isinstance(choice, str) or not _P2_COMMAND.fullmatch(choice)
                       for choice in commands)
                or len(set(commands)) != len(commands)
                or battle_sha != fingerprint(battle)
                or not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{64}", token)):
            raise WorkerUnavailable("P2 engine evidence not verifiably bound")
        return EngineP2CandidateSet(
            "ENGINE_CHECKED_SIMPLE_CHOICES", str(body.get("reason") or ""),
            tuple(commands), battle_sha, token,
            "PINNED_SHOWDOWN_SIMPLE_SINGLES_ONLY",
            False,
        )

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
