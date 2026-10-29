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
from queue import Queue, Full, Empty
from threading import Thread, Lock
from time import monotonic

from .live_diagnostics import redact_battle_detail
from uuid import uuid4

SCHEMA = "champions-local-live-debug-v1"
MAX_BYTES = 3 * 1024 * 1024
KEEP_FILES = 20
_ALLOWED_TEXT = frozenset({
    "status", "reason", "phase", "screen", "ui", "observed_ui",
    "request_id", "active_source", "display_source", "validation_code",
    "request_stage", "screen_label", "screen_label_hint", "transport",
    "screen_label_source", "live_reasoning_effort", "warning",
    "kind", "result", "model", "clock_source", "classifier_source",
    "next_screen", "ocr_status", "ocr_error_code",
    "intent", "active_job", "win_roles_status",
})
_ALLOWED_BOOL = frozenset({
    "recommendation_present", "selection_present", "matrix_compatible",
    "matrix_provisional", "superseded", "timer_ocr",
    "gate_dispatched", "late_advice_presented", "late_selection_presented",
    "active_thread", "prep_thread", "postmatch_thread",
    "cache_available", "experimental", "waited",
})
_ALLOWED_NUMBER = frozenset({
    "elapsed_ms", "capture_and_auth_ms", "at_ms", "ai_request_attempt",
    "state_revision", "previous_revision", "display_turn",
    "image_count", "opponent_recognition_attempts", "step", "preload_count",
    "matchups_evaluated", "consecutive_failures", "queue_elapsed_ms",
    "timeout_budget_ms", "latency_ms",
    "visual_first_attempt_elapsed_ms", "visual_transport_retry_elapsed_ms",
    "selection_visual_ms", "selection_tactical_ms",
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
    "model_elapsed_ms","prompt_chars","cached_matchups",
    "win_conditions","retired_threads","waited_ms",
    "elapsed_active_ms","active_elapsed_ms",
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
                 clock=monotonic, async_mode: bool = False):
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
        # The GUI opts into non-blocking writes. Direct/test callers retain
        # synchronous behavior and immediate file readability.
        self._async_mode=bool(async_mode)
        self._queue=Queue(maxsize=2048) if async_mode else None
        self._lock=Lock()
        self._dropped=0
        self._thread=None
        if async_mode:
            self._thread=Thread(target=self._write_worker,daemon=True,
                                name="champions-live-diagnostics")
            self._thread.start()
        self.write("SESSION_OPEN",source="AUTO_LOCAL_DIAGNOSTICS")

    def _persist(self, record: dict) -> bool:
        if self.failed or self.capped:
            return False
        try:
            line=json.dumps(record,ensure_ascii=False,separators=(",",":"))+"\n"
            with self._lock:
                size=self.path.stat().st_size if self.path.exists() else 0
                if size+len(line.encode("utf-8"))>MAX_BYTES:
                    self.capped=True
                    return False
                with self.path.open("a",encoding="utf-8",newline="\n") as out:
                    out.write(line)
        except OSError:
            self.failed=True
            return False
        return True

    def _write_worker(self) -> None:
        while True:
            row=self._queue.get()
            try:
                if row is None:
                    return
                self._persist(row)
            finally:
                self._queue.task_done()

    def _submit(self, record: dict) -> bool:
        if self.failed or self.capped:
            return False
        if self._queue is None:
            return self._persist(record)
        try:
            self._queue.put_nowait(record)
            return True
        except Full:
            self._dropped+=1
            return False

    def write(self,event: str,**fields) -> bool:
        if not isinstance(event,str) or not _SAFE_CODE.fullmatch(event):
            return False
        record={"schema":SCHEMA,"utc":datetime.now(timezone.utc).isoformat(),
                "event":event,
                "at_ms":int((self.clock()-self.started)*1000),
                **_clean(fields)}
        return self._submit(record)

    def write_diagnostic(self,event: str,detail: dict,**fields) -> bool:
        """Queued bounded structured evidence; never arbitrary API payload."""
        if not isinstance(event,str) or not _SAFE_CODE.fullmatch(event):
            return False
        record={"schema":SCHEMA,"utc":datetime.now(timezone.utc).isoformat(),
                "event":event,
                "at_ms":int((self.clock()-self.started)*1000),
                **_clean(fields),
                "detail":redact_battle_detail(detail)}
        return self._submit(record)

    def flush(self) -> None:
        """Explicit test/finalization barrier; never call during live turns."""
        if self._queue is not None:
            self._queue.join()

    def close(self) -> None:
        if self._thread is None:
            return
        self.write("DIAGNOSTIC_QUEUE_SUMMARY",ocr_results_dropped=self._dropped)
        self._queue.put(None)
        self._thread.join(timeout=2.0)
        self._thread=None

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


# Explicitly opted-in text-only GPT transcript. Separate from the bounded
# default diagnostics; never persist screenshots, HTTP headers or API tokens.
# The opt-in recorder runs on its own bounded queue, outside the Qt GUI thread.
AI_EXCHANGE_MAX_BYTES = 32 * 1024 * 1024
AI_EXCHANGE_EVENT_MAX_BYTES = 3 * 1024 * 1024
AI_EXCHANGE_KEEP_FILES = 10
_AI_CREDENTIAL = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{8,}|bearer\s+[a-z0-9._~+/-]{8,})"
)
_AI_SENSITIVE_KEYS = frozenset({
    "token", "accesstoken", "refreshtoken", "idtoken", "authtoken",
    "oauthtoken", "sessiontoken", "bearertoken", "apikey", "authkey",
    "authorization", "clientsecret", "secret", "secretkey",
    "password", "privatekey", "credentials",
})
# Normalize snake_case, camelCase, hyphens and namespaced credential keys
# to the same alphanumeric representation. Match whole secret key families
# rather than only the exact spellings that have appeared in review.
_AI_SENSITIVE_SUFFIXES = (
    "token", "apikey", "authkey", "authorization", "authorizationcode",
    "authcode", "oauthcode", "clientsecret", "secret", "password",
    "privatekey", "credential", "credentials", "codeverifier",
)
_AI_SENSITIVE_PREFIXES = (
    "authorization", "oauthcode", "authcode", "authkey",
)
# Credential families can occur in the *middle* of namespaced keys
# (aws_secret_access_key, aws_access_key_id, azure_storage_account_key).
# This is defense in depth, not a promise to detect arbitrary OCR secrets.
_AI_SENSITIVE_FRAGMENTS = (
    "secret", "token", "password", "credential", "authorization",
    "accesskey", "apikey", "authkey", "privatekey", "clientsecret",
    "codeverifier", "oauthcode", "authcode",
    "accountkey", "storageaccountkey",
)
# Unstructured OCR text must be treated conservatively: a quoted "authKey"
# field may appear inside a sentence that is not valid JSON. No word
# boundaries, because keys may be camelCased or namespaced.
_AI_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:token|api[_\s-]*(?:key|token|secret)|"
    r"auth[_\s-]*(?:key|code|token|secret)|"
    r"authorization|oauth[_\s-]*(?:key|code|token|secret)|"
    r"client[_\s-]*secret|code[_\s-]*verifier|"
    r"access[_\s-]*key(?:[_\s-]*id)?|storage[_\s-]*account[_\s-]*key|secret|"
    r"password|credentials?|private[_\s-]*key|bearer)"
)


