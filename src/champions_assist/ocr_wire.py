"""Minimal, request-scoped GPT OCR evidence; raw observations stay local."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from hashlib import sha256
from uuid import uuid4
import json

from .ocr_turn_context import _safe_same_display_text, compact_ocr_transport


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          default=str).encode("utf-8"))


def _scope(request_id):
    return (sha256(request_id.encode("utf-8")).hexdigest()[:12]
            if request_id else uuid4().hex[:12])


def pack_ocr_wire(rows: list[dict], *,
                  request_id: str | None = None) -> tuple[list[dict], dict[str, list[dict]]]:
    """Group safe visual episodes; keep original rows in private mapping."""
    episodes = []
    for row in rows:
        prev = episodes[-1][-1] if episodes else None
        if (prev and row.get("display_id")
                and row["display_id"] == prev.get("display_id")
                and row.get("field") == prev.get("field")
                and _safe_same_display_text(
                    str(row.get("raw_text", row.get("candidate", ""))),
                    str(prev.get("raw_text", prev.get("candidate", ""))),
                    str(row.get("field") or ""))):
            episodes[-1].append(row)
        else:
            episodes.append([row])
    output, evidence = [], {}
    scope = _scope(request_id)
    for i, episode in enumerate(episodes):
        token = f"e{scope}{i:x}"
        evidence[token] = list(episode)
        counts = Counter(str(r.get("raw_text", r.get("candidate", "")))
                         for r in episode)
        rep = max(episode, key=lambda r: (
            counts[str(r.get("raw_text", r.get("candidate", "")))],
            float(r.get("confidence") or 0)))
        output.append({"id": token,
                       "field": str(rep.get("field") or "").split(".")[-1],
                       "text": str(rep.get("raw_text", rep.get("candidate", "")))})
    legacy = compact_ocr_transport(rows)
    baseline = legacy if _size(legacy) <= _size(rows) else rows
    if _size(output) > _size(baseline):
        return [dict(x) for x in baseline], {}
    return output, evidence


def pack_pending_ocr_wire(pending: list[dict], *,
                          request_id: str | None = None
                          ) -> tuple[list[dict], dict[str, list[str]]]:
    """No raw frame IDs are sent in unresolved question references."""
    output, refs = [], {}
    scope = _scope(request_id)
    for i, question in enumerate(pending):
        frames = [f for f in question.get("source_frames", ())
                  if isinstance(f, str) and f]
        if not frames:
            continue
        key = f"p{scope}{i:x}"
        refs[key] = list(dict.fromkeys(frames))
        output.append({"source_frames": [key],
                       "question": question.get("question", ""),
                       "importance": question.get("importance", "IMPORTANT"),
                       "recheck_when": question.get("recheck_when", "")})
    return output, refs


def explicit_legacy_ocr_refs(transmitted: list[dict]) -> set[str]:
    """Only raw IDs literally included in the wire fallback are allowed."""
    result = set()
    for row in transmitted:
        if "id" in row:
            continue
        if isinstance(row.get("frame_id"), str):
            result.add(row["frame_id"])
        result.update(f for f in row.get("source_frames", ())
                      if isinstance(f, str))
    return result


def resolve_ocr_wire_answer(answer: dict, evidence: dict[str, list[dict]],
                            sent_rows: list[dict], *,
                            pending_refs: dict[str, list[str]] | None = None,
                            explicit_legacy_refs: set[str] | None = None) -> dict:
    """Reject invisible IDs; never ACK an ambiguous group or shared frame."""
    if not isinstance(answer, dict):
        return answer
    result = deepcopy(answer)
    pending_refs = pending_refs or {}
    explicit_legacy_refs = explicit_legacy_refs or set()
    frame_counts = Counter(row.get("frame_id") for row in sent_rows)
    def expand(refs, purpose):
        expanded = []
        for ref in refs if isinstance(refs, list) else ():
            if not isinstance(ref, str):
                return None
            if ref in evidence:
                group = evidence[ref]
                if purpose == "ack" and (
                        len(group) != 1
                        or frame_counts[group[0].get("frame_id")] != 1):
                    continue
                if purpose == "proof":
                    group = sorted(group, key=lambda r: (
                        -float(r.get("confidence") or 0),
                        float(r.get("observed_at") or 0)))[:2]
                frames = [r.get("frame_id") for r in group]
            elif purpose == "pending" and ref in pending_refs:
                frames = pending_refs[ref]
            elif ref in explicit_legacy_refs:
                frames = [ref]
            else:
                return None
            for frame in frames:
                if isinstance(frame, str) and frame not in expanded:
                    expanded.append(frame)
        return expanded
    for event in result.get("state_updates") or ():
        if isinstance(event, dict):
            event["evidence_frames"] = (
                expand(event.get("evidence_frames"), "proof") or [])
    proposed = result.get("pending_ocr_questions")
    if isinstance(proposed, list):
        for question in proposed:
            if not isinstance(question, dict):
                result["pending_ocr_questions"] = None
                break
            frames = expand(question.get("source_frames"), "pending")
            if not frames:
                result["pending_ocr_questions"] = None
                break
            question["source_frames"] = frames
    if isinstance(result.get("processed_ocr_frames"), list):
        result["processed_ocr_frames"] = (
            expand(result["processed_ocr_frames"], "ack") or [])
    return result
