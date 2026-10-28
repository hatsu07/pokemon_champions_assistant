"""Off-match timing regression, never real or fabricated Champions battle facts."""
from __future__ import annotations

import os
import unittest
from copy import deepcopy
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import numpy as np
from PySide6.QtWidgets import QApplication

from champions_assist.live_timing_test import (
    selection_test_prep_state,prep_timing_test,turn_timing_test)
from champions_assist.selection_test_dialog import SelectionTestDialog
from champions_assist.prep import LEAD_TAGS,BENCH_TAGS
from tests.test_match_replay import fixture_assets,OPP,OWN


def selection_fixture():
    return {
        "status":"PROVISIONAL_ADVICE",
        "opponent_six":list(OPP),
        "selected_three":[OWN[0],OWN[3],OWN[4]],
        "lead":OWN[0],
    }


def prep_payload(state):
    rows=[]
    for name in state["selected_three"]:
        tags = LEAD_TAGS if name==state["lead"] else BENCH_TAGS
        for opponent in state["opponent_six"]:
            code="|".join(f"{tag}=" + (
                "MOVE" if tag=="ACT" else
                "registered_move" if tag=="DPS" else
                "UNVERIFIED" if tag=="PROC" else
                "unknown") for tag in tags)
            rows.append({"our_pokemon":name,"opponent_pokemon":opponent,
                         "eval_code":code})
    return {"matchups_18":rows,"preserve":state["selected_three"][0],
            "stopper":state["selected_three"][1],
            "closer":state["selected_three"][2]}


class FakeClock:
    def __init__(self):self.now=0.0
    def __call__(self):return self.now


