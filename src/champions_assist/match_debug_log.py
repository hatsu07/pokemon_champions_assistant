"""Local automatic live-match diagnostics, never raw screenshot/API traffic.

Append-only bounded JSONL, stored in the per-user application directory.
The allowlist intentionally excludes token, image pixels, prompt, HTTP bodies,
raw user-facing error messages and raw team build contents.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from time import monotonic
from uuid import uuid4

SCHEMA = "champions-local-live-debug-v1"
MAX_BYTES = 3 * 1024 * 1024
KEEP_FILES = 20
_ALLOWED_TEXT = frozenset({
    "status", "reason", "phase", "screen", "ui", "observed_ui",
    "request_stage", "screen_label", "screen_label_hint", "transport",
    "screen_label_source", "live_reasoning_effort", "warning",
    "kind", "result", "model", "clock_source", "classifier_source",
    "next_screen", "ocr_status", "ocr_error_code",
})
_ALLOWED_BOOL = frozenset({
    "recommendation_present", "selection_present", "matrix_compatible",
    "matrix_provisional", "superseded", "timer_ocr",
    "gate_dispatched", "late_advice_presented", "late_selection_presented",
})
_ALLOWED_NUMBER = frozenset({
    "elapsed_ms", "capture_and_auth_ms", "at_ms", "ai_request_attempt",
    "image_count", "opponent_recognition_attempts", "step", "preload_count",
    "matchups_evaluated", "consecutive_failures", "queue_elapsed_ms",
    "timeout_budget_ms", "latency_ms",
    "visual_first_attempt_elapsed_ms", "visual_transport_retry_elapsed_ms",
    "visual_image_count",
    "visual_source_width","visual_source_height",
    "visual_crop_x","visual_crop_y","visual_crop_width","visual_crop_height",
    "visual_primary_jpeg_bytes","visual_total_jpeg_bytes",
    "visual_total_data_uri_bytes","visual_retry_total_jpeg_bytes",
    "visual_retry_total_data_uri_bytes","visual_retry_image_count",
    "comparison_elapsed_ms","comparison_percent","correct_slots",
    # Opaque chronology IDs; never hashes, pixels, OCR strings or requests.
    "frame_seq","turn_epoch","active_turn_epoch",
    "confidence","margin","ocr_confidence",
    "classifier_frames","capture_to_enqueue_ms",
    "pending_count","pending_overflow","accepted","raw_count",
    "unfinished","overflow","observed_candidates",
    "ocr_jobs_enqueued","ocr_jobs_completed",
    "ocr_rois_attempted","ocr_rois_parsed",
    "ocr_results_dropped","ocr_errors","ocr_skipped",
    "ocr_candidates_sent","ocr_queue_pending","ocr_results_pending",
    "ocr_last_latency_ms","ocr_max_latency_ms",
    "ocr_duplicates","ocr_conflicts",
    "auth_ms","image_prepare_ms","api_total_ms",
    "api_first_event_ms","api_first_output_ms","validation_ms",
})
_SAFE_CODE = re.compile(r"^[\w .:/+()\-]{1,100}$",re.UNICODE)
_SAFE_SESSION = re.compile(r"^capture-[0-9a-f]{32}$")


def _clean(data: dict) -> dict:
    result = {}
    for key,value in data.items():
        if key in _ALLOWED_TEXT and isinstance(value,str):
            if _SAFE_CODE.fullmatch(value):
                result[key]=value
        elif key in _ALLOWED_BOOL and type(value) is bool:
            result[key]=value
        elif key in _ALLOWED_NUMBER and type(value) in (int,float):
            if -1 <= value <= 10**8:
                result[key]=value
    # A guessed opponent identity is untrusted but useful for diagnosing
    # recognition; store at most six short labels, never the full vision reply.
    for key in ("opponent_six_candidate","opponent_six_validated"):
        names=data.get(key)
        if (isinstance(names,(list,tuple)) and len(names)<=6
                and all(isinstance(x,str) and 0<len(x)<=48 and
                        _SAFE_CODE.fullmatch(x) for x in names)):
            result[key]=list(names)
    return result


class MatchDebugLog:
    def __init__(self, app_root: str | Path, *, match_id: str | None = None,
                 clock=monotonic):
        self.started=clock()
        self.clock=clock
        self.match_id=match_id if match_id and _SAFE_SESSION.fullmatch(match_id) else (
            "capture-"+uuid4().hex)
        root=Path(app_root)/"debug-logs"
        root.mkdir(parents=True,exist_ok=True)
        self.path=root/(self.match_id+".jsonl")
        # New match ID means no overwrite; 20 bounded local files are retained.
        for old in sorted(root.glob("capture-*.jsonl"),
                          key=lambda p:p.stat().st_mtime,reverse=True)[KEEP_FILES-1:]:
            if old != self.path:
                try:
                    old.unlink()
                except OSError:
                    pass
        self.failed=False
        self.capped=False
        self.write("SESSION_OPEN",source="AUTO_LOCAL_DIAGNOSTICS")

    def write(self,event: str,**fields) -> bool:
        if self.failed or self.capped:
            return False
        if not isinstance(event,str) or not _SAFE_CODE.fullmatch(event):
            return False
        record={"schema":SCHEMA,"utc":datetime.now(timezone.utc).isoformat(),
                "event":event,
                "at_ms":int((self.clock()-self.started)*1000),
                **_clean(fields)}
        try:
            if self.path.exists() and self.path.stat().st_size>=MAX_BYTES:
                self.capped=True
                return False
            with self.path.open("a",encoding="utf-8",newline="\n") as out:
                out.write(json.dumps(record,ensure_ascii=False,separators=(",",":"))+"\n")
        except OSError:
            self.failed=True
            return False
        return True

    def report(self,report: dict,*,event: str = "UI_REPORT") -> bool:
        fields=_clean(report)
        names=report.get("observation",{}).get("opponent_six") if isinstance(
            report.get("observation"),dict) else None
        if not names and event=="COMPARISON_RESULT":
            names=report.get("opponent_six")
        if names:
            fields["opponent_six_candidate"]=names
        if report.get("screen")=="TEAM_SELECTION" and report.get("status") in (
                "OK","TEST_ONLY","PROVISIONAL_ADVICE"):
            names=report.get("opponent_six")
            if names:
                fields["opponent_six_validated"]=names
        return self.write(event,**fields)
