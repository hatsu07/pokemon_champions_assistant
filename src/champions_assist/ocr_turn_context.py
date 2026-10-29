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
    """Match-local OCR transport, exclusive reservations and evidence safety.

    Reserved rows are NOT sent/confirmed. The reservation survives a logical
    retirement until the old worker either releases it or the HTTP transport
    confirms a send; newer workers must not transmit those same rows.
    """

    # Reinspection is bounded, not a replay of the local OCR archive.
    MAX_REINSPECT_ROWS = 24
    MAX_REINSPECT_BYTES = 8192

    def __init__(self):
        self._lock = Lock()
        self._keys: set[str] = set()
        self._reserved: dict[str, set[str]] = {}
        self._request_rows: dict[str, list[dict]] = {}
        self._stale_requests: set[str] = set()
        self._reinspect_keys: set[str] = set()
        self._reinspected_keys: set[str] = set()
        self._needs_visual_confirmation = False

    def snapshot(self) -> set[str]:
        with self._lock:
            return set(self._keys)

    def unavailable_keys(self, request_id: str | None = None) -> set[str]:
        """All keys another in-flight request owns (never treated as sent)."""
        with self._lock:
            return {key for owner, keys in self._reserved.items()
                    if owner != request_id for key in keys}

    def reinspection_keys(self) -> set[str]:
        with self._lock:
            return set(self._reinspect_keys)

    def needs_visual_confirmation(self) -> bool:
        with self._lock:
            return self._needs_visual_confirmation

    def is_stale(self, request_id: str | None) -> bool:
        with self._lock:
            return bool(request_id and request_id in self._stale_requests)

    def reserve(self, request_id: str | None, rows: list[dict]) -> list[dict]:
        """Atomically claim an OCR delta immediately before request assembly.

        Late req-A and new req-B cannot reserve the same row. An older worker
        that was already retired gets no transport evidence at all.
        """
        if not request_id:
            return rows
        with self._lock:
            if request_id in self._stale_requests:
                return []
            owned_elsewhere = {
                key for owner, keys in self._reserved.items()
                if owner != request_id for key in keys
            }
            claimed = [row for row in rows
                       if _row_key(row) not in owned_elsewhere]
            self._reserved.setdefault(request_id, set()).update(
                _row_key(row) for row in claimed)
            return claimed

    def release(self, request_id: str | None) -> None:
        """Release only keys whose send has NOT been confirmed."""
        if not request_id:
            return
        with self._lock:
            self._reserved.pop(request_id, None)

    @staticmethod
    def _specific_reinspection_keys(rows: list[dict]) -> tuple[set[str], bool]:
        """Sample ONE failed request, not all match history, without inventing
        OCR meaning or equating repeated same text to the same battle event.

        For missing display IDs consecutive exact strings can be *sampled*,
        but never described as one event. The unabridged originals stay local.
        If important proof exceeds budget, require visual confirmation.
        """
        event_fields = ("battle_message", "system_message", "effect_popup",
                        "item_popup", "item_message")
        event_groups: dict[tuple, list[dict]] = {}
        hud_groups: dict[str, list[dict]] = {}
        for index, row in enumerate(rows):
            field = str(row.get("field") or "")
            if field.endswith(event_fields):
                raw = _compare_text(str(row.get(
                    "raw_text", row.get("candidate", ""))))
                display = row.get("display_id")
                # No display ID: group adjacent exact text only for sampling;
                # preserve a new group on changes, including opposite messages.
                if display:
                    episode = ("verified", str(display))
                else:
                    previous = rows[index-1] if index else None
                    prev_field = str(previous.get("field") or "") if previous else ""
                    prev_raw = _compare_text(str(previous.get(
                        "raw_text", previous.get("candidate", "")))) if previous else ""
                    prev_id = previous.get("display_id") if previous else None
                    if previous is not None and not prev_id and (
                            prev_field == field and prev_raw == raw):
                        episode = ("unknown", index-1)
                        # Use the previous group ID to continue a run.
                        if index >= 2:
                            for group_key, group_rows in reversed(list(event_groups.items())):
                                if group_rows[-1] is previous:
                                    episode = group_key[1]
                                    break
                    else:
                        episode = ("unknown", index)
                event_groups.setdefault((field, episode, raw), []).append(row)
            else:
                hud_groups.setdefault(field, []).append(row)
        chosen: list[dict] = []
        for group in event_groups.values():
            chosen.extend(group[:1] + group[-1:] if len(group) > 1 else group)
        for group in hud_groups.values():
            chosen.extend(group[-2:])
        # Chronological order, preserving time information and distinct events.
        ordinal = {id(row): n for n, row in enumerate(rows)}
        chosen.sort(key=lambda row: ordinal[id(row)])
        unique = []
        seen: set[str] = set()
        for row in chosen:
            key = _row_key(row)
            if key not in seen:
                unique.append(row)
                seen.add(key)
        overflow = False
        if len(unique) > OcrSentLedger.MAX_REINSPECT_ROWS:
            overflow = True
            unique = unique[-OcrSentLedger.MAX_REINSPECT_ROWS:]
        while unique and _json_bytes(unique) > OcrSentLedger.MAX_REINSPECT_BYTES:
            overflow = True
            unique.pop(0)
        return {_row_key(row) for row in unique}, overflow

    def _stage_stale(self, request_id: str) -> None:
        rows = self._request_rows.pop(request_id, [])
        keys, overflow = self._specific_reinspection_keys(rows)
        if overflow or keys & self._reinspected_keys:
            # The retry was already consumed, or the original set was too
            # large to show safely. Do not issue a text-only tactical decision.
            self._needs_visual_confirmation = True
        self._reinspect_keys.update(keys - self._reinspected_keys)

    def mark_sent(self, keys, *, request_id: str | None = None,
                  rows: list[dict] | None = None) -> None:
        with self._lock:
            delivered = set(str(key) for key in keys if key)
            self._keys.update(delivered)
            revisited = delivered & self._reinspect_keys
            self._reinspect_keys.difference_update(revisited)
            self._reinspected_keys.update(revisited)
            if request_id:
                self._reserved.pop(request_id, None)
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
            # Do not release a reservation here: its worker may already be
            # in client.responses.create(). Only that worker may release it.
            self._stage_stale(request_id)

    def mark_adopted(self, request_id: str | None, *,
                     visual_confirmed: bool = False) -> None:
        if not request_id:
            return
        with self._lock:
            self._request_rows.pop(request_id, None)
            self._reserved.pop(request_id, None)
            self._stale_requests.discard(request_id)
            if visual_confirmed:
                self._needs_visual_confirmation = False
                self._reinspect_keys.clear()
                self._reinspected_keys.clear()


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


