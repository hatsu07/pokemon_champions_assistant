"""Offline tests for off-match cached Champions evaluation reuse.

No model network, hidden game data or SV mechanics are used.
"""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from champions_assist.preload import MasterPreloader
from champions_assist.prep_cache import (
    cached_pairs, compile_matchup_cache, cached_evidence_count,
    validate_cached_preparation,
)
from champions_assist.match_replay import new_replay, confirm_actual_selection, run_replay_prep
from champions_assist.live_timing_test import prep_timing_test
from tests.test_match_replay import OWN, OPP


def matrix_file(*, missing: bool=False) -> str:
    names=",".join(f"{chr(65+i)}:{name}" for i,name in enumerate(OWN))
    head=[
        "# TEAM_SCOPED_MATRIX v1; source: test-frozen-Champions",
        "# OWN={"+names+"}",
        "# DERIVED_STATUS=PROVISIONAL_UNVERIFIED; TEAM_SCOPED",
        "# PROVENANCE=EXACT_REGISTERED_SET_ONLY; NO_CROSS_GAME_INFERENCE",
    ]
    rows=[]
    for i,opp in enumerate(OPP):
        hints=[]
        for j in range(6):
            if missing and i==0 and j==0:
                continue
            hints.append(chr(65+j)+":技=登録済技,S=?,危=積,処=仮,受=?")
        parts=[
            opp, "みず", "90",
            *(["?,?,?,?,U,0"]*6),
            "-", "積み警戒",
            "AI_UNVERIFIED: "+" ".join(hints),
            "/".join(["A?"]*6),"S",
        ]
        rows.append("|".join(parts))
    return "\n".join(head+rows)+"\n"


def loaded_assets(*, missing: bool=False):
    temp=TemporaryDirectory()
    root=Path(temp.name)
    (root/"Pokemon_Champions_MC_live_selection_matrix.txt").write_text(
        matrix_file(missing=missing),encoding="utf8")
    (root/"Pokemon_Champions_MC_60sec_lookup_master.txt").write_text(
        "\n".join(opp+"|"+"|".join(["-"]*11) for opp in OPP),encoding="utf8")
    (root/"Pokemon_Champions_MC_live_decision_gate.txt").write_text(
        "[CORE]\nPREP SAFETY",encoding="utf8")
    team_fields=["record","name","move1","move2","move3","move4",
                 "item","ability","nature","ap_h_a_b_c_d_s","role"]
    content=[",".join(team_fields)]
    for own in OWN:
        content.append(",".join(["MON",own,"じしん","まもる","かみくだく",
                                 "でんこうせっか","持ち物","特性","ようき","0-0-0-0-0-0",""]))
    (root/"Pokemon_Champions_MC_team_2026-10-03.csv").write_text(
        "\n".join(content)+"\n",encoding="utf8")
    assets=MasterPreloader(root).load_for_new_match()
    return temp,assets


def chosen_state(assets, *, partial: bool=False):
    state=new_replay(assets)
    assert state["matrix_provisional"]
    state["phase"]="SELECTION"
    state["opponent_six"] = list(OPP)
    state["matrix_rows_six"] = list(assets.matrix_rows_for(tuple(OPP)))
    if partial:
        state["opponent_six"][2]="未判明相手3"
        state["visual_unknown_opponents"]=["未判明相手3"]
        state["matrix_rows_six"] = [
            assets.matrix_index[n] for n in state["opponent_six"]
            if n in assets.matrix_index]
    return confirm_actual_selection(state,assets,list(OWN[:3]),OWN[0])


def roles():
    return {"preserve":OWN[0],"stopper":OWN[1],
            "closer":OWN[2],"warning":"積み対策を優先"}


