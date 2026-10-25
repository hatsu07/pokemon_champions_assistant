"""Offline invariants for full-team structured, adaptive and differential analysis."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from champions_assist.app_config import ConfigurationError
from champions_assist.team_management import TeamDocument
from champions_assist.team_live_assets import prepare_team_assets, UNKNOWN_CELL
from champions_assist.team_matchup_v2 import (
    SCHEMA_V2, eligible_v2_request, validate_v2_reply, compact_v2_hint)
from champions_assist.team_matchup_reuse import reuse_unchanged_matchups
from champions_assist.team_ai_analysis import save_proposals
from champions_assist.team_ai_matrix import publish_ai_matrix
from champions_assist.team_auto_analysis import run_automatic_analysis

ROOT=Path(__file__).resolve().parents[1]
MASTERS=ROOT/"masters"
TEAM=MASTERS/"Pokemon_Champions_MC_team_2026-10-03.csv"


def mock_reply(req: dict) -> dict:
    members={m["slot"]:m for m in req["own_members"]}
    opponents={o["name"]:o for o in req["opponents"]}
    rows=[]
    for c in req["cells"]:
        own=members[c["slot"]]
        opp=opponents[c["opponent"]]
        a=own["traits"].get("base_speed")
        b=opp["traits"].get("base_speed")
        speed=("BASE_FASTER" if a>b else "BASE_SLOWER" if a<b else "BASE_TIE") if (
            type(a) is int and type(b) is int) else "UNKNOWN"
        rows.append({
            **c,"attack_move":own["moves"][0]["name"] if own["moves"] else "UNKNOWN",
            "base_speed":speed, "damage":"UNKNOWN",
            "priority_risk":"POSSIBLE","setup_risk":"POSSIBLE",
            "direct_processor":"UNKNOWN","safe_switch":"UNKNOWN",
            "note":"未検証。直接攻撃と交代を状況比較","unknowns":"相手実際の技と被ダメージ",
        })
    return {"schema":SCHEMA_V2,"catalog_sha256":req["catalog_sha256"],"proposals":rows}


class StructuredTeamTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.team=self.root/"team.csv"
        doc=TeamDocument.load(TEAM)
        rows=[dict(r) for r in doc.rows]
        idx=next(i for i,r in enumerate(rows) if r.get("record")=="MON")
        rows[idx]["item"]="いのちのたま"
        self.team.write_text(TeamDocument(doc.fieldnames,tuple(rows)).to_text(),
                             encoding="utf-8")
        self.team_id="b"*32
        self.ready=prepare_team_assets(
            master_dir=MASTERS,data_root=self.root,
            team_id=self.team_id,team_path=self.team)
        self.catalog=self.ready.manifest_path.with_name("matchup-evidence.json")

    def _publish(self):
        return publish_ai_matrix(
            matrix_path=self.ready.matrix_path,
            manifest_path=self.ready.manifest_path,catalog_path=self.catalog)

    def test_compact_batch_and_six_row_matrix_preserve_uncertainty(self):
        req=eligible_v2_request(self.catalog,max_cells=36)
        self.assertEqual(len(req["cells"]),36)
        self.assertLessEqual(len(req["opponents"]),36)
        self.assertLessEqual(len(req["own_members"]),6)
        for c in req["cells"]:
            self.assertNotIn("opponent_species",c)
            self.assertNotIn("registered_moves",c)
        valid=validate_v2_reply(req,mock_reply(req))
        save_proposals(self.catalog.parent/"ai-proposal-v2.json",self.catalog,valid)
        result=self._publish()
        self.assertEqual(result["ai_proposal_cells"],36)
        self.assertFalse(result["ai_proposal_live_certified"])
        matrix=self.ready.matrix_path.read_text(encoding="utf-8")
        by_name={p[0]:p for p in (line.split("|") for line in matrix.splitlines()
                    if line and not line.startswith("#"))}
        for opponent in {r["opponent"] for r in valid["proposals"]}:
            row=by_name[opponent]
            self.assertEqual(len(row),14)
            self.assertEqual(row[3],UNKNOWN_CELL)
            self.assertIn("AI_UNVERIFIED:",row[11])
            self.assertIn("技=",row[11])
            self.assertNotIn("|",row[11])
            self.assertLessEqual(len(row[11]),720)
            self.assertEqual(row[12],"??/??/??/??/??/??")
        manifest=json.loads(self.ready.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["unverified_cells"],2148)
        self.assertFalse(manifest["quality_gate"]["live_ready"])
        self.assertEqual(self._publish(),result)

    def test_invalid_move_speed_or_certainty_is_rejected(self):
        req=eligible_v2_request(self.catalog,max_cells=12)
        r=mock_reply(req)
        r["proposals"][0]["attack_move"]="ゲーム外の技"
        with self.assertRaises(ConfigurationError):validate_v2_reply(req,r)
        r=mock_reply(req)
        r["proposals"][0]["base_speed"]="FASTER_GUARANTEED"
        with self.assertRaises(ConfigurationError):validate_v2_reply(req,r)
        r=mock_reply(req)
        r["proposals"][0]["direct_processor"]="CERTIFIED"
        with self.assertRaises(ConfigurationError):validate_v2_reply(req,r)
        r=mock_reply(req)
        r["proposals"][0]["safe_switch"]="SAFE"
        with self.assertRaises(ConfigurationError):validate_v2_reply(req,r)
        r=mock_reply(req)
        r["proposals"][0]["damage"]="OHKO"
        with self.assertRaises(ConfigurationError):validate_v2_reply(req,r)

    def test_delta_reuse_when_only_one_member_changes(self):
        req=eligible_v2_request(self.catalog,max_cells=36)
        saved=validate_v2_reply(req,mock_reply(req))
        save_proposals(self.catalog.parent/"ai-proposal-v2.json",self.catalog,saved)
        before={c["slot"] for c in req["cells"]}
        self.assertEqual(before,set(range(6)))
        doc=TeamDocument.load(self.team)
        records=[dict(r) for r in doc.rows]
        mon_idx=[i for i,r in enumerate(records) if r.get("record")=="MON"]
        changed_slot=1
        records[mon_idx[changed_slot]]["item"]="こだわりハチマキ"
        other=self.root/"team2.csv"
        other.write_text(TeamDocument(doc.fieldnames,tuple(records)).to_text(),
                         encoding="utf-8")
        next_assets=prepare_team_assets(
            master_dir=MASTERS,data_root=self.root,team_id="c"*32,team_path=other)
        catalog=next_assets.manifest_path.with_name("matchup-evidence.json")
        status=reuse_unchanged_matchups(catalog,self.root/"live-team-assets")
        self.assertEqual(status["reused_cells"],30)
        rows=[]
        for file in catalog.parent.glob("ai-proposal-reuse-*.json"):
            saved=json.loads(file.read_text(encoding="utf-8"))
            rows.extend(saved["proposals"])
        self.assertEqual(len(rows),30)
        self.assertTrue(all(r["slot"]!=changed_slot for r in rows))
        self.assertEqual(len(eligible_v2_request(catalog,max_cells=36)["cells"]),36)
        published=publish_ai_matrix(
            matrix_path=next_assets.matrix_path,
            manifest_path=next_assets.manifest_path,catalog_path=catalog)
        self.assertEqual(published["ai_proposal_cells"],30)
        self.assertEqual(reuse_unchanged_matchups(catalog,self.root/"live-team-assets")["reused_cells"],0)

    def test_adaptive_fallback_and_resume(self):
        sizes=[]
        def analyzer(request,**kwargs):
            sizes.append(len(request["cells"]))
            if len(request["cells"])>12:
                raise ConfigurationError("malformed larger batch")
            reply=mock_reply(request)
            return validate_v2_reply(request,reply)
        report=run_automatic_analysis(
            self.catalog,model="mock",access_token="mock",
            analyze=analyzer,structured=True,max_batches=5)
        self.assertIn(report["status"],("BATCH_LIMIT","COMPLETE","SOURCE_GAPS"))
        self.assertGreater(report["count"],0)
        self.assertGreaterEqual(report["retries"],1)
        self.assertEqual(sizes[0],24)
        self.assertEqual(sizes[1],12)
        self.assertEqual(len(list(self.catalog.parent.glob("ai-proposal-*.json"))),
                         report["batches"])
        # Invalid/missing-source evaluations are never marked as certified.
        self.assertFalse(self._publish()["ai_proposal_live_certified"])

    def test_cached_six_row_coverage_and_priority_lookup(self):
        from champions_assist.live_context import selection_hint_coverage
        names=tuple("ABCDEF")
        def row(name,hint):
            return "|".join([name,"X","80"]+["?,?,?,?,U,0"]*6+
                            ["-","先制:"+name,hint,"??/??/??/??/??/??","PS"])
        lines=[row(names[i],"AI_UNVERIFIED:"+chr(65+i)+":技=じしん")
               if i<4 else row(names[i],"-") for i in range(6)]
        coverage=selection_hint_coverage(names,lines)
        self.assertEqual(coverage["matrix_ai_hint_rows"],4)
        self.assertEqual(coverage["matrix_ai_hint_pairs"],4)
        self.assertEqual(coverage["matrix_ai_structured_rows"],4)
        self.assertEqual(coverage["matrix_ai_missing_opponents"],["E","F"])
        with self.assertRaises(ValueError):
            selection_hint_coverage(names,lines[:5])

    def test_v1_does_not_satisfy_v2_coverage(self):
        from champions_assist.team_ai_analysis import (
            build_request,validate_reply,SCHEMA)
        legacy=build_request(self.catalog)
        reply={"schema":SCHEMA,"catalog_sha256":legacy["catalog_sha256"],
               "proposals":[{"opponent":c["opponent"],"slot":c["slot"],
                             "member_signature":c["member_signature"],
                             "assessment":"未検証","risks":"相手不明","unknowns":"未測定"}
                            for c in legacy["cells"]]}
        save_proposals(self.catalog.parent/"ai-proposal-old.json",
                       self.catalog,validate_reply(legacy,reply))
        request=eligible_v2_request(self.catalog,max_cells=12)
        self.assertEqual(len(request["cells"]),12)
        legacy_keys={(c["opponent"],c["slot"]) for c in legacy["cells"]}
        v2_keys={(c["opponent"],c["slot"]) for c in request["cells"]}
        self.assertTrue(legacy_keys & v2_keys)


if __name__=="__main__":
    unittest.main()
