"""Read-only opt-in GPT/LOCAL_SHADOW bridge for a *real* UI dispatch.

Only claims the current GUI dispatched a GPT request with a frozen observed
projection. Screen classifier labels are NOT legally verified move menus,
and a real Champions Battle/RNG cannot be reconstructed from OCR.
The diagnostic therefore abstains until a separately verified source-bound
engine request is available. No GPT outputs, raw OCR, or frames are stored.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from time import monotonic
from typing import Any, Mapping

from ..preload import FrozenMatchAssets, REQUIRED_MASTER_FILES
from .models import (
    DecisionWindow, FrozenBattleState, WindowKind, action_digest,
)


@dataclass(frozen=True)
class CapturedShadowWindow:
    status: str
    reason: str
    snapshot: FrozenBattleState | None = None
    # The capture is an app event, not an authenticated game Battle.
    game_battle_authenticated: bool = False
    legal_actions_verified: bool = False


class FrozenRevisionLatch:
    """Worker-safe in-memory freshness check, independent of Qt access."""

    def __init__(self, snapshot: FrozenBattleState):
        if not isinstance(snapshot, FrozenBattleState):
            raise ValueError("FROZEN_SNAPSHOT_REQUIRED")
        self._snapshot: FrozenBattleState | None = snapshot
        self._lock = Lock()

    def current(self) -> FrozenBattleState:
        with self._lock:
            if self._snapshot is None:
                raise ValueError("LOCAL_SHADOW_WINDOW_SUPERSEDED")
            return self._snapshot

    def invalidate(self) -> None:
        with self._lock:
            self._snapshot = None


def capture_gpt_dispatch_window(
    assets: FrozenMatchAssets, *,
    match_id: str,
    request_id: str,
    turn_epoch: int,
    state_revision: int,
    screen_label: str,
    observed_state: Mapping[str, Any],
    deadline_monotonic: float,
    now: float | None = None,
) -> CapturedShadowWindow:
    """Freeze a minimum observed snapshot without *inventing* legal choices.

    Uses exactly the four preloaded sources. Only directly known actor/opponent
    and selected-three labels are copied; any unsupported/malformed reading
    is refused. An unknown active actor remains empty, NOT a guessed Pokémon.
    """
    if not isinstance(assets, FrozenMatchAssets):
        return CapturedShadowWindow("REFUSED", "FOUR_FROZEN_MASTERS_REQUIRED")
    if (not isinstance(observed_state, Mapping) or screen_label not in
            ("TURN_ACTION", "SWITCH_SELECTION")
            or not isinstance(match_id, str) or not match_id
            or not isinstance(request_id, str) or not request_id
            or type(turn_epoch) is not int or turn_epoch < 0
            or type(state_revision) is not int or state_revision < 0
            or not isinstance(deadline_monotonic, (float, int))
            or isinstance(deadline_monotonic, bool)):
        return CapturedShadowWindow("REFUSED", "MALFORMED_DISPATCH_WINDOW")
    current = monotonic() if now is None else now
    if current >= deadline_monotonic:
        return CapturedShadowWindow("REFUSED", "INPUT_DEADLINE_ALREADY_PASSED")
    manifest = tuple((key, assets.sha256.get(key, ""))
                     for key in REQUIRED_MASTER_FILES)
    if (len(assets.own_team) != 6 or
            any(len(v) != 64 or not all(c in "0123456789abcdef" for c in v)
                for _, v in manifest)):
        return CapturedShadowWindow("REFUSED", "FROZEN_MASTER_MANIFEST_INVALID")
    our_active = observed_state.get("our_active")
    opponent_active = observed_state.get("opponent_active")
    selected = observed_state.get("selected_three")
    if (our_active is not None and not isinstance(our_active, str)
            or opponent_active is not None and not isinstance(opponent_active, str)
            or selected is not None and
            (not isinstance(selected, (tuple, list))
             or len(selected) != 3
             or any(not isinstance(x, str) for x in selected))):
        return CapturedShadowWindow("REFUSED", "OBSERVED_STATE_TYPE_INVALID")
    try:
        # Deliberately do not treat a 6-class classifier label as confirmed
        # MOVE_MENU/PARTY_MENU or assign any legally verified action IDs.
        regulation = str(assets.own_team[0].get("reg") or "UNDECLARED")
        window = DecisionWindow(
            match_id=match_id,
            decision_window_id=f"shadow-{turn_epoch}-{state_revision}-{request_id}",
            window_kind=(WindowKind.MOVE_COMMAND if screen_label == "TURN_ACTION"
                         else WindowKind.NORMAL_SWITCH),
            turn_epoch=turn_epoch, state_revision=state_revision,
            screen_kind="UNKNOWN",
            active_pokemon=our_active or "",
            team_sha256=manifest[-1][1],
            four_master_sha256=manifest,
            regulation=regulation,
            simulator_sha="UNVERIFIED_SHOWDOWN_GAME_STATE",
            allowed_action_digest=action_digest(()),
            request_id=request_id,
            deadline_monotonic=float(deadline_monotonic),
        )
        # A real GPT dispatch occurred but the raw current UI remains
        # untrusted until a distinct legal-action/PP source is reviewed.
        state = {
            "our_active": our_active or "",
            "opponent_active": opponent_active or "",
            "selected_three": list(selected) if selected is not None else [],
        }
        snapshot = FrozenBattleState.freeze(
            window, state,
            {"visual_ui": "UNKNOWN", "screen_label_observed": screen_label},
            verified_action_ids=(), constraints_confirmed=False,
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return CapturedShadowWindow("REFUSED", "FROZEN_WINDOW_BUILD_FAILED")
    return CapturedShadowWindow("CAPTURED_UNVERIFIED_GAME_STATE",
                                "NO_PPVISIBILITY_LEGALITY_OR_RNG_AUTHORITY",
                                snapshot)
