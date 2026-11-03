"""#141 stage 1 conditional selection/PREP cache reuse; never game-certified."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from champions_assist.preload import FrozenMatchAssets, MasterPreloader
from champions_assist.local_strategy.conditional_preview_pipeline import (
    plan_conditional_preview,
)
from tests.test_issue_141_selection import make_assets, OPP


class PreviewPipelineTests(unittest.TestCase):
    def test_six_matrix_rows_are_fetched_once_for_selection_and_prep(self):
        assets = make_assets()
        with patch.object(FrozenMatchAssets, "matrix_rows_for", autospec=True,
                          side_effect=lambda instance, names:
                          tuple(instance.matrix_index[n] for n in names)) as rows:
            out = plan_conditional_preview(assets, OPP)
            rows.assert_called_once_with(assets, OPP)
        self.assertEqual(out.status, "CONDITIONAL_PREVIEW_ONLY", out.reason)
        self.assertEqual(out.selection.lead, "A")
        self.assertEqual(out.prep.proposed_pairs, 18)
        self.assertEqual(out.prep.result.prep_status, "DRAFT")
        self.assertFalse(out.ready_for_live)
        self.assertEqual(out.ranking_status, "NO_RANKING")

    def test_source_swapped_cached_row_rejected(self):
        from champions_assist.local_strategy.conditional_prep_builder import (
            build_conditional_prep_draft,
        )
        assets = make_assets()
        rows = list(assets.matrix_rows_for(OPP))
        rows[0] = rows[0].replace("A,G,mv", "A,G,forgedmove")
        out = build_conditional_prep_draft(
            assets, ("A","B","C"), "A", OPP,
            cached_matrix_rows_six=tuple(rows))
        self.assertEqual(out.status, "BLOCKED")
        self.assertEqual(out.reason, "CACHED_MATRIX_SIX_NOT_SOURCE_BOUND")

    def test_real_fixed_roster_has_all_18_draft_slots(self):
        root = Path(__file__).resolve().parents[1] / "masters"
        assets = MasterPreloader(root).load_for_new_match()
        opponents = ("ガブリアス", "アシレーヌ", "ミミッキュ",
                     "カバルドン", "グソクムシャ", "リザードン")
        result = plan_conditional_preview(assets, opponents)
        self.assertEqual(result.status, "CONDITIONAL_PREVIEW_ONLY",
                         result.reason)
        self.assertEqual(len(result.selection.selected_three), 3)
        self.assertIn(result.selection.lead, result.selection.selected_three)
        self.assertEqual(len(result.prep.result.matchups_18),18)
        self.assertFalse(result.prep.tactical_accuracy_verified)

    def test_provisional_matrix_does_not_generate_prep(self):
        out = plan_conditional_preview(make_assets(provisional=True), OPP)
        self.assertEqual(out.status,"BLOCKED")
        self.assertIsNone(out.prep)


if __name__ == "__main__":
    unittest.main()
