"""PySide6 desktop presentation layer for the observer-only assistant.

The module intentionally owns no capture loop, OCR engine, game input, or
strategic rule.  It renders immutable/session projections and emits explicit
commands to the surrounding runtime.  Model transport work is performed by an
optional ``QThread`` dispatcher; repainting a widget never schedules a request.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import time
from typing import Any, Callable

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .fsm import MatchController, MatchPhase, MatchSession, WindowKind, WindowStatus
from .model_adapter import ModelAdapter, ModelOutcome, ModelOutcomeStatus, ModelTransport
from .prep import PrepMatchup, PrepResult
from .preload import FrozenMatchAssets
from .session_payloads import _BATTLE_PHASES, build_session_payload, current_session_identity
from .settings import ModelSettings
from .settings_management import DesktopSettingsService
from .store import BattleStore


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


class ApiRunState(str, Enum):
    STOPPED = "STOPPED"
    ENABLED = "ENABLED"
    EMERGENCY_STOPPED = "EMERGENCY_STOPPED"


@dataclass(frozen=True)
class DesktopModelJob:
    """An immutable UI-to-worker handoff, safe to validate again on return."""

    request_id: str
    phase: str
    payload: dict[str, Any]
    identity: dict[str, Any]
    assets: FrozenMatchAssets
    api_epoch: int
    submitted_at_ms: int

    @property
    def request_key(self) -> tuple[str, str, int, str]:
        return (
            str(self.payload["match_id"]),
            str(self.payload["decision_window_id"]),
            int(self.payload["tactical_state_version"]),
            str(self.payload["allowed_action_digest"]),
        )


@dataclass(frozen=True)
class StateRow:
    label: str
    value: str
    certainty: str


@dataclass(frozen=True)
class DesktopPresentation:
    match_id: str | None
    phase: str
    battle_state: str
    decision_window_id: str | None
    decision_kind: str | None
    decision_status: str | None
    turn: str | None
    deadline: str
    recommendation: str
    selection: str
    preload: str
    prep: str
    api_state: str
    api_usage: str
    counterparty: str
    state_rows: tuple[StateRow, ...]
    review_text: str


def _display_json(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text if len(text) <= 280 else text[:277] + "…"


def _recommendation_line(session: MatchSession | None) -> str:
    if session is None:
        return "推奨: 試合開始待ち"
    window = session.current_window
    if window and window.status in {WindowStatus.RECOMMENDED, WindowStatus.REUSED_PLAN} and window.recommendation:
        action = window.recommendation
        if action.get("action_kind") == "MOVE":
            suffix = "（メガ）" if action.get("mega") else ""
            pivot = action.get("planned_pivot_target")
            return f"推奨: {action.get('move', '不明な技')}{suffix}" + (f" → {pivot}" if pivot else "")
        if action.get("action_kind") == "SWITCH":
            return f"推奨: {action.get('switch_target', '不明な交代先')} に交代"
    if session.phase is MatchPhase.S03_TEAM_SELECTION and session.selection_recommendation:
        selected = " / ".join(session.selection_recommendation.selected_three)
        return f"推奨選出: {selected} ｜先発 {session.selection_recommendation.lead}"
    return "推奨: 表示可能なAI回答はありません"


def _deadline_text(session: MatchSession | None, now_ms: int) -> str:
    if session is None:
        return "—"
    deadline = None
    if session.phase is MatchPhase.S03_TEAM_SELECTION:
        deadline = session.selection_deadline
    elif session.phase is MatchPhase.S04_PREP_40S:
        deadline = session.prep_deadline
    elif session.current_window is not None:
        deadline = session.current_window.deadline
    if deadline is None or deadline.remaining_ms(now_ms) is None:
        return "—"
    return f"{deadline.remaining_ms(now_ms) / 1000:.1f} 秒"


def _projection_rows(session: MatchSession | None, store: BattleStore | None) -> tuple[StateRow, ...]:
    if session is None:
        return ()
    fields: dict[str, Any] = {}
    if store is not None:
        fields = store.replay(session.match_id).get("fields", {})
    rows: list[StateRow] = []
    labels = {
        "our_active": "自軍・現在",
        "opponent_active": "相手・現在",
        "our_remaining": "自軍・残り",
        "opponent_revealed": "相手・判明ポケモン",
        "hp_status": "HP / 状態異常",
        "statuses": "状態異常",
        "stat_stages": "能力ランク",
        "known_moves_items_abilities": "判明技 / 特性 / 道具",
        "forms_and_types": "メガ / フォーム",
        "field_effects": "天候 / フィールド / 壁 / 設置技",
        "choice_locks": "技固定",
        "uncertainties": "不明事項",
        "opponent_control_classification": "対戦相手の区分",
    }
    for key, label in labels.items():
        record = fields.get(key)
        if record is None:
            raw = session.state_facts.get(key)
            rows.append(StateRow(label, "不明" if raw is None else _display_json(raw), "UNKNOWN"))
        else:
            certainty = str(record["certainty"])
            if certainty == "CONFLICT":
                value = _display_json(record["candidates"])
            else:
                value = _display_json(record["value"])
            rows.append(StateRow(label, value, certainty))
    return tuple(rows)


def build_desktop_presentation(
    session: MatchSession | None,
    store: BattleStore | None,
    *,
    api_state: ApiRunState,
    now_ms: int,
    latest_review: dict[str, Any] | None = None,
) -> DesktopPresentation:
    """Create a detached, display-only view model without changing session state."""
    if session is None:
        return DesktopPresentation(
            match_id=None,
            phase=MatchPhase.S00_WAIT_START.value,
            battle_state="—",
            decision_window_id=None,
            decision_kind=None,
            decision_status=None,
            turn=None,
            deadline="—",
            recommendation=_recommendation_line(None),
            selection="未選出",
            preload="未実行",
            prep="未開始",
            api_state=api_state.value,
            api_usage="0 calls / 0 tokens / $0.000000",
            counterparty="UNKNOWN（断定しない）",
            state_rows=(),
            review_text="レビューは試合後に表示します。CODEX O・X はライブ判断には使用しません。",
        )
    window = session.current_window
    selected = session.actual_selected_three or ()
    selection = " / ".join(selected) if selected else "未選出"
    if session.actual_lead:
        selection += f" ｜先発 {session.actual_lead}"
    prep = "未開始"
    if session.prep_validation is not None:
        prep = "完了" if session.prep_validation.complete else f"未完了: {session.prep_validation.reason}"
    usage = store.api_usage_summary(session.match_id) if store is not None else {
        "calls": 0, "total_tokens": 0, "cost_microusd": 0,
    }
    manifest_count = len(session.assets.manifest)
    facts = session.state_facts
    counterparty = str(facts.get("opponent_control_classification", "UNKNOWN")) + "（断定しない）"
    return DesktopPresentation(
        match_id=session.match_id,
        phase=session.phase.value,
        battle_state=session.battle_state.value,
        decision_window_id=None if window is None else window.decision_window_id,
        decision_kind=None if window is None else window.kind.value,
        decision_status=None if window is None else window.status.value,
        turn=session.game_turn_seq,
        deadline=_deadline_text(session, now_ms),
        recommendation=_recommendation_line(session),
        selection=selection,
        preload=f"{manifest_count}/4 読込・SHA-256固定" if manifest_count == 4 else "4ファイル読込失敗",
        prep=prep,
        api_state=api_state.value,
        api_usage=(
            f"{usage['calls']} calls / {usage['total_tokens']} tokens / "
            f"${usage['cost_microusd'] / 1_000_000:.6f}"
        ),
        counterparty=counterparty,
        state_rows=_projection_rows(session, store),
        review_text=(
            "レビュー未取得。CODEX O・X は対戦後の振り返り専用です。"
            if latest_review is None else _display_json(latest_review)
        ),
    )


class AdapterJobExecutor:
    """Worker-side adapter wrapper using an independent SQLite connection.

    The request and identity are frozen before handoff.  The GUI performs the
    second freshness check when the result returns, so a changed observer state
    can never be made current by this worker.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        transport_factory: Callable[[], ModelTransport],
        settings: ModelSettings | None = None,
    ) -> None:
        self._database_path = Path(database_path)
        self._transport_factory = transport_factory
        self._settings = settings or ModelSettings.from_environment()

    def __call__(self, job: DesktopModelJob) -> ModelOutcome:
        store = BattleStore(self._database_path)
        try:
            adapter = ModelAdapter(
                store=store,
                transport=self._transport_factory(),
                settings=self._settings,
                now_ms=monotonic_ms,
            )
            return adapter.decide(
                deepcopy(job.payload),
                job.assets,
                current_supplier=lambda: dict(job.identity),
            )
        finally:
            store.close()


