"""Candidate enumeration through the existing replay validator (one rule source).

The validator rejects known illegal commands but does not prove legality under
unknown effects. Only observed, current-window actions with explicit constraint
confirmation are promotable to LEGAL; otherwise UNKNOWN is not recommendable.
"""
from __future__ import annotations

from typing import Any

from .models import (
    ActionKind, CandidateAction, FrozenBattleState, LegalStatus, WindowKind,
    fingerprint,
)


def _validate(answer: dict, snapshot: FrozenBattleState, assets: Any) -> tuple[str, dict]:
    from ..match_replay import validate_battle_choice
    return validate_battle_choice(answer, snapshot.observation, snapshot.state, assets)


def _candidate(snapshot: FrozenBattleState, kind: ActionKind, move: str = "",
               target: str = "") -> CandidateAction:
    payload = {"action_kind": kind.value, "move": move, "switch_target": target}
    return CandidateAction(fingerprint(payload), kind, move, target,
                           window_id=snapshot.window.decision_window_id)


def _with_status(candidate: CandidateAction, status: LegalStatus,
                 reason: str) -> CandidateAction:
    return CandidateAction(candidate.action_id, candidate.kind, candidate.move,
                           candidate.switch_target, status, reason, candidate.window_id)


def _classify(candidate: CandidateAction, snapshot: FrozenBattleState,
              assets: Any) -> CandidateAction:
    w = snapshot.window
    kind = candidate.kind
    if candidate.window_id != w.decision_window_id:
        return _with_status(candidate, LegalStatus.ILLEGAL, "WRONG_WINDOW")
    if kind == ActionKind.MOVE and w.window_kind != WindowKind.MOVE_COMMAND:
        return _with_status(candidate, LegalStatus.ILLEGAL, "WRONG_WINDOW_KIND")
    if kind == ActionKind.SWITCH and w.window_kind not in (
            WindowKind.NORMAL_SWITCH, WindowKind.PIVOT_TARGET,
            WindowKind.FAINT_REPLACEMENT, WindowKind.EFFECT_REPLACEMENT):
        return _with_status(candidate, LegalStatus.ILLEGAL, "WRONG_WINDOW_KIND")
    if kind == ActionKind.PIVOT:
        # The target is an action in a FUTURE input window, after resolution.
        return _with_status(candidate, LegalStatus.UNKNOWN, "FUTURE_PIVOT_TARGET")
    code, _ = _validate(candidate.replay_answer(), snapshot, assets)
    if code != "OK":
        return _with_status(candidate, LegalStatus.ILLEGAL, code)
    if candidate.action_id not in snapshot.verified_action_ids:
        return _with_status(candidate, LegalStatus.UNKNOWN, "NOT_IN_OBSERVED_ALLOWED_SET")
    if not snapshot.constraints_confirmed:
        return _with_status(candidate, LegalStatus.UNKNOWN, "UNVERIFIED_CONSTRAINTS")
    obs = snapshot.observation
    if kind == ActionKind.MOVE:
        if obs.get("visual_ui") != "MOVE_MENU":
            return _with_status(candidate, LegalStatus.UNKNOWN, "MOVE_WINDOW_NOT_VISIBLE")
        pp = ((snapshot.state.get("battle_evidence") or {})
              .get("pp_by_pokemon", {}).get("own", {})
              .get(snapshot.state.get("our_active", ""), {}).get(candidate.move))
        if pp is None or not isinstance(pp, int) or pp <= 0:
            return _with_status(candidate, LegalStatus.UNKNOWN, "PP_NOT_POSITIVELY_VERIFIED")
    else:
        if obs.get("visual_ui") != "PARTY_MENU" or obs.get("_local_screen_only"):
            return _with_status(candidate, LegalStatus.UNKNOWN, "PARTY_WINDOW_NOT_VISIBLE")
    return _with_status(candidate, LegalStatus.LEGAL, "VALIDATED_IN_CURRENT_WINDOW")


def generate_candidates(snapshot: FrozenBattleState, assets: Any) -> tuple[CandidateAction, ...]:
    """Generate from registered moves/selected bench only, never Showdown inference."""
    state, obs = snapshot.state, snapshot.observation
    w = snapshot.window
    candidates = []
    if w.window_kind == WindowKind.MOVE_COMMAND:
        from ..match_replay import _member_for
        member = _member_for(state.get("our_active", ""), assets)
        if member is not None:
            for n in range(1, 5):
                move = member.get(f"move{n}", "")
                if move:
                    candidates.append(_candidate(snapshot, ActionKind.MOVE, move=move))
    else:
        from ..match_replay import _bench_candidates
        for name in _bench_candidates(state):
            candidates.append(_candidate(snapshot, ActionKind.SWITCH, target=name))
    # Preserve rejected candidates for debugging, never turn NO_ACTION into a command.
    return tuple(_classify(c, snapshot, assets) for c in candidates)


def revalidate_candidate(candidate: CandidateAction, snapshot: FrozenBattleState,
                         assets: Any) -> CandidateAction:
    """Fresh proof at display time; no cached LEGAL decision survives a window delta."""
    result = _classify(candidate, snapshot, assets)
    if candidate.window_id != snapshot.window.decision_window_id:
        return _with_status(result, LegalStatus.ILLEGAL, "STALE_WINDOW")
    return result