def _is_sensitive_ai_key(name: str) -> bool:
    normalized=re.sub(r"[^a-z0-9]", "", name.casefold())
    return (normalized in _AI_SENSITIVE_KEYS
            or normalized.endswith(_AI_SENSITIVE_SUFFIXES)
            or normalized.startswith(_AI_SENSITIVE_PREFIXES)
            or any(fragment in normalized
                   for fragment in _AI_SENSITIVE_FRAGMENTS))



def _sanitize_ai_exchange(value, *, depth=0):
    """Fail closed on embedded credentials before serializing private JSONL.

    Scrub mapping keys recursively, including JSON embedded in input text.
    An unparseable string mentioning credential keys is removed in full
    rather than relying on fragile post-JSON regex substitutions.
    """
    if depth > 24:
        return "[REDACTED_NESTING]"
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            name=str(key)
            if _is_sensitive_ai_key(name):
                out[name]="[REDACTED]"
            else:
                out[name]=_sanitize_ai_exchange(item,depth=depth+1)
        return out
    if isinstance(value, (list, tuple)):
        return [_sanitize_ai_exchange(item,depth=depth+1) for item in value]
    if isinstance(value, str):
        clean=_AI_CREDENTIAL.sub("[REDACTED]",value)
        # Always parse valid JSON first: a credential key such as
        # "userAccessToken" cannot be safely recognized by boundaries on
        # the raw string. This also handles escaped nested JSON strings.
        try:
            decoded=json.loads(clean)
        except (TypeError, ValueError, RecursionError):
            decoded=None
        if isinstance(decoded,(dict,list)):
            sanitized=_sanitize_ai_exchange(decoded,depth=depth+1)
            return json.dumps(sanitized,ensure_ascii=False,separators=(",",":"))
        if isinstance(decoded,str) and decoded!=clean:
            sanitized=_sanitize_ai_exchange(decoded,depth=depth+1)
            return json.dumps(sanitized,ensure_ascii=False)
        # For malformed or ordinary text, fail closed whenever a
        # credential-like key/marker appears. Never persist the unknown
        # surrounding value by guessing where a malformed field ends.
        if _AI_SENSITIVE_TEXT.search(clean):
            return "[REDACTED_SENSITIVE_TEXT]"
        return clean
    if value is None or isinstance(value,(bool,int,float)):
        return value
    return "[REDACTED_UNSUPPORTED_VALUE]"


