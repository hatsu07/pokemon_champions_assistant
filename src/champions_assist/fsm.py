"""Event-driven match and decision-window state machines.

This module accepts already-classified observations from a future observer.  It
does not inspect pixels, infer a game mechanic, select tactics, or control the
game.  Its only choices are safety gates: whether an observed player input window
requires a model decision, is covered by a still-valid pivot plan, or is blocked.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
from pathlib import Path
from typing import Any, Protocol

from .preload import FrozenMatchAssets, MasterPreloader
from .prep import PrepInput, PrepResult, PrepValidation, build_prep_input, validate_prep_result
from .reducer import canonical_json
from .store import BattleStore
from .wire import AllowedActions, PROTOCOL_VERSION


class MatchPhase(str, Enum):
    S00_WAIT_START = "S00_WAIT_START"
    S01_PRELOAD_4_FILES = "S01_PRELOAD_4_FILES"
    S02_MATCHMAKING = "S02_MATCHMAKING"
    S03_TEAM_SELECTION = "S03_TEAM_SELECTION"
    S04_PREP_40S = "S04_PREP_40S"
    S05_IN_BATTLE = "S05_IN_BATTLE"
    S06_MATCH_END_COMMIT = "S06_MATCH_END_COMMIT"
    S07_REVIEW = "S07_REVIEW"
    S08_NEXT_MATCH_READY = "S08_NEXT_MATCH_READY"


class BattleObservationState(str, Enum):
    B00_BATTLE_ENTER = "B00_BATTLE_ENTER"
    B10_OBSERVE_BOARD = "B10_OBSERVE_BOARD"
    B20_DETECT_CHOICE_UI = "B20_DETECT_CHOICE_UI"
    B30_WAIT_PLAYER_ACTION = "B30_WAIT_PLAYER_ACTION"
    B40_RESOLVE_ACTIONS = "B40_RESOLVE_ACTIONS"
    B50_AUTO_FORCED_SWITCH = "B50_AUTO_FORCED_SWITCH"
    B60_NEXT_PHASE_RECONCILE = "B60_NEXT_PHASE_RECONCILE"
    BX_SCREEN_UNCERTAIN = "BX_SCREEN_UNCERTAIN"
    B90_BATTLE_END = "B90_BATTLE_END"


class VisualUI(str, Enum):
    TEAM_SELECTION_UI = "TEAM_SELECTION_UI"
    COMMAND_MENU = "COMMAND_MENU"
    MOVE_MENU = "MOVE_MENU"
    PARTY_MENU = "PARTY_MENU"
    DETAIL_OVERLAY = "DETAIL_OVERLAY"
    ACTION_RESOLUTION = "ACTION_RESOLUTION"
    RESULT_SCREEN = "RESULT_SCREEN"
    UNKNOWN = "UNKNOWN"


class WindowKind(str, Enum):
    NORMAL_COMMAND = "NORMAL_COMMAND"
    PIVOT_TARGET = "PIVOT_TARGET"
    FAINT_REPLACEMENT = "FAINT_REPLACEMENT"
    EFFECT_REPLACEMENT = "EFFECT_REPLACEMENT"
    OTHER_CHOICE = "OTHER_CHOICE"


class WindowStatus(str, Enum):
    OPEN = "OPEN"
    REQUESTED = "REQUESTED"
    RECOMMENDED = "RECOMMENDED"
    REUSED_PLAN = "REUSED_PLAN"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


class ObservationEvent(str, Enum):
    OPPONENT_LEAD_VISIBLE = "OPPONENT_LEAD_VISIBLE"
    OUR_PIVOT_SUCCEEDED = "OUR_PIVOT_SUCCEEDED"
    OUR_FAINTED = "OUR_FAINTED"
    EFFECT_SWITCH_CHOICE_AVAILABLE = "EFFECT_SWITCH_CHOICE_AVAILABLE"
    RANDOM_FORCED_SWITCH = "RANDOM_FORCED_SWITCH"
    EFFECT_AUTO_SWITCH = "EFFECT_AUTO_SWITCH"
    MATCH_END_CONFIRMED = "MATCH_END_CONFIRMED"


class SessionInactiveError(RuntimeError):
    pass


@dataclass(frozen=True)
class Deadline:
    created_at_ms: int
    budget_ms: int | None

    @property
    def expires_at_ms(self) -> int | None:
        return None if self.budget_ms is None else self.created_at_ms + self.budget_ms

    def remaining_ms(self, now_ms: int) -> int | None:
        expires_at_ms = self.expires_at_ms
        return None if expires_at_ms is None else max(0, expires_at_ms - now_ms)

    def expired(self, now_ms: int) -> bool:
        return self.remaining_ms(now_ms) == 0


@dataclass
class DecisionWindow:
    match_id: str
    decision_window_id: str
    game_turn_seq: str | None
    kind: WindowKind
    cause_event_id: str | None
    allowed_actions: AllowedActions
    tactical_state_version: int
    deadline: Deadline
    status: WindowStatus
    requires_model: bool
    recommendation: dict[str, Any] | None = None
    request_id: str | None = None

    @property
    def allowed_action_digest(self) -> str:
        return self.allowed_actions.digest(self.kind.value)

    @property
    def request_key(self) -> tuple[str, str, int, str]:
        return (self.match_id, self.decision_window_id, self.tactical_state_version, self.allowed_action_digest)


@dataclass(frozen=True)
class PivotPlan:
    target: str
    dependency_signature: str
    based_on_state_version: int
    source_window_id: str


@dataclass(frozen=True)
class SelectionRecommendation:
    selected_three: tuple[str, str, str]
    lead: str


@dataclass(frozen=True)
class DecisionRequest:
    """A local mock-provider request, not a complete Phase 3 transport payload."""

    request_id: str
    match_id: str
    decision_window_id: str
    phase: str
    tactical_state_version: int
    observation_revision: int
    allowed_actions: dict[str, Any]
    allowed_action_digest: str
    deadline_ms_remaining: int
    source_manifest: tuple[dict[str, str], ...]
    previous_pivot_target: str | None

    def immutable_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MockDecision:
    """Scripted stand-in for OpenAI; it never derives a recommendation itself."""

    decision_status: str
    action_kind: str
    move: str | None = None
    switch_target: str | None = None
    planned_pivot_target: str | None = None
    mega: bool = False
    warning: str | None = None


class DecisionProvider(Protocol):
    def decide(self, request: DecisionRequest) -> MockDecision: ...


def _deadline_budget(default_ms: int, actual_ui_remaining_ms: int | None, *, reserve_ms: int = 0) -> int:
    """AI display deadline, retaining time for manual player input."""
    if actual_ui_remaining_ms is None:
        return default_ms
    if actual_ui_remaining_ms < 0:
        raise ValueError("actual UI time remaining cannot be negative")
    return max(0, min(default_ms, actual_ui_remaining_ms - reserve_ms))


class MatchSession:
    """One match's state. All calls take observed facts, never inferred tactics."""

    def __init__(
        self,
        *,
        match_id: str,
        assets: FrozenMatchAssets,
        now_ms: int,
        store: BattleStore | None = None,
    ) -> None:
        self.match_id = match_id
        self.assets = assets
        self.store = store
        self.phase = MatchPhase.S02_MATCHMAKING
        self.battle_state = BattleObservationState.B00_BATTLE_ENTER
        self.game_turn_seq: str | None = None
        self.decision_seq = 0
        self.observation_seq = 0
        self.tactical_state_version = 0
        self._active = True
        self._event_counter = 0
        self._seen_observation_ids: set[str] = set()
        self._state_facts: dict[str, Any] = {}
        self._state_signature = self._signature_for_state()
        self._pending_party_kind: WindowKind | None = None
        self._pending_party_event_id: str | None = None
        self.current_window: DecisionWindow | None = None
        self.last_pivot_plan: PivotPlan | None = None
        self.opponent_preview_six: tuple[str, str, str, str, str, str] | None = None
        self.matrix_rows_six: tuple[str, str, str, str, str, str] | None = None
        self.selection_recommendation: SelectionRecommendation | None = None
        self.actual_selected_three: tuple[str, str, str] | None = None
        self.actual_lead: str | None = None
        self.selection_deadline: Deadline | None = None
        self.selection_model_expired = False
        self.prep_deadline: Deadline | None = None
        self.prep_input: PrepInput | None = None
        self.prep_result: PrepResult | None = None
        self.prep_validation: PrepValidation | None = None
        self._request_keys: set[tuple[str, str, int, str]] = set()
        self._request_seq = 0
        if store is not None:
            store.freeze_match_assets(match_id, contents=assets.contents, sha256=assets.sha256)
        self._audit("MATCH_SESSION_STARTED", now_ms=now_ms)

    def _require_active(self) -> None:
        if not self._active:
            raise SessionInactiveError("session is inactive; start a newly preloaded match")

    def _audit(self, kind: str, **payload: Any) -> None:
        self._event_counter += 1
        if self.store is not None:
            self.store.append_event(
                self.match_id,
                f"fsm:{self.match_id}:{self._event_counter}",
                "NOTE",
                {"kind": kind, **payload},
            )

    def _signature_for_state(self) -> str:
        return "sha256:" + hashlib.sha256(canonical_json(self._state_facts).encode("utf-8")).hexdigest()

    @property
    def state_facts(self) -> dict[str, Any]:
        """A detached observer projection for v2.3 request construction."""
        return dict(self._state_facts)

    def update_observed_state(
        self,
        observation_id: str,
        facts: dict[str, Any],
        *,
        source_kind: str = "UI_DIRECT",
        quality: str = "CONFIRMED",
    ) -> bool:
        """Record an observer-confirmed state revision; no field is strategically ranked."""
        self._require_active()
        if observation_id in self._seen_observation_ids:
            return False
        self._seen_observation_ids.add(observation_id)
        self.observation_seq += 1
        if self.store is not None:
            for index, (field_name, value) in enumerate(sorted(facts.items()), start=1):
                self.store.append_evidence(
                    self.match_id,
                    f"{observation_id}:evidence:{index}",
                    field=field_name,
                    value=value,
                    source_kind=source_kind,
                    quality=quality,
                )
        before = self._state_signature
        self._state_facts.update(facts)
        self._state_signature = self._signature_for_state()
        if self._state_signature != before:
            self.tactical_state_version += 1
            if self.current_window and self.current_window.status in {WindowStatus.RECOMMENDED, WindowStatus.REQUESTED}:
                self.current_window.status = WindowStatus.STALE
        self._audit("OBSERVATION", observation_id=observation_id, changed=self._state_signature != before)
        return True

    def set_game_turn(self, game_turn_seq: str) -> None:
        self._require_active()
        if not game_turn_seq:
            raise ValueError("game turn identifier must be non-empty")
        self.game_turn_seq = game_turn_seq
        self._audit("GAME_TURN_OBSERVED", game_turn_seq=game_turn_seq)

    def confirm_turn_end(self, event_id: str, turn_id: str) -> bool:
        """Delegate idempotent turn accounting to the Phase 1 event store."""
        self._require_active()
        if self.store is None:
            return False
        applied = self.store.append_event(self.match_id, event_id, "TURN_END_CONFIRMED", {"turn_id": turn_id})
        self._audit("TURN_END_CONFIRMED", event_id=event_id, turn_id=turn_id, applied=applied)
        return applied

    def receive_opponent_preview(
        self,
        opponent_preview_six: tuple[str, str, str, str, str, str],
        *,
        now_ms: int,
        actual_ui_remaining_ms: int | None = None,
    ) -> tuple[str, ...]:
        self._require_active()
        if self.phase is not MatchPhase.S02_MATCHMAKING:
            raise RuntimeError("opponent preview is only accepted from matchmaking")
        rows = self.assets.matrix_rows_for(opponent_preview_six)
        self.opponent_preview_six = opponent_preview_six
        self.matrix_rows_six = rows
        self.phase = MatchPhase.S03_TEAM_SELECTION
        self.selection_deadline = Deadline(now_ms, _deadline_budget(90_000, actual_ui_remaining_ms))
        self._audit("OPPONENT_PREVIEW_SIX", opponents=list(opponent_preview_six), matrix_rows=6)
        return rows

    def record_selection_recommendation(self, selected_three: tuple[str, str, str], lead: str) -> None:
        self._require_active()
        if self.phase is not MatchPhase.S03_TEAM_SELECTION:
            raise RuntimeError("selection recommendation is not currently expected")
        if len(selected_three) != 3 or len(set(selected_three)) != 3 or lead not in selected_three:
            raise ValueError("selection must have three distinct Pokémon and an included lead")
        if any(name not in self.assets.own_team_names for name in selected_three):
            raise ValueError("selection contains a Pokémon outside the frozen team")
        self.selection_recommendation = SelectionRecommendation(selected_three, lead)
        self._audit("SELECTION_RECOMMENDED", selected_three=list(selected_three), lead=lead)

    def record_actual_selection(
        self,
        selected_three: tuple[str, str, str],
        lead: str,
        *,
        now_ms: int,
        actual_ui_remaining_ms: int | None = None,
    ) -> PrepInput:
        self._require_active()
        if self.phase is not MatchPhase.S03_TEAM_SELECTION or self.opponent_preview_six is None or self.matrix_rows_six is None:
            raise RuntimeError("actual selection requires an opponent preview")
        if len(selected_three) != 3 or len(set(selected_three)) != 3 or lead not in selected_three:
            raise ValueError("actual selection must have three distinct Pokémon and an included lead")
        if any(name not in self.assets.own_team_names for name in selected_three):
            raise ValueError("actual selection contains a Pokémon outside the frozen team")
        self.actual_selected_three = selected_three
        self.actual_lead = lead
        self.prep_input = build_prep_input(selected_three, lead, self.opponent_preview_six, self.matrix_rows_six)
        # Historical enum name is retained for wire compatibility; the
        # advisory PREP clock shares the original full 90-second preview.
        selection_started_ms = (
            self.selection_deadline.created_at_ms
            if self.selection_deadline is not None else now_ms)
        shared_remaining_ms = max(0, 90_000 - (now_ms - selection_started_ms))
        self.prep_deadline = Deadline(
            now_ms,
            _deadline_budget(shared_remaining_ms, actual_ui_remaining_ms),
        )
        self.phase = MatchPhase.S04_PREP_40S
        differs = self.selection_recommendation is not None and self.selection_recommendation.selected_three != selected_three
        self._audit("ACTUAL_SELECTION_LOCKED", selected_three=list(selected_three), lead=lead, differs_from_model=differs)
        return self.prep_input

    def correct_actual_selection(
        self,
        selected_three: tuple[str, str, str],
        lead: str,
        *,
        observation_id: str,
    ) -> None:
        """Append a late observed selection correction without editing history.

        A correction after S03 can invalidate PREP and a current recommendation,
        but it must not invent a replacement selection or restart a deadline.
        """
        self._require_active()
        if self.phase is MatchPhase.S03_TEAM_SELECTION:
            raise RuntimeError("use record_actual_selection while team selection is active")
        if self.phase not in {MatchPhase.S04_PREP_40S, MatchPhase.S05_IN_BATTLE, MatchPhase.S06_MATCH_END_COMMIT, MatchPhase.S07_REVIEW}:
            raise RuntimeError("selection correction is not available for this match phase")
        if self.opponent_preview_six is None or self.matrix_rows_six is None:
            raise RuntimeError("selection correction requires the frozen opponent preview")
        if len(selected_three) != 3 or len(set(selected_three)) != 3 or lead not in selected_three:
            raise ValueError("actual selection must have three distinct Pokémon and an included lead")
        if any(name not in self.assets.own_team_names for name in selected_three):
            raise ValueError("selection contains a Pokémon outside the frozen team")
        self.actual_selected_three = selected_three
        self.actual_lead = lead
        self.prep_input = build_prep_input(selected_three, lead, self.opponent_preview_six, self.matrix_rows_six)
        self.prep_result = None
        self.prep_validation = PrepValidation(False, "ACTUAL_SELECTION_CORRECTED_REPREP_REQUIRED")
        self.update_observed_state(
            observation_id,
            {"actual_selected_three": list(selected_three), "actual_lead": lead},
            source_kind="MANUAL_VERIFIED",
            quality="CONFIRMED",
        )
        self._audit("ACTUAL_SELECTION_CORRECTED", selected_three=list(selected_three), lead=lead)

    def enforce_deadlines(self, *, now_ms: int) -> tuple[str, ...]:
        """Expire only model/display work; never synthesize a player action."""
        self._require_active()
        expired: list[str] = []
        if self.phase is MatchPhase.S03_TEAM_SELECTION and self.selection_deadline and self.selection_deadline.expired(now_ms):
            self.selection_model_expired = True
            expired.append("SELECTION")
        if self.phase is MatchPhase.S04_PREP_40S and self.prep_deadline and self.prep_deadline.expired(now_ms):
            expired.append("PREP")
        if self.current_window and self.current_window.status in {WindowStatus.OPEN, WindowStatus.REQUESTED, WindowStatus.RECOMMENDED}:
            if self.current_window.deadline.expired(now_ms):
                expired.append("DECISION_WINDOW")
        if expired:
            self._audit("DEADLINE_EXPIRED", kinds=expired, now_ms=now_ms)
        return tuple(expired)

    def record_prep_result(self, result: PrepResult, *, now_ms: int) -> PrepValidation:
        self._require_active()
        if self.phase is not MatchPhase.S04_PREP_40S or self.prep_input is None or self.prep_deadline is None:
            raise RuntimeError("PREP is not active")
        self.prep_result = result
        self.prep_validation = validate_prep_result(self.prep_input, result)
        self._audit("PREP_RECORDED", complete=self.prep_validation.complete, reason=self.prep_validation.reason)
        return self.prep_validation

    def observe_event(self, event_id: str, event: ObservationEvent, *, now_ms: int) -> None:
        self._require_active()
        if event is ObservationEvent.OPPONENT_LEAD_VISIBLE:
            if self.phase is MatchPhase.S04_PREP_40S and self.prep_validation is None:
                self.prep_validation = PrepValidation(False, "PREP_INTERRUPTED_BY_OPPONENT_LEAD")
            self.phase = MatchPhase.S05_IN_BATTLE
            self.battle_state = BattleObservationState.B10_OBSERVE_BOARD
        elif event is ObservationEvent.OUR_PIVOT_SUCCEEDED:
            self.battle_state = BattleObservationState.B20_DETECT_CHOICE_UI
            self._pending_party_kind = WindowKind.PIVOT_TARGET
            self._pending_party_event_id = event_id
        elif event is ObservationEvent.OUR_FAINTED:
            self.battle_state = BattleObservationState.B20_DETECT_CHOICE_UI
            self._pending_party_kind = WindowKind.FAINT_REPLACEMENT
            self._pending_party_event_id = event_id
        elif event is ObservationEvent.EFFECT_SWITCH_CHOICE_AVAILABLE:
            self.battle_state = BattleObservationState.B20_DETECT_CHOICE_UI
            self._pending_party_kind = WindowKind.EFFECT_REPLACEMENT
            self._pending_party_event_id = event_id
        elif event in {ObservationEvent.RANDOM_FORCED_SWITCH, ObservationEvent.EFFECT_AUTO_SWITCH}:
            self.battle_state = BattleObservationState.B50_AUTO_FORCED_SWITCH
            self._pending_party_kind = None
            self._pending_party_event_id = None
        elif event is ObservationEvent.MATCH_END_CONFIRMED:
            self.phase = MatchPhase.S06_MATCH_END_COMMIT
            self.battle_state = BattleObservationState.B90_BATTLE_END
            self._cancel_current_window("MATCH_ENDED")
        self._audit("OBSERVED_EVENT", event_id=event_id, event=event.value, now_ms=now_ms)

    def present_ui(
        self,
        visual_ui: VisualUI,
        allowed_actions: AllowedActions | None,
        *,
        now_ms: int,
        actual_ui_remaining_ms: int | None = None,
    ) -> DecisionWindow | None:
        """Accept a classifier result. PARTY_MENU gets its reason from prior events."""
        self._require_active()
        if self.phase is not MatchPhase.S05_IN_BATTLE:
            return None
        if visual_ui is VisualUI.RESULT_SCREEN:
            self.observe_event(f"ui-result:{self.observation_seq}", ObservationEvent.MATCH_END_CONFIRMED, now_ms=now_ms)
            return None
        if visual_ui in {VisualUI.ACTION_RESOLUTION, VisualUI.TEAM_SELECTION_UI}:
            self.battle_state = BattleObservationState.B40_RESOLVE_ACTIONS
            return None
        if visual_ui in {VisualUI.MOVE_MENU, VisualUI.DETAIL_OVERLAY}:
            if self.current_window and self.current_window.kind is WindowKind.NORMAL_COMMAND:
                return self.current_window
            return None
        if visual_ui is VisualUI.UNKNOWN or allowed_actions is None:
            self.battle_state = BattleObservationState.BX_SCREEN_UNCERTAIN
            return None
        if visual_ui is VisualUI.COMMAND_MENU:
            return self._continue_or_open_normal(allowed_actions, now_ms, actual_ui_remaining_ms)
        if visual_ui is VisualUI.PARTY_MENU:
            if self._pending_party_kind is None:
                if (
                    self.current_window
                    and self.current_window.kind is WindowKind.NORMAL_COMMAND
                    and self.current_window.status not in {WindowStatus.RESOLVED, WindowStatus.CANCELLED, WindowStatus.EXPIRED}
                ):
                    # Navigation into/out of the party list preserves one normal input opportunity.
                    return self.current_window
                self.battle_state = BattleObservationState.BX_SCREEN_UNCERTAIN
                return None
            kind = self._pending_party_kind
            cause = self._pending_party_event_id
            self._pending_party_kind = None
            self._pending_party_event_id = None
            return self._open_window(kind, cause, allowed_actions, now_ms, actual_ui_remaining_ms)
        return None

    def _continue_or_open_normal(
        self,
        allowed_actions: AllowedActions,
        now_ms: int,
        actual_ui_remaining_ms: int | None,
    ) -> DecisionWindow:
        current = self.current_window
        if current and current.kind is WindowKind.NORMAL_COMMAND and current.status not in {WindowStatus.RESOLVED, WindowStatus.CANCELLED, WindowStatus.EXPIRED}:
            if current.tactical_state_version == self.tactical_state_version and current.allowed_action_digest == allowed_actions.digest(WindowKind.NORMAL_COMMAND.value):
                return current
            current.allowed_actions = allowed_actions
            current.tactical_state_version = self.tactical_state_version
            revised_budget = _deadline_budget(45_000, actual_ui_remaining_ms)
            current_deadline = current.deadline.expires_at_ms
            revised_end = now_ms + revised_budget
            current.deadline = Deadline(now_ms, max(0, min(revised_end, current_deadline) - now_ms) if current_deadline is not None else revised_budget)
            current.status = WindowStatus.OPEN if allowed_actions.completeness == "COMPLETE" else WindowStatus.BLOCKED
            current.requires_model = current.status is WindowStatus.OPEN
            current.recommendation = None
            self._audit("WINDOW_REVISED", decision_window_id=current.decision_window_id)
            return current
        return self._open_window(WindowKind.NORMAL_COMMAND, None, allowed_actions, now_ms, actual_ui_remaining_ms)

    def _open_window(
        self,
        kind: WindowKind,
        cause_event_id: str | None,
        allowed_actions: AllowedActions,
        now_ms: int,
        actual_ui_remaining_ms: int | None,
    ) -> DecisionWindow:
        self.decision_seq += 1
        budget_ms = (
            _deadline_budget(45_000, actual_ui_remaining_ms)
            if kind is WindowKind.NORMAL_COMMAND or actual_ui_remaining_ms is not None
            else None
        )
        status = (
            WindowStatus.OPEN
            if allowed_actions.completeness == "COMPLETE" and budget_ms is not None
            else WindowStatus.BLOCKED
        )
        window = DecisionWindow(
            match_id=self.match_id,
            decision_window_id=f"{self.match_id}:dw:{self.decision_seq:03d}",
            game_turn_seq=self.game_turn_seq,
            kind=kind,
            cause_event_id=cause_event_id,
            allowed_actions=allowed_actions,
            tactical_state_version=self.tactical_state_version,
            deadline=Deadline(now_ms, budget_ms),
            status=status,
            requires_model=status is WindowStatus.OPEN,
        )
        if kind is WindowKind.PIVOT_TARGET and self._pivot_plan_is_reusable(allowed_actions):
            window.status = WindowStatus.REUSED_PLAN
            window.requires_model = False
            window.recommendation = {"action_kind": "SWITCH", "switch_target": self.last_pivot_plan.target, "reused": True}
        self.current_window = window
        self.battle_state = BattleObservationState.B30_WAIT_PLAYER_ACTION
        if self.store is not None:
            self.store.record_decision_window(
                self.match_id,
                decision_window_id=window.decision_window_id,
                game_turn_seq=window.game_turn_seq,
                window_kind=window.kind.value,
                cause_event_id=cause_event_id,
                tactical_state_version=window.tactical_state_version,
                allowed_action_digest=window.allowed_action_digest,
                allowed_actions=window.allowed_actions.as_wire(),
                deadline_at_ms=window.deadline.expires_at_ms,
            )
        self._audit(
            "WINDOW_OPENED",
            decision_window_id=window.decision_window_id,
            window_kind=kind.value,
            requires_model=window.requires_model,
        )
        return window

    def _pivot_plan_is_reusable(self, allowed_actions: AllowedActions) -> bool:
        plan = self.last_pivot_plan
        return bool(
            plan
            and plan.dependency_signature == self._state_signature
            and plan.target in allowed_actions.switch_targets
            and allowed_actions.completeness == "COMPLETE"
        )

    def request_mock_decision(self, provider: DecisionProvider, *, now_ms: int) -> DecisionWindow:
        """Reserve a duplicate-safe request and validate a scripted mock response."""
        self._require_active()
        window = self.current_window
        if window is None:
            raise RuntimeError("no active decision window")
        if not window.requires_model:
            return window
        if window.status is WindowStatus.BLOCKED:
            return window
        if window.tactical_state_version != self.tactical_state_version:
            window.status = WindowStatus.STALE
            window.requires_model = False
            return window
        key = window.request_key
        if key in self._request_keys:
            return window
        self._request_keys.add(key)
        self._request_seq += 1
        request = DecisionRequest(
            request_id=f"{self.match_id}:request:{self._request_seq:03d}",
            match_id=self.match_id,
            decision_window_id=window.decision_window_id,
            phase=window.kind.value,
            tactical_state_version=window.tactical_state_version,
            observation_revision=self.observation_seq,
            allowed_actions=window.allowed_actions.as_wire(),
            allowed_action_digest=window.allowed_action_digest,
            deadline_ms_remaining=window.deadline.remaining_ms(now_ms) or 0,
            source_manifest=self.assets.manifest,
            previous_pivot_target=None if self.last_pivot_plan is None else self.last_pivot_plan.target,
        )
        window.status = WindowStatus.REQUESTED
        window.request_id = request.request_id
        if self.store is not None:
            snapshot_id = f"{request.request_id}:v{window.tactical_state_version}"
            self.store.freeze_api_snapshot(self.match_id, snapshot_id, request.immutable_record())
            self.store.reserve_api_call(
                self.match_id,
                decision_window_id=window.decision_window_id,
                tactical_state_version=window.tactical_state_version,
                allowed_action_digest=window.allowed_action_digest,
                phase=window.kind.value,
                request_snapshot_id=snapshot_id,
            )
        try:
            response = provider.decide(request)
        except Exception:
            window.status = WindowStatus.UNAVAILABLE
            window.requires_model = False
            self._audit("MOCK_DECISION_FAILED", decision_window_id=window.decision_window_id)
            return window
        self._accept_mock_response(window, response)
        return window

    def _accept_mock_response(self, window: DecisionWindow, response: MockDecision) -> None:
        if window is not self.current_window or window.tactical_state_version != self.tactical_state_version:
            window.status = WindowStatus.STALE
            window.requires_model = False
            return
        if response.decision_status != "RECOMMENDED":
            window.status = WindowStatus.UNAVAILABLE
            window.requires_model = False
            return
        actions = window.allowed_actions
        legal = False
        if response.action_kind == "MOVE":
            legal = window.kind is WindowKind.NORMAL_COMMAND and response.move in actions.moves and response.switch_target is None
            if actions.choice_lock is not None and response.move != actions.choice_lock:
                legal = False
            if response.planned_pivot_target is not None:
                legal = legal and response.move in actions.pivot_moves and response.planned_pivot_target in actions.switch_targets
            if response.mega and not actions.mega_available:
                legal = False
        elif response.action_kind == "SWITCH":
            legal = response.switch_target in actions.switch_targets and response.move is None and not response.mega
        if not legal:
            window.status = WindowStatus.UNAVAILABLE
            window.requires_model = False
            return
        window.recommendation = asdict(response)
        window.status = WindowStatus.RECOMMENDED
        window.requires_model = False
        if window.kind is WindowKind.NORMAL_COMMAND and response.planned_pivot_target is not None:
            self.last_pivot_plan = PivotPlan(
                target=response.planned_pivot_target,
                dependency_signature=self._state_signature,
                based_on_state_version=window.tactical_state_version,
                source_window_id=window.decision_window_id,
            )
        self._audit("MOCK_DECISION_ACCEPTED", decision_window_id=window.decision_window_id)

    def accept_validated_model_response(self, response: dict[str, Any]) -> None:
        """Apply only an already v2.3-validated, current model recommendation.

        This is presentation state, not game control.  Semantic validation stays
        in the model adapter so this method never evaluates tactical quality.
        """
        self._require_active()
        window = self.current_window
        if window is None:
            raise RuntimeError("no current decision window")
        required = {
            "match_id": self.match_id,
            "decision_window_id": window.decision_window_id,
            "tactical_state_version": window.tactical_state_version,
            "allowed_action_digest": window.allowed_action_digest,
        }
        if any(response.get(key) != value for key, value in required.items()):
            raise ValueError("validated response does not match current decision window")
        window.recommendation = dict(response)
        window.status = WindowStatus.RECOMMENDED
        window.requires_model = False
        if window.kind is WindowKind.NORMAL_COMMAND and response.get("planned_pivot_target") is not None:
            self.last_pivot_plan = PivotPlan(
                target=response["planned_pivot_target"],
                dependency_signature=self._state_signature,
                based_on_state_version=window.tactical_state_version,
                source_window_id=window.decision_window_id,
            )
        self._audit("MODEL_DECISION_ACCEPTED", decision_window_id=window.decision_window_id)

    def record_actual_action(self, action_id: str, observed_action: dict[str, Any]) -> None:
        """Only an observed player/game action resolves a displayed recommendation."""
        self._require_active()
        if self.current_window is None:
            raise RuntimeError("no active decision window to resolve")
        self.current_window.status = WindowStatus.RESOLVED
        self.battle_state = BattleObservationState.B40_RESOLVE_ACTIONS
        if self.store is not None:
            self.store.append_event(self.match_id, action_id, "ACTUAL_ACTION_CONFIRMED", observed_action)
        self._audit("ACTUAL_ACTION_CONFIRMED", action_id=action_id, decision_window_id=self.current_window.decision_window_id)

    def _cancel_current_window(self, reason: str) -> None:
        if self.current_window and self.current_window.status not in {WindowStatus.RESOLVED, WindowStatus.CANCELLED}:
            self.current_window.status = WindowStatus.CANCELLED
            self.current_window.requires_model = False
            self._audit("WINDOW_CANCELLED", decision_window_id=self.current_window.decision_window_id, reason=reason)
        self.current_window = None
        self.last_pivot_plan = None

    def begin_review(self) -> None:
        self._require_active()
        if self.phase is not MatchPhase.S06_MATCH_END_COMMIT:
            raise RuntimeError("review can begin only after a confirmed match end")
        self.phase = MatchPhase.S07_REVIEW

    def finish_review(self) -> None:
        self._require_active()
        if self.phase is not MatchPhase.S07_REVIEW:
            raise RuntimeError("review is not active")
        self.phase = MatchPhase.S08_NEXT_MATCH_READY

    def retire_for_next_match(self) -> None:
        """Discard all match-local windows, plans, and deadline state before reload."""
        self._cancel_current_window("NEXT_MATCH")
        self.selection_deadline = None
        self.selection_model_expired = False
        self.prep_deadline = None
        self._pending_party_kind = None
        self._pending_party_event_id = None
        self._active = False
        self.phase = MatchPhase.S08_NEXT_MATCH_READY


