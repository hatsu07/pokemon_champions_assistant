"""GPT-only OCR transport: small references, lossless match-local originals.

Do not infer game state from OCR text. The existing validators receive the
original frames and confidence after expansion of model-returned evidence IDs.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256

from .ocr_turn_context import _row_key, compact_ocr_transport


def pack_ocr_wire(rows: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """Minimal outgoing events plus a private reference->original row map."""
    originals: dict[str, dict] = {_row_key(row): row for row in rows}
    output, evidence = [], {}
    for group in compact_ocr_transport(rows):
        keys = group.get("source_row_keys") or [_row_key(group)]
        found = [originals[key] for key in keys if key in originals]
        if not found:
            raise ValueError("OCR_TRANSPORT_SOURCE_UNAVAILABLE")
        token = "e" + sha256("|".join(keys).encode("utf-8")).hexdigest()[:16]
        if token in evidence:
            token += "-" + str(len(output))
        evidence[token] = found
        output.append({
            "id": token,
            "field": str(group.get("field") or "").split(".")[-1],
            "text": str(group.get("raw_text", group.get("candidate", ""))),
        })
    return output, evidence


def resolve_ocr_wire_answer(answer: dict, evidence: dict[str, list[dict]],
                            sent_rows: list[dict]) -> dict:
    """Expand model evidence IDs to raw frames in this request only.

    A forged ID remains invalid for the existing proof verifier. A legacy
    frame ID is permitted only if the original frame was actually sent.
    """
    if not isinstance(answer, dict):
        return answer
    result = deepcopy(answer)
    sent_frames = {r.get("frame_id") for r in sent_rows}
    def expand(refs, *, proof: bool = False):
        expanded = []
        for ref in refs if isinstance(refs, list) else ():
            if not isinstance(ref, str):
                continue
            if ref in evidence:
                group = evidence[ref]
                if proof:
                    group = sorted(group, key=lambda r: (
                        -float(r.get("confidence") or 0),
                        float(r.get("observed_at") or 0)))
                frames = [r.get("frame_id") for r in group]
                if proof:
                    frames = list(dict.fromkeys(frames))[:2]
                for frame in frames:
                    if isinstance(frame, str) and frame not in expanded:
                        expanded.append(frame)
            elif ref in sent_frames or ref not in expanded:
                # Unknown references must not be resolved to any other row;
                # unchanged values fail validation / are rejected by sanitizer.
                if ref not in expanded:
                    expanded.append(ref)
        return expanded
    for event in result.get("state_updates") or []:
        if isinstance(event, dict):
            event["evidence_frames"] = expand(
                event.get("evidence_frames"), proof=True)
    for pending in result.get("pending_ocr_questions") or []:
        if isinstance(pending, dict):
            pending["source_frames"] = expand(pending.get("source_frames"))
    if isinstance(result.get("processed_ocr_frames"), list):
        result["processed_ocr_frames"] = expand(result["processed_ocr_frames"])
    return result
