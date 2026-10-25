"""Offline regression tests for team-specific AI proposals. No network needed."""
import json
import tempfile
import unittest
from pathlib import Path
from champions_assist.team_ai_analysis import (
    SCHEMA, build_request, validate_reply, save_proposals, analyze_request)
from champions_assist.app_config import ConfigurationError

class TeamAIAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.catalog=self.root/"catalog.json"
        cells=[]
        for slot in range(6):
            cells.append(dict(opponent="ガブリアス",slot=slot,own="test",
                member_signature=str(slot),evaluation="UNKNOWN",
                missing_source=[],own_species={},opponent_species={},
                registered_moves=[],registered_build={},
                opponent_priority_candidates=[],opponent_potential_high_power_moves=[]))
        self.catalog.write_text(json.dumps(dict(schema="champions-matchup-evidence-v1",
            regulation="M-C",member_signatures=[str(i) for i in range(6)],cells=cells)),encoding="utf-8")
        self.request=build_request(self.catalog)
    def reply(self):
        return dict(schema=SCHEMA,catalog_sha256=self.request["catalog_sha256"],
          proposals=[dict(opponent=c["opponent"],slot=c["slot"],
                          member_signature=c["member_signature"],assessment="未検証の候補",
                          risks="不明な相手技",unknowns="最大打点とS") for c in self.request["cells"]])
    def test_request_is_bounded_and_contains_no_live_conclusions(self):
        self.assertEqual(len(self.request["cells"]),6)
        self.assertNotIn("direct_processor",self.request["cells"][0])
        with self.assertRaises(ConfigurationError):build_request(self.catalog,max_cells=13)
    def test_valid_reply_remains_unverified(self):
        result=validate_reply(self.request,self.reply())
        self.assertFalse(result["live_approved"])
        self.assertFalse(result["processor_certified"])
        self.assertFalse(result["safe_switch_certified"])
        self.assertEqual(result["status"],"AI_PROPOSAL_UNVERIFIED")
    def test_stale_or_duplicate_or_extra_fields_rejected(self):
        r=self.reply();r["proposals"][1]=r["proposals"][0]
        with self.assertRaises(ConfigurationError):validate_reply(self.request,r)
        r=self.reply();r["proposals"][0]["processor_certified"]=True
        with self.assertRaises(ConfigurationError):validate_reply(self.request,r)
        r=self.reply();r["catalog_sha256"]="stale"
        with self.assertRaises(ConfigurationError):validate_reply(self.request,r)
    def test_successive_batches_skip_saved_proposals(self):
        proposal=validate_reply(self.request,self.reply())
        save_proposals(self.root/"ai-proposal-first.json",self.catalog,proposal)
        self.assertEqual(build_request(self.catalog)["cells"],[])

    def test_changed_catalog_and_overwrite_rejected(self):
        proposal=validate_reply(self.request,self.reply())
        target=self.root/"proposal.json"
        self.catalog.write_text("{}",encoding="utf-8")
        with self.assertRaises(ConfigurationError):save_proposals(target,self.catalog,proposal)
        self.assertFalse(target.exists())
        self.catalog.write_text(json.dumps({"schema":"champions-matchup-evidence-v1"}),encoding="utf-8")
    def test_no_missing_source_cell_sent(self):
        c=json.loads(self.catalog.read_text(encoding="utf-8"))
        c["cells"][0]["missing_source"]=["MASTER_MISSING"]
        self.catalog.write_text(json.dumps(c),encoding="utf-8")
        self.assertEqual(len(build_request(self.catalog)["cells"]),5)

if __name__=="__main__":
    unittest.main()