class CachedPrepTests(unittest.TestCase):
    def test_preload_compiles_all_six_times_six_no_network(self):
        tmp,assets=loaded_assets()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(len(assets.matchup_cache_index),6)
        self.assertEqual(len(assets.matchup_cache_index[OWN[0]]),6)
        rows=cached_pairs(assets,OWN[:3],OPP,OWN[0])
        self.assertEqual(len(rows),18)
        self.assertEqual(cached_evidence_count(rows),18)
        self.assertTrue(all(row["source"]=="AI_HINT" for row in rows))
        self.assertTrue(validate_cached_preparation(
            assets,OWN[:3],OPP,OWN[0],rows,OWN[:3])[0])
        # Neither a copy of the cache nor a partial first row can be promoted.
        copy=[dict(row) for row in rows]
        copy[0]["source"]="REVIEWED_CELL"
        self.assertFalse(validate_cached_preparation(
            assets,OWN[:3],OPP,OWN[0],copy,OWN[:3])[0])

    def test_preloaded_cache_skips_regenerating_138_fields(self):
        tmp,assets=loaded_assets()
        self.addCleanup(tmp.cleanup)
        state=chosen_state(assets)
        calls=[]
        def reply(_client, **kwargs):
            calls.append(kwargs)
            return roles()
        with patch("champions_assist.match_replay._stream_json",side_effect=reply):
            updated,report=run_replay_prep(
                state=state,assets=assets,access_token="test",model="mock",
                client_factory=lambda **_:object(),reasoning_effort="auto")
        self.assertEqual(report["status"],"PREP_CACHE_REUSED")
        self.assertEqual(report["matchups_evaluated"],18)
        self.assertEqual(report["missing_matchups"],0)
        self.assertEqual(updated["prep_status"],"CACHE_STRUCTURE_COMPLETE")
        self.assertEqual(len(updated["prep"]["matchups_18"]),18)
        self.assertEqual(updated["prep"]["preserve"],OWN[0])
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]["name"],"champions_cached_prep_roles")
        self.assertNotIn("lead_six",calls[0]["schema"]["properties"])
        prompt=json.loads(calls[0]["content"][0]["text"])
        self.assertEqual(len(prompt["cached_matchups"]),18)
        self.assertNotIn("lead_tags",prompt)
        self.assertEqual(calls[0]["reasoning_effort"],"low")

    def test_timing_accepts_exact_18_cached_rows_and_three_roles(self):
        tmp,assets=loaded_assets()
        self.addCleanup(tmp.cleanup)
        state=chosen_state(assets)
        with patch("champions_assist.match_replay._stream_json",
                   return_value=roles()):
            updated,report=prep_timing_test(
                state=state,assets=assets,access_token="dummy",model="mock",
                client_factory=lambda **_:object())
        self.assertTrue(report["pass"])
        self.assertEqual(report["status"],"PREP_TIMING_PASS")
        self.assertEqual((report["lead_evaluated"],report["bench_evaluated"],
                          report["win_conditions"]),(6,12,3))
        self.assertEqual(report["cached_matchups"],18)
        self.assertEqual(updated["prep"]["source"],"FROZEN_CACHE")

    def test_missing_one_cached_cell_is_not_certified(self):
        tmp,assets=loaded_assets(missing=True)
        self.addCleanup(tmp.cleanup)
        state=chosen_state(assets)
        with patch("champions_assist.match_replay._stream_json",
                   return_value=roles()):
            updated,report=prep_timing_test(
                state=state,assets=assets,access_token="dummy",model="mock",
                client_factory=lambda **_:object())
        self.assertFalse(report["pass"])
        self.assertTrue(report["cache_ready"])
        self.assertEqual(report["status"],"PREP_TIMING_CACHE_INCOMPLETE")
        self.assertEqual((report["lead_evaluated"],report["bench_evaluated"]),
                         (5,12))
        self.assertEqual(report["cached_matchups"],17)
        self.assertEqual(report["missing_matchups"],1)
        self.assertEqual(updated["prep_status"],"CACHE_INCOMPLETE")
        self.assertEqual(updated["prep"]["matchups_18"][0]["source"],"MISSING")

    def test_known_five_cache_is_conditional_not_full_pass(self):
        tmp,assets=loaded_assets()
        self.addCleanup(tmp.cleanup)
        state=chosen_state(assets,partial=True)
        with patch("champions_assist.match_replay._stream_json",
                   return_value=roles()):
            updated,report=prep_timing_test(
                state=state,assets=assets,access_token="dummy",model="mock",
                client_factory=lambda **_:object())
        self.assertFalse(report["pass"])
        self.assertTrue(report["partial_ready"])
        self.assertEqual((report["lead_evaluated"],report["bench_evaluated"],
                          report["unknown_matchups"]),(5,10,3))
        self.assertEqual(report["cached_matchups"],15)
        self.assertEqual(updated["prep"]["source"],"FROZEN_CACHE")
        self.assertNotIn("matchups_18",updated["prep"])

    def test_invalid_roles_never_promoted(self):
        tmp,assets=loaded_assets()
        self.addCleanup(tmp.cleanup)
        state=chosen_state(assets)
        with patch("champions_assist.match_replay._stream_json",
                   return_value={**roles(),"closer":"未登録"}):
            updated,report=run_replay_prep(
                state=state,assets=assets,access_token="dummy",model="mock",
                client_factory=lambda **_:object())
        self.assertEqual(report["status"],"PREP_CACHE_INCOMPLETE")
        self.assertEqual(report["reason"],"CACHE_ROLES_INVALID")
        self.assertIsNone(updated["prep"])


if __name__=="__main__":
    unittest.main()
