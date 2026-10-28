"""GPT-directed, lossless-local OCR context for one tactical decision.

There is no arbitrary OCR row-count cutoff. The first call receives all newly
captured rows. After that the same tactical model selects unresolved evidence
to retain, while every original reading remains in LocalBattleStateTracker.
This file never interprets Pokémon mechanics or certifies an OCR claim.
"""
from __future__ import annotations

from hashlib import sha256
import json

from .ocr_ai_evidence import select_ai_ocr_evidence


def _row_key(row: dict) -> str:
    """Opaque local presentation marker; raw text remains in the tracker."""
    material = json.dumps(
        (row.get("frame_id", ""), row.get("field", ""),
         row.get("raw_text", row.get("candidate", ""))),
        ensure_ascii=False, separators=(",", ":"))
    return sha256(material.encode("utf-8")).hexdigest()[:24]


def sanitize_pending_questions(questions, available_frames) -> list[dict]:
    """Keep GPT-selected unresolved questions only when their proof exists.

    No local importance ranking, quota, or age heuristic decides relevance.
    A question is never treated as a confirmed battle fact.
    """
    valid = set(available_frames)
    result = []
    for entry in questions if isinstance(questions, list) else ():
        if not isinstance(entry, dict):
            continue
        source = entry.get("source_frames")
        if not isinstance(source, list) or not source:
            continue
        frames = list(dict.fromkeys(
            frame for frame in source
            if isinstance(frame, str) and frame and frame in valid))
        # Reject the entire question rather than silently accepting a subset
        # of fabricated, unavailable, or malformed provenance frame IDs.
        if len(frames) != len(source):
            continue
        question = entry.get("question")
        if not isinstance(question, str) or not question.strip():
            continue
        importance = entry.get("importance")
        if importance not in ("CRITICAL", "IMPORTANT", "LOW"):
            importance = "IMPORTANT"
        recheck = entry.get("recheck_when")
        item = {
            "source_frames": frames,
            "question": question.strip(),
            "importance": importance,
            "recheck_when": recheck.strip() if isinstance(recheck, str) else "",
            "verified": False,
        }
        if item not in result:
            result.append(item)
    return result


def build_ocr_turn_context(state: dict, local_rows) -> tuple[list[dict], list[dict], dict]:
    """Return NEW OCR + source rows for GPT-retained unresolved questions.

    Every row from the complete local OCR ledger is eligible the first time.
    On later turns do not resend previously presented rows unless the model's
    prior response *explicitly* retained their source frame in an unresolved
    question. A newly observed row on an older frame is still new evidence.
    The model's omission only affects the transport view, not the ledger,
    verification status, or ability to explicitly revisit source records.
    """
    all_rows, _ = select_ai_ocr_evidence(local_rows)
    available = {r.get("frame_id") for r in all_rows}
    # Previously validated source IDs remain valid as *references* even if
    # the local OCR snapshot is temporarily unavailable (reader restart).
    # Never fabricate the missing raw rows or mark them confirmed.
    prior=state.get("_ai_ocr_unresolved") or []
    previously_cited={f for q in prior if isinstance(q,dict)
                      for f in q.get("source_frames",[])
                      if isinstance(f,str) and f}
    pending = sanitize_pending_questions(prior,available | previously_cited)
    retained_frames = {f for item in pending for f in item["source_frames"]}
    previous = set(state.get("_ai_ocr_presented_keys") or ())
    selected = [row for row in all_rows if
                _row_key(row) not in previous
                or row.get("frame_id") in retained_frames]
    before_bytes = len(json.dumps(all_rows, ensure_ascii=False,
                                  separators=(",", ":")).encode("utf-8"))
    after_bytes = len(json.dumps(selected, ensure_ascii=False,
                                 separators=(",", ":")).encode("utf-8"))
    new_count = sum(_row_key(r) not in previous for r in selected)
    return selected, pending, {
        "ledger_rows_available": len(all_rows),
        "api_rows_sent": len(selected),
        "new_rows_sent": new_count,
        "unresolved_rows_resent": len(selected)-new_count,
        "prior_observations_not_resent": len(all_rows)-len(selected),
        "unresolved_questions": len(pending),
        "ledger_json_bytes": before_bytes,
        "api_json_bytes": after_bytes,
        "ledger_not_mutated": True,
        "ledger_frames": sorted(f for f in available if f),
        "presented_keys": [_row_key(r) for r in selected],
    }


def apply_model_ocr_carryover(
    state: dict, model_answer: dict, local_rows, selected_rows, pending,
    *, rejected_frames=(), block_ack=False, commit_presentation=True,
) -> dict:
    """Apply *one* tactical reply's transport bookkeeping to a detached state.

    Both text-only and image fallback use this same rule. This updates no
    battle facts: verified state changes still go through their existing
    independent validator. Merely showing a frame never means validating it.
    """
    from copy import deepcopy

    updated = deepcopy(state)
    presented_frames = {row.get("frame_id") for row in selected_rows}
    known_to_model = presented_frames | {
        frame for question in pending for frame in question["source_frames"]}
    proposed = model_answer.get("pending_ocr_questions")
    # The model must explicitly return a relevance decision (even []) before
    # any OCR is considered *presented*. An older/malformed reply that omits
    # the field has NOT evaluated whether old evidence can be omitted.
    relevance_decided = isinstance(proposed, list)
    questions=(sanitize_pending_questions(proposed, known_to_model)
               if relevance_decided else list(pending))
    updated["_ai_ocr_unresolved"] = questions
    retained = {frame for item in questions for frame in item["source_frames"]}
    if commit_presentation and relevance_decided:
        seen = set(updated.get("_ai_ocr_presented_keys") or ())
        seen.update(_row_key(row) for row in selected_rows)
        updated["_ai_ocr_presented_keys"] = sorted(seen)
    forbidden = set(rejected_frames) | retained
    ack = set(updated.get("_ai_ocr_ack_frames") or ())
    if not block_ack and relevance_decided:
        for frame in model_answer.get("processed_ocr_frames") or ():
            # Only accept a frame the model actually received in this call;
            # confirmation still depends on separate state-update validation.
            if isinstance(frame, str) and frame in presented_frames and frame not in forbidden:
                ack.add(frame)
    updated["_ai_ocr_ack_frames"] = sorted(ack)
    return updated
