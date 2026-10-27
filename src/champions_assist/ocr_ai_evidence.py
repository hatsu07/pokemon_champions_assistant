"""Bounded chronological OCR evidence for one AI tactical request.

Only chooses OCR *regions*, recency and capture frames. Game-text meaning
belongs to ChatGPT, never this module. Preserve battle messages even when
HUD noise dominates OCR volume; do not blindly slice the first/last N rows.
"""
from __future__ import annotations

from math import isfinite

MAX_AI_OCR_ROWS=36
MAX_OCR_HISTORY=144
OCR_LOOKBACK_SECONDS=95.0
_MESSAGE_SUFFIXES=("battle_message","system_message")
_EFFECT_SUFFIXES=("item_popup","effect_popup","ability_popup")


def _frame_id(row):
    if row.get("frame_id"):
        return str(row["frame_id"])
    ids=row.get("evidence_ids")
    return str(ids[0]) if isinstance(ids,(list,tuple)) and ids else ""


def _at(row, default):
    value=row.get("observed_at")
    if type(value) in (int,float) and isfinite(value) and value>=0:
        return float(value)
    return float(default)


def _representative(items, count, early=0):
    """Stable old/new coverage within each kind of OCR region."""
    if len(items)<=count:
        return list(items)
    early=min(early,count//3)
    head=items[:early]
    tail=items[-(count-early):]
    return head+tail


def select_ai_ocr_evidence(rows, *, max_rows=MAX_AI_OCR_ROWS):
    """Return (chronological evidence, diagnostic counters).

    At most 24 battle text rows and 6 popup/6 HUD rows are initially
    reserved, with unused slots filled from the newest remaining rows.
    All slots share the same cap and are allocated without parsing prose.
    """
    if not 1<=max_rows<=MAX_AI_OCR_ROWS:
        raise ValueError("OCR AI row cap must be in 1..36")
    source=[r for r in rows or () if isinstance(r,dict)
            and _frame_id(r) and r.get("field")
            and (r.get("raw_text") is not None or "candidate" in r)]
    indexed=sorted(enumerate(source),key=lambda row:(
        _at(row[1],row[0]),row[0]))
    if indexed:
        last=max(_at(row,i) for i,row in indexed)
        # Keep event history for up to approximately two turns. A caller
        # without capture timestamps still works through stable input order.
        indexed=[(i,r) for i,r in indexed if
                 "observed_at" not in r or
                 last-_at(r,i)<=OCR_LOOKBACK_SECONDS]
    unique={}
    for i,r in indexed:
        key=(_frame_id(r),str(r["field"]),
             str(r.get("raw_text") or r.get("candidate") or ""))
        # Preserve strongest reading when a frame/ROI is duplicated.
        old=unique.get(key)
        if old is None or (r.get("confidence") or 0)>(
                old[1].get("confidence") or 0):
            unique[key]=(i,r)
    ordered=sorted(unique.values(),key=lambda t:(_at(t[1],t[0]),t[0]))
    messages=[x for x in ordered if str(x[1]["field"]).endswith(_MESSAGE_SUFFIXES)]
    popups=[x for x in ordered if str(x[1]["field"]).endswith(_EFFECT_SUFFIXES)]
    hud=[x for x in ordered if x not in messages and x not in popups]
    kept=[]
    def reserve(pool,n,early):
        for row in _representative(pool,min(n,max_rows-len(kept)),early):
            if row not in kept:
                kept.append(row)
    reserve(messages,24,4)
    reserve(popups,6,0)
    reserve(hud,6,0)
    selected_keys={(i,_frame_id(r),str(r.get("field")))
                   for i,r in kept}
    if len(kept)<max_rows:
        remainder=[x for x in ordered
                   if (x[0],_frame_id(x[1]),str(x[1].get("field")))
                   not in selected_keys]
        kept.extend(remainder[-(max_rows-len(kept)):])
    kept=sorted(kept,key=lambda t:(_at(t[1],t[0]),t[0]))
    selected=[dict(x[1]) for x in kept]
    selected_ids={id(x[1]) for x in kept}
    excluded=[x[1] for x in ordered if id(x[1]) not in selected_ids]
    return selected,{
        "source_count":len(source),
        "eligible_count":len(ordered),
        "sent_count":len(selected),
        "dropped_count":len(source)-len(selected),
        "battle_messages_sent":sum(
            str(x.get("field","")).endswith(_MESSAGE_SUFFIXES)
            for x in selected),
        "selected_frames":list(dict.fromkeys(
            _frame_id(x) for x in selected))[-36:],
        "dropped_frames":list(dict.fromkeys(
            _frame_id(x) for x in excluded))[-12:],
    }
