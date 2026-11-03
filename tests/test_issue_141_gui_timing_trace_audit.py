"""#141⑥ Qt event clock regression tests: all events synthetic.

None of these trace entries are independent game video or proof that the
reported GPT move was legal/correct; no LOCAL_SHADOW GUI result is simulated.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest

from champions_assist.local_strategy.gui_timing_trace_audit import (
    SCHEMA, SOURCE, audit_gui_timing_export,
)


def scene(turns=2, delay=3000):
    rows = []
    for i in range(1, turns + 1):
        start = (i - 1) * 50000
        rows.extend((
            {"event": "TURN_SCREEN_OBSERVED", "at_ms": start,
             "turn_epoch": i, "screen_label": "TURN_ACTION"},
            {"event": "AI_REQUEST_DISPATCH", "at_ms": start + 400,
             "turn_epoch": i, "screen_label": "TURN_ACTION",
             "match_id": "capture-actual-opaque", "request_id": "req-" + str(i),
             "decision_window_id": "turn-" + str(i), "state_revision": i},
            {"event": "AI_RESULT_DISPLAYED", "at_ms": start + delay - 100,
             "turn_epoch": i, "match_id": "capture-actual-opaque",
             "request_id": "req-" + str(i), "state_revision": i,
             "status": "ADVICE", "recommendation_present": True},
            {"event": "ACTION_GUI_RENDERED", "at_ms": start + delay,
             "turn_epoch": i, "match_id": "capture-actual-opaque",
             "request_id": "req-" + str(i),
             "decision_window_id": "turn-" + str(i), "state_revision": i,
             "status": "ADVICE", "recommendation_present": True},
        ))
    return {"timing_observations": {
        "schema": SCHEMA,
        "source": SOURCE,
        "automatically_certified": False,
        "events": rows,
    }}


class GuiTimingTraceAuditTests(unittest.TestCase):
    def test_two_complete_qt_turn_chains_still_never_verify_game_or_shadow(self):
        result = audit_gui_timing_export(scene())
        self.assertEqual(result.status, "GUI_MONOTONIC_TIMING_ONLY")
        self.assertEqual(result.observed_turns, 2)
        self.assertEqual(result.complete_gpt_display_turns, 2)
        self.assertEqual(result.late_gpt_display_turns, 0)
        self.assertEqual(result.missing_or_unmatched_turns, 0)
        self.assertEqual((result.p50_gpt_screen_ms, result.p95_gpt_screen_ms,
                          result.p99_gpt_screen_ms), (3000, 3000, 3000))
        self.assertFalse(result.actual_device_video_aligned)
        self.assertFalse(result.local_shadow_ui_display_verified)
        self.assertFalse(result.actionable_champions_advice_verified)
        self.assertFalse(result.phase2_accepted)

    def test_actual_selection_then_turns_export_excludes_only_nonturn(self):
        # Real GUI _turn_epoch=0 during selection; the same exported trace
        # contains a selection dispatch and its result before TURN_SCREEN.
        trace = scene(turns=2)
        events = trace["timing_observations"]["events"]
        for event in events:
            event["at_ms"] += 15000
        selection = [
            {"event": "AI_REQUEST_DISPATCH", "at_ms": 1000,
             "turn_epoch": 0, "screen_label": "TEAM_SELECTION",
             "decision_window_id": "selection",
             "match_id": "capture-actual-opaque", "request_id": "req-select",
             "state_revision": 0},
            {"event": "AI_RESULT_DISPLAYED", "at_ms": 8000,
             "turn_epoch": 0, "match_id": "capture-actual-opaque",
             "request_id": "req-select", "state_revision": 0,
             "status": "OK", "recommendation_present": True},
        ]
        events[:0] = selection
        got = audit_gui_timing_export(trace)
        self.assertEqual(got.status, "GUI_MONOTONIC_TIMING_ONLY",
                         got.reason)
        self.assertEqual(got.complete_gpt_display_turns, 2)
        self.assertEqual(got.p99_gpt_screen_ms, 3000)
        # A malformed true TURN_ACTION cannot be hidden by labelling
        # its window as "selection".
        bad = deepcopy(trace)
        bad["timing_observations"]["events"][0]["screen_label"] = "TURN_ACTION"
        self.assertEqual(audit_gui_timing_export(bad).status,
                         "GUI_TRACE_REFUSED")

    def test_cancelled_or_superseded_old_reply_is_never_a_valid_render(self):
        for explicit_cancel in (True, False):
            trace = scene(turns=1)
            e = trace["timing_observations"]["events"]
            old_dispatch = deepcopy(e[1])
            old_dispatch["request_id"] = "req-old"
            old_dispatch["at_ms"] = 180
            e.insert(1, old_dispatch)
            if explicit_cancel:
                e.insert(2, {"event": "AI_REQUEST_LOGICALLY_CANCELLED",
                             "at_ms": 260, "turn_epoch": 1,
                             "request_id": "req-old",
                             "reason": "NEW_AI_REQUEST_DISPATCH"})
            # Old request is answered after a newer req was dispatched.
            e[-2]["request_id"] = "req-old"
            e[-1]["request_id"] = "req-old"
            got = audit_gui_timing_export(trace)
            self.assertEqual(got.status, "GUI_TRACE_REFUSED",
                             (explicit_cancel, got))
            self.assertFalse(got.phase2_accepted)

    def test_cancelled_old_request_followed_by_current_result_is_valid(self):
        trace = scene(turns=1)
        e = trace["timing_observations"]["events"]
        old_dispatch = deepcopy(e[1])
        old_dispatch["request_id"] = "req-old"
        old_dispatch["at_ms"] = 180
        e[1:1] = [
            old_dispatch,
            {"event": "AI_REQUEST_LOGICALLY_CANCELLED",
             "at_ms": 260, "turn_epoch": 1,
             "request_id": "req-old",
             "reason": "NEW_AI_REQUEST_DISPATCH"},
        ]
        got = audit_gui_timing_export(trace)
        self.assertEqual(got.complete_gpt_display_turns, 1)
        self.assertEqual(got.status, "GUI_MONOTONIC_TIMING_ONLY")

    def test_render_timestamp_emitted_after_actual_widget_text_updates(self):
        # Source-order contract for Qt integration; synthetic clock tests
        # by themselves could not detect an early "rendered" event.
        path = (Path(__file__).resolve().parents[1] / "src" /
                "champions_assist" / "match_replay_dialog.py")
        code = path.read_text(encoding="utf-8")
        render = code.split("    def _render(self, report):", 1)[1].split(
            "    def _export(self):", 1)[0]
        self.assertLess(render.index("self.action_banner.setText(displayed)"),
                        render.index("self.output.setPlainText("))
        self.assertLess(render.index("self.output.setPlainText("),
                        render.index('self._trace_event("ACTION_GUI_RENDERED"'))

    def test_display_past_45_seconds_is_measured_not_promoted(self):
        trace = scene(turns=1, delay=45001)
        got = audit_gui_timing_export(trace)
        self.assertEqual(got.status, "GUI_MONOTONIC_TIMING_ONLY")
        self.assertEqual(got.late_gpt_display_turns, 1)
        self.assertEqual(got.p99_gpt_screen_ms, 45001)
        self.assertFalse(got.phase2_accepted)

    def test_missing_display_is_incomplete_not_success(self):
        trace = scene()
        trace["timing_observations"]["events"].pop()
        got = audit_gui_timing_export(trace)
        self.assertEqual(got.complete_gpt_display_turns, 1)
        self.assertEqual(got.missing_or_unmatched_turns, 1)
        self.assertFalse(got.phase2_accepted)

    def test_ocr_retry_same_turn_and_old_result_do_not_count_as_current(self):
        trace = scene(turns=1)
        e = trace["timing_observations"]["events"]
        old = deepcopy(e[1])
        old["request_id"] = "req-old"
        old["at_ms"] = 100
        e.insert(1, old)
        got = audit_gui_timing_export(trace)
        self.assertEqual(got.complete_gpt_display_turns, 1)
        self.assertFalse(got.phase2_accepted)

    def test_mismatched_revision_request_and_out_of_order_refuse(self):
        cases = [
            (2, "state_revision", 99),
            (3, "request_id", "req-OTHER"),
            (3, "decision_window_id", "turn-OTHER"),
            (3, "match_id", "other-match"),
            (3, "recommendation_present", False),
            (0, "at_ms", 9000),
            (1, "turn_epoch", 99),
        ]
        for index, key, replacement in cases:
            case = scene(turns=1)
            case["timing_observations"]["events"][index][key] = replacement
            with self.subTest(index=index, key=key):
                report = audit_gui_timing_export(case)
                self.assertEqual(report.status, "GUI_TRACE_REFUSED")
                self.assertFalse(report.phase2_accepted)

    def test_test_only_not_treated_as_verified_or_displayed_advice(self):
        trace = scene(turns=1)
        trace["timing_observations"]["events"][2]["status"] = "TEST_ONLY"
        trace["timing_observations"]["events"][3]["status"] = "TEST_ONLY"
        result = audit_gui_timing_export(trace)
        self.assertEqual(result.status, "GUI_TRACE_REFUSED")
        self.assertFalse(result.phase2_accepted)

    def test_duplicate_render_and_legacy_unbound_render_fail_closed(self):
        for legacy in (False, True):
            trace = scene(turns=1)
            events = trace["timing_observations"]["events"]
            if legacy:
                events[3].pop("request_id")
            else:
                duplicate = deepcopy(events[3])
                duplicate["at_ms"] += 1
                events.append(duplicate)
            report = audit_gui_timing_export(trace)
            self.assertEqual(report.status, "GUI_TRACE_REFUSED")
            self.assertFalse(report.phase2_accepted)

    def test_reject_forged_certification_bad_schema_and_empty(self):
        for edit in (
            lambda p: p.update(automatically_certified=True),
            lambda p: p.update(schema="OLD"),
            lambda p: p.update(source="VIDEO_VERIFIED"),
            lambda p: p.update(events=[]),
            lambda p: p["events"][1].update(at_ms=100.2),
        ):
            trace = scene(turns=1)
            edit(trace["timing_observations"])
            self.assertEqual(audit_gui_timing_export(trace).status,
                             "GUI_TRACE_REFUSED")


if __name__ == "__main__":
    unittest.main()
