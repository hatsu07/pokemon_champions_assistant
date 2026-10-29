"""GPT-directed, lossless-local OCR context for one tactical decision.

There is no arbitrary OCR row-count cutoff. The first call receives all newly
captured rows. After that the same tactical model selects unresolved evidence
to retain, while every original reading remains in LocalBattleStateTracker.
This file never interprets Pokémon mechanics or certifies an OCR claim.
"""
from __future__ import annotations

from hashlib import sha256
import json
from threading import Lock
from unicodedata import normalize

from .ocr_ai_evidence import select_ai_ocr_evidence


class OcrSentLedger:
    """Transport facts plus one-shot proof reinspection after an unadopted reply.

    A sent OCR row is NOT a confirmed battle event. Only a request whose reply
    was lost/superseded exposes chosen original frames for ONE subsequent call.
    Match-local threading lock handles late HTTP dispatch after UI retirement.
    """

    def __init__(self):
        self._lock = Lock()
        self._keys: set[str] = set()
        self._request_rows: dict[str, list[dict]] = {}
        self._stale_requests: set[str] = set()
        self._reinspect_keys: set[str] = set()
        self._reinspected_keys: set[str] = set()

    def snapshot(self) -> set[str]:
        with self._lock:
            return set(self._keys)

    def reinspection_keys(self) -> set[str]:
        with self._lock:
            return set(self._reinspect_keys)

    @staticmethod
    def _specific_reinspection_keys(rows: list[dict]) -> set[str]:
        """Choose proof by OCR *region and display*, not Pokémon semantics.

        Preserve two readings per distinct battle message display, which can
        be necessary for corroborating an actual switch. A missing display ID
        is not merged with any other message. For HUD fields select the latest
        reading and its predecessor so changes remain available. This is
        scoped to the lost request, never the whole match history.
        """
        event_fields = ("battle_message", "system_message", "effect_popup",
                        "item_popup", "item_message")
        event_groups: dict[tuple, list[dict]] = {}
        hud_groups: dict[str, list[dict]] = {}
        for index, row in enumerate(rows):
            field = str(row.get("field") or "")
            if field.endswith(event_fields):
                # Same visual display is one event; absent IDs are distinct.
                episode = row.get("display_id") or f"single:{index}"
                group = (field, str(episode))
                event_groups.setdefault(group, []).append(row)
            else:
                hud_groups.setdefault(field, []).append(row)
        chosen: set[str] = set()
        for group in event_groups.values():
            for row in (group[:2] if len(group) >= 2 else group):
                chosen.add(_row_key(row))
        for group in hud_groups.values():
            for row in group[-2:]:
                chosen.add(_row_key(row))
        return chosen

    def _stage_stale(self, request_id: str) -> None:
        rows = self._request_rows.pop(request_id, [])
        self._reinspect_keys.update(
            self._specific_reinspection_keys(rows) - self._reinspected_keys)

    def mark_sent(self, keys, *, request_id: str | None = None,
                  rows: list[dict] | None = None) -> None:
        with self._lock:
            delivered = set(str(key) for key in keys if key)
            self._keys.update(delivered)
            # Each row can be selectively re-shown once; it never becomes NEW.
            revisited = delivered & self._reinspect_keys
            self._reinspect_keys.difference_update(revisited)
            self._reinspected_keys.update(revisited)
            if request_id and rows:
                self._request_rows[request_id] = [
                    dict(row) for row in rows if _row_key(row) in delivered]
                if request_id in self._stale_requests:
                    self._stage_stale(request_id)

    def mark_stale(self, request_id: str | None) -> None:
        if not request_id:
            return
        with self._lock:
            self._stale_requests.add(request_id)
            self._stage_stale(request_id)

    def mark_adopted(self, request_id: str | None) -> None:
        if not request_id:
            return
        with self._lock:
            self._request_rows.pop(request_id, None)
            self._stale_requests.discard(request_id)


def _compare_text(value: str) -> str:
    return "".join(normalize("NFKC", value).split())


def _json_bytes(value) -> int:
    return len(json.dumps(value, ensure_ascii=False,
                          separators=(",", ":"), default=str).encode("utf-8"))


