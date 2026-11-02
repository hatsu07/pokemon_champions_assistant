"""Capture-time, pixel-bound receipt for the existing append-only observer.

The source/capture clock and named actors are NOT independently authenticated.
The receipt is not a per-field provenance certificate, OCR truth, engine state,
or a WindowEvidenceRecord for the synthetic #186 validator. No pixels leave
the local process; only a digest and non-sensitive frame metadata are stored.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .capture import Frame
from .ocr import image_digest


@dataclass(frozen=True)
class FrameCaptureReceipt:
    match_id: str
    raw_observation_id: str
    frame_id: str
    capture_seq: int
    captured_at_ms: int
    image_rgb_sha256: str
    image_width: int
    image_height: int
    source_claim: str
    game_turn_seq_before_ingest: str | None
    observation_seq_before_ingest: int
    tactical_state_version_before_ingest: int
    decision_window_id_before_ingest: str | None
    window_state_version_before_ingest: int | None
    capture_origin_authenticated: bool = False
    capture_timestamp_authenticated: bool = False
    actor_identity_authenticated: bool = False
    game_observation_verified: bool = False
    ready_for_engine: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def capture_frame_receipt(frame: Frame, session: Any,
                          raw_observation_id: str) -> FrameCaptureReceipt:
    """Bind exact RGB pixels to the *pre-frame* observer and window revision.

    Frame.captured_at_ms may be an offline video offset or source-supplied
    monotonic clock: it must NEVER be converted to a UTC OCR capture timestamp.
    Likewise, a current FSM window was opened by earlier evidence; storing
    its identity must not imply that this new frame has been attributed to it.
    """
    if (not isinstance(frame, Frame)
            or not isinstance(raw_observation_id, str)
            or not raw_observation_id
            or not isinstance(session.match_id, str)
            or not session.match_id):
        raise ValueError("invalid capture receipt identity")
    window = session.current_window
    return FrameCaptureReceipt(
        match_id=session.match_id,
        raw_observation_id=raw_observation_id,
        frame_id=frame.frame_id,
        capture_seq=frame.capture_seq,
        captured_at_ms=frame.captured_at_ms,
        image_rgb_sha256=image_digest(frame.image),
        image_width=int(frame.image.shape[1]),
        image_height=int(frame.image.shape[0]),
        source_claim=frame.source,
        game_turn_seq_before_ingest=session.game_turn_seq,
        observation_seq_before_ingest=session.observation_seq,
        tactical_state_version_before_ingest=session.tactical_state_version,
        decision_window_id_before_ingest=(
            None if window is None else window.decision_window_id),
        window_state_version_before_ingest=(
            None if window is None else window.tactical_state_version),
    )


def matches_original_frame(receipt: FrameCaptureReceipt,
                           frame: Frame, match_id: str) -> bool:
    """Detect mutation/stale replay buffer before reinterpreting old pixels."""
    return (isinstance(receipt, FrameCaptureReceipt)
            and isinstance(frame, Frame)
            and receipt.match_id == match_id
            and receipt.frame_id == frame.frame_id
            and receipt.capture_seq == frame.capture_seq
            and receipt.captured_at_ms == frame.captured_at_ms
            and receipt.image_width == frame.image.shape[1]
            and receipt.image_height == frame.image.shape[0]
            and receipt.image_rgb_sha256 == image_digest(frame.image))
