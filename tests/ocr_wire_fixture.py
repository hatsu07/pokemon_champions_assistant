"""Adapt older mocked GPT replies to the visible Issue #131 wire contract.

Test-only: an actual GPT sees compact IDs, not raw original frame names.
Unknown, stale or unsent references are deliberately NOT remapped.
"""
from copy import deepcopy
import json

from champions_assist.ocr_turn_context import _safe_same_display_text


def reply_with_wire_ids(kwargs, response, original_rows):
    prompt=json.loads(kwargs["content"][0]["text"])
    sent=prompt.get("ocr_candidates_unverified") or []
    result=deepcopy(response)
    mapping={}
    for row in original_rows:
        frame=row.get("frame_id")
        field=str(row.get("field") or "").split(".")[-1]
        raw=str(row.get("raw_text", row.get("candidate","")))
        candidates=[
            item["id"] for item in sent
            if "id" in item and item.get("field")==field
            and _safe_same_display_text(raw,str(item.get("text") or ""),
                                        str(row.get("field") or ""))]
        if len(candidates)==1 and isinstance(frame,str):
            mapping[frame]=candidates[0]
    # The previous question is a separate, explicitly transmitted short ID.
    pending=prompt.get("ocr_pending_questions_unverified") or []
    pending_by_question={
        item.get("question"): item.get("source_frames",[])
        for item in pending if isinstance(item,dict)}
    def convert(refs, question=None):
        replacement=pending_by_question.get(question) if question else None
        return [mapping.get(ref, (replacement[0] if
                isinstance(replacement,list) and len(replacement)==1
                else ref)) for ref in refs]
    for event in result.get("state_updates") or []:
        if isinstance(event,dict):
            event["evidence_frames"]=convert(event.get("evidence_frames") or [])
    for item in result.get("pending_ocr_questions") or []:
        if isinstance(item,dict):
            item["source_frames"]=convert(
                item.get("source_frames") or [],item.get("question"))
    if isinstance(result.get("processed_ocr_frames"),list):
        result["processed_ocr_frames"]=convert(result["processed_ocr_frames"])
    return result
