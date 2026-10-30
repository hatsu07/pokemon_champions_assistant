"""GPT-only OCR transport: small references, lossless match-local originals.

Do not infer game state from OCR text. The existing validators receive the
original frames and confidence after expansion of model-returned evidence IDs.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256

from .ocr_turn_context import _row_key, _safe_same_display_text


def pack_ocr_wire(rows: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """Minimal outgoing events plus a private reference->original row map."""
    # Do NOT use the legacy metadata-heavy transport objects as a grouping
    # oracle: for a 2-3-frame event they may be larger than separate originals.
    # Group *local records* first; serialize only a minimal separate wire view.
    episodes: list[list[dict]] = []
    for row in rows:
        previous = episodes[-1][-1] if episodes else None
        if (previous and row.get("display_id")
                and row["display_id"] == previous.get("display_id")
                and row.get("field") == previous.get("field")
                and _safe_same_display_text(
                    str(row.get("raw_text",row.get("candidate",""))),
                    str(previous.get("raw_text",previous.get("candidate",""))),
                    str(row.get("field") or ""))):
            episodes[-1].append(row)
        else:
            episodes.append([row])
    output, evidence = [], {}
    for episode in episodes:
        keys = [_row_key(row) for row in episode]
        token = "e" + sha256("|".join(keys).encode("utf-8")).hexdigest()[:16]
        if token in evidence:
            token += "-" + str(len(output))
        evidence[token] = list(episode)
        # Representative is only an OCR observation, not a confirmed event.
        # Majority text and confidence break ties deterministically.
        frequencies = {}
        for row in episode:
            literal = str(row.get("raw_text",row.get("candidate","")))
            frequencies[literal] = frequencies.get(literal,0)+1
        representative = max(episode, key=lambda row: (
            frequencies[str(row.get("raw_text",row.get("candidate","")))],
            float(row.get("confidence") or 0)))
        output.append({
            "id": token,
            "field": str(representative.get("field") or "").split(".")[-1],
            "text": str(representative.get("raw_text",
                                        representative.get("candidate", ""))),
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
