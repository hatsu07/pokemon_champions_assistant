"""Local crash/error evidence for the frozen Windows desktop.

Never records exception messages, OCR text, image pixels, prompts or tokens.
Python failures are stored as type + basename/line/function. faulthandler
writes native/Python thread stacks to a SEPARATE local-only file; native
fail-fast termination or power loss cannot be guaranteed to be captured.
"""
from __future__ import annotations

import atexit
from datetime import datetime, timezone
import faulthandler
import json
import logging
import os
from pathlib import Path
import re
import sys
import threading
import traceback

_ALLOWED = re.compile(r"^[A-Za-z0-9_.:+-]{1,100}$")
_recorder = None


def _safe(value: object, *, fallback: str = "UNKNOWN") -> str:
    return value if isinstance(value, str) and _ALLOWED.fullmatch(value) else fallback


class _Recorder:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        base = f"runtime-{stamp}-{os.getpid()}"
        self.jsonl_path = root / (base + ".jsonl")
        self.fatal_path = root / (base + "-native.log")
        self.fd = os.open(str(self.jsonl_path),
                          os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self.lock = threading.Lock()
        self.fatal_file = open(self.fatal_path, "a", encoding="utf-8")
        try:
            faulthandler.enable(file=self.fatal_file, all_threads=True)
        except (RuntimeError, ValueError, OSError):
            self.record("FATAL_HANDLER_UNAVAILABLE")
        self.record("RUNTIME_DIAGNOSTICS_ENABLED")

    def record(self, event: str, **details: object) -> None:
        record = {
            "utc": datetime.now(timezone.utc).isoformat(),
            "event": _safe(event),
            "pid": os.getpid(),
        }
        for key, value in details.items():
            if key in {"stage", "component", "provider", "device", "error_type",
                       "error_code", "thread", "logging_name", "qt_category"}:
                record[key] = _safe(value)
            elif key in {"gpu_id", "exit_code", "frame_seq"} and type(value) is int:
                record[key] = value
            elif key == "frames" and isinstance(value, list):
                record[key] = value[:20]
        line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self.lock:
            os.write(self.fd, line)
            os.fsync(self.fd)

    def exception(self, event: str, exc: BaseException, *,
                  stage: str = "UNKNOWN", tb=None) -> None:
        frames = traceback.extract_tb(tb if tb is not None else exc.__traceback__)
        safe_frames = [
            {"file": _safe(Path(frame.filename).name),
             "line": frame.lineno,
             "function": _safe(frame.name)}
            for frame in frames[-20:]
        ]
        self.record(event, stage=stage,
                    error_type=type(exc).__name__,
                    error_code=getattr(exc, "code", None),
                    thread=threading.current_thread().name,
                    frames=safe_frames)


def record_event(event: str, **details: object) -> None:
    recorder = _recorder
    if recorder is None:
        return
    try:
        recorder.record(event, **details)
    except (OSError, ValueError, TypeError):
        pass  # Error reporting must never crash a live turn.


def record_exception(event: str, exc: BaseException, *,
                     stage: str = "UNKNOWN", tb=None) -> None:
    recorder = _recorder
    if recorder is None:
        return
    try:
        recorder.exception(event, exc, stage=stage, tb=tb)
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def install_crash_diagnostics(root: str | Path) -> Path | None:
    """Install once, before importing/starting Qt or ONNX Runtime threads."""
    global _recorder
    if _recorder is not None:
        return _recorder.jsonl_path
    try:
        recorder = _Recorder(Path(root))
    except (OSError, ValueError, RuntimeError):
        return None
    _recorder = recorder

    original_excepthook = sys.excepthook
    def on_exception(exc_type, exc, tb):
        record_exception("UNHANDLED_MAIN_EXCEPTION", exc, stage="PYTHON", tb=tb)
        original_excepthook(exc_type, exc, tb)
    sys.excepthook = on_exception

    original_thread_hook = threading.excepthook
    def on_thread_exception(args):
        record_exception("UNHANDLED_THREAD_EXCEPTION", args.exc_value,
                         stage="THREAD", tb=args.exc_traceback)
        original_thread_hook(args)
    threading.excepthook = on_thread_exception

    original_unraisable = sys.unraisablehook
    def on_unraisable(args):
        record_exception("UNRAISABLE_EXCEPTION", args.exc_value,
                         stage="FINALIZER", tb=args.exc_traceback)
        original_unraisable(args)
    sys.unraisablehook = on_unraisable

    class ErrorHandler(logging.Handler):
        def emit(self, entry):
            record_event("PYTHON_LOG_ERROR",
                         logging_name=entry.name,
                         error_type=entry.levelname)
            if entry.exc_info and entry.exc_info[1] is not None:
                record_exception("PYTHON_LOG_EXCEPTION", entry.exc_info[1],
                                 stage="LOGGING", tb=entry.exc_info[2])
    handler = ErrorHandler(level=logging.ERROR)
    logging.getLogger().addHandler(handler)

    # Keep reference alive for entire Qt event loop. Never copy raw Qt message.
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
        def on_qt_message(mode, context, message):
            if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg,
                        QtMsgType.QtFatalMsg):
                record_event("QT_RUNTIME_MESSAGE", error_type=mode.name,
                             qt_category=getattr(context, "category", None))
        recorder.qt_callback = on_qt_message
        recorder.previous_qt_callback = qInstallMessageHandler(on_qt_message)
    except (ImportError, RuntimeError, AttributeError):
        record_event("QT_MESSAGE_HANDLER_UNAVAILABLE")

    # An unclean native exit will omit this marker; fatal stack output is
    # written separately by faulthandler where the OS/CPython supports it.
    atexit.register(record_event, "PYTHON_INTERPRETER_EXIT")
    return recorder.jsonl_path
