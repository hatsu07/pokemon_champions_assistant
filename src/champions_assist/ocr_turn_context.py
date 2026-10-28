"""Bounded *transport* view of the lossless local Pokémon Champions OCR ledger.

No battle-text interpretation, state promotion, or evidence destruction happens
here. The model receives new observations, verified state (from the caller),
and a bounded number of source-linked unresolved observations in one action
request. Rows withheld by the transport budget remain in LocalBattleStateTracker
and are eligible on subsequent turns.
"""
from __future__ import annotations

from hashlib import sha256
import json

from .ocr_ai_evidence import select_ai_ocr_evidence

MAX_TURN_OCR_ROWS = 112
MAX_PENDING_EVIDENCE_ROWS = 32  # reserve room for the current actionable turn
MAX_PENDING_QUESTIONS = 16
MAX_PENDING_SOURCE_FRAMES = 4
MAX_QUESTION_CHARS = 180
MAX_RECHECK_CHARS = 120


def _row_key(row: dict) -> str:
    # The full OCR text remains in the local ledger, not in this metadata.
    material = json.dumps(
        (row.get("frame_id", ""), row.get("field", ""),
         row.get("raw_text", row.get("candidate", ""))),
        ensure_ascii=False, separators=(",", ":"))
    return sha256(material.encode("utf-8")).hexdigest()[:24]


def _frames(question: dict) -> list[str]:
    source = question.get("source_frames", [])
    if not isinstance(source, list):
        return []
    return list(dict.fromkeys(
        x for x in source[:MAX_PENDING_SOURCE_FRAMES]
        if isinstance(x, str) and 0 < len(x) <= 120))


def sanitize_pending_questions(questions, available_frames) -> list[dict]:
    """Accept model *questions*, not conclusions, only with local proof IDs."""
    valid = set(available_frames)
    result = []
    for entry in questions if isinstance(questions, list) else ():
        if not isinstance(entry, dict):
            continue
        frames = _frames(entry)
        if not frames or not all(f in valid for f in frames):
            continue
        question = str(entry.get("question") or "").strip()[:MAX_QUESTION_CHARS]
        if not question:
            continue
        importance = entry.get("importance")
        if importance not in ("CRITICAL", "IMPORTANT", "LOW"):
            importance = "IMPORTANT"
        item = {
            "source_frames": frames,
            "question": question,
            "importance": importance,
            "recheck_when": str(entry.get("recheck_when") or "").strip()[:MAX_RECHECK_CHARS],
            "verified": False,
        }
        if item not in result:
            result.append(item)
        if len(result) == MAX_PENDING_QUESTIONS:
            break
    return result


def build_ocr_turn_context(state: dict, local_rows) -> tuple[list[dict], list[dict], dict]:
    """Bound API rows; keep all upstream candidates and every unverified claim.

    Previously presented but unacknowledged OCR stays in the *local* ledger;
    important items are sent again as model-authored questions with source IDs.
    New, never-presented rows always outrank already-presented nonpending rows.
    A backlog is gradually consumed on subsequent turn decisions.
    """
    all_rows, _ = select_ai_ocr_evidence(local_rows)
    total = len(all_rows)
    available = {row.get("frame_id") for row in all_rows}
    pending = sanitize_pending_questions(
        state.get("_ai_ocr_unresolved") or [], available)
    pending_frames = {
        frame for item in pending for frame in item["source_frames"]}
    previous = set(state.get("_ai_ocr_presented_keys") or ())
    pending_rows = [row for row in all_rows
                    if row.get("frame_id") in pending_frames]
    unseen = [row for row in all_rows if _row_key(row) not in previous]
    # First cover evidence for unresolved critical questions. Then prioritize
    # recent never-seen events (new turn) while reserving capacity for the
    # oldest never-presented backlog so old events eventually get a hearing.
    ordered = []
    taken = set()
    def take(row):
        key = _row_key(row)
        if key in taken or len(ordered) >= MAX_TURN_OCR_ROWS:
            return
        ordered.append(row)
        taken.add(key)
    # Old unresolved evidence must not consume the entire current-turn
    # budget. The questions themselves are also passed to the AI by source ID.
    for row in pending_rows[-MAX_PENDING_EVIDENCE_ROWS:]:
        take(row)
    # Source metadata can identify a conflict without interpreting its text.
    # Preserve up to eight conflicting observations in addition to pending.
    conflicts = [r for r in all_rows if r.get("certainty", r.get("status")) == "CONFLICT"]
    for row in conflicts[-8:]:
        take(row)
    unseen_remaining = [r for r in unseen if _row_key(r) not in taken]
    if len(unseen_remaining) <= MAX_TURN_OCR_ROWS - len(ordered):
        for row in unseen_remaining:
            take(row)
    else:
        remaining = MAX_TURN_OCR_ROWS - len(ordered)
        oldest_quota = min(24, remaining // 4)
        for row in unseen_remaining[:oldest_quota]:
            take(row)
        for row in unseen_remaining[-(remaining-oldest_quota):]:
            take(row)
    # No AI acknowledgment means still UNVERIFIED, not permanently
    # consumed. When the unseen backlog is drained, revisit previous
    # unacknowledged evidence; never mark it as confirmed or delete it.
    for row in reversed(all_rows):
        if len(ordered) >= MAX_TURN_OCR_ROWS:
            break
        take(row)
    sent = sorted(ordered, key=lambda r: (
        float(r.get("observed_at")) if type(r.get("observed_at")) in (int, float)
        else 0.0, str(r.get("frame_id") or "")))
    sent_frames = {r.get("frame_id") for r in sent}
    all_frame_counts = {}
    sent_frame_counts = {}
    for row in all_rows:
        frame = row.get("frame_id")
        all_frame_counts[frame] = all_frame_counts.get(frame, 0) + 1
    for row in sent:
        frame = row.get("frame_id")
        sent_frame_counts[frame] = sent_frame_counts.get(frame, 0) + 1
    partial = sorted(f for f in sent_frames if
                     sent_frame_counts.get(f) != all_frame_counts.get(f))
    before_bytes = len(json.dumps(all_rows, ensure_ascii=False,
                                  separators=(",", ":")).encode("utf-8"))
    after_bytes = len(json.dumps(sent, ensure_ascii=False,
                                 separators=(",", ":")).encode("utf-8"))
    meta = {
        "ledger_rows_available": total,
        "api_rows_sent": len(sent),
        "rows_deferred": total - len(sent),
        "new_rows_sent": sum(_row_key(r) not in previous for r in sent),
        "unresolved_questions": len(pending),
        "partial_frames": partial,
        "ledger_json_bytes": before_bytes,
        "api_json_bytes": after_bytes,
        "ledger_not_mutated": True,
        "ledger_frames": sorted(f for f in available if f),
        "presented_keys": [_row_key(r) for r in sent],
    }
    return sent, pending, meta
