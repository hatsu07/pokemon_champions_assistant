from __future__ import annotations

import json
from io import StringIO
import os
from pathlib import Path
import tempfile
import threading
import unittest
from contextlib import redirect_stdout

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QSettings, QTimer

from champions_assist.__main__ import main
from champions_assist.desktop import (
    DesktopController,
    DesktopMainWindow,
    DesktopModelDispatcher,
    DesktopPreferences,
    ensure_application,
)
from champions_assist.fsm import MatchController, ObservationEvent, VisualUI, WindowStatus
from champions_assist.model_adapter import ModelOutcome, ModelOutcomeStatus
from champions_assist.store import BattleStore
from champions_assist.wire import AllowedActions


OPPONENTS = ("Opp1", "Opp2", "Opp3", "Opp4", "Opp5", "Opp6")
TEAM = ("Own1", "Own2", "Own3", "Own4", "Own5", "Own6")


def write_masters(root: Path) -> None:
    (root / "Pokemon_Champions_MC_live_decision_gate.txt").write_text("LIVE GATE\n", encoding="utf-8")
    (root / "Pokemon_Champions_MC_live_selection_matrix.txt").write_text("\n".join(f"{name}|matrix" for name in OPPONENTS) + "\n", encoding="utf-8")
    (root / "Pokemon_Champions_MC_60sec_lookup_master.txt").write_text("\n".join(f"{name}|lookup" for name in OPPONENTS) + "\n", encoding="utf-8")
    header = "record,name,item,ability,nature,ap_h_a_b_c_d_s,move1,move2,move3,move4,role,form_change,runtime_note\n"
    rows = "\n".join(
        f"MON,{name},item,ability,nature,evs,{name}-m1,{name}-m2,{name}-m3,{name}-m4,role,,note" for name in TEAM
    )
    (root / "Pokemon_Champions_MC_team_2026-10-03.csv").write_text(header + rows + "\n", encoding="utf-8")


def battle_response(payload: dict) -> dict:
    return {
        "protocol_version": "2.3.0",
        "request_id": payload["request_id"],
        "match_id": payload["match_id"],
        "decision_window_id": payload["decision_window_id"],
        "tactical_state_version": payload["tactical_state_version"],
        "allowed_action_digest": payload["allowed_action_digest"],
        "decision_status": "RECOMMENDED",
        "reason_code": "GUI_FIXTURE",
        "warning": None,
        "action_kind": "MOVE",
        "move": "move-a",
        "mega": False,
        "switch_target": None,
        "planned_pivot_target": None,
        "keep_back": None,
    }