class MatchController:
    """Owns S00/S01 transitions and guarantees a fresh preload for every match."""

    def __init__(self, master_directory: str | Path, *, store: BattleStore | None = None) -> None:
        self._master_directory = Path(master_directory)
        self._team_source: Path | None = None
        self._preloader = MasterPreloader(self._master_directory)
        self._store = store
        self.phase = MatchPhase.S00_WAIT_START
        self.active_session: MatchSession | None = None

    def set_team_source_for_next_match(self, team_source: str | Path | None) -> None:
        """Choose a validated private CSV for the next four-file preload.

        Existing ``MatchSession.assets`` objects remain untouched, so changing
        this value during a match cannot alter an in-flight API payload or PREP.
        The settings layer decides when this method may be called.
        """
        self._team_source = None if team_source is None else Path(team_source)
        self._preloader = MasterPreloader(self._master_directory, team_source=self._team_source)

    def set_store_for_next_match(self, store: BattleStore) -> None:
        """Switch the append-only database only at a next-match boundary."""
        self._store = store

    def start_match(self, match_id: str, *, now_ms: int) -> MatchSession:
        if self.active_session is not None:
            self.active_session.retire_for_next_match()
        self.phase = MatchPhase.S01_PRELOAD_4_FILES
        assets = self._preloader.load_for_new_match()
        session = MatchSession(match_id=match_id, assets=assets, now_ms=now_ms, store=self._store)
        self.active_session = session
        self.phase = MatchPhase.S02_MATCHMAKING
        return session