class _ModelWorker(QObject):
    completed = Signal(object, object)

    def __init__(self, executor: Callable[[DesktopModelJob], ModelOutcome]) -> None:
        super().__init__()
        self._executor = executor

    @Slot(object)
    def execute(self, job: DesktopModelJob) -> None:
        try:
            outcome = self._executor(job)
        except Exception as exc:  # no exception message is copied to UI/logs
            outcome = ModelOutcome(ModelOutcomeStatus.UNAVAILABLE, f"WORKER_FAILURE:{type(exc).__name__}")
        self.completed.emit(job, outcome)


class DesktopModelDispatcher(QObject):
    """Run a caller-supplied model executor outside the GUI thread."""

    _submit = Signal(object)
    model_outcome = Signal(object, object)

    def __init__(self, executor: Callable[[DesktopModelJob], ModelOutcome]) -> None:
        super().__init__()
        self._thread = QThread(self)
        self._worker = _ModelWorker(executor)
        self._worker.moveToThread(self._thread)
        self._submit.connect(self._worker.execute)
        self._worker.completed.connect(self.model_outcome)
        self._thread.start()

    @Slot(object)
    def submit(self, job: DesktopModelJob) -> None:
        self._submit.emit(job)

    def close(self) -> None:
        self._thread.quit()
        self._thread.wait(2_000)


class DesktopObserverBridge(QObject):
    """Queued worker-to-GUI notification bridge for observer updates.

    Capture/OCR/FSM ownership stays outside the widget thread.  A runtime calls
    ``publish_observation`` only after its worker-owned pipeline has appended
    evidence; this bridge merely schedules a display refresh and optionally
    records capture-to-GUI timing.  It never requests a model job.
    """

    observation_available = Signal(str)
    capture_health_available = Signal(object)

    def __init__(self, controller: "DesktopController", *, latency_ledger: Any | None = None) -> None:
        super().__init__()
        self._controller = controller
        self._latency_ledger = latency_ledger
        self.observation_available.connect(self._present_observation)
        self.capture_health_available.connect(self._present_capture_health)

    def publish_observation(self, frame_id: str) -> None:
        self.observation_available.emit(frame_id)

    def publish_capture_health(self, report: dict[str, Any]) -> None:
        self.capture_health_available.emit(dict(report))

    @Slot(str)
    def _present_observation(self, frame_id: str) -> None:
        if self._latency_ledger is not None:
            self._latency_ledger.observer_completed(frame_id, self._controller.clock_ms())
        self._controller.refresh()
        if self._latency_ledger is not None:
            self._latency_ledger.gui_presented(frame_id, self._controller.clock_ms())

    @Slot(object)
    def _present_capture_health(self, _report: object) -> None:
        # Health is an informational update.  It intentionally does not alter
        # state, tactical version, or API scheduling.
        self._controller.refresh()