class Phase5TestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = ensure_application()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "masters"
        self.root.mkdir()
        write_masters(self.root)
        self.store = BattleStore(Path(self.temp.name) / "battle.sqlite3")
        self.match_controller = MatchController(self.root, store=self.store)
        self.clock = 100

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def _now(self) -> int:
        return self.clock

    def battle_session(self):
        session = self.match_controller.start_match("phase5-match", now_ms=0)
        session.receive_opponent_preview(OPPONENTS, now_ms=1)
        session.record_actual_selection(TEAM[:3], TEAM[0], now_ms=2)
        session.observe_event("lead", ObservationEvent.OPPONENT_LEAD_VISIBLE, now_ms=3)
        window = session.present_ui(
            VisualUI.COMMAND_MENU,
            AllowedActions(completeness="COMPLETE", moves=("move-a",), switch_targets=("Own2", "Own3")),
            now_ms=4,
        )
        assert window is not None
        return session

    def desktop_for_battle(self) -> tuple[DesktopController, object]:
        session = self.battle_session()
        return DesktopController(store=self.store, session=session, clock_ms=self._now), session

    def test_main_view_shows_frozen_preload_input_window_and_unknowns(self) -> None:
        desktop, session = self.desktop_for_battle()
        desktop.apply_manual_correction("our_active", "Own1", certainty="CONFIRMED")
        view = desktop.presentation()
        self.assertEqual(view.phase, "S05_IN_BATTLE")
        self.assertEqual(view.preload, "4/4 読込・SHA-256固定")
        self.assertEqual(view.decision_kind, "NORMAL_COMMAND")
        self.assertIn("表示可能なAI回答", view.recommendation)
        active = next(row for row in view.state_rows if row.label == "自軍・現在")
        self.assertEqual(active.value, '"Own1"')
        self.assertEqual(active.certainty, "CONFIRMED")
        self.assertEqual(session.current_window.status, WindowStatus.OPEN)

    def test_model_result_is_applied_once_and_duplicate_send_is_suppressed(self) -> None:
        desktop, session = self.desktop_for_battle()
        jobs: list[object] = []
        desktop.model_job_requested.connect(jobs.append)
        desktop.set_api_enabled(True)
        job = desktop.request_model_job("NORMAL_COMMAND")
        self.assertIsNotNone(job)
        self.assertIsNone(desktop.request_model_job("NORMAL_COMMAND"))
        self.assertEqual(len(jobs), 1)
        assert job is not None
        desktop.apply_model_outcome(job, ModelOutcome(ModelOutcomeStatus.DISPLAYABLE, "OK", battle_response(job.payload), sent=True))
        self.assertEqual(session.current_window.status, WindowStatus.RECOMMENDED)
        self.assertIn("move-a", desktop.presentation().recommendation)

    def test_manual_correction_invalidates_an_inflight_recommendation(self) -> None:
        desktop, session = self.desktop_for_battle()
        desktop.set_api_enabled(True)
        job = desktop.request_model_job("NORMAL_COMMAND")
        assert job is not None
        desktop.latest_review = {"outcome": "STALE_FIXTURE"}
        desktop.apply_manual_correction("hp_status", [{"side": "ours", "current": 80, "max": 100}], certainty="CONFIRMED")
        self.assertEqual(session.current_window.status, WindowStatus.STALE)
        self.assertIsNone(desktop.latest_review)
        desktop.apply_model_outcome(job, ModelOutcome(ModelOutcomeStatus.DISPLAYABLE, "OK", battle_response(job.payload), sent=True))
        self.assertEqual(session.current_window.status, WindowStatus.STALE)
        self.assertNotIn("move-a", desktop.presentation().recommendation)

    def test_late_actual_selection_correction_is_evidence_and_invalidates_prep(self) -> None:
        desktop, session = self.desktop_for_battle()
        desktop.set_api_enabled(True)
        self.assertIsNotNone(desktop.request_model_job("NORMAL_COMMAND"))
        desktop.apply_actual_selection(("Own2", "Own3", "Own4"), "Own2")
        self.assertEqual(session.actual_selected_three, ("Own2", "Own3", "Own4"))
        self.assertEqual(session.actual_lead, "Own2")
        self.assertFalse(session.prep_validation.complete)
        self.assertEqual(session.prep_validation.reason, "ACTUAL_SELECTION_CORRECTED_REPREP_REQUIRED")
        self.assertEqual(session.current_window.status, WindowStatus.STALE)
        fields = self.store.replay(session.match_id)["fields"]
        self.assertEqual(fields["actual_lead"]["value"], "Own2")

    def test_api_stop_and_emergency_stop_prevent_new_or_old_display(self) -> None:
        desktop, session = self.desktop_for_battle()
        self.assertIsNone(desktop.request_model_job("NORMAL_COMMAND"))
        desktop.set_api_enabled(True)
        job = desktop.request_model_job("NORMAL_COMMAND")
        assert job is not None
        desktop.emergency_stop()
        self.assertIsNone(desktop.request_model_job("NORMAL_COMMAND"))
        desktop.apply_model_outcome(job, ModelOutcome(ModelOutcomeStatus.DISPLAYABLE, "OK", battle_response(job.payload), sent=True))
        self.assertEqual(session.current_window.status, WindowStatus.REQUESTED)
        self.assertNotIn("move-a", desktop.presentation().recommendation)

    def test_corrections_are_append_only_and_history_export_excludes_master_contents(self) -> None:
        desktop, session = self.desktop_for_battle()
        desktop.apply_manual_correction("field_effects", [{"weather": "rain"}], certainty="CANDIDATE")
        events = self.store.events(session.match_id)
        self.assertTrue(any(event["event_type"] == "EVIDENCE" for event in events))
        export = self.store.export_match(session.match_id)
        self.assertEqual(len(export["source_manifest"]), 4)
        self.assertNotIn("LIVE GATE", json.dumps(export, ensure_ascii=False))
        self.assertFalse(self.store.list_matches()[0]["hidden"])
        self.assertTrue(desktop.hide_match(session.match_id))
        self.assertTrue(self.store.list_matches()[0]["hidden"])
        with tempfile.TemporaryDirectory() as directory:
            path = desktop.export_match(session.match_id, Path(directory) / "match.json")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["match_id"], session.match_id)

    def test_usage_is_displayed_without_any_secret_field(self) -> None:
        desktop, session = self.desktop_for_battle()
        self.store.record_api_usage(
            session.match_id,
            request_id="usage-1",
            transport="mock",
            model="test-model",
            external=False,
            input_tokens=12,
            output_tokens=8,
            total_tokens=20,
            estimated_cost_microusd=0,
            outcome="DISPLAYABLE",
        )
        self.assertIn("20 tokens", desktop.presentation().api_usage)
        self.assertNotIn("key", desktop.presentation().api_usage.lower())

    def test_permanent_history_purge_requires_exact_confirmation(self) -> None:
        _desktop, session = self.desktop_for_battle()
        with self.assertRaises(ValueError):
            self.store.purge_match_permanently(session.match_id, confirmation_match_id="wrong-match")
        self.assertGreater(self.store.event_count(session.match_id), 0)
        self.store.purge_match_permanently(session.match_id, confirmation_match_id=session.match_id)
        self.assertEqual(self.store.list_matches(), [])

    def test_qt_dispatcher_runs_executor_off_gui_thread(self) -> None:
        desktop, _ = self.desktop_for_battle()
        desktop.set_api_enabled(True)
        job = desktop.request_model_job("NORMAL_COMMAND")
        assert job is not None
        gui_thread = threading.get_ident()
        worker_threads: list[int] = []
        outcomes: list[ModelOutcome] = []

        def executor(_job):
            worker_threads.append(threading.get_ident())
            return ModelOutcome(ModelOutcomeStatus.UNAVAILABLE, "TEST_WORKER")

        dispatcher = DesktopModelDispatcher(executor)
        dispatcher.model_outcome.connect(lambda _job, outcome: outcomes.append(outcome))
        try:
            dispatcher.submit(job)
            loop = QEventLoop()
            QTimer.singleShot(1_000, loop.quit)
            QTimer.singleShot(20, loop.quit)
            loop.exec()
        finally:
            dispatcher.close()
        self.assertEqual(len(outcomes), 1)
        self.assertNotEqual(worker_threads, [gui_thread])

    def test_window_compact_preference_and_no_render_side_effect(self) -> None:
        desktop, session = self.desktop_for_battle()
        settings = QSettings(str(Path(self.temp.name) / "ui.ini"), QSettings.Format.IniFormat)
        window = DesktopMainWindow(desktop, preferences=DesktopPreferences(settings))
        before = self.store.event_count(session.match_id)
        try:
            window.show()
            self.application.processEvents()
            window.compact_box.setChecked(True)
            self.application.processEvents()
            self.assertFalse(window.tabs.isVisible())
            desktop.refresh()
            self.application.processEvents()
            self.assertEqual(self.store.event_count(session.match_id), before)
        finally:
            window.close()

    def test_offscreen_cli_demo_completes_full_mock_lifecycle(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            code = main(["desktop-demo", "--master-dir", str(self.root), "--offscreen"])
        report = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["mode"], "SIMULATED_ONLY")
        self.assertEqual(report["final_phase"], "S08_NEXT_MATCH_READY")
        self.assertEqual(report["mock_model_jobs"], 4)
        self.assertEqual(report["openai_network"], "NOT_USED")


if __name__ == "__main__":
    unittest.main()
