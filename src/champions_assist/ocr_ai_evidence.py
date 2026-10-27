"""Chronological, lossless selection of acquired local OCR text for ChatGPT.

The app does not interpret battle text. Duplicate readings of the *same*
frame/ROI are collapsed, but no row is evicted by age, region or count.
Previously AI-acknowledged rows are filtered by the battle tracker upstream.
"""
from __future__ import annotations
from math import isfinite


def _frame_id(row):
    if row.get("frame_id"):
        return str(row["frame_id"])
    ids=row.get("evidence_ids")
    return str(ids[0]) if isinstance(ids,(list,tuple)) and ids else ""


def _when(row,index):
    value=row.get("observed_at")
    if type(value) in (int,float) and isfinite(value) and value>=0:
        return float(value)
    return float(index)


def select_ai_ocr_evidence(rows):
    """Return every distinct OCR candidate in acquisition-time order.

    Never infer Pokémon, moves, the significance of a message or whether
    an OCR reading is reliable. The tactical model interprets the original
    text; its cited observations must pass independent state verification.
    """
    source=list(rows or ())
    unique={}
    for i,row in enumerate(source):
        if not isinstance(row,dict) or not row.get("field"):
            continue
        frame=_frame_id(row)
        if not frame or (row.get("raw_text") is None and
                         "candidate" not in row):
            continue
        key=(frame,str(row["field"]),str(
            row.get("raw_text") if row.get("raw_text") is not None
            else row.get("candidate")))
        # Retain the strongest *same-frame* reading, never drop different
        # frames just because the battle text repeats.
        old=unique.get(key)
        if old is None or (row.get("confidence") or 0) > (
                old[1].get("confidence") or 0):
            unique[key]=(i,row)
    ordered=sorted(unique.values(),key=lambda pair:(
        _when(pair[1],pair[0]),pair[0]))
    selected=[dict(row) for _,row in ordered]
    frames=list(dict.fromkeys(_frame_id(row) for row in selected))
    return selected,{
        "source_count":len(source),
        "eligible_count":len(ordered),
        "sent_count":len(selected),
        "dropped_count":len(source)-len(selected),
        "battle_messages_sent":sum(
            str(row.get("field","")).endswith((
                "battle_message","system_message")) for row in selected),
        "selected_frames":frames,
        "dropped_frames":[],
    }