class TimingCoreTest(unittest.TestCase):
    def setUp(self):
        self.assets=fixture_assets()
        self.state=selection_test_prep_state(self.assets,selection_fixture())
        self.clock=FakeClock()

    def fake_prep(self, elapsed=25, mutate=None):
        def runner(**kwargs):
            self.assertGreaterEqual(kwargs["limit_seconds"],120)
            self.clock.now=elapsed
            state=deepcopy(kwargs["state"])
            state["prep_status"]="STRUCTURE_COMPLETE"
            state["prep"]=prep_payload(state)
            if mutate:mutate(state)
            return state,{"status":"PREP_PROVISIONAL","reason":"OK"}
        return runner

    def test_selection_is_simulated_never_game_confirmed(self):
        self.assertEqual(self.state["phase"],"PREP")
        self.assertEqual(self.state["selected_three"],
                         selection_fixture()["selected_three"])
        with self.assertRaises(ValueError):
            selection_test_prep_state(self.assets,{"status":"BLOCKED"})

    def test_prep_all_18_and_three_roles_under_budget(self):
        updated,report=prep_timing_test(
            state=self.state,assets=self.assets,access_token="dummy",
            model="mock",clock=self.clock,started_at=0,
            prep_runner=self.fake_prep(elapsed=27))
        self.assertTrue(report["pass"])
        self.assertEqual(report["lead_evaluated"],6)
        self.assertEqual(report["bench_evaluated"],12)
        self.assertEqual(report["win_conditions"],3)
        self.assertEqual(len(updated["prep"]["matchups_18"]),18)

    def test_partial_selection_runs_known_five_prep_without_18_pass(self):
        partial={
            **selection_fixture(),
            "status":"PARTIAL_ADVICE",
            "opponent_six":[OPP[0],OPP[1],"未判明相手3",*OPP[3:]],
        }
        state=selection_test_prep_state(self.assets,partial)
        self.assertEqual(state["visual_unknown_opponents"],["未判明相手3"])
        self.assertEqual(len(state["matrix_rows_six"]),5)
        self.assertEqual(state["phase"],"PREP")
        lead_code=("ACT=conditional|KO=unknown|DPS=move|PRI=unknown|S=unknown|"
                   "OUR=unknown|OPP=unknown|SAFE=unknown|PROC=unverified|"
                   "LOCK=none|MEGA=unknown")
        bench_code=("S=unknown|OUR=unknown|OPP=unknown|PRI=unknown|"
                    "SAFE=unknown|PROC=unverified")
        answer={
            "lead_six":[lead_code]*5,
            "bench_twelve":[bench_code]*10,
            "preserve":state["selected_three"][0],
            "stopper":state["selected_three"][1],
            "closer":state["selected_three"][2],
        }
        seen=[]
        def mock_response(_client,**kwargs):
            import json
            seen.append(json.loads(kwargs["content"][0]["text"]))
            return answer
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=mock_response):
            updated,report=prep_timing_test(
                state=state,assets=self.assets,access_token="dummy",
                model="mock",clock=self.clock,started_at=0,
                client_factory=lambda **_:object())
        self.assertEqual(report["status"],"PREP_TIMING_PARTIAL")
        self.assertFalse(report["pass"])
        self.assertTrue(report["partial_ready"])
        self.assertEqual((report["lead_evaluated"],report["bench_evaluated"],
                          report["win_conditions"]),(5,10,3))
        self.assertEqual(report["unknown_matchups"],3)
        self.assertEqual(updated["prep_status"],"PARTIAL_STRUCTURE_COMPLETE")
        self.assertEqual(len(updated["prep"]["matchups_known"]),15)
        self.assertEqual(len(updated["prep"]["unknown_pairs"]),3)
        self.assertNotIn("matchups_18",updated["prep"])
        self.assertEqual(len(seen),1)
        self.assertEqual(len(seen[0]["matrix_rows_six"]),5)
        self.assertEqual(seen[0]["unknown_opponent_slots"],["未判明相手3"])
        self.assertNotIn("未判明相手3",str(seen[0]["matrix_rows_six"]))

    def test_partial_wrong_reply_not_promoted_to_prep(self):
        partial={
            **selection_fixture(),"status":"PARTIAL_ADVICE",
            "opponent_six":[OPP[0],OPP[1],"未判明相手3",*OPP[3:]],
        }
        state=selection_test_prep_state(self.assets,partial)
        with patch("champions_assist.match_replay._stream_json",
                   return_value={"lead_six":[],"bench_twelve":[],
                                 "preserve":state["selected_three"][0],
                                 "stopper":state["selected_three"][1],
                                 "closer":state["selected_three"][2]}):
            updated,report=prep_timing_test(
                state=state,assets=self.assets,access_token="dummy",
                model="mock",clock=self.clock,started_at=0,
                client_factory=lambda **_:object())
        self.assertEqual(report["status"],"PREP_TIMING_FAIL")
        self.assertFalse(report["partial_ready"])
        self.assertIsNone(updated["prep"])

    def test_partial_two_unknown_slots_are_not_inferred(self):
        partial={
            **selection_fixture(),"status":"PARTIAL_ADVICE",
            "opponent_six":[OPP[0],"未判明相手2",OPP[2],
                            "未判明相手4",OPP[4],OPP[5]],
        }
        state=selection_test_prep_state(self.assets,partial)
        self.assertEqual(len(state["matrix_rows_six"]),4)
        self.assertEqual(state["visual_unknown_opponents"],
                         ["未判明相手2","未判明相手4"])

    def test_prep_over_40s_passes_inside_shared_87s_deadline(self):
        updated,report=prep_timing_test(
            state=self.state,assets=self.assets,access_token="dummy",
            model="mock",clock=self.clock,started_at=0,
            limit_seconds=72,prep_runner=self.fake_prep(elapsed=50))
        self.assertTrue(report["pass"])
        self.assertEqual((report["lead_evaluated"],report["bench_evaluated"],
                          report["win_conditions"]),(6,12,3))
        self.assertEqual(report["limit_ms"],72000)
        self.assertEqual(len(updated["prep"]["matchups_18"]),18)

    def test_late_full_prep_remains_structurally_valid_but_fails_timing(self):
        updated,report=prep_timing_test(
            state=self.state,assets=self.assets,access_token="dummy",
            model="mock",clock=self.clock,started_at=0,
            limit_seconds=72,prep_runner=self.fake_prep(elapsed=74))
        self.assertFalse(report["pass"])
        self.assertEqual(report["reason"],"DEADLINE_EXCEEDED")
        self.assertTrue(report["late_valid_result"])
        self.assertIsNotNone(updated["prep"])

    def test_prep_budget_shortens_after_slow_selection(self):
        updated,report=prep_timing_test(
            state=self.state,assets=self.assets,access_token="dummy",
            model="mock",clock=self.clock,started_at=0,limit_seconds=12,
            prep_runner=self.fake_prep(elapsed=13))
        self.assertFalse(report["pass"])
        self.assertEqual(report["limit_ms"],12000)
        self.assertEqual(report["reason"],"DEADLINE_EXCEEDED")
        self.assertIsNotNone(updated["prep"])

    def test_prep_rejects_missing_bench_or_lead_tags(self):
        for index in (6,0):
            def bad(state):
                state["prep"]["matchups_18"][index]["eval_code"]="PROC=unknown"
            self.clock.now=0.0
            updated,report=prep_timing_test(
                state=self.state,assets=self.assets,access_token="dummy",
                model="mock",clock=self.clock,started_at=0,
                prep_runner=self.fake_prep(elapsed=5,mutate=bad))
            self.assertFalse(report["pass"])
            self.assertIsNone(updated["prep"])

    def test_prep_rejects_unknown_processor_value(self):
        def unknown(state):
            code=state["prep"]["matchups_18"][6]["eval_code"]
            state["prep"]["matchups_18"][6]["eval_code"]=(
                code.replace("PROC=UNVERIFIED","PROC=unknown"))
        changed,result=prep_timing_test(
            state=self.state,assets=self.assets,access_token="dummy",
            model="mock",clock=self.clock,started_at=0,
            prep_runner=self.fake_prep(elapsed=5,mutate=unknown))
        self.assertFalse(result["pass"])
        self.assertIsNone(changed["prep"])

    def test_prep_rejects_unknown_roles_and_duplicate_pair(self):
        def bad_role(state):
            state["prep"]["stopper"]="NOT_SELECTED"
        def bad_pair(state):
            state["prep"]["matchups_18"][6]=dict(
                state["prep"]["matchups_18"][0])
        for damage in (bad_role,bad_pair):
            self.clock.now=0.0
            _,report=prep_timing_test(
                state=self.state,assets=self.assets,access_token="dummy",
                model="mock",clock=self.clock,started_at=0,
                prep_runner=self.fake_prep(elapsed=7,mutate=damage))
            self.assertFalse(report["pass"])

    def test_turn_requires_input_ready_action_before_30(self):
        self.clock.now=0
        def runner(**kwargs):
            self.assertLessEqual(kwargs["total_seconds"],30)
            self.clock.now=19.4
            return kwargs["state"],{
                "status":"PROVISIONAL_ADVICE","screen":"MOVE_MENU",
                "action":{"kind":"MOVE","move":"10まんボルト"},
                "recommendation":"10まんボルト。"}
        _,report=turn_timing_test(
            image=np.zeros((240,480,3),dtype=np.uint8),
            state=self.state,assets=self.assets,model="mock",access_token="x",
            clock=self.clock,started_at=0,turn_runner=runner)
        self.assertTrue(report["pass"])
        self.assertEqual(report["target_ms"],30000)
        self.assertEqual(report["input_margin_ms"],25600)
        self.assertEqual(report["recommendation"],"10まんボルト。")

    def test_turn_timeout_noaction_and_illegal_status_fail(self):
        variants=[
            (31.0,"PROVISIONAL_ADVICE","MOVE_MENU",{"kind":"MOVE"}, "10まんボルト。"),
            (3.0,"NO_ACTION","MOVE_MENU",{"kind":"NO_ACTION"}, ""),
            (3.0,"BLOCKED","MOVE_MENU",{"kind":"MOVE"}, ""),
            (3.0,"PROVISIONAL_ADVICE","COMMAND_MENU",{"kind":"MOVE"}, "10まんボルト。"),
        ]
        for sec,status,screen,action,advice in variants:
            self.clock.now=0
            def runner(**kwargs):
                self.clock.now=sec
                return kwargs["state"],{"status":status,"screen":screen,
                  "action":action,"recommendation":advice}
            _,report=turn_timing_test(
                image=None,state=self.state,assets=self.assets,access_token="x",
                model="mock",clock=self.clock,started_at=0,turn_runner=runner)
            self.assertFalse(report["pass"])
            self.assertEqual(report["recommendation"],"")

    def test_no_prep_after_invalid_selection(self):
        for invalid in ({"status":"ERROR"},{
            **selection_fixture(),"opponent_six":list(OPP[:5])}):
            with self.assertRaises(ValueError):
                selection_test_prep_state(self.assets,invalid)


class TimingQtFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=TemporaryDirectory()
        self.assets=fixture_assets()
        self.dialog=SelectionTestDialog(
            assets=self.assets,model="mock",reasoning_effort="auto",
            app_root=Path(self.temp.name))
        self.dialog.set_image(np.zeros((250,500,3),dtype=np.uint8))

    def tearDown(self):
        self.dialog.close()
        self.temp.cleanup()

    def test_selection_display_automatically_schedules_prep(self):
        import time
        self.dialog.selection_started_at=time.perf_counter()-4.0
        report=selection_fixture()
        report["test_total_elapsed_ms"]=3800
        with patch.object(self.dialog,"_start_timing_test") as start:
            self.dialog._complete(report)
            self.assertTrue(self.dialog.auto_prep_pending)
            self.assertFalse(self.dialog.prep_completed)
            self.assertEqual(self.dialog.details.text().count("初手:"),1)
            self.dialog._clear_worker()  # previously active thread has exited
            start.assert_called_once_with("PREP")
            self.assertFalse(self.dialog.auto_prep_pending)

    def test_partial_selection_schedules_prep_and_displays_conditional(self):
        import time
        self.dialog.selection_started_at=time.perf_counter()-4.0
        report={
            **selection_fixture(),"status":"PARTIAL_ADVICE",
            "opponent_six":[OPP[0],OPP[1],"未判明相手3",*OPP[3:]],
            "test_total_elapsed_ms":4000,
        }
        with patch.object(self.dialog,"_start_timing_test") as launch:
            self.dialog._complete(report)
            self.assertTrue(self.dialog.auto_prep_pending)
            self.assertIsNotNone(self.dialog.test_state)
            self.assertIn("暫定PREP",self.dialog.timing_status.text())
            self.dialog._clear_worker()
            launch.assert_called_once_with("PREP")
        self.dialog.timing_requested_at=time.perf_counter()-4.0
        self.dialog.prep_limit_seconds=83.0
        self.dialog._timing_complete({
            "mode":"PREP","state":self.dialog.test_state,
            "report":{
                "status":"PREP_TIMING_PARTIAL",
                "reason":"KNOWN_ONLY_UNCONFIRMED_OPPONENT",
                "pass":False,"partial_ready":True,
                "lead_evaluated":5,"bench_evaluated":10,
                "win_conditions":3,"known_opponents":5,"unknown_matchups":3,
            }})
        self.assertFalse(self.dialog.prep_completed)
        self.assertTrue(self.dialog.prep_attempted)
        self.assertIn("暫定PREP実行",self.dialog.timing_status.text())
        self.assertIn("未評価 3対面",self.dialog.timing_status.text())
        self.assertNotIn("合格",self.dialog.timing_status.text())

    def test_partial_ui_deadline_overrun_does_not_report_partial_success(self):
        import time
        partial={
            **selection_fixture(),"status":"PARTIAL_ADVICE",
            "opponent_six":[OPP[0],OPP[1],"未判明相手3",*OPP[3:]],
        }
        state=selection_test_prep_state(self.assets,partial)
        self.dialog.test_state=state
        self.dialog.selection_started_at=time.perf_counter()-89.0
        self.dialog.selection_display_elapsed_ms=42000
        self.dialog.timing_requested_at=time.perf_counter()-2.0
        self.dialog.prep_limit_seconds=43.0
        self.dialog._timing_complete({
            "mode":"PREP","state":state,
            "report":{"status":"PREP_TIMING_PARTIAL",
                      "partial_ready":True,"pass":False,
                      "lead_evaluated":5,"bench_evaluated":10,
                      "win_conditions":3,"known_opponents":5,
                      "unknown_matchups":3}})
        self.assertFalse(self.dialog.prep_completed)
        self.assertIn("未達",self.dialog.timing_status.text())
        self.assertNotIn("合格",self.dialog.timing_status.text())
        self.assertIn("COMBINED_90S_BUDGET_EXCEEDED",
                      self.dialog.timing_status.text())

    def test_prep_near_shared_90s_deadline_still_dispatches(self):
        import time
        self.dialog.selection_started_at=time.perf_counter()-89.0
        self.dialog.selection_display_elapsed_ms=89000
        self.dialog.test_state=selection_test_prep_state(self.assets,selection_fixture())
        with patch("champions_assist.selection_test_dialog.QThread") as thread,patch(
                "champions_assist.selection_test_dialog._TimingTestWorker") as worker:
            self.dialog._start_timing_test("PREP")
            self.assertFalse(self.dialog.prep_completed)
            self.assertEqual(round(self.dialog.prep_limit_seconds),1)
            thread.return_value.start.assert_called_once()
            worker.assert_called_once()
            self.dialog.thread=None
            self.dialog.worker=None

    def test_qt_accepts_complete_prep_over_40s_before_shared_deadline(self):
        import time
        self.dialog.selection_started_at=time.perf_counter()-60.0
        self.dialog.selection_display_elapsed_ms=14000
        self.dialog.prep_limit_seconds=73.0
        self.dialog.timing_requested_at=time.perf_counter()-46.0
        state=selection_test_prep_state(self.assets,selection_fixture())
        state["prep"]=prep_payload(state)
        self.dialog.test_state=state
        self.dialog._timing_complete({
            "mode":"PREP","state":state,
            "report":{"pass":True,"status":"PREP_TIMING_PASS","reason":"OK",
                      "lead_evaluated":6,"bench_evaluated":12,
                      "win_conditions":3}})
        self.assertTrue(self.dialog.prep_completed)
        self.assertIn("合格",self.dialog.timing_status.text())

    def test_combined_ui_deadline_cannot_pass_even_if_prep_worker_says_pass(self):
        import time
        self.dialog.selection_started_at=time.perf_counter()-89.0
        self.dialog.selection_display_elapsed_ms=20000
        self.dialog.test_state=selection_test_prep_state(self.assets,selection_fixture())
        self.dialog.timing_requested_at=time.perf_counter()-2.0
        self.dialog.prep_limit_seconds=67.0
        self.dialog._timing_complete({
            "mode":"PREP","state":self.dialog.test_state,
            "report":{"pass":True,"status":"PREP_TIMING_PASS","reason":"OK",
                      "lead_evaluated":6,"bench_evaluated":12,
                      "win_conditions":3}})
        self.assertIn("未達",self.dialog.timing_status.text())
        self.assertIn("COMBINED_90S_BUDGET_EXCEEDED",
                      self.dialog.timing_status.text())
        self.assertFalse(self.dialog.prep_completed)
        self.assertTrue(self.dialog.prep_attempted)
        self.dialog._clear_worker()
        # Turn screenshot benchmark remains usable as a separate test, while
        # incomplete PREP never becomes a verified tactical cache.
        self.assertTrue(self.dialog.turn_choose_button.isEnabled())


if __name__=="__main__":
    unittest.main()