def _own_actor_identification_candidate(row: dict) -> bool:
    """Check OCR input *kind*, never locally certify a Pokémon identity.

    HP/percentage, timer, and random effect text alone cannot establish a
    first-turn own actor. A HUD name or an outgoing 'go' message is evidence
    GPT can interpret. Substantial OCR typos may still yield NO_ACTION.
    """
    field = str(row.get("field") or "").lower()
    if field.endswith(("own_active_name", "own_pokemon_name",
                       "own_name", "own_species")):
        text = str(row.get("raw_text", row.get("candidate", ""))).strip(
            "!！?？。._- :：")
        return len(text) >= 2
    if not field.endswith("battle_message"):
        return False
    text = _compare_text(str(row.get("raw_text", row.get("candidate", ""))))
    for marker in ("ゆけっ", "ゆげっ", "いけっ", "ゆけ", "ゆげ", "いけ"):
        if marker in text:
            name_fragment = text.split(marker, 1)[1].strip("!！?？。._- :：")
            # Do not locally resolve the name. Only check for enough OCR
            # material for GPT to infer one; 'ゆけっ！' alone is insufficient.
            return len(name_fragment) >= 3 or bool(row.get("detected_pokemon"))
    return False


def has_ocr_transport_evidence(state: dict, local_rows, *,
                               sent_ledger=None) -> bool:
    """True only if this request can SEND an own-actor identification clue.

    Unknown actor + HP-only OCR must use vision/abstain. A reinspection row
    explicitly chosen from a stale request may serve as a clue once more.
    """
    all_rows, _ = select_ai_ocr_evidence(local_rows)
    previous = set(state.get("_ai_ocr_presented_keys") or ())
    reinspect = set()
    unavailable = set()
    if sent_ledger is not None:
        if sent_ledger.needs_visual_confirmation():
            return False
        previous.update(sent_ledger.snapshot())
        reinspect = sent_ledger.reinspection_keys()
        unavailable = sent_ledger.unavailable_keys()
    retained = {
        frame for q in (state.get("_ai_ocr_unresolved") or ())
        if isinstance(q, dict)
        for frame in q.get("source_frames", [])
        if isinstance(frame, str)
    }
    return any((_row_key(row) not in previous
                or _row_key(row) in reinspect
                or row.get("frame_id") in retained)
               and _row_key(row) not in unavailable
               and _own_actor_identification_candidate(row)
               for row in all_rows)


def build_ocr_turn_context(state: dict, local_rows, *, sent_ledger=None,
                           request_id: str | None = None) -> tuple[list[dict], list[dict], dict]:
    """Return NEW OCR, GPT-retained questions, and one-shot stale proof.

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
    reinspect_keys = set()
    if sent_ledger is not None:
        previous.update(sent_ledger.snapshot())
        reinspect_keys = sent_ledger.reinspection_keys()
    selected = [row for row in all_rows if
                _row_key(row) not in previous
                or _row_key(row) in reinspect_keys
                or row.get("frame_id") in retained_frames]
    if sent_ledger is not None and request_id is not None:
        # Claim OCR rows atomically; selection alone does not mark them sent.
        # Other concurrent requests cannot send our claimed originals.
        selected = sent_ledger.reserve(request_id, selected)
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
        "stale_reinspection_rows": sum(
            _row_key(row) in reinspect_keys for row in selected),
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
