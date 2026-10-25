"""Provisional exact-team reasoning is separate from verified LIVE grades.

No game-device accuracy claims: all tactical responses here are fake and
verified only for structural correctness, timing and legal action filtering.
"""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from champions_assist.match_replay import (
    new_replay, advance_observation, confirm_actual_selection, run_replay_prep)
from champions_assist.matrix_compatibility import (
    matrix_matches_registered_team, provisional_matrix_matches_registered_team)
from champions_assist.preload import MasterPreloader
from champions_assist.team_live_assets import prepare_team_assets, UNKNOWN_CELL
from champions_assist.team_management import TeamDocument
from tests.test_match_replay import frame

ROOT=Path(__file__).resolve().parents[1]
MASTER=ROOT/"masters"


class ProvisionalTeamReasoningTests(unittest.TestCase):
    def setUp(self):
        root=TemporaryDirectory()
        self.addCleanup(root.cleanup)
        tmp=Path(root.name)
        source=MASTER/"Pokemon_Champions_MC_team_2026-10-03.csv"
        self.team=tmp/"test.csv"
        self.team.write_bytes(source.read_bytes())
        document=TeamDocument.load(self.team)
        rows=[dict(row) for row in document.rows]
        idx=next(i for i,row in enumerate(rows) if row.get("record")=="MON")
        rows[idx]["item"]="いのちのたま"
        self.team.write_text(TeamDocument(document.fieldnames,tuple(rows)).to_text(),
                             encoding="utf-8")
        prepared=prepare_team_assets(master_dir=MASTER,data_root=tmp,
                                     team_id="a"*32,team_path=self.team)
        self.assets=MasterPreloader(
            MASTER,team_source=self.team,matrix_source=prepared.matrix_path,
            lookup_source=prepared.lookup_path).load_for_new_match()
        self.names=tuple(self.assets.own_team_names)
        self.opponents=tuple(self.assets.matrix_index.keys())[:6]
        self.selections=[]

    def select(self,ctx,_remaining,kind):
        if kind == "SELECTION":
            self.selections.append(ctx)
            return {"selected_three":list(self.names[:3]),"lead":self.names[0]}
        if kind=="ACTION":
            return {"action_kind":"MOVE","move":self.assets.own_team[0]["move1"],
                    "switch_target":"","warning":"相手技や確定KOは未知"}
        raise AssertionError(kind)

    def make_selection(self):
        state=new_replay(self.assets)
        state,out=advance_observation(
            state,self.assets,
            frame("TEAM_SELECTION",our_six=list(self.names),
                  opponent_six=list(self.opponents)),
            self.select,elapsed_seconds=lambda:0.5)
        self.assertEqual(out["status"],"PROVISIONAL_ADVICE")
        self.assertIn("暫定",out["recommendation"])
        return state,out

    def test_only_exact_generated_provisional_team_can_reason(self):
        self.assertFalse(matrix_matches_registered_team(self.assets))
        self.assertTrue(provisional_matrix_matches_registered_team(self.assets))
        state,out=self.make_selection()
        self.assertEqual(len(state["matrix_rows_six"]),6)
        ctx=self.selections[-1]
        self.assertEqual(len(ctx["matrix_rows_six"]),6)
        self.assertEqual(ctx["frozen_gate"],"")
        self.assertEqual(ctx["evidence_scope"],"CHAMPIONS_TEAM_SPECIFIC_HYPOTHESIS_ONLY")
        self.assertEqual(ctx["own_team"][0]["item"],"いのちのたま")
        self.assertEqual(len(out["selected_three"]),3)
        self.assertTrue(any(UNKNOWN_CELL in row for row in state["matrix_rows_six"]))

    def test_prep18_and_action_are_provisional_not_certified(self):
        state,out=self.make_selection()
        state=confirm_actual_selection(state,self.assets,out["selected_three"],out["lead"])
        lead_code=("ACT=conditional_attack|KO=unknown|DPS=known_move|PRI=unknown|"
                   "S=unknown|OUR=registered|OPP=unknown|SAFE=unknown|PROC=unknown|"
                   "LOCK=unknown|MEGA=unknown")
        bench_code=("S=unknown|OUR=registered|OPP=unknown|PRI=unknown|"
                    "SAFE=unknown|PROC=unknown")
        response={
            "matchups_18":[
                {"our_pokemon":member,"opponent_pokemon":opponent,
                 "eval_code":lead_code if member==self.names[0] else bench_code}
                for member in self.names[:3] for opponent in self.opponents
            ],
            "preserve":self.names[0],"stopper":self.names[1],"closer":self.names[2],
        }
        class Stream:
            def __enter__(self):return self
            def __exit__(self,*_):return False
            def __iter__(self):
                yield SimpleNamespace(type="response.output_text.delta",
                                      delta=json.dumps(response,ensure_ascii=False))
                yield SimpleNamespace(type="response.completed")
        class Client:
            def __init__(self):self.calls=[]
            @property
            def responses(self):return self
            def create(self,**kwargs):
                self.calls.append(kwargs)
                return Stream()
        client=Client()
        state,prep=run_replay_prep(
            state=state,assets=self.assets,model="mock",access_token="dummy",
            client_factory=lambda **_:client)
        self.assertEqual(prep["status"],"PREP_PROVISIONAL")
        self.assertEqual(prep["matchups_evaluated"],18)
        self.assertEqual(len(state["prep"]["matchups_18"]),18)
        self.assertEqual(client.calls[0]["stream"],True)
        self.assertNotIn("OLD TEAM",client.calls[0]["input"][0]["content"][0]["text"])
        self.assertIn("CHAMPIONS_TEAM_SPECIFIC_HYPOTHESIS_ONLY",
                      client.calls[0]["input"][0]["content"][0]["text"])
        state,_=advance_observation(
            state,self.assets,
            frame("COMMAND_MENU",our_active=self.names[0],
                  opponent_active=self.opponents[0]),
            self.select,elapsed_seconds=lambda:0.4)
        state,result=advance_observation(
            state,self.assets,
            frame("MOVE_MENU",our_active=self.names[0],
                  opponent_active=self.opponents[0],
                  visible_moves=[self.assets.own_team[0]["move1"]]),
            self.select,elapsed_seconds=lambda:0.4)
        self.assertEqual(result["status"],"PROVISIONAL_ADVICE")
        self.assertEqual(result["action"]["kind"],"MOVE")

    def test_unrelated_source_matrix_cannot_claim_provisional(self):
        from dataclasses import replace
        standard=MasterPreloader(MASTER,team_source=self.team).load_for_new_match()
        self.assertFalse(provisional_matrix_matches_registered_team(standard))
        # The original legacy matrix is only name-tagged, so do not pretend
        # it can detect a changed held item without generator provenance.
        # A genuinely different species list must be refused by both checks.
        changed=[dict(x) for x in standard.own_team]
        changed[0]["name"]="ピカチュウ"
        unrelated=replace(standard,own_team=tuple(changed))
        self.assertFalse(provisional_matrix_matches_registered_team(unrelated))
        self.assertFalse(matrix_matches_registered_team(unrelated))


if __name__=="__main__":
    unittest.main()
