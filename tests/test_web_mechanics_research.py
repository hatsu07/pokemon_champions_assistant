"""Research candidates must remain provenance-tagged, non-LIVE and format sound."""
from __future__ import annotations
from collections import Counter
import csv
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "masters"
MECHANICS = ROOT / "Pokemon_Champions_mechanics_web_evidence_2026-10-25.jsonl"
CHART = ROOT / "Pokemon_Champions_type_chart_candidate_2026-10-25.json"
DAMAGE = ROOT / "Pokemon_Champions_damage_reference_cases_candidate_2026-10-25.json"


class ResearchCandidatesTests(unittest.TestCase):
    def test_all_mechanics_candidates_have_sources_and_never_autoload(self):
        entries=[json.loads(line) for line in MECHANICS.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertGreaterEqual(len(entries),35)
        self.assertEqual(len({x["id"] for x in entries}),len(entries))
        self.assertTrue(all(x["schema"]=="ChampionsMechanicsWebEvidence.v1" for x in entries))
        for e in entries:
            with self.subTest(rule=e["id"]):
                self.assertTrue(e["sources"])
                self.assertTrue(all(s.startswith("https://") for s in e["sources"]))
                self.assertEqual(e["checked_at"],"2026-10-25")
                self.assertFalse(e["game_observation_verified"])
                self.assertFalse(e["live_autoload_allowed"])
                self.assertEqual(e["rule_validation"],"PENDING_IN_GAME_CORROBORATION")
                self.assertTrue(e["candidate_value"] is not None)
        self.assertIn("STATUS_PARALYSIS_ACTION",{x["id"] for x in entries})
        self.assertIn("STAT_NON_HP",{x["id"] for x in entries})

    def test_18x18_chart_excludes_incompatible_stellar_and_is_not_verified(self):
        x=json.loads(CHART.read_text(encoding="utf-8"))
        self.assertEqual(x["schema"],"ChampionsTypeChartCandidate.v1")
        self.assertEqual(len(x["types"]),18)
        self.assertEqual(len(set(x["types"])),18)
        self.assertNotIn("Stellar",x["types"])
        self.assertFalse(x["game_observation_verified"])
        self.assertFalse(x["live_autoload_allowed"])
        self.assertEqual(x["verification_status"],"DERIVED_MAINLINE_NOT_GAME_VERIFIED")
        self.assertTrue(x["attribution"])
        self.assertIn("CC BY",x["source_license"])
        self.assertEqual(set(x["chart"]),set(x["types"]))
        for attack,row in x["chart"].items():
            with self.subTest(attack=attack):
                self.assertEqual(set(row),set(x["types"]))
                self.assertTrue(all(type(v) in (float,int) and v in (0,0.5,1,2)
                                    for v in row.values()))
        self.assertEqual(x["chart"]["Ground"]["Flying"],0)
        self.assertEqual(x["chart"]["Dragon"]["Fairy"],0)
        self.assertEqual(x["chart"]["Fire"]["Grass"],2)

    def test_damage_examples_are_calculator_not_device_evidence(self):
        x=json.loads(DAMAGE.read_text(encoding="utf-8"))
        self.assertEqual(x["case_count"],len(x["cases"]))
        self.assertGreaterEqual(x["case_count"],4)
        self.assertFalse(x["live_autoload_allowed"])
        self.assertEqual(x["verification_status"],
                         "THIRD_PARTY_CALCULATOR_EXAMPLES_NOT_GAME_DEVICE_VALIDATED")
        self.assertEqual(len({case["id"] for case in x["cases"]}),x["case_count"])
        for case in x["cases"]:
            with self.subTest(case=case["id"]):
                self.assertGreater(case["damage_range"][0],0)
                self.assertLessEqual(case["damage_range"][0],case["damage_range"][1])
        self.assertEqual(x["cases"][0]["damage_range"],[138,164])

    def test_research_candidates_are_never_live_preload_inputs(self):
        from champions_assist.preload import REQUIRED_MASTER_FILES
        for name in (MECHANICS.name,CHART.name,DAMAGE.name):
            self.assertNotIn(name,REQUIRED_MASTER_FILES)
        self.assertEqual(len(REQUIRED_MASTER_FILES),4)


if __name__=="__main__":
    unittest.main()
