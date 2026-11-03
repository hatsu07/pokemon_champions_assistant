"""Executable conditional PREP draft tests. No real-game win/safety claims."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from champions_assist.local_strategy.conditional_prep_builder import (
    build_conditional_prep_draft,
)
from champions_assist.prep import validate_prep_result, build_prep_input
from champions_assist.preload import MasterPreloader
from tests.test_issue_141_selection import make_assets, OWN, OPP


class ConditionalPrepBuilderTests(unittest.TestCase):
    def test_synthetic_all_18_registered_moves_but_not_accepted_prep(self):
        assets = make_assets()
        draft = build_conditional_prep_draft(assets, OWN[:3], OWN[0], OPP)
        self.assertEqual(draft.status, "CONDITIONAL_18_DRAFT", draft.reason)
        self.assertEqual(draft.proposed_pairs, 18)
        self.assertEqual(draft.registered_move_checks, 18)
        self.assertFalse(draft.tactical_accuracy_verified)
        self.assertEqual(draft.ranking_status, "NO_RANKING")
        self.assertEqual(draft.result.prep_status, "DRAFT")
        self.assertEqual(len(draft.result.matchups_18), 18)
        for row in draft.result.matchups_18:
            self.assertIn("OUR=mv", row.eval_code)
            self.assertIn("SAFE=unsafe_matrix", row.eval_code)
            self.assertNotIn("confirmed", row.eval_code.lower())
        frozen_rows = assets.matrix_rows_for(OPP)
        raw_input = build_prep_input(
            OWN[:3], OWN[0], OPP, frozen_rows,
            registered_moves={name: ("mv",) for name in OWN[:3]})
        self.assertFalse(validate_prep_result(raw_input, draft.result).complete)

    def test_only_registered_moves_not_guessed_from_fallback(self):
        assets = make_assets()
        modified = tuple(
            {**m, "move1": "different"} if m["name"] == "A" else m
            for m in assets.own_team)
        bad = replace(assets, own_team=modified)
        draft = build_conditional_prep_draft(bad, OWN[:3], OWN[0], OPP)
        self.assertEqual(draft.status, "BLOCKED")
        self.assertTrue(draft.reason.startswith("MATRIX_MOVE_NOT_REGISTERED"))
        self.assertIsNone(draft.result)
        self.assertEqual(draft.proposed_pairs, 0)

    def test_real_fixed_masters_no_cross_game_guesses(self):
        master = Path(__file__).resolve().parents[1] / "masters"
        assets = MasterPreloader(master).load_for_new_match()
        selected = ("マスカーニャ", "アシレーヌ", "ルカリオ → メガルカリオZ")
        opponents = ("ガブリアス", "アシレーヌ", "ミミッキュ",
                     "カバルドン", "グソクムシャ", "リザードン")
        out = build_conditional_prep_draft(
            assets, selected, selected[0], opponents)
        self.assertEqual(out.status, "CONDITIONAL_18_DRAFT", out.reason)
        self.assertEqual(out.registered_move_checks, 18)
        self.assertFalse(out.tactical_accuracy_verified)
        self.assertEqual(out.ranking_status, "NO_RANKING")
        self.assertIn("ルカリオ → メガルカリオZ",
                      (out.result.preserve, out.result.stopper, out.result.closer))
        self.assertTrue(all(
            "SAFE=safe" not in row.eval_code and "KO=confirmed" not in row.eval_code
            for row in out.result.matchups_18
        ))

    def test_matrix_mismatch_never_drafts_18(self):
        out = build_conditional_prep_draft(
            make_assets(provisional=True), OWN[:3], OWN[0], OPP)
        self.assertEqual(out.status, "BLOCKED")
        self.assertIsNone(out.result)


if __name__ == "__main__":
    unittest.main()
