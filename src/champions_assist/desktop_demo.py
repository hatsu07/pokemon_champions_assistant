"""Explicitly simulated, no-network desktop demonstration for Phase 5."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QTimer

from .capture import Frame, SyntheticFrameSource
from .desktop import DesktopController, DesktopMainWindow, ensure_application
from .fsm import MatchController, ObservationEvent, VisualUI
from .model_adapter import MockResponsesTransport, ModelAdapter
from .session_payloads import request_session_model_job
from .settings import ModelSettings
from .simulate import _simulated_response
from .store import BattleStore
from .wire import AllowedActions


@dataclass
class SyntheticDesktopDemo:
    """Owns the in-memory objects for an explicitly labelled demo run."""

    store: BattleStore
    desktop: DesktopController
    phase_trace: tuple[str, ...]
    mock_model_jobs: int
    synthetic_frames_seen: int

    def close(self) -> None:
        self.store.close()


def build_synthetic_desktop_demo(master_directory: str | Path) -> SyntheticDesktopDemo:
    """Build SELECT → PREP → battle → REVIEW using only fixture responses.

    The blank frame exists only to demonstrate an offline ``SyntheticFrameSource``
    handoff.  It is not claimed to resemble a Champions screen or to validate
    OCR/capture accuracy.
    """
    store = BattleStore(":memory:")
    controller = MatchController(master_directory, store=store)
    session = controller.start_match("desktop-simulated-match-001", now_ms=0)
    trace = [session.phase.value]
    opponents = tuple(list(session.assets.matrix_index)[:6])
    session.receive_opponent_preview(opponents, now_ms=1)
    trace.append(session.phase.value)
    transport = MockResponsesTransport([_simulated_response] * 4)
    adapter = ModelAdapter(store=store, transport=transport, settings=ModelSettings(), now_ms=lambda: 0)
    request_session_model_job(session, adapter, phase="SELECT", request_id="desktop-simulated-select", now_ms=2)
    selected = session.selection_recommendation
    assert selected is not None
    session.record_actual_selection(selected.selected_three, selected.lead, now_ms=3)
    trace.append(session.phase.value)
    request_session_model_job(session, adapter, phase="PREP", request_id="desktop-simulated-prep", now_ms=4)
    session.observe_event("desktop-simulated-lead", ObservationEvent.OPPONENT_LEAD_VISIBLE, now_ms=5)
    trace.append(session.phase.value)
    window = session.present_ui(
        VisualUI.COMMAND_MENU,
        AllowedActions(completeness="COMPLETE", moves=("SIMULATED_MOVE",)),
        now_ms=6,
    )
    assert window is not None
    request_session_model_job(session, adapter, phase="NORMAL_COMMAND", request_id="desktop-simulated-battle", now_ms=7)
    session.record_actual_action("desktop-simulated-action", {"action": "SIMULATED_MOVE", "source": "synthetic"})
    session.observe_event("desktop-simulated-end", ObservationEvent.MATCH_END_CONFIRMED, now_ms=8)
    trace.append(session.phase.value)
    session.begin_review()
    review = request_session_model_job(session, adapter, phase="REVIEW", request_id="desktop-simulated-review", now_ms=9)
    session.finish_review()
    trace.append(session.phase.value)

    frame = Frame(
        frame_id="desktop-synthetic-blank",
        capture_seq=1,
        captured_at_ms=10,
        image=np.zeros((8, 8, 3), dtype=np.uint8),
        source="SYNTHETIC_PHASE5_DEMO",
    )
    synthetic_frames_seen = sum(1 for _ in SyntheticFrameSource((frame,)))
    desktop = DesktopController(store=store, session=session, clock_ms=lambda: 10)
    desktop.latest_review = review.response
    return SyntheticDesktopDemo(
        store=store,
        desktop=desktop,
        phase_trace=tuple(trace),
        mock_model_jobs=len(transport.requests),
        synthetic_frames_seen=synthetic_frames_seen,
    )


def run_desktop_demo(master_directory: str | Path, *, hold_ms: int = 0) -> dict[str, Any]:
    """Render the fixture to a PySide6 window, optionally holding it open."""
    demo = build_synthetic_desktop_demo(master_directory)
    application = ensure_application()
    window = DesktopMainWindow(demo.desktop)
    try:
        window.show()
        if hold_ms > 0:
            QTimer.singleShot(hold_ms, application.quit)
            application.exec()
        else:
            application.processEvents()
        presentation = demo.desktop.presentation()
        return {
            "mode": "SIMULATED_ONLY",
            "phase_trace": list(demo.phase_trace),
            "final_phase": presentation.phase,
            "prep": presentation.prep,
            "mock_model_jobs": demo.mock_model_jobs,
            "synthetic_frames_seen": demo.synthetic_frames_seen,
            "openai_network": "NOT_USED",
            "game_control": "NOT_IMPLEMENTED",
            "windows_capture": "NOT_USED",
        }
    finally:
        window.close()
        demo.close()
