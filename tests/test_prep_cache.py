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
from champions_assist.match_replay import (new_replay, confirm_actual_selection,
                                    run_replay_prep, advance_observation)
from champions_assist.live_timing_test import prep_timing_test
from tests.test_match_replay import OWN, OPP, frame


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


def evaluated_prep(*, known=6):
    """Structurally assessed fixture, unlike AI_UNVERIFIED cache hints."""
    lead=("ACT=attack|KO=not_guaranteed|DPS=じしん|PRI=none|S=slower|"
          "OUR=じしん|OPP=coverage_risk|SAFE=unsafe|PROC=unverified|"
          "LOCK=none|MEGA=recheck")
    bench=("S=slower|OUR=じしん|OPP=coverage_risk|PRI=none|"
           "SAFE=unsafe|PROC=unverified")
    return {**roles(),"lead_six":[lead]*known,
            "bench_twelve":[bench]*(2*known)}


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
        # All 18 cells are merely provisional hints, not 18 evaluations.
        self.assertFalse(validate_cached_preparation(
            assets,OWN[:3],OPP,OWN[0],rows,OWN[:3])[0])
        # Neither a copy of the cache nor a partial first row can be promoted.
        copy=[dict(row) for row in rows]
        copy[0]["source"]="REVIEWED_CELL"
        self.assertFalse(validate_cached_preparation(
            assets,OWN[:3],OPP,OWN[0],copy,OWN[:3])[0])

    def test_provisional_hints_do_not_bypass_eighteen_field_evaluation(self):
        tmp,assets=loaded_assets()
        self.addCleanup(tmp.cleanup)
        state=chosen_state(assets)
        calls=[]
        def reply(_client, **kwargs):
            calls.append(kwargs)
            return evaluated_prep()
        with patch("champions_assist.match_replay._stream_json",side_effect=reply):
            updated,report=run_replay_prep(
                state=state,assets=assets,access_token="test",model="mock",
                client_factory=lambda **_:object(),reasoning_effort="auto")
        self.assertEqual(report["status"],"PREP_PROVISIONAL")
        self.assertEqual(report["matchups_evaluated"],18)
        self.assertEqual(updated["prep_status"],"STRUCTURE_COMPLETE")
        self.assertEqual(len(updated["prep"]["matchups_18"]),18)
        self.assertEqual(updated["prep"]["preserve"],OWN[0])
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]["name"],"champions_replay_prep")
        self.assertIn("lead_six",calls[0]["schema"]["properties"])
        prompt=json.loads(calls[0]["content"][0]["text"])
        self.assertIn("lead_tags",prompt["response_order"])
        self.assertEqual(calls[0]["reasoning_effort"],"low")

    def test_timing_accepts_exact_18_cached_rows_and_three_roles(self):
        tmp,assets=loaded_assets()
        self.addCleanup(tmp.cleanup)
        state=chosen_state(assets)
        with patch("champions_assist.match_replay._stream_json",
                   return_value=evaluated_prep()):
            updated,report=prep_timing_test(
                state=state,assets=assets,access_token="dummy",model="mock",
                client_factory=lambda **_:object())
        self.assertTrue(report["pass"])
        self.assertEqual(report["status"],"PREP_TIMING_PASS")
        self.assertEqual((report["lead_evaluated"],report["bench_evaluated"],
                          report["win_conditions"]),(6,12,3))
        self.assertEqual(report["source_status"],"PREP_PROVISIONAL")
        self.assertEqual(updated["prep_status"],"STRUCTURE_COMPLETE")

    def test_missing_cached_hint_still_requires_model_fields(self):
        tmp,assets=loaded_assets(missing=True)
        self.addCleanup(tmp.cleanup)
        state=chosen_state(assets)
        cached=cached_pairs(assets,OWN[:3],OPP,OWN[0])
        self.assertEqual(cached_evidence_count(cached),17)
        self.assertFalse(validate_cached_preparation(
            assets,OWN[:3],OPP,OWN[0],cached,OWN[:3])[0])
        with patch("champions_assist.match_replay._stream_json",
                   return_value=evaluated_prep()):
            updated,report=prep_timing_test(
                state=state,assets=assets,access_token="dummy",model="mock",
                client_factory=lambda **_:object())
        self.assertEqual(report["source_status"],"PREP_PROVISIONAL")
        self.assertEqual(updated["prep_status"],"STRUCTURE_COMPLETE")

    def test_known_five_is_conditional_not_full_pass(self):
        tmp,assets=loaded_assets()
        self.addCleanup(tmp.cleanup)
        state=chosen_state(assets,partial=True)
        with patch("champions_assist.match_replay._stream_json",
                   return_value=evaluated_prep(known=5)):
            updated,report=prep_timing_test(
                state=state,assets=assets,access_token="dummy",model="mock",
                client_factory=lambda **_:object())
        self.assertFalse(report["pass"])
        self.assertTrue(report["partial_ready"])
        self.assertEqual((report["lead_evaluated"],report["bench_evaluated"],
                          report["unknown_matchups"]),(5,10,3))
        self.assertEqual(updated["prep_status"],"PARTIAL_STRUCTURE_COMPLETE")
        self.assertNotIn("matchups_18",updated["prep"])

    def test_turn_uses_current_opponent_cache_not_all_eighteen(self):
        tmp,assets=loaded_assets()
        self.addCleanup(tmp.cleanup)
        state=chosen_state(assets)
        with patch("champions_assist.match_replay._stream_json",
                   return_value=evaluated_prep()):
            state,report=run_replay_prep(
                state=state,assets=assets,access_token="dummy",model="mock",
                client_factory=lambda **_:object())
        self.assertEqual(report["status"],"PREP_PROVISIONAL")
        state["phase"]="BATTLE"
        contexts=[]
        def tactical(ctx,limit,mode):
            contexts.append(ctx)
            return {"action_kind":"MOVE","move":"じしん",
                    "switch_target":"","warning":""}
        updated,result=advance_observation(
            state,assets,
            frame("MOVE_MENU",our_active=OWN[0],
                  opponent_active=OPP[0],visible_moves=["じしん"]),
            tactical,elapsed_seconds=lambda:1.0)
        self.assertEqual(result["status"],"PROVISIONAL_ADVICE")
        self.assertEqual(len(contexts),1)
        self.assertNotIn("cached_matchup_hypotheses",contexts[0])
        self.assertEqual(len(contexts[0]["prep_matchup_evaluations"]),3)
        self.assertTrue(all(r["opponent"]==OPP[0] for r in
                            contexts[0]["prep_matchup_evaluations"]))
        self.assertTrue(all("eval_code" in r for r in
                            contexts[0]["prep_matchup_evaluations"]))
        self.assertEqual(contexts[0]["cached_win_roles"]["preserve"],OWN[0])
        self.assertEqual(len(updated["prep"]["matchups_18"]),18)


    def test_all_unassessed_required_tags_cannot_be_declared_complete(self):
        from champions_assist.prep import (
            build_prep_input, PrepMatchup, PrepResult, validate_prep_result,
            validate_partial_prep_result, mocked_complete_prep,
        )
        from itertools import product
        own=tuple(OWN[:3])
        lead=own[0]
        inp=build_prep_input(own,lead,tuple(OPP),
                             tuple("matrix" for _ in OPP))
        lead_code=("ACT=attack|KO=?|DPS=registered_move|PRI=?|S=?|OUR=?|"
                   "OPP=?|SAFE=?|PROC=unverified|LOCK=?|MEGA=?")
        bench_code="S=?|OUR=?|OPP=?|PRI=?|SAFE=?|PROC=unverified"
        rows=tuple(PrepMatchup(mon,opp,lead_code if mon==lead else bench_code)
                   for mon,opp in product(own,OPP))
        failed=PrepResult("COMPLETE",rows,*own)
        self.assertEqual(validate_prep_result(inp,failed).reason,
                         "REQUIRED_FIELD_NOT_ASSESSED")
        self.assertFalse(validate_prep_result(inp,failed).complete)
        partial=PrepResult("PARTIAL",tuple(
            r for r in rows if r.opponent_pokemon!=OPP[-1]),*own)
        v=validate_partial_prep_result(own,lead,tuple(OPP[:5]),
                                       ("未判明相手6",),partial)
        self.assertFalse(v.complete)
        self.assertEqual(v.reason,"REQUIRED_FIELD_NOT_ASSESSED")
        # The labelled offline mock must still explicitly assess fields.
        self.assertTrue(validate_prep_result(inp,mocked_complete_prep(inp)).complete)

    def test_evidence_unknown_is_not_the_same_as_missing_evaluation(self):
        from champions_assist.prep import (
            build_prep_input, PrepMatchup, PrepResult, validate_prep_result,
            prep_evidence_status,
        )
        from itertools import product
        own=tuple(OWN[:3])
        inp=build_prep_input(own,own[0],tuple(OPP),
                             tuple("matrix" for _ in OPP))
        lead=("ACT=attack|KO=unknown|DPS=registered_move|PRI=unknown|"
              "S=unknown|OUR=unknown|OPP=unknown|SAFE=unknown|"
              "PROC=unverified|LOCK=unknown|MEGA=unknown")
        bench=("S=unknown|OUR=unknown|OPP=unknown|PRI=unknown|"
               "SAFE=unknown|PROC=unverified")
        rows=tuple(PrepMatchup(mon,opp,lead if mon==own[0] else bench)
                   for mon,opp in product(own,OPP))
        result=PrepResult("COMPLETE",rows,*own)
        validation=validate_prep_result(inp,result)
        self.assertFalse(validation.complete)
        self.assertEqual(validation.reason,"MATCHUP_NOT_ASSESSED")
        evaluated_rows=tuple(PrepMatchup(
            r.our_pokemon,r.opponent_pokemon,
            r.eval_code.replace("OUR=unknown","OUR=registered_move"))
            for r in rows)
        evaluated=PrepResult("COMPLETE",evaluated_rows,*own)
        self.assertTrue(validate_prep_result(inp,evaluated).complete)
        self.assertEqual(prep_evidence_status(evaluated_rows),
                         "ASSESSED_WITH_UNCERTAINTY")

    def test_invalid_roles_never_promoted(self):
        tmp,assets=loaded_assets()
        self.addCleanup(tmp.cleanup)
        state=chosen_state(assets)
        with patch("champions_assist.match_replay._stream_json",
                   return_value={**evaluated_prep(),"closer":"未登録"}):
            updated,report=run_replay_prep(
                state=state,assets=assets,access_token="dummy",model="mock",
                client_factory=lambda **_:object())
        self.assertEqual(report["status"],"PREP_INCOMPLETE")
        self.assertEqual(report["reason"],"WIN_CONDITIONS_INVALID")
        self.assertIsNone(updated["prep"])


if __name__=="__main__":
    unittest.main()
