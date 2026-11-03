"""Stage1 software postmatch report cannot claim Champions acceptance."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch
import json
import unittest

from champions_assist.local_strategy.ui_shadow_dispatcher import UiShadowDispatcher
from champions_assist.local_strategy.shadow_comparison_store import (
    read_shadow_comparisons,
)
from scripts.report_phase2_shadow_software import (
    main, stage1_software_shadow_report,
)
from tests.test_issue_141_selection import make_assets
from tests.test_issue_141_gui_timing_trace_audit import scene


def receipt(path):
    bridge=UiShadowDispatcher(path)
    assert bridge.prepare_storage() == "READY"
    status=bridge.start(
        make_assets(), enabled=True, match_id="capture-1111",
        request_id="req-1", turn_epoch=1, state_revision=4,
        screen_label="TURN_ACTION",
        observed_state={
            "our_active":"A","opponent_active":"O1",
            "selected_three":["A","B","C"]},
        deadline_monotonic=monotonic()+30)
    assert status=="RUNNING_UNVERIFIED", status
    assert bridge.attach_gpt(
        request_id="req-1", turn_epoch=1, state_revision=4,
        status="ADVICE", advice_present=True, elapsed_ms=75,
        fresh=True)=="JOIN_SUBMITTED_NONACTIONABLE"
    result=bridge.last_future.result(timeout=10)
    assert result.status=="RECORDED", result.reason
    bridge.invalidate()
    return read_shadow_comparisons(path)


def gui_for_one():
    gui=scene(turns=1)
    for e in gui["timing_observations"]["events"]:
        e["match_id"]="capture-1111"
        e["request_id"]="req-1"
        e["state_revision"]=4
    return gui


class Stage1SoftwareQualityTests(unittest.TestCase):
    def test_join_same_qt_dispatch_still_never_claims_game_acceptance(self):
        with TemporaryDirectory() as d:
            path=Path(d)/"private"/"shadow.sqlite"
            rows=receipt(path)
            report=stage1_software_shadow_report(rows,gui_for_one())
            self.assertEqual(report["status"],
                             "SOFTWARE_QT_PAIR_COMPLETE_NOT_GAME_CERTIFIED",
                             report)
            self.assertEqual(report["qt_gpt_pair_matched"],1)
            self.assertEqual(report["qt_gpt_pair_unmatched"],0)
            self.assertFalse(report["phase2_accepted"])
            self.assertFalse(report["champions_mechanics_verified"])
            self.assertFalse(report["live_local_action_verified"])
            self.assertEqual(report["quality"]["actionable_count"],0)
            self.assertEqual(report["quality"]["abstain_count"],1)
            self.assertFalse(report["quality"]["pass_phase2_quality_gate"])
            self.assertGreater(len(report["quality"]["missing_goldens"]),0)

    def test_no_gui_and_wrong_revision_are_incomplete(self):
        with TemporaryDirectory() as d:
            rows=receipt(Path(d)/"private"/"shadow.sqlite")
            no_gui=stage1_software_shadow_report(rows)
            self.assertEqual(no_gui["qt_gpt_pair_unmatched"],1)
            bad=gui_for_one()
            bad["timing_observations"]["events"][1]["state_revision"]=5
            mismatched=stage1_software_shadow_report(rows,bad)
            self.assertNotEqual(mismatched["status"],
                                "SOFTWARE_QT_PAIR_COMPLETE_NOT_GAME_CERTIFIED")
            self.assertEqual(mismatched["qt_gpt_pair_matched"],0)

    def test_cli_saved_evidence_never_emits_phase2_true(self):
        with TemporaryDirectory() as d:
            path=Path(d)/"private"/"shadow.sqlite"
            receipt(path)
            gui_path=Path(d)/"gui.json"
            gui_path.write_text(json.dumps(gui_for_one()), encoding="utf-8")
            output=StringIO()
            with patch("sys.argv", [
                    "report_phase2_shadow_software.py",
                    "--shadow-sqlite",str(path),
                    "--gui-export",str(gui_path)]),redirect_stdout(output):
                code=main()
            self.assertEqual(code,0)
            result=json.loads(output.getvalue())
            self.assertFalse(result["phase2_accepted"])
            self.assertEqual(result["sqlite_window_count"],1)
            self.assertEqual(result["qt_gpt_pair_matched"],1)


if __name__ == "__main__":
    unittest.main()
