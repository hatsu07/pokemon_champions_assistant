"""Isolated, explicitly triggered test of the real postmatch review pipeline.

No game capture and no production match history/learning files are touched.
The *simulated* match end deliberately contains no gameplay assertions. Both
test modes call the production record_finished() and request_review() paths:
offline uses a protocol fake, while API mode uses the signed-in ChatGPT model.
"""
from __future__ import annotations

from contextlib import closing

import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Callable
from types import SimpleNamespace
from uuid import uuid4

from .matchup_learning_store import MatchupLearningStore
from .postmatch_learning import REVIEW_SCHEMA, request_review

SOURCE = "SYNTHETIC_TEST_POSTMATCH_NOT_GAME_EVIDENCE"


def _offline_client_factory(match_id: str):
    """Minimal streaming protocol stand-in, not simulated game analysis."""
    reply = json.dumps({
        "schema": REVIEW_SCHEMA,
        "match_id": match_id,
        "findings": [],
        "next_match_notes": "模擬終了データのみ。実戦の戦術評価は実施していません。",
    }, ensure_ascii=False)

    class OfflineStream:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def __iter__(self):
            yield SimpleNamespace(type="response.output_text.delta", delta=reply)
            yield SimpleNamespace(type="response.completed")

    class OfflineResponses:
        def create(self, **_):
            return OfflineStream()

    class OfflineClient:
        def __init__(self, **_):
            self.responses = OfflineResponses()

    return OfflineClient


def run_postmatch_review_e2e(*, model: str, reasoning_effort: str = "auto",
                             access_token: str | None = None,
                             use_api: bool = False, client_factory=None,
                             on_stage: Callable[[str], None] | None = None) -> dict:
    """Return a stage-by-stage, non-sensitive report after storage read-back.

    A new temporary SQLite database is used for every test. The finished
    match is synthetic and is NOT a test of real result recognition, OCR,
    capture, strategic accuracy or the main-window signal connection.
    """
    start = perf_counter()
    match_id = "review-test-" + uuid4().hex
    report = {
        "status": "FAILED",
        "stage": "NOT_STARTED",
        "mode": "API" if use_api else "OFFLINE",
        "match_id": match_id,
        "source": SOURCE,
        "production_db_modified": False,
        "game_capture": "NOT_USED",
        "synthetic_match_result": True,
        "api_used": bool(use_api),
        "record_saved": False,
        "review_saved": False,
        "review_status": "NOT_STARTED",
        "findings": 0,
        "next_match_notes": "",
    }
    def stage(name: str) -> None:
        report["stage"] = name
        if on_stage is not None:
            on_stage(name)

    try:
        if use_api and (not access_token or not model):
            stage("AUTH_REQUIRED")
            return report
        with TemporaryDirectory(prefix="champions-postmatch-e2e-") as temp:
            store = MatchupLearningStore(Path(temp))
            simulated = {
                "phase": "ENDED", "result": "WIN",
                "opponent_six": [], "selected_three": [], "lead": None,
                "history": [], "battle_evidence": {}, "observed_hp": {},
            }
            stage("RECORD_END")
            inserted = store.record_finished(
                match_id=match_id, team_id=None, state=simulated, source=SOURCE)
            with closing(sqlite3.connect(store.db_path)) as db:
                saved = db.execute(
                    "SELECT result,source FROM match_history WHERE match_id=?",
                    (match_id,)).fetchone()
            if (not inserted or store.pending_match(match_id) is None
                    or saved != ("WIN", SOURCE)):
                raise RuntimeError("TEST_MATCH_RECORD_NOT_PENDING")
            report["record_saved"] = True

            stage("REVIEW_REQUEST")
            factory = (client_factory if client_factory is not None else
                       None if use_api else _offline_client_factory(match_id))
            result = request_review(
                data_root=temp, match_id=match_id,
                access_token=access_token or "OFFLINE_TEST_NO_AUTH",
                model=model if use_api else "offline-test-model",
                reasoning_effort=reasoning_effort, client_factory=factory)
            report["review_status"] = str(result.get("status", "UNKNOWN"))
            if result.get("status") != "STORED_UNVERIFIED_REVIEW":
                raise RuntimeError("TEST_REVIEW_NOT_STORED")

            stage("READBACK")
            with closing(sqlite3.connect(store.db_path)) as db:
                row = db.execute(
                    "SELECT review_json,status FROM postmatch_reviews WHERE match_id=?",
                    (match_id,)).fetchone()
            if row is None or row[1] != "AI_HYPOTHESIS_UNVERIFIED":
                raise RuntimeError("TEST_REVIEW_MISSING_IN_SQLITE")
            decoded = json.loads(row[0])
            if (decoded.get("schema") != REVIEW_SCHEMA
                    or decoded.get("match_id") != match_id
                    or not isinstance(decoded.get("findings"), list)
                    or store.pending_match(match_id) is not None):
                raise RuntimeError("TEST_REVIEW_READBACK_MISMATCH")
            stage("COMPLETE")
            report.update(
                status="PASS", review_saved=True,
                findings=len(decoded["findings"]),
                next_match_notes=str(decoded.get("next_match_notes", ""))[:360],
            )
            return report
    except Exception as exc:
        # No credentials, stack traces, source data, or raw API errors in GUI.
        stage("FAILED")
        from .team_ai_analysis import safe_ai_error
        report["error_type"] = type(exc).__name__
        report["error"] = safe_ai_error(exc)
        return report
    finally:
        report["elapsed_ms"] = round((perf_counter() - start) * 1000)
