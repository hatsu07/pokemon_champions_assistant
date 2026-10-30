"""Event-time, queued local OCR. This module makes NO network/API requests.

The Qt capture thread only compares tiny ROI previews and queues frames. All
OCR runs on a daemon worker; AI alone interprets its observations.
A missing GPU runtime, invalid viewport, optional bounded queue or stale generation
fails closed to labelled CANDIDATE data and optional existing image fallback.
"""
from __future__ import annotations
from uuid import uuid4

from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic
from typing import Any

import numpy as np

from .calibrated_screen_fields import (
    IGNORED_OCR_ROIS, ScreenFieldProfile, parse_field_text, live_ocr_region_enabled,
)
from .observation_events import ScreenLexicon, TextObservationParser
from .ocr import OcrEngine, TesseractJapaneseOcr
from .battle_message_ruby import prepare_battle_message
from .crash_diagnostics import record_event, record_exception


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
    display_id: str = ""
    preprocess_status: str = ""
    ruby_mask_rois: tuple[tuple[int, int, int, int], ...] = ()
    ocr_elapsed_ms: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return dict(
            field=self.field, candidate=self.value, frame_id=self.frame_id,
            observed_at=self.timestamp, confidence=self.confidence,
            roi=self.roi, source=self.source, raw_text=self.text[:160],
            detected_moves=list(self.detected_moves),
            detected_pokemon=list(self.detected_pokemon),
            certainty=self.status, game_validated=False,
            **({"display_id":self.display_id} if self.display_id else {}),
            **({"preprocess":self.preprocess_status,
                "ruby_mask_rois":[list(roi) for roi in self.ruby_mask_rois],
                "ocr_elapsed_ms":self.ocr_elapsed_ms}
               if self.preprocess_status else {}),
        )


