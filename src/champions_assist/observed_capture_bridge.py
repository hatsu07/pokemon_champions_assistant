"""Opt-in physical observer -> detached local OCR reducer bridge.

This is a real *in-process* path from ObserverPipeline's persisted RGB/ROI
receipt to the existing local OCR state reducer. It does NOT write to the
production GPT state, trust a source label, or certify a Pokémon actor.
Raw receipts are matched to exact pixels and ROI bytes, never guessed from
frame IDs alone. Only explicitly configured, known OCR field ROIs are read.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from .calibrated_screen_fields import FieldRegion, parse_field_text
from .capture import Frame
from .capture_provenance import FrameCaptureReceipt, matches_original_frame
from .live_state_updates import apply_local_ocr
from .local_event_worker import LocalFieldRead
from .ocr import image_digest
from .roi import RoiConfiguration

_FIELDS = {
    "battle_message": "MESSAGE",
    "own_active_name": "TEXT",
    "opponent_active_name": "TEXT",
    "own_hp_fraction": "HP_FRACTION",
    "opponent_hp_percent": "HP_PERCENT",
    "own_pp_move1": "PP_FRACTION",
    "own_pp_move2": "PP_FRACTION",
    "own_pp_move3": "PP_FRACTION",
    "own_pp_move4": "PP_FRACTION",
}


@dataclass(frozen=True)
class ObservedLinkResult:
    status: str
    reason: str
    match_id: str
    raw_observation_id: str
    state_revision_before: int
    state_revision_after: int
    applied: int
    audit: tuple[dict[str, Any], ...]


class LinkedObserverState:
    """Keep a detached local state; an opt-in ObserverPipeline calls ingest.

    No retroactive read, no file/Web IO, and no automatic UI recommendation.
    Rejected, ambiguous or low-confidence ROI text can never turn into a
    confirmed observed Pokémon, status, or engine input.
    """

    def __init__(self, *, match_id: str, assets: Any,
                 initial_state: Mapping[str, Any],
                 roi_fields: Mapping[str, str]) -> None:
        if (not isinstance(match_id, str) or not match_id
                or initial_state.get("match_id") != match_id
                or not roi_fields or len(set(roi_fields.values())) != len(roi_fields)
                or any(not isinstance(k, str) or not isinstance(v, str)
                       or v not in _FIELDS for k, v in roi_fields.items())):
            raise ValueError("EXPLICIT_CURRENT_MATCH_OCR_MAPPING_REQUIRED")
        self.match_id = match_id
        self.assets = assets
        self.state = deepcopy(dict(initial_state))
        self.roi_fields = dict(roi_fields)
        self.last_capture_seq = -1
        self._seen_frames: set[str] = set()

    @property
    def linked_raw_observation_ids(self) -> frozenset[str]:
        return frozenset(self._seen_frames)

    def ingest(self, frame: Frame, receipt: FrameCaptureReceipt,
               raw_rois: Mapping[str, Any],
               roi_config: RoiConfiguration) -> ObservedLinkResult:
        before = self.state.get("_live_state_revision", 0)
        def blocked(reason: str) -> ObservedLinkResult:
            return ObservedLinkResult("NO_LINKED_UPDATE", reason, self.match_id,
                                      getattr(receipt, "raw_observation_id", ""),
                                      before, before, 0, ())
        if (not matches_original_frame(receipt, frame, self.match_id)
                or receipt.raw_observation_id != (
                    f"{self.match_id}:frame:{frame.capture_seq}:{frame.frame_id}")
                or type(frame.capture_seq) is not int
                or frame.capture_seq <= self.last_capture_seq
                or receipt.raw_observation_id in self._seen_frames):
            return blocked("STALE_OR_UNMATCHED_FRAME")
        if not isinstance(raw_rois, Mapping):
            return blocked("INVALID_ROI_RECEIPT")
        # No mutating the OCR reducer before ALL configured ROI digests are
        # cross-checked against the actual source pixels.
        records = []
        for roi, field in self.roi_fields.items():
            item = raw_rois.get(roi)
            if item is None:
                continue
            crop = roi_config.crop(roi, frame.image)
            if (crop is None or not isinstance(item, dict)
                    or item.get("roi_rgb_sha256") != image_digest(crop)):
                return blocked("ROI_PIXELS_NOT_BOUND_TO_FRAME")
            text = item.get("text")
            conf = item.get("confidence")
            if (not isinstance(text, str) or not text
                    or type(conf) not in (int, float)
                    or not 0 <= conf <= 1):
                continue
            region = FieldRegion(field, _FIELDS[field], None, ())
            candidate = parse_field_text(
                region, text, frame_id=receipt.raw_observation_id)
            if candidate is None:
                continue
            records.append(LocalFieldRead(
                field=candidate.field, value=candidate.value,
                frame_id=frame.frame_id,
                timestamp=frame.captured_at_ms / 1000.0,
                confidence=float(conf), roi=roi,
                source="OBSERVER_LINKED_UNAUTHENTICATED",
                text=text, status="CANDIDATE",
                display_id=receipt.raw_observation_id,
            ))
        try:
            proposed, audit = apply_local_ocr(
                self.state, tuple(records), self.assets,
                capture_receipts={frame.frame_id: receipt})
        except (ValueError, KeyError, TypeError, AttributeError):
            # The isolated opt-in reducer must never crash a working capture
            # pipeline or mutate the detached state on malformed inputs.
            return blocked("DETACHED_OCR_REDUCER_REFUSED_INPUT")
        # Even an OCR event marked APPLIED by the bounded reducer is not
        # verified gameplay. The provenance carries false authenticity flags.
        if proposed.get("match_id") != self.match_id:
            return blocked("MATCH_CHANGED_DURING_REDUCE")
        self.state = proposed
        self._seen_frames.add(receipt.raw_observation_id)
        self.last_capture_seq = frame.capture_seq
        applied = sum(a.get("status") == "APPLIED" for a in audit)
        return ObservedLinkResult(
            "LINKED_CANDIDATE_STATE" if applied else "LINKED_NO_APPLIED_FIELDS",
            "LOCAL_OCR_REDUCER_ONLY_NOT_GAME_ATTESTATION",
            self.match_id, receipt.raw_observation_id,
            int(before), int(proposed.get("_live_state_revision", 0)),
            applied, tuple(audit),
        )