def compact_ocr_transport(rows: list[dict]) -> list[dict]:
    """Lossless *transport* view: join only adjacent normalized exact matches.

    A row is eligible for merging only when an independently determined visual
    display ID is present and unchanged. Fuzzy matching is deliberately off:
    '上がった'/'下がった' can be dangerously similar. If metadata makes a
    group larger than its inputs, send the original rows instead.
    """
    episodes: list[list[dict]] = []
    for row in rows:
        raw = str(row.get("raw_text", row.get("candidate", "")))
        previous = episodes[-1][-1] if episodes else None
        if (previous and row.get("display_id")
                and row["display_id"] == previous.get("display_id")
                and row.get("field") == previous.get("field")
                and _compare_text(raw) == _compare_text(str(
                    previous.get("raw_text", previous.get("candidate", ""))))):
            episodes[-1].append(row)
        else:
            episodes.append([row])

    output = []
    offset = 0
    for episode in episodes:
        if len(episode) == 1:
            output.append(dict(episode[0]))
            offset += 1
            continue
        # Representative stays unverified. Raw variants stay available to GPT.
        counts = {}
        for row in episode:
            raw = str(row.get("raw_text", row.get("candidate", "")))
            counts[raw] = counts.get(raw, 0) + 1
        representative = max(
            episode, key=lambda r: (
                counts[str(r.get("raw_text", r.get("candidate", "")))],
                float(r.get("confidence") or 0)))
        compact = dict(representative)
        raw_rep = str(representative.get(
            "raw_text", representative.get("candidate", "")))
        compact["source_frames"] = [r["frame_id"] for r in episode]
        compact["source_row_keys"] = [_row_key(r) for r in episode]
        # Parallel arrays preserve exact one-to-one identity while avoiding
        # the repetition of long JSON field names for each OCR reading.
        compact["source_sequence_indices"] = list(range(offset, offset + len(episode)))
        compact["source_observed_at"] = [r.get("observed_at") for r in episode]
        compact["source_confidences"] = [r.get("confidence") for r in episode]
        compact["observation_count"] = len(episode)
        compact["variants"] = list(dict.fromkeys(
            str(r.get("raw_text", r.get("candidate", "")))
            for r in episode if str(r.get(
                "raw_text", r.get("candidate", ""))) != raw_rep))
        # Never grow a prompt merely to label a small number of observations.
        # Original evidence remains in LocalBattleStateTracker either way.
        if _json_bytes(compact) < _json_bytes(episode):
            output.append(compact)
        else:
            output.extend(dict(r) for r in episode)
        offset += len(episode)
    return output

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


def has_ocr_transport_evidence(state: dict, local_rows, *,
                               sent_ledger=None) -> bool:
    """Can an unidentified active be evaluated from OCR in the NEXT request?

    This is a transport gate, not a local Pokémon/HP interpretation.
    If req-A sent OCR but its answer was discarded, those rows are sent and
    req-B must use a fresh screenshot or safely abstain, never an empty OCR
    text prompt. Already verified active Pokémon do not need this gate.
    """
    all_rows, _ = select_ai_ocr_evidence(local_rows)
    previous = set(state.get("_ai_ocr_presented_keys") or ())
    if sent_ledger is not None:
        previous.update(sent_ledger.snapshot())
    retained = {
        frame for q in (state.get("_ai_ocr_unresolved") or ())
        if isinstance(q, dict)
        for frame in q.get("source_frames", [])
        if isinstance(frame, str)
    }
    return any(_row_key(row) not in previous or
               row.get("frame_id") in retained for row in all_rows)


def build_ocr_turn_context(state: dict, local_rows, *, sent_ledger=None) -> tuple[list[dict], list[dict], dict]:
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
    if sent_ledger is not None:
        previous.update(sent_ledger.snapshot())
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
    # Relevance affects unresolved questions, NEVER whether OCR was sent.
    relevance_decided = isinstance(proposed, list)
    questions=(sanitize_pending_questions(proposed, known_to_model)
               if relevance_decided else list(pending))
    updated["_ai_ocr_unresolved"] = questions
    retained = {frame for item in questions for frame in item["source_frames"]}
    forbidden = set(rejected_frames) | retained
    ack = set(updated.get("_ai_ocr_ack_frames") or ())
    # Processed-frame acknowledgments predate the carryover field and are a
    # separate, explicit decision. Preserve verified historical ACKs even
    # if an older model/test double omits pending_ocr_questions: only the
    # relevance-based compression must wait for the new field.
    if not block_ack:
        for frame in model_answer.get("processed_ocr_frames") or ():
            # Only accept a frame the model actually received in this call;
            # confirmation still depends on separate state-update validation.
            if isinstance(frame, str) and frame in presented_frames and frame not in forbidden:
                ack.add(frame)
    updated["_ai_ocr_ack_frames"] = sorted(ack)
    return updated