@dataclass(frozen=True)
class _Job:
    frame_id: str
    timestamp: float
    label: str
    crops: tuple[tuple[str, str, np.ndarray, str, dict], ...]


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
                 max_pending: int | None = None, max_results: int | None = None,
                 change_threshold: float = 0.030):
        if (max_pending is not None and max_pending < 1) or (max_results is not None and max_results < 1):
            raise ValueError("queue limits must be positive")
        self.profile=profile
        self.max_results=max_results
        self.change_threshold=change_threshold
        # Keep all acquired changed frames. OCR backlog is observable; no
        # silent eviction at four pending jobs.
        self._queue: Queue[_Job | None]=Queue(maxsize=max_pending or 0)
        self._out: Queue[_Result]=Queue(maxsize=max_results or 0)
        self._stop=Event()
        self._signatures: dict[str,np.ndarray]={}
        self._display_source=uuid4().hex[:12]
        self._display_episodes: dict[str,int]={}
        self._display_last_time: dict[str,float]={}
        self._display_last_label: dict[str,str]={}
        self._counter=0
        self.skipped=0
        # Track *all* offer attempts, including early validation rejects which
        # did not previously increment ocr_skipped or create any OCR job.
        self.offers=0
        self.last_offer_reason="NOT_OFFERED"
        self.rejection_counts={key:0 for key in (
            "OCR_DISABLED", "PROFILE_MISSING", "IMAGE_FORMAT_INVALID",
            "ASPECT_RATIO_MISMATCH", "LABEL_NOT_ELIGIBLE",
            "NO_APPLICABLE_ROI", "ROI_UNCHANGED", "QUEUE_FULL")}
        self.overflow=0
        self.errors=0
        self.jobs_enqueued=0
        self.jobs_completed=0
        self.rois_attempted=0
        self.rois_parsed=0
        self.results_dropped=0
        self.last_latency_ms=0
        self.last_queue_wait_ms=0
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
    def with_onnx_ocr(cls, profile: ScreenFieldProfile | None,
                      *, lexicon: ScreenLexicon | None=None,
                      ocr_engine: OcrEngine | None=None,
                      device: str="auto", gpu_id: int=0,
                      isolate_process: bool=False):
        """Route Windows GPU OCR through an independent, persistent child."""
        if profile is None:
            return cls(None,lexicon=lexicon)
        if ocr_engine is None:
            from .ocr import OcrExecutionError
            try:
                if isolate_process and device != "cpu":
                    from .ocr_sidecar import OcrSidecarClient
                    ocr_engine=OcrSidecarClient(device=device,gpu_id=gpu_id)
                else:
                    from .ocr_onnx import make_onnx_ocr
                    ocr_engine=make_onnx_ocr(device=device,gpu_id=gpu_id)
            except (OcrExecutionError,OSError,ValueError) as exc:
                instance=cls(profile,lexicon=lexicon)
                instance.reason=getattr(exc,"code","ONNX_OCR_INITIALIZATION_FAILED")
                return instance
        return cls(profile,ocr_engine=ocr_engine,lexicon=lexicon)

    @classmethod
    def with_gpu_paddle(cls, profile: ScreenFieldProfile | None,
                        *, lexicon: ScreenLexicon | None=None,
                        ocr_engine: OcrEngine | None=None):
        """Compatibility alias for legacy fixtures, using ONNX Runtime."""
        return cls.with_onnx_ocr(profile,lexicon=lexicon,
                                 ocr_engine=ocr_engine)

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

    def _reject(self, reason: str) -> bool:
        self.last_offer_reason=reason
        self.rejection_counts[reason]+=1
        return False

    def offer(self, image: np.ndarray, *, label: str,
              frame_id: str | None=None, timestamp: float | None=None) -> bool:
        """Fast ROI change gate. Never OCR or wait in the caller's thread.

        Only BATTLE_EVENT and verified local action/party UI are eligible.
        Work stays on the worker, never the render or AI turn thread.
        """
        self.offers+=1
        profile=self.profile
        if profile is None:
            return self._reject("PROFILE_MISSING")
        if not self.enabled or self._stop.is_set():
            return self._reject("OCR_DISABLED")
        if (not isinstance(image,np.ndarray) or image.ndim!=3
                or image.shape[2]!=3 or image.dtype!=np.uint8
                or not image.shape[0] or not image.shape[1]):
            return self._reject("IMAGE_FORMAT_INVALID")
        if not profile.compatible(image):
            return self._reject("ASPECT_RATIO_MISMATCH")
        if label not in ("BATTLE_EVENT","TURN_ACTION","SWITCH_SELECTION"):
            return self._reject("LABEL_NOT_ELIGIBLE")
        crops=[]
        eligible_rois=0
        updated={}
        captured_at=monotonic() if timestamp is None else timestamp
        new_display_episodes={}
        new_display_time={}
        new_display_labels={}
        for region in profile.regions:
            if not live_ocr_region_enabled(profile,region):
                continue
            # Optional calibrated PP fields are read only on action screens.
            # Unknown scene/timer is deliberately never interpreted.
            if region.kind not in ("HP_FRACTION","HP_PERCENT","MESSAGE",
                                   "TEXT","PP_FRACTION"):
                continue
            if label not in region.applicable_labels:
                continue
            eligible_rois+=1
            crop, preprocess=prepare_battle_message(image,profile,region)
            small=crop[::max(1,crop.shape[0]//20),
                       ::max(1,crop.shape[1]//48)].astype(np.float32)
            key=region.name
            previous=self._signatures.get(key)
            changed=(previous is None or previous.shape!=small.shape
                     or float(np.mean(np.abs(previous-small)))/255
                        >=self.change_threshold)
            if changed:
                difference=(float(np.mean(np.abs(previous-small)))/255
                            if previous is not None and previous.shape==small.shape
                            else 1.0)
                # Visual identity, not OCR string similarity, permits merging.
                # A screen change or substantial ROI change starts a new event.
                stable=(difference<=max(self.change_threshold,0.045)
                        and 0<=captured_at-self._display_last_time.get(key,-1e10)<=2.0
                        and self._display_last_label.get(key)==label)
                episode=self._display_episodes.get(key,0)+(0 if stable else 1)
                new_display_episodes[key]=episode
                new_display_time[key]=captured_at
                new_display_labels[key]=label
                crops.append((region.name,region.kind,crop.copy(),
                              f"{self._display_source}:{key}:visual-{episode}",
                              preprocess))
                updated[key]=small
        if not crops:
            self.skipped+=1
            return self._reject("ROI_UNCHANGED" if eligible_rois
                                else "NO_APPLICABLE_ROI")
        self._counter+=1
        event_id=frame_id or f"local-frame-{self._counter}"
        # Messages precede HUD text within a frame; no semantic OCR parse.
        crops.sort(key=lambda crop: 0 if crop[1]=="MESSAGE" or
                   "message" in crop[0] else 1)
        job=_Job(event_id,captured_at,label,tuple(crops))
        try:
            self._queue.put_nowait(job)
        except Full:
            self.overflow+=1
            return self._reject("QUEUE_FULL")
        # Commit dedup state only after a successful enqueue; lost events can
        # be retried on the next 300ms frame.
        self._signatures.update(updated)
        self._display_episodes.update(new_display_episodes)
        self._display_last_time.update(new_display_time)
        self._display_last_label.update(new_display_labels)
        self.jobs_enqueued+=1
        self.last_offer_reason="ENQUEUED"
        return True

    def _run(self) -> None:
        # Warm the model in the OCR worker, never on the Qt render thread.
        record_event("LOCAL_OCR_WORKER_STARTED", component="OCR_WORKER")
        try:
            warmup=getattr(self._ocr,"warmup",None)
            if warmup is not None:
                record_event("LOCAL_OCR_WARMUP_BEGIN", component="OCR_WORKER")
                warmup()
                provider=getattr(self._ocr,"provider","UNKNOWN")
                record_event("LOCAL_OCR_WARMUP_COMPLETE", provider=provider)
                self.reason=("ONNX_OCR_READY_GPU" if provider in
                             ("DmlExecutionProvider","CUDAExecutionProvider")
                             else "ONNX_OCR_READY_CPU" if provider=="CPUExecutionProvider"
                             else "ONNX_OCR_READY_UNVERIFIED")
        except Exception as exc:
            record_exception("LOCAL_OCR_WARMUP_ERROR", exc, stage="OCR_WORKER")
            self.errors+=1
            self.reason=getattr(exc,"code","ONNX_OCR_INITIALIZATION_FAILED")
            # Do not consume queued evidence with an unusable inference
            # engine; report failure rather than inventing observations.
            self.enabled=False
            return
        while not self._stop.is_set():
            try:
                job=self._queue.get(timeout=0.1)
            except Empty:
                continue
            if job is None:
                break
            records=[]
            started=monotonic()
            self.last_queue_wait_ms=max(0,int((started-job.timestamp)*1000))
            first_error=None
            first_error_code=None
            for name,kind,crop,display_id,preprocess in job.crops:
                if self._stop.is_set() or not self.enabled:
                    break
                region=next((r for r in self.profile.regions
                             if r.name==name),None)
                if region is None:
                    continue
                self.rois_attempted+=1
                try:
                    ocr_started=monotonic()
                    reading=self._ocr.read(crop,roi_name=name)
                    ocr_elapsed_ms=max(0,round((monotonic()-ocr_started)*1000))
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
                        detected_pokemon=parsed.detected_pokemon,
                        display_id=display_id,
                        preprocess_status=(preprocess["preprocess"]
                                           if name=="battle_message" else ""),
                        ruby_mask_rois=tuple(tuple(roi) for roi in preprocess["ruby_mask_rois"]),
                        ocr_elapsed_ms=ocr_elapsed_ms))
                except Exception as exc:
                    record_exception("LOCAL_OCR_ROI_ERROR", exc, stage="OCR_ROI")
                    # One failing ROI never discards another successfully
                    # recognized HP/message in the same battle-event frame.
                    self.errors+=1
                    # Child-process failure must not silently leave OCR marked
                    # READY, nor keep submitting work to an exited process.
                    error_code=getattr(exc,"code","")
                    if (isinstance(error_code,str)
                            and error_code.startswith("OCR_SIDECAR_")
                            and error_code not in ("OCR_SIDECAR_INVALID_IMAGE",
                                                   "OCR_SIDECAR_IMAGE_TOO_LARGE")):
                        self.reason=error_code
                        self.enabled=False
                        self._ocr.stop()
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
            if not self.enabled:
                return

    def diagnostic_counts(self) -> dict[str,int]:
        """Cheap bounded, non-blocking aggregate metrics; no OCR text or ROI."""
        return {
            "ocr_offers":self.offers,
            "ocr_reject_disabled":self.rejection_counts["OCR_DISABLED"],
            "ocr_reject_no_profile":self.rejection_counts["PROFILE_MISSING"],
            "ocr_reject_bad_image":self.rejection_counts["IMAGE_FORMAT_INVALID"],
            "ocr_reject_aspect":self.rejection_counts["ASPECT_RATIO_MISMATCH"],
            "ocr_reject_label":self.rejection_counts["LABEL_NOT_ELIGIBLE"],
            "ocr_reject_no_roi":self.rejection_counts["NO_APPLICABLE_ROI"],
            "ocr_reject_unchanged":self.rejection_counts["ROI_UNCHANGED"],
            "ocr_reject_queue_full":self.rejection_counts["QUEUE_FULL"],
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
            "ocr_last_queue_wait_ms":self.last_queue_wait_ms,
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
        ocr=self._ocr
        if ocr is not None and hasattr(ocr,"stop"):
            # Sidecar shutdown never blocks the render/turn thread.
            ocr.stop()
        # Absolutely no join() on the GUI/turn thread.
