"""#141⑥ OFF-MATCH audit of actual Qt GUI *clock trace*, never game truth.

The real MatchReplayDialog already exports local monotonic timing events.
This pure analyzer binds first observed turn screen -> dispatched GPT request
-> non-superseded result -> GUI rendered recommendation by exact opaque
match/request/window/revision. It measures actual callback event intervals,
NOT a video-verified game clock, legal Champions action, or LOCAL_SHADOW
display. No live callback invokes this function.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

SCHEMA = "champions-local-ui-timing-candidates-v1"
SOURCE = "UNVERIFIED_LOCAL_GUI_MONOTONIC_CLOCK"
_URGENT = frozenset({"TURN_ACTION", "SWITCH_SELECTION"})
_DISPLAYED = frozenset({"ADVICE", "PROVISIONAL_ADVICE"})


@dataclass(frozen=True)
class GuiTurnTraceAudit:
    status: str
    reason: str
    observed_turns: int = 0
    complete_gpt_display_turns: int = 0
    late_gpt_display_turns: int = 0
    missing_or_unmatched_turns: int = 0
    p50_gpt_screen_ms: int | None = None
    p95_gpt_screen_ms: int | None = None
    p99_gpt_screen_ms: int | None = None
    # Only the captured Qt callbacks are measured. The game turn clock and
    # whether an action is legal remain beyond the trace's authority.
    actual_device_video_aligned: bool = False
    local_shadow_ui_display_verified: bool = False
    actionable_champions_advice_verified: bool = False
    phase2_accepted: bool = False
    # Opaque local GUI match label, not independent actual game identity.
    source_match_id: str = ""


def _refuse(reason: str) -> GuiTurnTraceAudit:
    return GuiTurnTraceAudit("GUI_TRACE_REFUSED", reason)


def _pct(values: list[int], p: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, ceil(len(ordered) * p) - 1)]


def audit_gui_timing_export(export: Any) -> GuiTurnTraceAudit:
    """Never use inferred timestamps or material from GPT response text.

    Multiple dispatched requests per turn (OCR re-evaluation) are expected;
    only an exact same-window result/render chain is counted. Multiple rendered
    recommendations in one turn are refused, not interpreted as a valid first
    choice. Event identities and source labels can be user-edited; independent
    video review is therefore still mandatory.
    """
    if not isinstance(export, dict):
        return _refuse("GUI_EXPORT_OBJECT_REQUIRED")
    packet = export.get("timing_observations", export)
    if (not isinstance(packet, dict)
            or packet.get("schema") != SCHEMA
            or packet.get("source") != SOURCE
            or packet.get("automatically_certified") is not False):
        return _refuse("UNATTESTED_GUI_TIMING_EXPORT_REQUIRED")
    events = packet.get("events")
    if (not isinstance(events, list) or not 1 <= len(events) <= 3000):
        return _refuse("MISSING_OR_OVERSIZED_GUI_EVENTS")
    postmatch_start_jobs = {
        "RESULT_SCREEN_GPT_REQUEST": "result_screen",
        "OCR_MATCH_RESULT_TEXT_AI_SENT": "result",
    }
    allowed = frozenset({
        "TURN_SCREEN_OBSERVED", "AI_REQUEST_DISPATCH",
        "AI_RESULT_DISPLAYED", "ACTION_GUI_RENDERED",
        "AI_REQUEST_LOGICALLY_CANCELLED", "NON_TURN_RESULT_AI_COMPLETED",
        *postmatch_start_jobs,
    })
    turns: dict[int, int] = {}
    dispatches: dict[str, tuple[str, int, int, str, int]] = {}
    replies: dict[str, tuple[str, int, int, str, int, bool]] = {}
    displays: dict[int, int] = {}
    # Selection, result-screen and other nonurgent requests use the explicit
    # "selection" decision-window label in the real GUI's event exporter.
    # Track those request IDs and their responses, not blanket-ignore events
    # lacking a turn epoch (which could hide a corrupt action dispatch).
    nonturn_requests: dict[str, str] = {}
    # Match-end verification is not a battle action. The real GUI sends
    # dedicated postmatch start events (not AI_REQUEST_DISPATCH), then a
    # dedicated completion event. Bind exact IDs so arbitrary bad turn results
    # cannot be silently skipped as "non-turn".
    postmatch_requests: dict[str, tuple[str, int, str]] = {}
    postmatch_completed: set[str] = set()
    postmatch_cancelled: set[str] = set()
    current_turn_request: dict[int, str] = {}
    cancelled_requests: set[str] = set()
    last_ms = -1
    known_match: str | None = None
    for event in events:
        if not isinstance(event, dict) or type(event.get("at_ms")) is not int:
            return _refuse("INVALID_GUI_EVENT_TIME_OR_TYPE")
        now = event["at_ms"]
        if now < last_ms or now < 0:
            return _refuse("NONMONOTONIC_GUI_EVENT_TIMES")
        last_ms = now
        name = event.get("event")
        if not isinstance(name, str):
            return _refuse("INVALID_GUI_EVENT_NAME")
        if name not in allowed:
            continue
        request = event.get("request_id")
        if name in postmatch_start_jobs:
            match = event.get("match_id")
            turn = event.get("turn_epoch")
            if (not isinstance(request, str)
                    or not 1 <= len(request) <= 128
                    or not isinstance(match, str)
                    or not 1 <= len(match) <= 128
                    or type(turn) is not int or turn < 0
                    or request in nonturn_requests
                    or request in postmatch_requests
                    or request in dispatches
                    or (known_match is not None and known_match != match)):
                return _refuse("INVALID_EXPLICIT_POSTMATCH_REQUEST")
            postmatch_requests[request] = (
                match, turn, postmatch_start_jobs[name])
            continue
        if name == "AI_REQUEST_LOGICALLY_CANCELLED" and (
                isinstance(request, str) and request in postmatch_requests):
            # The real GUI's retired result-screen / OCR-result worker
            # emits turn_epoch=None (or an old active turn epoch); the
            # identity is the unique postmatch request ID, whose start
            # already binds the game, turn and job. Never reinterpret this
            # as a battle-turn cancellation, nor forgive unbound cancels.
            bound_match, _, bound_job = postmatch_requests[request]
            cancel_turn = event.get("turn_epoch")
            if (request in postmatch_cancelled
                    or request in postmatch_completed
                    or (cancel_turn is not None
                        and (type(cancel_turn) is not int
                             or cancel_turn < 0))
                    or (known_match is not None
                        and known_match != bound_match)
                    or (event.get("match_id") is not None
                        and event["match_id"] != bound_match)
                    or (event.get("active_job") is not None
                        and event["active_job"] != bound_job)
                    or not isinstance(event.get("reason"), str)
                    or not event["reason"]):
                return _refuse("INVALID_POSTMATCH_CANCELLED_REQUEST")
            postmatch_cancelled.add(request)
            continue
        if name == "NON_TURN_RESULT_AI_COMPLETED":
            match = event.get("match_id")
            turn = event.get("turn_epoch")
            started = (postmatch_requests.get(request)
                       if isinstance(request, str) else None)
            if (started is None
                    or type(turn) is not int
                    or (match, turn, event.get("active_job")) != started
                    or request in postmatch_completed
                    or request in postmatch_cancelled
                    or not isinstance(event.get("status"), str)):
                return _refuse("UNBOUND_OR_DUPLICATED_POSTMATCH_RESULT")
            postmatch_completed.add(request)
            continue
        if (name == "AI_REQUEST_DISPATCH"
                and event.get("decision_window_id") == "selection"
                and event.get("screen_label") not in _URGENT):
            # Explicit non-turn dispatch (selection/PREP/result screen).
            # A TURN_ACTION with a bogus "selection" window is NOT skipped.
            match = event.get("match_id")
            turn = event.get("turn_epoch")
            if (not isinstance(event.get("screen_label"), str)
                    or not isinstance(request, str)
                    or not 1 <= len(request) <= 128
                    or not isinstance(match, str) or len(match) > 128
                    or type(turn) is not int or turn < 0
                    or request in nonturn_requests or request in dispatches
                    or request in postmatch_requests):
                return _refuse("INVALID_EXPLICIT_NON_ACTION_DISPATCH")
            nonturn_requests[request] = match
            continue
        if name in ("AI_RESULT_DISPLAYED", "AI_REQUEST_LOGICALLY_CANCELLED"):
            if isinstance(request, str) and request in nonturn_requests:
                if (name == "AI_RESULT_DISPLAYED"
                        and event.get("match_id") != nonturn_requests[request]):
                    return _refuse("NON_ACTION_RESPONSE_MATCH_MISMATCH")
                continue
        turn = event.get("turn_epoch")
        if type(turn) is not int or turn <= 0:
            return _refuse("INVALID_GUI_TURN_EPOCH")
        if name == "TURN_SCREEN_OBSERVED":
            if event.get("screen_label") not in _URGENT or turn in turns:
                return _refuse("DUPLICATE_OR_UNSUPPORTED_TURN_SCREEN")
            turns[turn] = now
            continue
        if turn not in turns or now < turns[turn]:
            return _refuse("GUI_EVENT_WITHOUT_OBSERVED_TURN")
        # The GUI emits this when a worker is retired. It has request and
        # turn_epoch but intentionally no match/window/revision fields.
        if name == "AI_REQUEST_LOGICALLY_CANCELLED":
            if (not isinstance(request, str)
                    or request not in dispatches
                    or dispatches[request][1] != turn
                    or request in cancelled_requests):
                return _refuse("INVALID_GPT_CANCELLED_REQUEST")
            cancelled_requests.add(request)
            if current_turn_request.get(turn) == request:
                current_turn_request.pop(turn)
            continue
        match = event.get("match_id")
        rev = event.get("state_revision")
        window = event.get("decision_window_id")
        if (not isinstance(request, str) or not 1 <= len(request) <= 128
                or not isinstance(match, str) or not 1 <= len(match) <= 128
                or type(rev) is not int or rev < 0
                or (name != "AI_RESULT_DISPLAYED"
                    and window != "turn-" + str(turn))):
            return _refuse("MISSING_EXACT_GUI_WINDOW_REQUEST_ID")
        if known_match is None:
            known_match = match
        elif known_match != match:
            return _refuse("MULTIPLE_MATCHES_IN_ONE_GUI_TIMING_EXPORT")
        if name == "AI_REQUEST_DISPATCH":
            if (request in dispatches or request in nonturn_requests
                    or event.get("screen_label") not in _URGENT):
                return _refuse("DUPLICATED_OR_NONURGENT_GPT_DISPATCH")
            # An old request must not count after a new dispatch for this
            # same observed game turn, even without explicit cancel telemetry.
            previous = current_turn_request.get(turn)
            if previous is not None:
                cancelled_requests.add(previous)
            dispatches[request] = (match, turn, rev, window, now)
            current_turn_request[turn] = request
        elif name == "AI_RESULT_DISPLAYED":
            dispatched = dispatches.get(request)
            if (dispatched is None
                    or dispatched[:3] != (match, turn, rev)
                    or now < dispatched[4]
                    or request in replies
                    or request in cancelled_requests
                    or current_turn_request.get(turn) != request):
                return _refuse("UNMATCHED_OR_DUPLICATED_GPT_RESULT")
            status = event.get("status")
            if not isinstance(status, str):
                return _refuse("INVALID_GPT_RESULT_STATUS")
            replies[request] = (
                match, turn, rev, status, now,
                event.get("recommendation_present") is True)
        else:  # ACTION_GUI_RENDERED
            dispatched = dispatches.get(request)
            replied = replies.get(request)
            if (dispatched is None or replied is None
                    or dispatched[:4] != (match, turn, rev, window)
                    or replied[:3] != (match, turn, rev)
                    or request in cancelled_requests
                    or current_turn_request.get(turn) != request
                    or now < replied[4]
                    or event.get("status") not in _DISPLAYED
                    or replied[3] != event.get("status")
                    or not replied[5]
                    or event.get("recommendation_present") is not True
                    or turn in displays):
                return _refuse("UNMATCHED_OR_DUPLICATED_GPT_GUI_RENDER")
            displays[turn] = now - turns[turn]
    if not turns:
        return _refuse("NO_ACTUAL_QT_TURN_SCREEN_EVENTS")
    durations = list(displays.values())
    overdue = sum(t > 45_000 for t in durations)
    return GuiTurnTraceAudit(
        "GUI_MONOTONIC_TIMING_ONLY",
        "QT_CALLBACK_TIMESTAMPS_NOT_INDEPENDENT_GAME_VIDEO",
        len(turns), len(displays), overdue, len(turns) - len(displays),
        _pct(durations, .50) if durations else None,
        _pct(durations, .95) if durations else None,
        _pct(durations, .99) if durations else None,
        source_match_id=known_match or "",
    )