class PrivateAiExchangeLog:
    """User-opted-in local GPT text transcript; off by default.

    May contain player names, opponent names, OCR strings and team details.
    Requests omit image bytes and network/auth parameters by construction.
    """

    def __init__(self, app_root, *, match_id):
        root = Path(app_root) / "debug-logs" / "ai-exchanges"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / (match_id + ".jsonl")
        for old in sorted(root.glob("capture-*.jsonl"),
                          key=lambda p: p.stat().st_mtime, reverse=True)[
                              AI_EXCHANGE_KEEP_FILES-1:]:
            if old != self.path:
                try:
                    old.unlink()
                except OSError:
                    pass
        self.queue = Queue(maxsize=256)
        self.capped = False
        self.failed = False
        self._worker = Thread(target=self._drain, daemon=True,
                              name="champions-ai-exchange-log")
        self._worker.start()

    def write(self, event, payload):
        if event not in ("GPT_REQUEST", "GPT_RESPONSE", "GPT_ERROR"):
            return
        if self.capped or self.failed:
            return
        record = {"schema": "champions-private-ai-transcript-v1",
                  "utc": datetime.now(timezone.utc).isoformat(),
                  "event": event, "payload": _sanitize_ai_exchange(payload)}
        try:
            line = json.dumps(record, ensure_ascii=False,
                              separators=(",", ":"), default=str)
            line = _AI_CREDENTIAL.sub("[REDACTED]", line) + "\n"
            encoded = line.encode("utf-8")
            if len(encoded) > AI_EXCHANGE_EVENT_MAX_BYTES:
                encoded = (json.dumps({
                    "schema": record["schema"], "utc": record["utc"],
                    "event": "GPT_EVENT_TOO_LARGE",
                    "original_event": event,
                    "original_bytes": len(encoded)
                }, ensure_ascii=False) + "\n").encode("utf-8")
            self.queue.put_nowait(encoded)
        except (Full, OSError, TypeError, ValueError):
            # A diagnostic failure must not block the live battle.
            self.failed = True

    def _drain(self):
        written = 0
        try:
            with self.path.open("ab") as out:
                while True:
                    encoded = self.queue.get()
                    try:
                        if encoded is None:
                            return
                        if written + len(encoded) > AI_EXCHANGE_MAX_BYTES:
                            self.capped = True
                            continue
                        out.write(encoded)
                        out.flush()
                        written += len(encoded)
                    finally:
                        self.queue.task_done()
        except OSError:
            self.failed = True

    def close(self):
        # Never block a live GUI on disk I/O during shutdown.
        try:
            self.queue.put_nowait(None)
        except Full:
            pass
        self._worker.join(timeout=0.5)
