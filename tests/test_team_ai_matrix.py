"""Offline integration of ChatGPT-proposed team matrix, selection and PREP.

These tests use fake AI output and fixed Champions masters only: no network,
no raw screenshots and no certification of unverified battle outcomes.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from itertools import product
from unittest.mock import patch
import unittest

from champions_assist.app_config import ConfigurationError
from champions_assist.live_context import critical_lookup_rows, SELECTION_CHECKLIST
from champions_assist.matrix_compatibility import (
    matrix_matches_registered_team, provisional_matrix_matches_registered_team)
from champions_assist.preload import MasterPreloader
from champions_assist.selection_vision import decision_context
from champions_assist.team_ai_matrix import publish_ai_matrix
from champions_assist.team_ai_analysis import build_request, save_proposals, validate_reply, SCHEMA
from champions_assist.team_live_assets import prepare_team_assets, UNKNOWN_CELL
from champions_assist.team_management import TeamDocument
from champions_assist.match_replay import new_replay, run_replay_prep

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "masters"
BASE = MASTER / "Pokemon_Champions_MC_team_2026-10-03.csv"


class TeamProvisionalMatrixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.team = self.root / "edited.csv"
        self.team.write_bytes(BASE.read_bytes())
        doc = TeamDocument.load(self.team)
        rows = [dict(row) for row in doc.rows]
        idx = next(i for i,row in enumerate(rows) if row.get("record")=="MON")
        rows[idx]["item"] = "いのちのたま"
        self.team.write_text(
            TeamDocument(doc.fieldnames, tuple(rows)).to_text(), encoding="utf-8")
        self.ready = prepare_team_assets(
            master_dir=MASTER, data_root=self.root, team_id="c"*32,
            team_path=self.team)
        self.catalog = self.ready.manifest_path.with_name("matchup-evidence.json")
        self.assertEqual(self.ready.status, "PROVISIONAL_UNVERIFIED")

    def propose(self):
        request = build_request(self.catalog, max_cells=12)
        self.assertTrue(request["cells"])
        reply = {
            "schema": SCHEMA, "catalog_sha256":request["catalog_sha256"],
            "proposals":[{
                "opponent":cell["opponent"],"slot":cell["slot"],
                "member_signature":cell["member_signature"],
                "assessment":"未検証: 技候補と相手の行動保証を比較",
                "risks":"先制技と交代時の最大打点に注意",
                "unknowns":"実測ダメージと相手の型"
            } for cell in request["cells"]],
        }
        validated = validate_reply(request, reply)
        save_proposals(self.catalog.parent/"ai-proposal-test.json",
                       self.catalog, validated)
        return request

    def publish(self):
        return publish_ai_matrix(
            matrix_path=self.ready.matrix_path,
            manifest_path=self.ready.manifest_path,
            catalog_path=self.catalog)

    def test_ai_proposals_become_visible_in_frozen_six_rows_not_certified(self):
        original = self.ready.matrix_path.read_text(encoding="utf-8")
        request = self.propose()
        status = self.publish()
        self.assertEqual(status["ai_proposal_cells"],len(request["cells"]))
        self.assertFalse(status["ai_proposal_live_certified"])
        manifest = json.loads(self.ready.manifest_path.read_text(encoding="utf-8"))
        self.assertFalse(manifest["quality_gate"]["live_ready"])
        self.assertEqual(manifest["live_grade_certified_from_reviews"],0)
        after = self.ready.matrix_path.read_text(encoding="utf-8")
        self.assertNotEqual(original,after)
        original_map = {line.split("|",1)[0]:line.split("|")
                        for line in original.splitlines()
                        if line and not line.startswith("#")}
        changed_map = {line.split("|",1)[0]:line.split("|")
                       for line in after.splitlines()
                       if line and not line.startswith("#")}
        self.assertEqual(len(original_map),len(changed_map))
        for key, old in original_map.items():
            new = changed_map[key]
            self.assertEqual(len(new),14)
            for pos in list(range(0,11))+[12,13]:
                self.assertEqual(new[pos],old[pos])
            self.assertNotIn("AI_UNVERIFIED:",new[3])
        first = request["cells"][0]["opponent"]
        self.assertIn("AI_UNVERIFIED:",changed_map[first][11])
        self.assertEqual(changed_map[first][3],UNKNOWN_CELL)
        assets = MasterPreloader(
            MASTER,team_source=self.team,
            matrix_source=self.ready.matrix_path,
            lookup_source=self.ready.lookup_path).load_for_new_match()
        self.assertFalse(matrix_matches_registered_team(assets))
        self.assertTrue(provisional_matrix_matches_registered_team(assets))
        opponents = tuple(list(changed_map)[:6])
        selection = decision_context(assets, opponents)
        self.assertEqual(len(selection["matrix_rows_six"]),6)
        self.assertIn(first, changed_map)
        self.assertIn("AI_UNVERIFIED:",selection["matrix_rows_six"][0])
        self.assertIn("not general usage",selection["selection_checklist"])
        self.assertLessEqual(len(selection["lookup_rows_critical"]),2)
        self.assertEqual(self.publish(),status)
        self.assertTrue(prepare_team_assets(
            master_dir=MASTER,data_root=self.root,team_id="c"*32,
            team_path=self.team).cache_hit)

    def test_prep_uses_18_pairs_same_frozen_matrix_and_two_critical_lookups(self):
        self.propose()
        self.publish()
        assets = MasterPreloader(
            MASTER,team_source=self.team,
            matrix_source=self.ready.matrix_path,
            lookup_source=self.ready.lookup_path).load_for_new_match()
        opponents = tuple(assets.matrix_index)[:6]
        chosen = tuple(assets.own_team_names)[:3]
        state = new_replay(assets)
        state["phase"] = "PREP"
        state["selected_three"] = list(chosen)
        state["lead"] = chosen[0]
        state["opponent_six"] = list(opponents)
        state["matrix_rows_six"] = list(assets.matrix_rows_for(opponents))
        captured=[]
        def fake_stream(_client,**kwargs):
            prompt=json.loads(kwargs["content"][0]["text"])
            captured.append(prompt)
            return {
                "matchups_18":[{
                    "our_pokemon":our,"opponent_pokemon":opp,
                    "eval_code": (
                        "ACT=attack|KO=unknown|DPS=review|PRI=unknown|S=unknown|"
                        "OUR=unknown|OPP=unknown|SAFE=unknown|PROC=check|"
                        "LOCK=none|MEGA=unknown"
                        if our==chosen[0] else
                        "S=unknown|OUR=unknown|OPP=unknown|PRI=unknown|"
                        "SAFE=unknown|PROC=check")
                } for our,opp in product(chosen,opponents)],
                "preserve":chosen[0],"stopper":chosen[1],"closer":chosen[2],
            }
        with patch("champions_assist.match_replay._stream_json",
                   side_effect=fake_stream):
            updated, result = run_replay_prep(
                state=state,assets=assets,access_token="fake",model="fake",
                client_factory=lambda **kwargs:object())
        self.assertEqual(result["status"],"PREP_PROVISIONAL")
        self.assertEqual(result["matchups_evaluated"],18)
        self.assertLessEqual(len(captured[0]["lookup_rows_critical"]),2)
        self.assertEqual(captured[0]["matrix_rows_six"],
                         list(assets.matrix_rows_for(opponents)))
        self.assertEqual(len(updated["prep"]["matchups_18"]),18)
        self.assertIn("未検証",updated["prep"]["note"])

    def test_stale_or_fake_approved_proposals_cannot_contaminate_matrix(self):
        self.propose()
        path = self.catalog.parent/"ai-proposal-test.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["live_approved"] = True
        path.write_text(json.dumps(saved,ensure_ascii=False),encoding="utf-8")
        # SQLite-primary already holds the legitimate unverified proposal.
        # An edited compatibility JSON cannot promote that record to approved.
        self.assertEqual(self.publish()["ai_proposal_cells"],12)
        self.assertIn("AI_UNVERIFIED:",
                      self.ready.matrix_path.read_text(encoding="utf-8"))
        saved["live_approved"] = False
        saved["catalog_sha256"] = "f"*64
        path.write_text(json.dumps(saved,ensure_ascii=False),encoding="utf-8")
        self.assertEqual(self.publish()["ai_proposal_cells"],12)
        # Wrong current matrix hash rejects publication, preventing silent
        # overwrites of user edited or truncated LIVE data.
        self.ready.matrix_path.write_text("tampered",encoding="utf-8")
        with self.assertRaises(ConfigurationError):
            self.publish()

    def test_exact_reviewed_baseline_stays_untouched(self):
        ready = prepare_team_assets(
            master_dir=MASTER,data_root=self.root,team_id="d"*32,
            team_path=BASE)
        prior = ready.matrix_path.read_bytes()
        status = publish_ai_matrix(
            matrix_path=ready.matrix_path,manifest_path=ready.manifest_path,
            catalog_path=ready.manifest_path.with_name("matchup-evidence.json"))
        self.assertEqual(status["ai_proposal_status"],"EXACT_BASELINE_REUSED")
        self.assertEqual(prior,ready.matrix_path.read_bytes())


class CriticalLookupTests(unittest.TestCase):
    def test_matrix_first_two_critical_lookups_only(self):
        keys = tuple("ABCDEF")
        warnings = ["先制候補", "", "積み候補", "", "メガ警戒", ""]
        rows = []
        for key,warn in zip(keys,warnings):
            columns=[key,"type","90"]+["?,?,?,?,U,0"]*6+["-",warn,"-","??","-"]
            self.assertEqual(len(columns),14)
            rows.append("|".join(columns))
        class Assets:
            lookup_index={key:key+"|secondary" for key in keys}
        selection = critical_lookup_rows(Assets(),keys,rows)
        self.assertEqual(selection,["A|secondary","C|secondary"])
        self.assertEqual(critical_lookup_rows(Assets(),keys,rows,limit=0),[])
        with self.assertRaises(ValueError):
            critical_lookup_rows(Assets(),keys,rows,limit=6)


if __name__ == "__main__":
    unittest.main()
