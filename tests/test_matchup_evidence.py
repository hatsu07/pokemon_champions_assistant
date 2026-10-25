"""Offline review and source-grounding tests; no inferred Champions mechanics."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from champions_assist.app_config import ConfigurationError
from champions_assist.matchup_evidence import (
    SCHEMA, REVIEW_SCHEMA, import_reviews, validate_review_packet,
)
from champions_assist.team_live_assets import prepare_team_assets
from champions_assist.team_management import TeamDocument

MASTER=Path(__file__).resolve().parents[1]/"masters"
TEAM=MASTER/"Pokemon_Champions_MC_team_2026-10-03.csv"


class MatchupEvidenceTests(unittest.TestCase):
    def setUp(self):
        t=TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.team_id="b"*32
        self.source=self.root/"team.csv"
        self.source.write_bytes(TEAM.read_bytes())

    def build(self):
        return prepare_team_assets(
            master_dir=MASTER,data_root=self.root,
            team_id=self.team_id,team_path=self.source)

    def catalog(self):
        a=self.build()
        return json.loads(a.manifest_path.with_name("matchup-evidence.json").read_text(encoding="utf-8"))

    def changed_build(self, field, value):
        document=TeamDocument.load(self.source)
        rows=[dict(row) for row in document.rows]
        idx=next(i for i,m in enumerate(rows) if m.get("record")=="MON")
        rows[idx][field]=value
        self.source.write_text(TeamDocument(document.fieldnames,tuple(rows)).to_text(),encoding="utf-8")

    def packet(self, catalog, status="CONDITIONAL"):
        first=catalog["cells"][0]
        entry={
            "opponent":first["opponent"],
            "slot":first["slot"],
            "member_signature":first["member_signature"],
            "status":status,
            "reviewed_at":"2026-10-25T15:00:00+09:00",
            "reviewer":"player-confirmed",
            "rationale":"ゲーム内表示を確認した条件付き記録。型を確定しない。",
            "conditions":{"observed_opponent_moves":["観測した技"]},
            "sources":[
                {"kind":"USER_INGAME_CONFIRMED",
                 "observed_at":"2026-10-25T15:00:00+09:00",
                 "sha256":"1"*64},
            ],
        }
        if status=="CONFIRMED":
            entry["sources"].append({
                "kind":"HUMAN_REVIEWED_BATTLE_RECORD",
                "observed_at":"2026-10-25T15:15:00+09:00",
                "sha256":"2"*64,
            })
        return {
            "schema":REVIEW_SCHEMA,"team_id":self.team_id,
            "regulation":catalog["regulation"],
            "player_reviewed":True,
            "member_signatures":catalog["member_signatures"],
            "reviews":[entry],
        }

    def test_generates_all_opponent_by_slot_source_separated_candidates(self):
        x=self.catalog()
        self.assertEqual(x["schema"],SCHEMA)
        self.assertGreater(x["opponents"],300)
        self.assertEqual(x["total_cells"],x["opponents"]*6)
        self.assertEqual(len(x["cells"]),x["total_cells"])
        self.assertEqual(
            {(c["opponent"],c["slot"]) for c in x["cells"]}.__len__(),
            x["total_cells"])
        self.assertEqual(len(x["master_sha256"]),3)
        self.assertTrue(all(len(s)==64 for s in x["master_sha256"].values()))
        first=x["cells"][0]
        self.assertEqual(first["actual_damage"],"UNKNOWN")
        self.assertEqual(first["speed_order_after_stats_items"],"UNKNOWN")
        self.assertEqual(first["safe_hard_switch"],"UNKNOWN")
        self.assertEqual(first["direct_processor"],"UNKNOWN")
        self.assertIn("registered_build",first)
        self.assertIn("opponent_priority_candidates",first)
        self.assertEqual(x["counts"]["CONFIRMED"],0)
        self.assertEqual(x["counts"]["UNKNOWN"],0)  # existing exact six reviewed baseline
        self.assertFalse(x["quality_gate"]["live_ready"])
        self.assertTrue(x["quality_gate"]["legacy_matrix_reuse_allowed"])

    def test_changed_team_has_unknown_cell_metadata_and_never_live_ready(self):
        self.changed_build("item","いのちのたま")
        a=self.build()
        x=self.catalog()
        self.assertEqual(a.status,"PROVISIONAL_UNVERIFIED")
        self.assertEqual(x["counts"]["UNKNOWN"],x["opponents"])
        self.assertEqual(x["counts"]["CONFIRMED"],0)
        self.assertFalse(x["quality_gate"]["live_ready"])
        self.assertFalse(x["quality_gate"]["legacy_matrix_reuse_allowed"])
        self.assertEqual(x["cells"][0]["evaluation"],"UNKNOWN")
        self.assertEqual(json.loads(a.manifest_path.read_text())["unknown_rate"],
                         round(1/6,6))

    def test_human_review_accepts_conditional_but_does_not_upgrade_live(self):
        self.changed_build("item","いのちのたま")
        preview=self.catalog()
        packet=self.packet(preview)
        input_path=self.root/"review.json"
        input_path.write_text(json.dumps(packet,ensure_ascii=False),encoding="utf-8")
        path=import_reviews(data_root=self.root,team_id=self.team_id,
                            packet_path=input_path,candidate_catalog=preview)
        self.assertTrue(path.is_file())
        after=self.build()
        evidence=self.catalog()
        first=evidence["cells"][0]
        self.assertEqual(first["evaluation"],"CONDITIONAL")
        self.assertEqual(first["safe_hard_switch"],"UNKNOWN")
        self.assertEqual(first["direct_processor"],"UNKNOWN")
        meta=json.loads(after.manifest_path.read_text())
        self.assertEqual(meta["review_status_counts"]["CONDITIONAL"],1)
        self.assertEqual(meta["candidate_status_counts"]["UNKNOWN"],
                         meta["opponent_rows"]-1)
        self.assertFalse(meta["quality_gate"]["live_ready"])
        self.assertTrue(self.build().cache_hit)

    def test_confirmed_review_not_equal_to_tactical_live_certification(self):
        self.changed_build("item","いのちのたま")
        candidate=self.catalog()
        packet=self.packet(candidate,status="CONFIRMED")
        path=self.root/"human.json"
        path.write_text(json.dumps(packet,ensure_ascii=False),encoding="utf-8")
        import_reviews(data_root=self.root,team_id=self.team_id,
                       packet_path=path,candidate_catalog=candidate)
        x=self.catalog()
        self.assertEqual(x["counts"]["CONFIRMED"],1)
        self.assertFalse(x["quality_gate"]["live_ready"])
        self.assertEqual(x["cells"][0]["direct_processor"],"UNKNOWN")
        self.assertEqual(x["cells"][0]["safe_hard_switch"],"UNKNOWN")

    def test_rejects_review_from_wrong_build_or_regulation_and_missing_sources(self):
        self.changed_build("item","いのちのたま")
        candidate=self.catalog()
        good=self.packet(candidate)
        scenarios=[
            ("wrong team",{"team_id":"c"*32}),
            ("wrong reg",{"regulation":"M-OLD"}),
            ("approval missing",{"player_reviewed":False}),
            ("changed signature",{"member_signatures":["x"]*6}),
        ]
        for label,change in scenarios:
            bad={**good,**change}
            with self.subTest(label=label),self.assertRaises(ConfigurationError):
                validate_review_packet(
                    bad,team_id=self.team_id,regulation=candidate["regulation"],
                    signatures=candidate["member_signatures"],
                    opponents={x["opponent"] for x in candidate["cells"]})
        bad=self.packet(candidate)
        bad["reviews"][0]["sources"]=[]
        with self.assertRaises(ConfigurationError):
            validate_review_packet(
                bad,team_id=self.team_id,regulation=candidate["regulation"],
                signatures=candidate["member_signatures"],
                opponents={x["opponent"] for x in candidate["cells"]})

    def test_duplicate_fake_independent_proof_fails_and_last_good_persists(self):
        self.changed_build("item","いのちのたま")
        candidate=self.catalog()
        accepted=self.packet(candidate)
        input_path=self.root/"reviews.json"
        input_path.write_text(json.dumps(accepted,ensure_ascii=False),encoding="utf-8")
        stored=import_reviews(data_root=self.root,team_id=self.team_id,
                              packet_path=input_path,candidate_catalog=candidate)
        before=stored.read_bytes()
        invalid=self.packet(candidate,status="CONFIRMED")
        invalid["reviews"][0]["sources"][1] = dict(invalid["reviews"][0]["sources"][0])
        input_path.write_text(json.dumps(invalid,ensure_ascii=False),encoding="utf-8")
        with self.assertRaises(ConfigurationError):
            import_reviews(data_root=self.root,team_id=self.team_id,
                           packet_path=input_path,candidate_catalog=candidate)
        self.assertEqual(before,stored.read_bytes())

    def test_stale_reviews_are_ignored_not_bound_to_a_modified_team(self):
        self.changed_build("item","いのちのたま")
        candidate=self.catalog()
        packet=self.packet(candidate)
        path=self.root/"input.json"
        path.write_text(json.dumps(packet,ensure_ascii=False),encoding="utf-8")
        import_reviews(data_root=self.root,team_id=self.team_id,
                       packet_path=path,candidate_catalog=candidate)
        self.changed_build("ap_h_a_b_c_d_s","0-32-0-0-0-32")
        a=self.build()
        meta=json.loads(a.manifest_path.read_text())
        self.assertEqual(meta["review_status_counts"]["CONDITIONAL"],0)
        self.assertIn("STALE_OR_INVALID_REVIEW_IGNORED",meta["review_warning"])
        self.assertFalse(meta["quality_gate"]["live_ready"])

if __name__=="__main__":
    unittest.main()