class DesktopController(QObject):
    """The GUI-facing coordinator; it schedules no model work during repaint."""

    presentation_changed = Signal(object)
    model_job_requested = Signal(object)
    model_result_ignored = Signal(str)

    def __init__(
        self,
        *,
        store: BattleStore,
        match_controller: MatchController | None = None,
        session: MatchSession | None = None,
        clock_ms: Callable[[], int] = monotonic_ms,
        settings_service: DesktopSettingsService | None = None,
    ) -> None:
        super().__init__()
        self.store = store
        self.match_controller = match_controller
        self.session = session
        self.clock_ms = clock_ms
        self.settings_service = settings_service
        self.api_state = ApiRunState.STOPPED
        self._api_epoch = 0
        self._request_sequence = 0
        self._correction_sequence = 0
        self._pending: set[tuple[str, str, int, str]] = set()
        self._database_worker_rebind_required = False
        self.latest_review: dict[str, Any] | None = None

    def bind_dispatcher(self, dispatcher: DesktopModelDispatcher) -> None:
        self.model_job_requested.connect(dispatcher.submit)
        dispatcher.model_outcome.connect(self.apply_model_outcome)

    def presentation(self) -> DesktopPresentation:
        return build_desktop_presentation(
            self.session,
            self.store,
            api_state=self.api_state,
            now_ms=self.clock_ms(),
            latest_review=self.latest_review,
        )

    def refresh(self) -> None:
        self.presentation_changed.emit(self.presentation())

    def start_new_match(self, match_id: str) -> MatchSession:
        if self.match_controller is None:
            raise RuntimeError("no MatchController is attached")
        if self.settings_service is not None:
            # A storage move must finish outside the prior session's SQLite
            # connection.  Retiring it first preserves its append-only audit.
            if self.settings_service.settings.pending_storage is not None and self.match_controller.active_session is not None:
                self.match_controller.active_session.retire_for_next_match()
                self.match_controller.active_session = None
            applied = self.settings_service.apply_for_next_match(
                self.match_controller,
                before_storage_migration=self.store.checkpoint,
            )
            if applied.storage_changed:
                old_store = self.store
                database_path = Path(applied.settings.storage.data_root) / old_store.database_path.name
                old_store.close()
                self.store = BattleStore(database_path)
                self.match_controller.set_store_for_next_match(self.store)
                # A worker bound to the prior database cannot safely serve the
                # new storage location.  Stop it explicitly; no tactical
                # replacement is attempted.
                self.emergency_stop()
                self._database_worker_rebind_required = True
        self._api_epoch += 1
        self._pending.clear()
        self.latest_review = None
        self.session = self.match_controller.start_match(match_id, now_ms=self.clock_ms())
        self.refresh()
        return self.session

    def set_api_enabled(self, enabled: bool) -> None:
        if enabled and self._database_worker_rebind_required:
            self.api_state = ApiRunState.STOPPED
            self.refresh()
            return
        if enabled:
            self.api_state = ApiRunState.ENABLED
        else:
            self.api_state = ApiRunState.STOPPED
            self._api_epoch += 1
            self._pending.clear()
        self.refresh()

    def emergency_stop(self) -> None:
        """Invalidate queued/in-flight UI entitlements while observation continues."""
        self.api_state = ApiRunState.EMERGENCY_STOPPED
        self._api_epoch += 1
        self._pending.clear()
        self.refresh()

    def request_model_job(self, phase: str) -> DesktopModelJob | None:
        """Explicitly queue one model job.  This method performs no transport."""
        session = self.session
        if session is None or self.api_state is not ApiRunState.ENABLED or self._database_worker_rebind_required:
            return None
        now_ms = self.clock_ms()
        try:
            session.enforce_deadlines(now_ms=now_ms)
            self._request_sequence += 1
            request_id = f"{session.match_id}:desktop:{self._request_sequence:04d}"
            payload = build_session_payload(session, phase=phase, request_id=request_id, now_ms=now_ms)
            identity = current_session_identity(session, phase=phase)
        except (RuntimeError, ValueError):
            self.refresh()
            return None
        job = DesktopModelJob(
            request_id=request_id,
            phase=phase,
            payload=deepcopy(payload),
            identity=dict(identity),
            assets=session.assets,
            api_epoch=self._api_epoch,
            submitted_at_ms=now_ms,
        )
        if job.request_key in self._pending:
            return None
        if not identity.get("window_open", True):
            return None
        self._pending.add(job.request_key)
        if phase in _BATTLE_PHASES and session.current_window is not None:
            session.current_window.status = WindowStatus.REQUESTED
        self.model_job_requested.emit(job)
        self.refresh()
        return job

    @Slot(object, object)
    def apply_model_outcome(self, job: DesktopModelJob, outcome: ModelOutcome) -> None:
        self._pending.discard(job.request_key)
        session = self.session
        if (
            session is None
            or job.api_epoch != self._api_epoch
            or self.api_state is not ApiRunState.ENABLED
            or current_session_identity(session, phase=job.phase) != job.identity
        ):
            self.model_result_ignored.emit("STALE_OR_STOPPED")
            self.refresh()
            return
        if outcome.status is not ModelOutcomeStatus.DISPLAYABLE or outcome.response is None:
            if job.phase in _BATTLE_PHASES and session.current_window is not None:
                session.current_window.status = WindowStatus.UNAVAILABLE
                session.current_window.requires_model = False
            self.model_result_ignored.emit(outcome.status.value)
            self.refresh()
            return
        try:
            response = outcome.response
            if job.phase == "SELECT":
                session.record_selection_recommendation(tuple(response["selected_three"]), response["lead"])
            elif job.phase == "PREP":
                prep = PrepResult(
                    response["prep_status"],
                    tuple(PrepMatchup(row["our_pokemon"], row["opponent_pokemon"], row["eval_code"]) for row in response["matchups_18"]),
                    response["preserve"],
                    response["stopper"],
                    response["closer"],
                    response["unfinished_reason"],
                )
                session.record_prep_result(prep, now_ms=self.clock_ms())
            elif job.phase in _BATTLE_PHASES:
                session.accept_validated_model_response(response)
            elif job.phase == "REVIEW":
                self.latest_review = dict(response)
            else:
                raise ValueError("unsupported desktop model phase")
        except (KeyError, RuntimeError, ValueError, TypeError):
            self.model_result_ignored.emit("RESULT_APPLY_REJECTED")
        self.refresh()

    def apply_manual_correction(self, field: str, value: Any, *, certainty: str) -> bool:
        if self.session is None:
            raise RuntimeError("a match must be active before correction")
        if certainty not in {"CONFIRMED", "CANDIDATE"}:
            raise ValueError("certainty must be CONFIRMED or CANDIDATE")
        self._correction_sequence += 1
        applied = self.session.update_observed_state(
            f"manual:{self.session.match_id}:{self._correction_sequence:04d}",
            {field: value},
            source_kind="MANUAL_VERIFIED",
            quality=certainty,
        )
        # A post-match review is also a state-bound model result.  Do not keep
        # displaying it after a manual correction changes that state.
        if applied:
            self.latest_review = None
        self.refresh()
        return applied

    def apply_actual_selection(self, selected_three: tuple[str, str, str], lead: str) -> None:
        if self.session is None:
            raise RuntimeError("a match must be active before selection correction")
        if self.session.phase is MatchPhase.S03_TEAM_SELECTION:
            self.session.record_actual_selection(selected_three, lead, now_ms=self.clock_ms())
        else:
            self._correction_sequence += 1
            self.session.correct_actual_selection(
                selected_three,
                lead,
                observation_id=f"manual-selection:{self.session.match_id}:{self._correction_sequence:04d}",
            )
            self.latest_review = None
        self.refresh()

    def record_observed_action(self, action_id: str, action: dict[str, Any]) -> None:
        if self.session is None:
            raise RuntimeError("a match must be active before recording an action")
        self.session.record_actual_action(action_id, action)
        self.refresh()

    def hide_match(self, match_id: str) -> bool:
        applied = self.store.mark_match_hidden(
            match_id,
            event_id=f"desktop:{match_id}:hide:{int(time.time() * 1000)}",
        )
        self.refresh()
        return applied

    def export_match(self, match_id: str, path: str | Path) -> Path:
        target = Path(path)
        target.write_text(json.dumps(self.store.export_match(match_id), ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def purge_match(self, match_id: str, *, confirmation_match_id: str) -> None:
        if self.session is not None and self.session.match_id == match_id:
            raise RuntimeError("the active match cannot be permanently deleted")
        self.store.purge_match_permanently(match_id, confirmation_match_id=confirmation_match_id)
        self.refresh()


class DesktopPreferences:
    """Non-secret window preferences.  API keys are intentionally absent."""

    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings or QSettings("PokemonChampionsAssistant", "Desktop")

    def restore(self, window: QMainWindow) -> None:
        geometry = self._settings.value("window/geometry")
        if geometry is not None:
            window.restoreGeometry(geometry)
        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._settings.value("window/always_on_top", False, type=bool))

    def save(self, window: QMainWindow, *, compact: bool, always_on_top: bool) -> None:
        self._settings.setValue("window/geometry", window.saveGeometry())
        self._settings.setValue("window/compact", compact)
        self._settings.setValue("window/always_on_top", always_on_top)

    def compact(self) -> bool:
        return self._settings.value("window/compact", False, type=bool)

    def always_on_top(self) -> bool:
        return self._settings.value("window/always_on_top", False, type=bool)


class _PostmatchReviewWorker(QThread):
    """Low-priority review after match END, never queued during a turn."""
    result_ready=Signal(object)

    def __init__(self, data_root, match_id, app_root, model, effort, parent=None):
        super().__init__(parent)
        self.data_root,self.match_id=data_root,match_id
        self.app_root,self.model,self.effort=app_root,model,effort

    def run(self):
        try:
            from .postmatch_learning import request_review
            from .siwc_auth import ChatGPTSignIn,ProtectedSessionStore
            token=ChatGPTSignIn(ProtectedSessionStore(self.app_root)).access_token()
            result=request_review(
                data_root=self.data_root,match_id=self.match_id,
                model=self.model,access_token=token,reasoning_effort=self.effort)
            # Issue #92: a bounded after-match aggregation of saved GPT review
            # hypotheses. No additional model request, LIVE master changes,
            # or tactical-state queries are performed. A research failure must
            # never invalidate a successfully stored postmatch review.
            if result.get("status")=="STORED_UNVERIFIED_REVIEW":
                try:
                    from .autonomous_improvement import AutonomousImprovementStore
                    research=AutonomousImprovementStore(self.data_root)
                    result["improvement_research"]=research.aggregate_after_review(
                        match_id=self.match_id)
                except Exception as research_error:
                    result["improvement_research"]={
                        "status":"RESEARCH_FAILED_REVIEW_RETAINED",
                        "reason":type(research_error).__name__,
                        "candidate_count":0}
            self.result_ready.emit(result)
        except Exception as exc:
            from .team_ai_analysis import safe_ai_error
            self.result_ready.emit({"status":"FAILED_REVIEW_RETAINED",
                                    "reason":safe_ai_error(exc)})


class DesktopMainWindow(QMainWindow):
    """Usable local dashboard.  It recommends only already-validated responses."""

    correction_fields = (
        "our_active", "opponent_active", "our_remaining", "opponent_revealed",
        "hp_status", "statuses", "stat_stages", "known_moves_items_abilities",
        "forms_and_types", "field_effects", "choice_locks", "uncertainties",
        "opponent_control_classification",
    )

    def __init__(
        self,
        controller: DesktopController,
        *,
        preferences: DesktopPreferences | None = None,
        settings_service: DesktopSettingsService | None = None,
        settings_validation_only: bool = False,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.preferences = preferences or DesktopPreferences()
        self.settings_service = settings_service or controller.settings_service
        self.settings_validation_only = settings_validation_only
        self._settings_dialog = None
        self._auto_match_dialog = None
        # On the actual desktop use one visually observed match session; the
        # legacy controller dashboard remains available only in validation mode.
        self._unified = self.settings_service is not None and not settings_validation_only
        self._unified_match_id = None
        self._unified_last_report_key = None
        self._unified_event_number = 0
        self._unified_team_id = None
        self._unified_team_sha = ""
        self._p1_context = {}
        self._p1_buffer = None
        self._postmatch_worker = None
        self._postmatch_match_id = None
        self._postmatch_waiting_match_id = None
        self.setWindowTitle("Pokémon Champions Assistant — observer only")
        self.setMinimumSize(680, 460)
        self._build_ui()
        self.preferences.restore(self)
        self.on_top_box.setChecked(self.preferences.always_on_top())
        self.compact_box.setChecked(self.preferences.compact())
        if not self._unified:
            self.controller.presentation_changed.connect(self.render)
            self.render(self.controller.presentation())
        if self.settings_service is not None and self.settings_service.first_launch:
            QTimer.singleShot(0, self._open_settings)

    def _build_ui(self) -> None:
        if self._unified:
            self._build_unified_ui()
            return
        root = QWidget(self)
        layout = QVBoxLayout(root)
        recommendation_box = QGroupBox("OpenAI 推奨（ユーザーが手動で実行）")
        recommendation_layout = QVBoxLayout(recommendation_box)
        self.recommendation_label = QLabel()
        self.recommendation_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.recommendation_label.setWordWrap(False)
        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        self.recommendation_label.setFont(font)
        self.recommendation_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        recommendation_layout.addWidget(self.recommendation_label)
        layout.addWidget(recommendation_box)

        header = QGridLayout()
        self.header_labels: dict[str, QLabel] = {}
        for index, (key, text) in enumerate((
            ("phase", "フェーズ"), ("window", "入力機会"), ("turn", "ターン"), ("deadline", "残り時間"),
            ("preload", "4ファイル"), ("prep", "PREP"), ("api", "API"), ("usage", "利用量"),
        )):
            header.addWidget(QLabel(text), index // 4 * 2, index % 4)
            label = QLabel("—")
            label.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)
            header.addWidget(label, index // 4 * 2 + 1, index % 4)
            self.header_labels[key] = label
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._state_tab(), "対戦状態")
        self.tabs.addTab(self._correction_tab(), "手動訂正")
        self.tabs.addTab(self._operations_tab(), "運用")
        self.tabs.addTab(self._history_tab(), "試合履歴")
        self.tabs.addTab(self._review_tab(), "振り返り")
        layout.addWidget(self.tabs, 1)

        controls = QHBoxLayout()
        self.on_top_box = QCheckBox("最前面")
        self.on_top_box.toggled.connect(self._set_on_top)
        self.compact_box = QCheckBox("コンパクト")
        self.compact_box.toggled.connect(self._set_compact)
        controls.addWidget(self.on_top_box)
        controls.addWidget(self.compact_box)
        if self.settings_service is not None:
            auto_button = QPushButton("自動キャプチャ対戦支援")
            auto_button.clicked.connect(self._open_auto_match_support)
            auto_button.setEnabled(not self.settings_validation_only)
            controls.addWidget(auto_button)
            settings_button = QPushButton("設定")
            settings_button.clicked.connect(self._open_settings)
            controls.addWidget(settings_button)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.setCentralWidget(root)

    def _build_unified_ui(self) -> None:
        """Real user-facing UI, driven exclusively by MatchReplayDialog.state."""
        self._close_pending=False
        from .match_replay_dialog import MatchReplayDialog
        from .app_config import default_app_root

        root = QWidget(self)
        layout = QVBoxLayout(root)
        recommendation_box = QGroupBox("AI推奨（ゲーム操作はユーザーが実行）")
        rec_layout = QVBoxLayout(recommendation_box)
        self.recommendation_label = QLabel("推奨: 試合開始待ち")
        font = QFont()
        font.setPointSize(17)
        font.setBold(True)
        self.recommendation_label.setFont(font)
        self.recommendation_label.setWordWrap(True)
        self.recommendation_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        rec_layout.addWidget(self.recommendation_label)
        layout.addWidget(recommendation_box)

        self.header_labels = {}
        header = QGridLayout()
        for index, (key, heading) in enumerate((
            ("phase", "フェーズ"), ("capture", "画面監視"), ("preload", "4ファイル"),
            ("prep", "PREP"), ("selected", "選出"), ("model", "モデル"),
            ("report", "最新結果"), ("records", "ログ記録数"),
            ("ai", "AI通信状態"), ("clock", "処理・期限（推定）"),
        )):
            header.addWidget(QLabel(heading), (index // 4) * 2, index % 4)
            value = QLabel("—")
            value.setWordWrap(True)
            value.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)
            header.addWidget(value, (index // 4) * 2 + 1, index % 4)
            self.header_labels[key] = value
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self._match_scroll = QScrollArea(self.tabs)
        self._match_scroll.setWidgetResizable(True)
        self._match_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._match_panel = MatchReplayDialog(
            model="", reasoning_effort="auto",
            master_directory=str(self.settings_service.master_directory),
            team_path="", capture_config=self.settings_service.settings.capture,
            store_root=default_app_root(), parent=self._match_scroll,
            embedded=True, before_new_match=self._prepare_unified_match,
            on_capture_reconnected=self._persist_reconnected_capture,
        )
        self._match_panel.state_changed.connect(self._on_unified_state)
        self._match_panel.shutdown_finished.connect(self._retry_close_after_workers)
        self._match_panel.ai_status_changed.connect(
            self.header_labels["ai"].setText)
        self._match_panel.live_clock_changed.connect(
            self.header_labels["clock"].setText)
        self._match_scroll.setWidget(self._match_panel)
        self.tabs.addTab(self._match_scroll, "対戦支援")
        state_tab = QWidget()
        state_layout = QVBoxLayout(state_tab)
        self.state_tree = QTreeWidget()
        self.state_tree.setHeaderLabels(["情報", "現在値", "根拠"])
        self.state_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        state_layout.addWidget(self.state_tree)
        self.tabs.addTab(state_tab, "観測状態")
        self.tabs.addTab(self._history_tab(), "試合履歴")
        review_tab = QWidget(self.tabs)
        review_layout = QVBoxLayout(review_tab)
        self.postmatch_status_label = QLabel("未開始")
        self.postmatch_status_label.setWordWrap(True)
        review_layout.addWidget(self.postmatch_status_label)
        self.postmatch_match_label = QLabel("試合ID：—")
        review_layout.addWidget(self.postmatch_match_label)
        self.postmatch_storage_label = QLabel(
            "保存先：SQLite（対戦履歴・学習DB）／レビュー本文は未保存")
        self.postmatch_storage_label.setWordWrap(True)
        review_layout.addWidget(self.postmatch_storage_label)
        self.postmatch_detail = QPlainTextEdit(self.tabs)
        self.postmatch_detail.setReadOnly(True)
        review_layout.addWidget(self.postmatch_detail)
        self.postmatch_retry_button = QPushButton("未処理レビューを再試行")
        self.postmatch_retry_button.setEnabled(False)
        self.postmatch_retry_button.clicked.connect(self._retry_pending_postmatch_review)
        review_layout.addWidget(self.postmatch_retry_button)
        self.tabs.addTab(review_tab, "対戦後レビュー")
        layout.addWidget(self.tabs, 1)

        controls = QHBoxLayout()
        self.on_top_box = QCheckBox("最前面")
        self.on_top_box.toggled.connect(self._set_on_top)
        self.compact_box = QCheckBox("コンパクト")
        self.compact_box.toggled.connect(self._set_compact)
        controls.addWidget(self.on_top_box)
        controls.addWidget(self.compact_box)
        test_button = QPushButton("テスト全般（個別・実ゲーム統合）")
        test_button.clicked.connect(self._open_selection_test)
        controls.addWidget(test_button)
        settings_button = QPushButton("設定")
        settings_button.clicked.connect(self._open_settings)
        controls.addWidget(settings_button)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.setCentralWidget(root)
        self._on_unified_state({}, {"status": "NOT_STARTED"})

    def _open_selection_test(self) -> None:
        """Unified offline diagnostics and opt-in read-only live-game integration test."""
        from .app_config import default_app_root, ConfigurationError
        from .vision_preferences import VisionPreferenceStore
        from .preload import MasterPreloader
        from .selection_test_dialog import SelectionTestDialog

        panel=self._match_panel
        if (panel._auto_running or panel._thread is not None
                or panel._prep_thread is not None
                or (panel.state is not None and panel.state.get("phase") not in
                    ("ENDED",))):
            QMessageBox.warning(self,"選出テスト不可",
                                "対戦支援を終了・リセットしてから試してください。")
            return
        try:
            prefs=VisionPreferenceStore(default_app_root())
            model=prefs.selected_model()
            team_id=self.settings_service.settings.teams.active_team_id
            if not model or not team_id:
                raise ConfigurationError(
                    "設定でChatGPTモデルと次戦の自軍構築を選択してください")
            team_path=self.settings_service.teams.path_for_next_match(team_id)
            prepared=self.settings_service.prepare_team_live_assets(
                team_id,match_active=False)
            assets=MasterPreloader(
                self.settings_service.master_directory,team_source=team_path,
                matrix_source=prepared.matrix_path,lookup_source=prepared.lookup_path,
            ).load_for_new_match()
        except (OSError,ValueError,RuntimeError,KeyError) as exc:
            QMessageBox.warning(self,"選出テスト不可",str(exc)[:300])
            return
        # Qt parent manages lifetime; keep Python ref until dialog closes.
        dialog=SelectionTestDialog(
            assets=assets,model=model,reasoning_effort=prefs.reasoning_effort(),
            app_root=default_app_root(),parent=self,
            master_directory=self.settings_service.master_directory,
            team_path=team_path,
            capture_config=self.settings_service.settings.capture,
            matrix_source=prepared.matrix_path,
            lookup_source=prepared.lookup_path)
        dialog.exec()

    def _prepare_unified_match(self) -> None:
        """Apply current model/team/capture selection BEFORE the four reads."""
        from .app_config import default_app_root, ConfigurationError
        from .vision_preferences import VisionPreferenceStore

        if self.controller.session is not None:
            raise ConfigurationError("別の対戦状態が進行中です。既存の運用機能を併用できません。")
        preferences = VisionPreferenceStore(default_app_root())
        model = preferences.selected_model()
        if not model:
            raise ConfigurationError("設定 → ChatGPTでモデルを選択してください。")
        team_id = self.settings_service.settings.teams.active_team_id
        if not team_id:
            raise ConfigurationError("設定 → 自軍構築で次戦の構築を選択してください。")
        team_path = self.settings_service.teams.path_for_next_match(team_id)
        prepared = self.settings_service.prepare_team_live_assets(
            team_id, match_active=False)
        self._unified_team_id = team_id
        from hashlib import sha256
        self._unified_team_sha = sha256(Path(team_path).read_bytes()).hexdigest()
        # P1: freeze provenance and legal *registered* moves before game start.
        # The LIVE callback only creates bounded in-memory entries; no new
        # file access, GPT transport, or SQLite writes in the battle loop.
        from .offmatch_evidence import freeze_team_context
        from .team_management import TeamDocument
        from hashlib import sha256 as _sha256
        manifest = json.loads(prepared.manifest_path.read_text(encoding="utf-8"))
        file_hashes = {
            "matrix": _sha256(prepared.matrix_path.read_bytes()).hexdigest(),
            "lookup": _sha256(prepared.lookup_path.read_bytes()).hexdigest(),
        }
        self._p1_context = freeze_team_context(
            team_id=team_id, team_sha=self._unified_team_sha, model=model,
            regulation=TeamDocument.load(team_path).team_rows[0].get("reg", ""),
            regulation_source="REGISTERED_TEAM_CSV_UNVERIFIED",
            environment_source=manifest.get("environment_source_kind", ""),
            environment_observed_at=manifest.get("environment_generated_at", ""),
            tactical_version=manifest.get("fingerprint", ""),
            frozen_assets_sha=file_hashes,
            own_members=list(TeamDocument.load(team_path).members))
        self._p1_buffer = None
        self._match_panel.matrix_source = str(prepared.matrix_path)
        self._match_panel.lookup_source = str(prepared.lookup_path)
        self._match_panel.live_assets_status = prepared.status
        self._match_panel.model = model
        self._match_panel.reasoning_effort = preferences.reasoning_effort()
        self._match_panel.team_path = str(team_path)
        self._match_panel.capture_config = self.settings_service.settings.capture

    def _persist_reconnected_capture(self, config) -> None:
        """User-selected, successfully previewed mirror-window identity."""
        self.settings_service.save_capture(config)

    def _halt_unified_capture(self) -> None:
        """Saving a changed capture target never silently retargets a live match."""
        self._match_panel._stop_auto()
        self.controller.emergency_stop()

    @Slot(object, object)
    def _on_unified_state(self, state: dict, report: dict) -> None:
        """One replay snapshot drives advice, labels, evidence and SQLite history."""
        panel = self._match_panel
        phase = state.get("phase", "S00_WAIT_START")
        # P1 issue #114: freeze *each received* recommendation and observation
        # now, never reconstruct it from a later end-of-battle state. The
        # bounded buffer is memory-only until the confirmed MATCH_END.
        # Simple legacy/isolated windows may not have prepared a team context.
        if panel.match_id and (getattr(self, "_p1_buffer", None) is None
                               or self._p1_buffer.match_id != panel.match_id):
            from .offmatch_evidence import MatchEvidenceBuffer
            self._p1_buffer = MatchEvidenceBuffer(
                match_id=panel.match_id,
                context=getattr(self, "_p1_context", {}))
        if getattr(self, "_p1_buffer", None) is not None and state:
            self._p1_buffer.capture(state, report)
        # A model timeout can leave the internal phase at MATCHMAKING after
        # the actual preview advanced. UI label marks this uncertainty.
        display_phase=report.get("display_phase") or phase
        self.header_labels["phase"].setText(str(display_phase))
        self.header_labels["capture"].setText(
            "再取得待機" if panel._auto_running and panel._capture_paused
            else "監視中" if panel._auto_running else "停止")
        self.header_labels["preload"].setText("4/4 読込済み" if panel.assets is not None and state else "未実行")
        prep_raw = str(state.get("prep_status", "NOT_STARTED"))
        # A complete cached structure is NOT confirmation of the actual
        # in-game selection, opponent set or safe tactical switches.
        prep_names = {
            "NOT_STARTED": "未開始",
            "PENDING": "実行待機",
            "RUNNING": "評価中",
            "CACHE_PREP_BASE_READY": "対面キャッシュ抽出済み・勝ち筋未完成",
            "CACHE_STRUCTURE_COMPLETE": "18対面＋勝ち筋3点（構造確認）",
            "CACHE_KNOWN_ONLY": "既知相手のみの暫定PREP",
            "CACHE_INCOMPLETE": "キャッシュ評価未完成",
            "INTERRUPTED_BY_NEW_BATTLE_SCREEN": "対戦画面優先により中断",
            "DEADLINE_EXCEEDED": "期限超過・未完成",
        }
        prep_label = prep_names.get(prep_raw,prep_raw)
        if state.get("selection_assumed"):
            prep_label += "／ゲーム内選出未確認"
        if state.get("matrix_provisional"):
            prep_label += "／戦術根拠は暫定"
        self.header_labels["prep"].setText(prep_label)
        selected = state.get("selected_three") or []
        proposed=panel._proposed_selection
        if selected:
            chosen_text=("提案（ゲーム内選出は未確認）：" if state.get("selection_assumed")
                         else "登録済み：")+" / ".join(selected)
            if state.get("lead"):
                chosen_text+=" / 初手："+str(state["lead"])
        elif proposed:
            chosen_text="提案："+" / ".join(proposed[0])+" / 初手："+str(proposed[1])
        else:
            chosen_text="未提示"
        self.header_labels["selected"].setText(chosen_text)
        self.header_labels["model"].setText(panel.model or "未設定")
        self.header_labels["report"].setText(str(report.get("status", "—")))
        self.header_labels["records"].setText(str(len(panel.entries)))
        self.header_labels["ai"].setText(panel._ai_last_status)
        self.header_labels["clock"].setText(panel.deadline_banner.text())

        status = report.get("status", "")
        suggestion = str(report.get("recommendation") or "")
        if status in ("TEST_ONLY", "PROVISIONAL_ADVICE", "PARTIAL_ADVICE",
                      "ADVICE", "OK") and suggestion:
            if status == "TEST_ONLY":
                suggestion = "【機能試験・戦術未検証】" + suggestion
            elif status == "PROVISIONAL_ADVICE":
                suggestion = "【暫定AI判断・対面未検証】" + suggestion
            elif status == "PARTIAL_ADVICE":
                suggestion = "【相手一部未確定／既知対面のみPREP】" + suggestion
            self.recommendation_label.setText(suggestion)
        elif status in ("MATCH_STARTED", "RESET"):
            self.recommendation_label.setText("推奨: 試合開始待ち")
        elif status == "CAPTURE_TARGET_RECONNECTED":
            self.recommendation_label.setText("撮影対象の再接続完了。必要な画面を登録し直して監視を再開")
        elif status in ("AUTO_STARTED", "CAPTURE_RESUMED"):
            if not panel._last_selection_text:
                self.recommendation_label.setText("撮影中。次のAI判断を待機")
        elif status == "CONFIRMED":
            self.recommendation_label.setText(
                (panel._last_selection_text or "選出確定")
                +"。相手初手・技選択画面を待機")
        elif status == "BLOCKED" and report.get("reason") in (
                "OPPONENT_SIX_INCOMPLETE", "OPPONENT_SIX_DUPLICATES",
                "UNCERTAIN_TEAM_SLOT", "OPPONENT_FORM_AMBIGUOUS",
                "OPPONENT_MATRIX_MISSING"):
            attempts=report.get("opponent_recognition_attempts",1)
            self.recommendation_label.setText(
                str(report.get("recommendation") or
                    "選出不可：相手6匹の認識が確定しません（AI再認識 "+
                    str(attempts)+"回分）。推測で選出しません。")
                + " [診断: "+str(report.get("reason"))+"]")
        elif status=="ERROR" and report.get("phase_recognition_pending"):
            self.recommendation_label.setText(
                "選出画面を認識できません（AI通信タイムアウト）。"
                "内部のMATCHMAKING表示は未更新です。相手6匹の認識待ち。")
        elif status in ("BLOCKED", "NO_ACTION", "DEADLINE_EXCEEDED", "ERROR",
                        "CAPTURE_PAUSED", "SUPERSEDED", "PREP_UNAVAILABLE"):
            self.recommendation_label.setText("行動推奨なし：" + str(report.get("reason") or status))
        elif status == "MATCH_END":
            self.recommendation_label.setText(suggestion or "対戦終了")
        elif status in ("OBSERVED", "RESULT_UNCONFIRMED"):
            if report.get("screen")=="COMMAND_MENU":
                self.recommendation_label.setText(
                    "『たたかう』を選択し、技一覧を表示してください。"
                    "（技が表示されるまで技名は確定できません）")
            else:
                self.recommendation_label.setText(
                    "行動推奨待ち（現在の画面を認識中）")
        elif status in ("AI_REQUEST_QUEUED","PREP_PROVISIONAL_RUNNING",
                        "PREP_RUNNING","PREP_DEADLINE_EXCEEDED",
                        "PREP_CACHE_REUSED","PREP_CACHE_KNOWN_ONLY",
                        "PREP_PARTIAL","PREP_CACHE_INCOMPLETE",
                        "PROVISIONAL_PREP_STORED"):
            if phase in ("SELECTION","PREP") and panel._last_selection_text:
                self.recommendation_label.setText(
                    panel._last_selection_text+"／PREP："+str(
                        state.get("prep_status","未開始")))
            elif phase=="BATTLE":
                self.recommendation_label.setText(
                    "ターン画面をAI確認中。行動提案は未確定です。")

        observations = state.get("image_observations_unverified") or []
        last_obs = observations[-1] if observations else {}
        rows = [
            ("自軍選出", " / ".join(selected) or "未確定", "手動確定"),
            ("初手", str(state.get("lead") or "不明"), "手動確定"),
            ("相手6匹", " / ".join(state.get("opponent_six") or []) or "未判明", "画像観測"),
            ("現在の自軍", str(state.get("our_active") or "不明"), "画像候補"),
            ("現在の相手", str(state.get("opponent_active") or "不明"), "画像候補"),
            ("HP", json.dumps(state.get("observed_hp") or {}, ensure_ascii=False), "画像候補"),
            ("前回の観測UI", str(last_obs.get("ui") or "未観測"), "画像候補・未検証"),
            ("最終イベント", str(state.get("last_event") or "NONE"), "画像候補"),
            ("勝敗", str(state.get("result") or "UNKNOWN"), "画像候補"),
            ("マトリクス", "対応" if state.get("matrix_compatible") else
             "根拠限定・暫定AI判断" if panel.live_assets_status=="PROVISIONAL_UNVERIFIED"
             else "不一致・TEST_ONLY", "4ファイル"),
        ]
        self.state_tree.clear()
        for a,b,c in rows:
            self.state_tree.addTopLevelItem(QTreeWidgetItem([a,b,c]))

        # The panel's snapshot is the only active match. This database writes
        # an audit/history copy; it never creates or controls a second FSM.
        if panel.match_id and panel.assets is not None:
            try:
                if self._unified_match_id != panel.match_id:
                    self.controller.store.freeze_match_assets(
                        panel.match_id, contents=panel.assets.contents,
                        sha256=panel.assets.sha256)
                    self._unified_match_id = panel.match_id
                    self._unified_event_number = 0
                    self._unified_last_report_key = None
                key = json.dumps(report, ensure_ascii=False, sort_keys=True)
                if key != self._unified_last_report_key:
                    self._unified_event_number += 1
                    self.controller.store.append_event(
                        panel.match_id, f"live:{self._unified_event_number:06d}",
                        "NOTE", {"source": "CAPTURE_SUPPORT", "phase": phase,
                                 "report": report, "observed_state": dict(state)})
                    self._unified_last_report_key = key
                self._refresh_history()
            except (OSError, ValueError, RuntimeError) as exc:
                self.header_labels["records"].setText("SQLite記録失敗: " + type(exc).__name__)
        if status=="MATCH_END" and panel.match_id:
            self._postmatch_match_id=panel.match_id
            try:
                self.settings_service.environment.record_finished_match(
                    match_id=panel.match_id,state=state)
                from .matchup_learning_store import MatchupLearningStore
                learning=MatchupLearningStore(self.settings_service.settings.storage.data_root)
                result_source=report.get("result_source") or "AI_VISUAL_UNVERIFIED"
                inserted=learning.record_finished(
                    match_id=panel.match_id,team_id=self._unified_team_id,state=state,
                    team_signature=self._unified_team_sha,source=result_source)
                # Postmatch-only SQLite archive. Failure cannot erase the
                # finished match or prevent an already queued GPT review.
                buffer=getattr(self, "_p1_buffer", None)
                if buffer is not None and buffer.match_id==panel.match_id:
                    try:
                        from .offmatch_evidence import MatchEvidenceArchive
                        if buffer.context.get("team_id")==self._unified_team_id:
                            archived = MatchEvidenceArchive(
                                self.settings_service.settings.storage.data_root
                            ).save_ended(buffer.finish())
                            if archived.get("status") not in (
                                    "STORED_UNVERIFIED", "ALREADY_STORED"):
                                self.header_labels["records"].setText(
                                    "P1証拠未保存: " + str(archived.get("status")))
                    except Exception as evidence_error:
                        self.header_labels["records"].setText(
                            "P1証拠保存失敗: " + type(evidence_error).__name__)
                if learning.postmatch_review(panel.match_id) is not None:
                    self._show_saved_postmatch_review(panel.match_id)
                elif inserted or learning.pending_match(panel.match_id) is not None:
                    # Retries of MATCH_END never duplicate history. The pending
                    # review survives missing model, busy worker and failures.
                    if inserted:
                        self._set_postmatch_status("対戦記録済み",panel.match_id)
                    if self._postmatch_worker is None:
                        self._start_postmatch_review(panel.match_id)
                    elif getattr(self._postmatch_worker, "match_id", None) != panel.match_id:
                        self._postmatch_waiting_match_id=panel.match_id
                        self._set_postmatch_status(
                            "レビュー起動待ち（別試合のAI処理中）",panel.match_id)
                else:
                    self._set_postmatch_status(
                        "未実行（終了記録なし・再試行不可）",panel.match_id)
            except (OSError,ValueError,RuntimeError) as exc:
                # Review errors are visible, but never block the match UI.
                self._set_postmatch_status(
                    "失敗（記録またはDB処理）",panel.match_id,
                    reason=type(exc).__name__)
        elif status == "RESET":
            self._unified_match_id = None
            self._unified_last_report_key = None

    def _postmatch_store(self):
        from .matchup_learning_store import MatchupLearningStore
        return MatchupLearningStore(self.settings_service.settings.storage.data_root)

    def _set_postmatch_status(self, message: str, match_id: str,
                              *, reason: str = "") -> None:
        self._postmatch_match_id=match_id
        self.postmatch_status_label.setText(message)
        self.postmatch_match_label.setText("試合ID：" + match_id)
        try:
            storage_path=str(self._postmatch_store().db_path)
        except (OSError,ValueError,RuntimeError):
            storage_path="SQLiteにアクセスできません"
        self.postmatch_storage_label.setText("保存先：" + storage_path)
        if reason:
            self.postmatch_detail.setPlainText("理由：" + reason)
        try:
            can_retry=(self._postmatch_worker is None
                       and self._postmatch_store().pending_match(match_id) is not None)
        except (OSError,ValueError,RuntimeError):
            can_retry=False
        self.postmatch_retry_button.setEnabled(can_retry)

    def _show_saved_postmatch_review(self, match_id: str) -> None:
        stored=self._postmatch_store().postmatch_review(match_id)
        if stored is None:
            self._set_postmatch_status("失敗（SQLite読戻し不可）",match_id)
            return
        review=stored["review"]
        findings=review.get("findings",[])
        details=[
            "保存状態：" + stored["status"],
            "保存時刻：" + str(stored["reviewed_at"]),
            "スキーマ：" + stored["schema"],
            "次戦向けAI要約（未検証）：" + str(review.get("next_match_notes","")),
            "分析件数：" + str(len(findings)),
        ]
        for idx,item in enumerate(findings,1):
            details.append(
                f"{idx}. [{item.get('certainty','HYPOTHESIS')}] "
                f"{item.get('opponent','')} / {item.get('our_pokemon','')}："
                f"{item.get('issue','')} → {item.get('suggestion','')}"
            )
        self._set_postmatch_status("完了・SQLite保存済み（仮説／未検証）",match_id)
        self.postmatch_detail.setPlainText("\n".join(details))
        self.postmatch_retry_button.setEnabled(False)

    def _start_postmatch_review(self, match_id: str) -> None:
        if self._postmatch_worker is not None:
            self._set_postmatch_status(
                "レビュー起動待ち（別試合のワーカーが実行中）",match_id)
            return
        if self._postmatch_store().pending_match(match_id) is None:
            if self._postmatch_store().postmatch_review(match_id) is not None:
                self._show_saved_postmatch_review(match_id)
            else:
                self._set_postmatch_status("未実行（未処理記録なし）",match_id)
            return
        from .app_config import default_app_root
        from .vision_preferences import VisionPreferenceStore
        prefs=VisionPreferenceStore(default_app_root())
        model=prefs.selected_model()
        if not model:
            self._set_postmatch_status(
                "未実行（モデル未設定・SQLiteに未処理記録を保持）",match_id)
            return
        worker=_PostmatchReviewWorker(
            self.settings_service.settings.storage.data_root,
            match_id,default_app_root(),model,prefs.reasoning_effort(),self)
        self._postmatch_worker=worker
        self._set_postmatch_status("レビュー実行中（AI通信）",match_id)
        self.postmatch_retry_button.setEnabled(False)
        worker.result_ready.connect(self._postmatch_review_done)
        worker.finished.connect(self._postmatch_review_finished)
        worker.start()

    @Slot(object)
    def _postmatch_review_done(self, result: dict) -> None:
        worker=self._postmatch_worker
        match_id=str(result.get("match_id") or (
            worker.match_id if worker is not None else self._postmatch_match_id or ""))
        if (self._postmatch_waiting_match_id
                and self._postmatch_waiting_match_id != match_id):
            # The older review was stored/failed in SQLite, but do not
            # replace the newer match's pending status in the UI.
            return
        if result.get("status")=="STORED_UNVERIFIED_REVIEW":
            try:
                self._show_saved_postmatch_review(match_id)
                research=result.get("improvement_research") or {}
                count=research.get("candidate_count",0)
                if research.get("status")=="STORED_PROVISIONAL_UNVERIFIED":
                    self.postmatch_detail.appendPlainText(
                        f"複数試合の改善研究候補: {count}件（未検証・自動適用なし）")
                elif research.get("status")=="RESEARCH_FAILED_REVIEW_RETAINED":
                    self.postmatch_detail.appendPlainText(
                        "改善研究の保存失敗（対戦後レビューは保存済み）")
            except (OSError,ValueError,RuntimeError) as exc:
                self._set_postmatch_status(
                    "失敗（レビューSQLite読戻し）",match_id,
                    reason=type(exc).__name__)
        else:
            self._set_postmatch_status(
                "失敗（レビュー未保存・再試行可能）",match_id,
                reason=str(result.get("status") or "UNKNOWN"))
        # A worker is still finishing; retry becomes enabled on finished.

    @Slot()
    def _postmatch_review_finished(self) -> None:
        worker=self._postmatch_worker
        self._postmatch_worker=None
        if worker is not None:
            worker.deleteLater()
        if self._postmatch_waiting_match_id:
            waiting=self._postmatch_waiting_match_id
            self._postmatch_waiting_match_id=None
            self._set_postmatch_status("レビュー起動待ち（前試合の処理終了）",waiting)
        if self._postmatch_match_id:
            try:
                pending=self._postmatch_store().pending_match(self._postmatch_match_id)
                self.postmatch_retry_button.setEnabled(pending is not None)
            except (OSError,ValueError,RuntimeError):
                self.postmatch_retry_button.setEnabled(False)
        self._retry_close_after_workers()

    def _retry_pending_postmatch_review(self) -> None:
        if not self._unified or self._postmatch_worker is not None:
            return
        if (self._match_panel.state is not None
                and self._match_panel.state.get("phase") != "ENDED"):
            self._set_postmatch_status(
                "未実行（対戦中のAIを優先）",self._postmatch_match_id or "—")
            return
        if self._postmatch_match_id:
            self._start_postmatch_review(self._postmatch_match_id)

    @Slot()
    def _retry_close_after_workers(self):
        if not getattr(self,"_close_pending",False):
            return
        panel=self._match_panel
        if (panel._thread is None and panel._prep_thread is None
                and (self._postmatch_worker is None
                     or not self._postmatch_worker.isRunning())):
            QTimer.singleShot(0,self.close)

    def _match_active_for_settings(self) -> bool:
        if self._unified and self._match_panel.state is not None:
            if self._match_panel.state.get("phase") != "ENDED":
                return True
        session = self.controller.session
        return session is not None and session.phase not in {MatchPhase.S00_WAIT_START, MatchPhase.S08_NEXT_MATCH_READY}

    def _open_settings(self) -> None:
        if self.settings_service is None:
            return
        if self._settings_dialog is not None:
            self._settings_dialog.show()
            return
        from .settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            self.settings_service,
            match_active=self._match_active_for_settings,
            first_launch=self.settings_service.first_launch,
            on_active_capture_change=(self._halt_unified_capture
                                      if self._unified else self.controller.emergency_stop),
            parent=self,
        )
        self._settings_dialog = dialog

        def closed(_result: int) -> None:
            self._settings_dialog = None
            if not self._unified:
                self.controller.refresh()

        dialog.finished.connect(closed)
        dialog.open()

    def _open_auto_match_support(self) -> None:
        """Focus the ONLY live support view; never create a second window."""
        if self._unified:
            self.tabs.setCurrentWidget(self._match_scroll)

    def _state_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.selection_label = QLabel("選出: 未選出")
        self.counterparty_label = QLabel("対戦相手の区分: UNKNOWN（断定しない）")
        layout.addWidget(self.selection_label)
        layout.addWidget(self.counterparty_label)
        self.state_tree = QTreeWidget()
        self.state_tree.setHeaderLabels(["項目", "観測値", "根拠"])
        self.state_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.state_tree)
        return tab

    def _correction_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        group = QGroupBox("状態訂正（既存イベントを変更せず EVIDENCE を追記）")
        form = QFormLayout(group)
        self.correction_field = QComboBox()
        self.correction_field.addItems(self.correction_fields)
        self.correction_value = QPlainTextEdit("null")
        self.correction_value.setMaximumHeight(92)
        self.correction_certainty = QComboBox()
        self.correction_certainty.addItems(("CONFIRMED", "CANDIDATE"))
        apply_button = QPushButton("訂正を追記")
        apply_button.clicked.connect(self._apply_correction)
        form.addRow("項目", self.correction_field)
        form.addRow("JSON値", self.correction_value)
        form.addRow("確定度", self.correction_certainty)
        form.addRow(apply_button)
        layout.addWidget(group)

        selection_group = QGroupBox("実際の選出（選出確定後は訂正として追記）")
        selection_form = QFormLayout(selection_group)
        self.actual_three = QLineEdit()
        self.actual_three.setPlaceholderText("3匹をカンマ区切りで入力")
        self.actual_lead = QLineEdit()
        selection_button = QPushButton("実選出を記録／訂正")
        selection_button.clicked.connect(self._apply_actual_selection)
        selection_form.addRow("実選出3匹", self.actual_three)
        selection_form.addRow("実際の先発", self.actual_lead)
        selection_form.addRow(selection_button)
        layout.addWidget(selection_group)
        layout.addStretch(1)
        return tab

    def _operations_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        start_group = QGroupBox("次戦開始（4ファイルを新規ロードして固定）")
        start_form = QFormLayout(start_group)
        self.match_id_input = QLineEdit()
        self.match_id_input.setPlaceholderText("空欄なら時刻ベースのローカルID")
        self.start_match_button = QPushButton("次戦を開始")
        self.start_match_button.clicked.connect(self._start_new_match)
        start_form.addRow("match_id", self.match_id_input)
        start_form.addRow(self.start_match_button)
        layout.addWidget(start_group)
        self.api_enabled_box = QCheckBox("API通信を開始（明示的オプトイン設定が別途必要）")
        self.api_enabled_box.toggled.connect(self.controller.set_api_enabled)
        stop_button = QPushButton("緊急停止（新規送信停止・進行中結果を無効化）")
        stop_button.clicked.connect(self._emergency_stop)
        self.send_phase = QComboBox()
        self.send_phase.addItems(("SELECT", "PREP", "NORMAL_COMMAND", "PIVOT_TARGET", "FAINT_REPLACEMENT", "EFFECT_REPLACEMENT", "REVIEW"))
        self.send_model_button = QPushButton("現在の入力機会を送信キューへ")
        self.send_model_button.clicked.connect(self._request_model_job)
        layout.addWidget(self.api_enabled_box)
        layout.addWidget(stop_button)
        layout.addWidget(QLabel("送信対象（表示更新では送信しない）"))
        layout.addWidget(self.send_phase)
        layout.addWidget(self.send_model_button)
        layout.addWidget(QLabel("APIキーはこの画面・SQLite・ログに表示または保存しません。"))
        if self.settings_validation_only:
            self.match_id_input.setEnabled(False)
            self.start_match_button.setEnabled(False)
            self.api_enabled_box.setEnabled(False)
            self.send_phase.setEnabled(False)
            self.send_model_button.setEnabled(False)
            layout.addWidget(QLabel("このポータブル検証モードでは、設定・読み取りプレビューのみを行います。対戦開始とAPI送信は無効です。"))
        layout.addStretch(1)
        return tab

    def _history_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        buttons = QHBoxLayout()
        refresh = QPushButton("一覧更新")
        refresh.clicked.connect(self._refresh_history)
        export = QPushButton("選択試合をエクスポート")
        export.clicked.connect(self._export_selected)
        hide = QPushButton("選択試合を非表示（追記）")
        hide.clicked.connect(self._hide_selected)
        purge = QPushButton("選択試合を永続削除")
        purge.clicked.connect(self._purge_selected)
        buttons.addWidget(refresh)
        buttons.addWidget(export)
        buttons.addWidget(hide)
        buttons.addWidget(purge)
        layout.addLayout(buttons)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(["match_id", "作成", "イベント", "最終更新", "非表示"])
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.history_table.itemSelectionChanged.connect(self._show_selected_events)
        self.history_events = QPlainTextEdit()
        self.history_events.setReadOnly(True)
        splitter.addWidget(self.history_table)
        splitter.addWidget(self.history_events)
        layout.addWidget(splitter)
        return tab

    def _review_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel("選出・立ち回り・支援側の問題は、モデルの REVIEW 結果を分けて確認します。"))
        self.review_text = QPlainTextEdit()
        self.review_text.setReadOnly(True)
        layout.addWidget(self.review_text)
        return tab

    @Slot(object)
    def render(self, presentation: DesktopPresentation) -> None:
        if self._unified:
            # A detached legacy DesktopController must never overwrite the
            # visible state of the live screenshot-based match.
            return
        self.recommendation_label.setText(presentation.recommendation)
        self.selection_label.setText(f"選出: {presentation.selection}")
        self.counterparty_label.setText(f"対戦相手の区分: {presentation.counterparty}")
        self.header_labels["phase"].setText(presentation.phase)
        window_text = " / ".join(value for value in (presentation.decision_kind, presentation.decision_status, presentation.decision_window_id) if value) or "—"
        self.header_labels["window"].setText(window_text)
        self.header_labels["turn"].setText(presentation.turn or "—")
        self.header_labels["deadline"].setText(presentation.deadline)
        self.header_labels["preload"].setText(presentation.preload)
        self.header_labels["prep"].setText(presentation.prep)
        self.header_labels["api"].setText(presentation.api_state)
        self.header_labels["usage"].setText(presentation.api_usage)
        self.state_tree.clear()
        for row in presentation.state_rows:
            self.state_tree.addTopLevelItem(QTreeWidgetItem([row.label, row.value, row.certainty]))
        self.review_text.setPlainText(presentation.review_text)
        self._refresh_history()

    def _set_on_top(self, enabled: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.show()

    def _set_compact(self, enabled: bool) -> None:
        if self._unified:
            # Never hide the active input/stop controls, even in compact mode.
            self._match_panel.output.setVisible(not enabled)
            self.resize(self.width(), 360 if enabled else max(650, self.height()))
            return
        self.tabs.setVisible(not enabled)
        self.selection_label.setVisible(not enabled)
        self.counterparty_label.setVisible(not enabled)
        self.resize(self.width(), 185 if enabled else max(460, self.height()))

    def _apply_correction(self) -> None:
        try:
            value = json.loads(self.correction_value.toPlainText())
            self.controller.apply_manual_correction(
                self.correction_field.currentText(), value, certainty=self.correction_certainty.currentText()
            )
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "訂正できません", str(exc))

    def _apply_actual_selection(self) -> None:
        names = tuple(item.strip() for item in self.actual_three.text().split(",") if item.strip())
        try:
            if len(names) != 3:
                raise ValueError("実選出は重複しない3匹を入力してください")
            self.controller.apply_actual_selection((names[0], names[1], names[2]), self.actual_lead.text().strip())
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "実選出を記録できません", str(exc))

    def _emergency_stop(self) -> None:
        self.api_enabled_box.blockSignals(True)
        self.api_enabled_box.setChecked(False)
        self.api_enabled_box.blockSignals(False)
        self.controller.emergency_stop()

    def _start_new_match(self) -> None:
        match_id = self.match_id_input.text().strip() or f"desktop-{int(time.time() * 1000)}"
        try:
            self.controller.start_new_match(match_id)
        except Exception as exc:
            QMessageBox.warning(self, "次戦を開始できません", f"4ファイルの読込に失敗しました: {type(exc).__name__}")

    def _request_model_job(self) -> None:
        if self.controller.request_model_job(self.send_phase.currentText()) is None:
            QMessageBox.information(self, "送信なし", "API停止中、入力機会不一致、期限切れ、または重複送信です。")

    def _refresh_history(self) -> None:
        records = self.controller.store.list_matches()
        self.history_table.setRowCount(len(records))
        for row, record in enumerate(records):
            values = (record["match_id"], record["created_at"], str(record["event_count"]), str(record["last_event_at"] or "—"), "はい" if record["hidden"] else "いいえ")
            for column, value in enumerate(values):
                self.history_table.setItem(row, column, QTableWidgetItem(value))

    def _selected_match_id(self) -> str | None:
        selected = self.history_table.selectedItems()
        return None if not selected else selected[0].text()

    def _show_selected_events(self) -> None:
        match_id = self._selected_match_id()
        if match_id is None:
            return
        self.history_events.setPlainText(json.dumps(self.controller.store.events(match_id), ensure_ascii=False, indent=2))

    def _export_selected(self) -> None:
        match_id = self._selected_match_id()
        if match_id is None:
            return
        target, _ = QFileDialog.getSaveFileName(self, "試合をエクスポート", f"{match_id}.json", "JSON (*.json)")
        if target:
            self.controller.export_match(match_id, target)

    def _hide_selected(self) -> None:
        match_id = self._selected_match_id()
        if match_id is None:
            return
        if QMessageBox.question(self, "履歴を非表示", "追記イベントで一覧から非表示と記録します。元データは削除しません。続行しますか？") == QMessageBox.StandardButton.Yes:
            self.controller.hide_match(match_id)

    def _purge_selected(self) -> None:
        match_id = self._selected_match_id()
        if match_id is None:
            return
        confirmation, accepted = QInputDialog.getText(
            self,
            "永続削除の確認",
            "エクスポート後、完全に削除する match_id をそのまま入力してください。",
        )
        if not accepted:
            return
        try:
            self.controller.purge_match(match_id, confirmation_match_id=confirmation)
        except (KeyError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "削除されませんでした", str(exc))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt callback name
        if self._unified:
            self._close_pending=True
            if not self._match_panel._shutdown_ready:
                self._match_panel.request_shutdown("close")
            if (self._match_panel._thread is not None or
                    self._match_panel._prep_thread is not None or
                    (self._postmatch_worker is not None
                     and self._postmatch_worker.isRunning())):
                # Cancel new capture/replies now, close automatically as soon
                # as QThreads have finished. Never force-destroy live workers.
                event.ignore()
                return
        else:
            # A closed legacy dashboard must not render a queued presentation
            # after its test/runtime owner has closed the SQLite store.
            try:
                self.controller.presentation_changed.disconnect(self.render)
            except (RuntimeError, TypeError):
                pass
        self.preferences.save(self, compact=self.compact_box.isChecked(), always_on_top=self.on_top_box.isChecked())
        super().closeEvent(event)


def ensure_application() -> QApplication:
    """Return the singleton application without selecting a display backend."""
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])
