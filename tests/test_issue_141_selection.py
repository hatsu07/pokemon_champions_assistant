"""Isolated synthetic Phase 2 selection regression; no game parity claim."""
from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
import hashlib
import unittest
from unittest.mock import patch

from champions_assist.local_strategy.selector import rank_local_selection
from champions_assist.preload import FrozenMatchAssets, REQUIRED_MASTER_FILES

OWN = ("A", "B", "C", "D", "E", "F")
OPP = ("O1", "O2", "O3", "O4", "O5", "O6")
PROCESSORS = ("A", "B", "C", "A", "B", "C")


def make_assets(*, provisional=False, bad_team=False,
                insurance_last=False, bad_cell=False) -> FrozenMatchAssets:
    names = OWN if not bad_team else ("A", "B", "C", "D", "E", "Other")
    header = "# OWN={" + ",".join(
        f"{slot}:{member}" for slot, member in zip(OWN, names)) + "}"
    tagged = "# DERIVED_STATUS=PROVISIONAL_UNVERIFIED" if provisional else ""
    rows = {}
    for i, opponent in enumerate(OPP):
        cells = []
        for j, member in enumerate(OWN):
            rate = "A" if member == "A" else "B" if member in ("B", "C") else "C"
            insurance = "1" if insurance_last and i == 5 and j == 2 else "0"
            cells.append(f"{rate},G,mv,1,U,{insurance}")
        if bad_cell and i == 5:
            cells[0] = "?,?,?,?,U,0"
        rows[opponent] = "|".join(
            (opponent, "test/type", "80", *cells, PROCESSORS[i],
             "warning unknown", "-", "A?/A?/A?/A?/A?/A?", "S"))
    text = "\n".join((header, tagged, *rows.values()))
    contents = {name: text if i == 1 else f"frozen-master-{i}"
                for i, name in enumerate(REQUIRED_MASTER_FILES)}
    sha = {name: hashlib.sha256(body.encode("utf-8")).hexdigest()
           for name, body in contents.items()}
    return FrozenMatchAssets(
        contents=MappingProxyType(contents),
        sha256=MappingProxyType(sha),
        matrix_index=MappingProxyType(rows),
        lookup_index=MappingProxyType(rows),
        own_team=tuple({"name": name, "record": "MON", "move1": "mv"}
                       for name in OWN),
        frozen_at="SYNTHETIC_TEST_ONLY",
    )


class Phase2SelectionTests(unittest.TestCase):
    def test_explicit_processors_cover_all_six_and_choose_strongest_lead(self):
        assets = make_assets()
        with patch.object(FrozenMatchAssets, "matrix_rows_for",
                          autospec=True, wraps=None) as wrapped:
            wrapped.side_effect = lambda actual, six: tuple(
                actual.matrix_index[name] for name in six)
            result = rank_local_selection(assets, OPP)
            wrapped.assert_called_once_with(assets, OPP)
        self.assertEqual(result.status, "MODEL_PROVISIONAL")
        self.assertEqual(set(result.selected_three), {"A", "B", "C"})
        self.assertEqual(result.lead, "A")
        self.assertEqual(result.uncovered_processors, ())
        self.assertEqual(len(result.matrix_rows_six), 6)
        self.assertEqual(result.team_sha256,
                         assets.sha256[REQUIRED_MASTER_FILES[3]])
        self.assertEqual(result.reason,
                         "ORDINAL_MATRIX_RANKING_NOT_SAFE_SWITCH_OR_WIN_RATE")

    def test_insurance_only_is_not_processor_or_safe_switch(self):
        result = rank_local_selection(make_assets(insurance_last=True), OPP)
        self.assertEqual(result.status, "MODEL_PROVISIONAL")
        self.assertEqual(result.selected_three, ("A", "B", "C"))
        self.assertEqual(result.uncovered_processors, ("O6",))
        # U is never promoted to a certified safe receiving choice.
        self.assertTrue(result.quality_ordinal)
        self.assertNotIn("SAFE_SWITCH", result.status)

    def test_unmatched_provisional_and_missing_matrix_are_not_promoted(self):
        for assets, opponent in (
            (make_assets(provisional=True), OPP),
            (make_assets(bad_team=True), OPP),
            (make_assets(bad_cell=True), OPP),
            (make_assets(), OPP[:-1] + ("UNKNOWN",)),
            (make_assets(), OPP[:-1] + (OPP[0],)),
        ):
            with self.subTest(opponent=opponent,
                              variant=assets.contents[REQUIRED_MASTER_FILES[1]][:85]):
                result = rank_local_selection(assets, opponent)
                self.assertEqual(result.status, "BLOCKED")
                self.assertFalse(result.selected_three)

    def test_does_not_modify_frozen_roster_or_matrix(self):
        assets = make_assets()
        before = tuple(assets.matrix_index.values())
        result = rank_local_selection(assets, OPP)
        self.assertEqual(tuple(assets.matrix_index.values()), before)
        self.assertEqual(result.opponent_six, OPP)
        self.assertNotEqual(result.quality_ordinal, ())
        # There is no percent or inferred damage in the ordinal result.
        self.assertTrue(all(isinstance(x, int) for x in result.quality_ordinal))

    def test_unknown_six_and_non_frozen_input_rejected(self):
        for opponent in ((), ("O1",)*6, ("O1", "O2", "O3", "O4", "O5", "")):
            self.assertEqual(rank_local_selection(make_assets(), opponent).status,
                             "BLOCKED")
        self.assertEqual(rank_local_selection({}, OPP).status, "BLOCKED")


if __name__ == "__main__":
    unittest.main()
