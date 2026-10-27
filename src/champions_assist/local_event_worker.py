"""Event-time, bounded local OCR. This module makes NO network/API requests.

The Qt capture thread only compares tiny ROI previews and queues frames. All
OCR and interpretation run on a daemon worker. No turn command waits for OCR.
A missing OCR executable, invalid viewport, full queue or stale generation
fails closed to labelled CANDIDATE data and optional existing image fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic
from typing import Any

import numpy as np

from .calibrated_screen_fields import (
    IGNORED_OCR_ROIS, ScreenFieldProfile, parse_field_text,
)
from .observation_events import ScreenLexicon, TextObservationParser
from .ocr import OcrEngine, TesseractJapaneseOcr


@dataclass(frozen=True)
class LocalFieldRead:
    field: str
    value: object
    frame_id: str
    timestamp: float
    confidence: float | None
    roi: str
    source: str
    text: str
    detected_moves: tuple[str, ...] = ()
    detected_pokemon: tuple[str, ...] = ()
    status: str = "CANDIDATE"

    def as_dict(self) -> dict[str, Any]:
        return dict(
            field=self.field, candidate=self.value, frame_id=self.frame_id,
            observed_at=self.timestamp, confidence=self.confidence,
            roi=self.roi, source=self.source, raw_text=self.text[:160],
            detected_moves=list(self.detected_moves),
            detected_pokemon=list(self.detected_pokemon),
            certainty=self.status, game_validated=False,
        )


@dataclass(frozen=True)
class _Job:
    frame_id: str
    timestamp: float
    label: str
    crops: tuple[tuple[str, str, np.ndarray], ...]


@dataclass(frozen=True)
class _Result:
    frame_id: str
    timestamp: float
    observations: tuple[LocalFieldRead, ...]
    error: str | None = None
    error_code: str | None = None


class AsyncBattleEventReader:
    """Thread-isolated bounded OCR queue; drains are strictly non-blocking."""

    def __init__(self, profile: ScreenFieldProfile | None,
                 *, ocr_engine: OcrEngine | None = None,
                 lexicon: ScreenLexicon | None = None,
                 max_pending: int = 4, max_results: int = 12,
                 change_threshold: float = 0.030):
        if max_pending < 1 or max_results < 1:
            raise ValueError("queue limits must be positive")
        self.profile=profile
        self.max_results=max_results
        self.change_threshold=change_threshold
        self._queue: Queue[_Job | None]=Queue(maxsize=max_pending)
        self._out: Queue[_Result]=Queue(maxsize=max_results)
        self._stop=Event()
        self._signatures: dict[str,np.ndarray]={}
        self._counter=0
        self.skipped=0
        self.overflow=0
        self.errors=0
        self.jobs_enqueued=0
        self.jobs_completed=0
        self.rois_attempted=0
        self.rois_parsed=0
        self.results_dropped=0
        self.last_latency_ms=0
        self.max_latency_ms=0
        self.enabled=profile is not None and ocr_engine is not None
        self.reason="READY" if self.enabled else "OCR_NOT_CONFIGURED"
        self._parser=TextObservationParser(lexicon=lexicon or ScreenLexicon())
        self._ocr=ocr_engine
        self._thread: Thread | None=None
        if self.enabled:
            self._thread=Thread(target=self._run,daemon=True,
                                name="champions-local-event-ocr")
            self._thread.start()

    @classmethod
    def with_local_tesseract(cls, profile: ScreenFieldProfile | None,
                             *, lexicon: ScreenLexicon | None=None,
                             ocr_engine: OcrEngine | None=None):
        if ocr_engine is None:
            # A portable Windows EXE includes native tesseract and tessdata;
            # merely finding "tesseract" on PATH is insufficient without jpn.
            from .ocr import discover_japanese_tesseract
            if discover_japanese_tesseract() is None:
                instance=cls(profile,lexicon=lexicon)
                instance.reason="JAPANESE_TESSERACT_RUNTIME_UNAVAILABLE"
                return instance
            ocr_engine=TesseractJapaneseOcr()
        return cls(profile,ocr_engine=ocr_engine,lexicon=lexicon)

    def offer(self, image: np.ndarray, *, label: str,
              frame_id: str | None=None, timestamp: float | None=None) -> bool:
        """Fast ROI change gate. Never OCR or wait in the caller's thread.

        Only BATTLE_EVENT is eligible. No menu, timer or unknown-screen OCR.
        """
        profile=self.profile
        if (not self.enabled or self._stop.is_set() or profile is None
                or not profile.compatible(image)
                or label!="BATTLE_EVENT"):
            return False
        crops=[]
        updated={}
        for region in profile.regions:
            if region.name in IGNORED_OCR_ROIS:
                continue
            # TIMER and TEXT regions are intentionally never read.
            if region.kind not in ("HP_FRACTION","HP_PERCENT","MESSAGE"):
                continue
            if "BATTLE_EVENT" not in region.applicable_labels:
                continue
            crop=profile.crop(image,region)
            small=crop[::max(1,crop.shape[0]//20),
                       ::max(1,crop.shape[1]//48)].astype(np.float32)
            key=region.name
            previous=self._signatures.get(key)
            changed=(previous is None or previous.shape!=small.shape
                     or float(np.mean(np.abs(previous-small)))/255
                        >=self.change_threshold)
            if changed:
                crops.append((region.name,region.kind,crop.copy()))
                updated[key]=small
        if not crops:
            self.skipped+=1
            return False
        self._counter+=1
        event_id=frame_id or f"local-frame-{self._counter}"
        job=_Job(event_id,monotonic() if timestamp is None else timestamp,
                 label,tuple(crops))
        try:
            self._queue.put_nowait(job)
        except Full:
            self.overflow+=1
            return False
        # Commit dedup state only after a successful enqueue; lost events can
        # be retried on the next 300ms frame.
        self._signatures.update(updated)
        self.jobs_enqueued+=1
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job=self._queue.get(timeout=0.1)
            except Empty:
                continue
            if job is None:
                break
            records=[]
            started=monotonic()
            first_error=None
            first_error_code=None
            for name,kind,crop in job.crops:
                if self._stop.is_set():
                    break
                region=next((r for r in self.profile.regions
                             if r.name==name),None)
                if region is None:
                    continue
                self.rois_attempted+=1
                try:
                    reading=self._ocr.read(crop,roi_name=name)
                    observed=parse_field_text(region,reading.text,
                                              frame_id=job.frame_id)
                    if observed is None:
                        continue
                    self.rois_parsed+=1
                    parsed=self._parser.parse(
                        reading,roi_name=name,
                        raw_observation_id=job.frame_id,episode_id=0)
                    records.append(LocalFieldRead(
                        field=observed.field,value=observed.value,
                        frame_id=job.frame_id,timestamp=job.timestamp,
                        confidence=reading.confidence,roi=name,
                        source="ROI_OCR_UNVERIFIED",text=reading.text,
                        detected_moves=parsed.detected_moves,
                        detected_pokemon=parsed.detected_pokemon))
                except Exception as exc:
                    # One failing ROI never discards another successfully
                    # recognized HP/message in the same battle-event frame.
                    self.errors+=1
                    if first_error is None:
                        first_error=type(exc).__name__
                        status=getattr(exc,"status",None)
                        first_error_code=(getattr(exc,"code",None) or (
                            "TESSERACT_EXIT_"+str(status)
                            if type(status) is int and 0<=status<=255
                            else first_error))
            result=_Result(job.frame_id,job.timestamp,tuple(records),
                           error=first_error,error_code=first_error_code)
            latency=max(0,int((monotonic()-started)*1000))
            self.last_latency_ms=latency
            self.max_latency_ms=max(self.max_latency_ms,latency)
            self.jobs_completed+=1
            if not self._stop.is_set():
                try:
                    self._out.put_nowait(result)
                except Full:
                    self.results_dropped+=1
                    # Leave the latest information receivable by evicting the
                    # oldest completed result, never block the OCR worker.
                    try:
                        self._out.get_nowait()
                    except Empty:
                        pass
                    self._out.put_nowait(result)

    def diagnostic_counts(self) -> dict[str,int]:
        """Cheap bounded, non-blocking aggregate metrics; no OCR text or ROI."""
        return {
            "ocr_jobs_enqueued":self.jobs_enqueued,
            "ocr_jobs_completed":self.jobs_completed,
            "ocr_rois_attempted":self.rois_attempted,
            "ocr_rois_parsed":self.rois_parsed,
            "ocr_results_dropped":self.results_dropped,
            "ocr_errors":self.errors,
            "ocr_skipped":self.skipped,
            "ocr_queue_pending":self._queue.qsize(),
            "ocr_results_pending":self._out.qsize(),
            "overflow":self.overflow,
            "ocr_last_latency_ms":self.last_latency_ms,
            "ocr_max_latency_ms":self.max_latency_ms,
        }

    def drain(self) -> tuple[_Result, ...]:
        """Collect completed results immediately; do not await active OCR."""
        ready=[]
        while True:
            try:
                row=self._out.get_nowait()
            except Empty:
                break
            ready.append(row)
        return tuple(ready)

    def stop(self) -> None:
        self._stop.set()
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                break
        try:
            self._queue.put_nowait(None)
        except Full:
            pass
        # Absolutely no join() on the GUI/turn thread.
