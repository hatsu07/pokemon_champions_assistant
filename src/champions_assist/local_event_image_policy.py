"""Text-first, fail-closed selection of battle-event images for ChatGPT.

Only skip an event image when the corresponding locally buffered event has
credible Japanese OCR evidence and there were NO errors, drops, or incomplete
jobs. This is an optimisation, never a promotion of OCR to verified mechanics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


_EVENT_TEXT_ROIS=frozenset((
    "battle_message","opponent_effect_popup","own_item_popup","system_message"
))
_MIN_OCR_CONFIDENCE=0.90


@dataclass(frozen=True)
class EventImagePlan:
    include_event_images: bool
    reason: str
    buffered: int
    recognized: int

    def summary(self)->dict:
        return {
            "image_fallback":self.include_event_images,
            "fallback_reason":self.reason,
            "event_images_buffered":self.buffered,
            "event_images_recognized":self.recognized,
        }


def event_image_plan(
    frame_ids: Sequence[str], candidates: Sequence[Mapping],
    *, reader_status: str, roi_validated: bool,
    ocr_counters: Mapping[str,int] | None=None,
) -> EventImagePlan:
    """Evaluate complete coverage of buffered events, not aggregate OCR rate.

    A different frame can contain a separate message; matching one OCR result
    is never proof that all event frames were transcribed.
    """
    total=len(frame_ids)
    if total==0:
        return EventImagePlan(False,"NO_EVENT_FRAMES",0,0)
    if reader_status!="READY":
        return EventImagePlan(True,"OCR_UNAVAILABLE",total,0)
    if not roi_validated:
        return EventImagePlan(True,"ROI_NOT_VALIDATED_ON_CAPTURE",total,0)
    if not ocr_counters:
        return EventImagePlan(True,"OCR_COUNTS_UNAVAILABLE",total,0)
    if (ocr_counters.get("ocr_queue_pending",0)>0
            or ocr_counters.get("ocr_jobs_enqueued",0)>
               ocr_counters.get("ocr_jobs_completed",0)):
        return EventImagePlan(True,"OCR_INCOMPLETE",total,0)
    if any(ocr_counters.get(name,0)>0 for name in (
        "overflow","ocr_results_dropped","ocr_errors")):
        return EventImagePlan(True,"OCR_DROPPED_OR_ERROR",total,0)
    accepted=set()
    for item in candidates:
        if (item.get("certainty")!="CANDIDATE"
                or item.get("game_validated") is not False
                or item.get("roi") not in _EVENT_TEXT_ROIS
                or not isinstance(item.get("raw_text"),str)
                or len(item["raw_text"].strip())<3):
            continue
        confidence=item.get("confidence")
        if type(confidence) not in (float,int) or confidence<_MIN_OCR_CONFIDENCE:
            continue
        if item.get("frame_id") in frame_ids:
            accepted.add(item["frame_id"])
    covered=len(set(frame_ids)&accepted)
    if covered<len(set(frame_ids)):
        return EventImagePlan(True,"EVENT_TEXT_NOT_FULLY_RECOGNIZED",total,covered)
    return EventImagePlan(False,"EVENT_TEXT_RECOGNIZED",total,covered)
