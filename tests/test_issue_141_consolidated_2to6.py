"""#141: one PR / one report for ②〜⑥. ALL generated captures are FAKE.

Tests prove source-binding, negative tampering, timing and acceptance
boundaries; they do NOT verify actual gameplay, mechanics, tactics or
Windows live local model display.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from champions_assist.local_strategy.registered_team_source_binding import (
    RegisteredSourceContext, bind_registered_team_sources,
)
from champions_assist.local_strategy.device_shadow_evidence_link import (
    audit_offline_device_shadow_link,
)
from champions_assist.local_strategy.consolidated_phase2_audit import (
    consolidated_phase2_report,
)
from champions_assist.local_strategy.gui_timing_trace_audit import (
    audit_gui_timing_export,
)
from champions_assist.local_strategy.roster_mapping_readiness import (
    audit_frozen_roster_mapping,
)
from champions_assist.team_live_assets import prepare_team_assets
from champions_assist.team_management import (
    TeamRepository, TeamDocument, regulation_scope,
)
from champions_assist.preload import MasterPreloader
from champions_assist.local_strategy.shadow_comparison_store import append_shadow_comparison
from tests.test_issue_141_device_shadow_evidence_link import fixture
from tests.test_issue_141_gui_timing_trace_audit import scene

MASTER = Path(__file__).resolve().parents[1] / "masters"
ORIGINAL = MASTER / "Pokemon_Champions_MC_team_2026-10-03.csv"


def register_and_prepare(root: Path):
    teams = TeamRepository(root / "teams", catalog=None)
    registered = teams.import_csv(ORIGINAL)
    csv_path = root / "teams" / (registered.team_id + ".csv")
    assets = prepare_team_assets(
        master_dir=MASTER, data_root=root,
        team_id=registered.team_id, team_path=csv_path)
    fingerprint = json.loads(assets.manifest_path.read_text(
        encoding="utf-8"))["fingerprint"]
    raw_sha = sha256(csv_path.read_bytes()).hexdigest()
    regulation, _ = regulation_scope(TeamDocument.load(csv_path))
    return registered, fingerprint, raw_sha, regulation, csv_path, assets


class ConsolidatedPhase2Tests(unittest.TestCase):
    def test_current_registered_source_and_frozen_6x24_real_team_binding(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            record, fp, raw_sha, reg, csv, prepared = register_and_prepare(root)
            bound = bind_registered_team_sources(
                RegisteredSourceContext(root, MASTER), team_id=record.team_id,
                team_fingerprint=fp, frozen_team_sha256=raw_sha,
                regulation=reg)
            self.assertEqual(bound.status, "LOCAL_REGISTERED_SOURCES_CROSS_BOUND",
                             bound.reason)
            self.assertTrue(bound.source_identity_cross_bound)
            self.assertFalse(bound.game_capture_independently_authenticated)
            self.assertFalse(bound.phase2_accepted)
            assets = MasterPreloader(
                MASTER, team_source=csv, matrix_source=prepared.matrix_path,
                lookup_source=prepared.lookup_path).load_for_new_match()
            roster = audit_frozen_roster_mapping(assets)
            self.assertEqual(roster.inspected_members, 6)
            self.assertEqual(roster.checked_move_slots, 24)
            self.assertFalse(roster.mapping_independently_attested)
            self.assertFalse(roster.ready_for_live)

    def test_different_team_sha_id_fingerprint_and_stale_csv_are_rejected(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            record, fp, digest, reg, csv, _ = register_and_prepare(root)
            source = RegisteredSourceContext(root, MASTER)
            variants = [
                (record.team_id, fp, "0" * 64, reg),
                (record.team_id, "1" * 64, digest, reg),
                ("f" * 32, fp, digest, reg),
                (record.team_id, fp, digest, "WRONG_REG"),
            ]
            for mid, f, sha, rule in variants:
                report = bind_registered_team_sources(
                    source, team_id=mid, team_fingerprint=f,
                    frozen_team_sha256=sha, regulation=rule)
                self.assertFalse(report.source_identity_cross_bound,
                                 report.reason)
            csv.write_bytes(csv.read_bytes() + b"\n")
            self.assertFalse(bind_registered_team_sources(
                source, team_id=record.team_id, team_fingerprint=fp,
                frozen_team_sha256=digest, regulation=reg
            ).source_identity_cross_bound)

    def test_forged_index_team_path_or_saved_manifest_refused(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            record, fp, digest, reg, _, prepared = register_and_prepare(root)
            source = RegisteredSourceContext(root, MASTER)
            index = root / "teams" / "index.json"
            original = index.read_bytes()
            payload = json.loads(index.read_text(encoding="utf-8"))
            payload[0]["csv_path"] = str(ORIGINAL)
            index.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(bind_registered_team_sources(
                source, team_id=record.team_id, team_fingerprint=fp,
                frozen_team_sha256=digest, regulation=reg
            ).reason, "REGISTERED_TEAM_CSV_PATH_MISMATCH")
            index.write_bytes(original)
            manifest = json.loads(prepared.manifest_path.read_text())
            manifest["team_file"] = "another-team.csv"
            prepared.manifest_path.write_text(json.dumps(manifest),
                                              encoding="utf-8")
            self.assertFalse(bind_registered_team_sources(
                source, team_id=record.team_id, team_fingerprint=fp,
                frozen_team_sha256=digest, regulation=reg
            ).source_identity_cross_bound)

    def test_registered_source_binding_prevents_cross_team_false_verified_rows(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            registered, fp, rawsha, reg, _, _ = register_and_prepare(root)
            device, rows, frames, goldens = fixture(root)
            device["team_id"] = registered.team_id
            device["team_fingerprint"] = fp
            rows = tuple(replace(row, team_sha256=rawsha) for row in rows)
            link = audit_offline_device_shadow_link(
                device, team_id=registered.team_id,
                team_fingerprint=fp, frozen_team_sha256=rawsha,
                regulation=reg, shadow_rows=rows,
                frames=frames, golden_claims=goldens,
                source_context=RegisteredSourceContext(root, MASTER))
            self.assertEqual(link.status, "STRUCTURALLY_LINKED_UNVERIFIED",
                             link.reason)
            self.assertTrue(link.team_identity_cross_bound)
            self.assertEqual(link.same_revision_shadow_turns, 10)
            self.assertEqual(link.unverified_aligned_shadow_rows, 0)
            self.assertNotIn("TEAM_IDENTITY_NOT_CROSS_BOUND",
                             link.missing_evidence)
            self.assertFalse(link.game_capture_independently_authenticated)
            self.assertFalse(link.phase2_accepted)
            # Mutating the packet to another team cannot preserve that claim.
            tampered = dict(device, team_id="a" * 32)
            refused = audit_offline_device_shadow_link(
                tampered, team_id="a" * 32, team_fingerprint=fp,
                frozen_team_sha256=rawsha, regulation=reg,
                shadow_rows=rows, frames=frames, golden_claims=goldens,
                source_context=RegisteredSourceContext(root, MASTER))
            self.assertEqual(refused.status, "EVIDENCE_LINK_REFUSED")
            self.assertFalse(refused.team_identity_cross_bound)

    def test_one_report_includes_all_five_sections_and_never_asserts_game_pass(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            record, fp, raw_sha, reg, csv, prepared = register_and_prepare(root)
            packet, rows, frames, goldens = fixture(root)
            packet["team_id"], packet["team_fingerprint"] = record.team_id, fp
            rows = tuple(replace(row, team_sha256=raw_sha) for row in rows)
            device = audit_offline_device_shadow_link(
                packet, team_id=record.team_id, team_fingerprint=fp,
                frozen_team_sha256=raw_sha, regulation=reg,
                shadow_rows=rows, frames=frames, golden_claims=goldens,
                source_context=RegisteredSourceContext(root, MASTER))
            assets = MasterPreloader(
                MASTER, team_source=csv, matrix_source=prepared.matrix_path,
                lookup_source=prepared.lookup_path).load_for_new_match()
            roster = audit_frozen_roster_mapping(assets)
            # GUI exports intentionally only cover one of five fake games.
            trace = scene(turns=2)
            for e in trace["timing_observations"]["events"]:
                if e.get("match_id") == "capture-actual-opaque":
                    e["match_id"] = "SYNTHETIC-0"
            gui = audit_gui_timing_export(trace)
            self.assertEqual(gui.source_match_id, "SYNTHETIC-0")
            report = consolidated_phase2_report(
                roster=roster, device=device,
                expected_match_ids=(m["match_id"] for m in packet["matches"]),
                gui_clocks=(gui,))
            self.assertEqual(report.status, "INCOMPLETE_REAL_GAME_ACCEPTANCE")
            for stage in ("section_2", "section_3", "section_4",
                          "section_5", "section_6"):
                self.assertTrue(getattr(report, stage))
            self.assertEqual(report.locally_bound_shadow_windows, 10)
            self.assertEqual(report.gpt_gui_matched_matches, 1)
            self.assertEqual(report.golden_label_categories, 8)
            self.assertIn("INCOMPLETE_GUI_TIME_EVENTS_FOR_ALL_MATCHES",
                          report.missing_evidence)
            self.assertIn("OWN_SIX_AND_24_MOVE_ID_FIELDS_NOT_PRESENT",
                          report.missing_evidence)
            self.assertFalse(report.game_capture_independently_authenticated)
            self.assertFalse(report.champions_mechanics_verified)
            self.assertFalse(report.phase2_accepted)

    def test_single_cli_accepts_all_five_input_types_but_never_game_passes(self):
        # This is the actual user-facing one-command path. All five alleged
        # captures are synthetic bytes; CLI may not claim real battle truth.
        with TemporaryDirectory() as d:
            root = Path(d)
            record, fp, ownsha, reg, _, _ = register_and_prepare(root)
            packet, rows, frames, goldens = fixture(root)
            packet.update(team_id=record.team_id, team_fingerprint=fp,
                          regulation=reg)
            csv_sha_rows = tuple(replace(row, team_sha256=ownsha)
                                 for row in rows)
            db = root / "offline.sqlite"
            for record_row in csv_sha_rows:
                self.assertEqual(append_shadow_comparison(db, record_row),
                                 "RECORDED")
            packet_path = root / "device.json"
            packet_path.write_text(json.dumps(packet), encoding="utf-8")
            ann_path = root / "annotations.json"
            ann_path.write_text(json.dumps({
                "frames": [asdict(v) for v in frames],
                "goldens": [asdict(v) for v in goldens],
            }), encoding="utf-8")
            gui = scene(turns=2)
            for e in gui["timing_observations"]["events"]:
                if e.get("match_id") == "capture-actual-opaque":
                    e["match_id"] = "SYNTHETIC-0"
            gui_path = root / "match-export.json"
            gui_path.write_text(json.dumps(gui), encoding="utf-8")
            repo_root = Path(__file__).resolve().parents[1]
            env = dict(os.environ)
            env["PYTHONPATH"] = os.pathsep.join((
                str(repo_root / "src"), str(repo_root),
                env.get("PYTHONPATH", "")))
            env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            result = subprocess.run([
                sys.executable, "scripts/report_phase2_2to6.py",
                "--device-packet", str(packet_path),
                "--annotations", str(ann_path),
                "--shadow-sqlite", str(db),
                "--data-root", str(root),
                "--masters-dir", str(MASTER),
                "--team-id", record.team_id,
                "--team-fingerprint", fp,
                "--frozen-team-sha256", ownsha,
                "--regulation", reg,
                "--gui-export", str(gui_path),
            ], cwd=repo_root, env=env, capture_output=True,
               text=True, encoding="utf-8", timeout=50)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            doc = json.loads(result.stdout)
            self.assertEqual(doc["status"], "INCOMPLETE_REAL_GAME_ACCEPTANCE")
            self.assertEqual(doc["registered_source_binding"]["source_identity_cross_bound"], True)
            self.assertTrue(doc["device_and_shadow"]["team_identity_cross_bound"])
            self.assertEqual(doc["phase2_2to6"]["locally_bound_shadow_windows"], 10)
            self.assertEqual(doc["phase2_2to6"]["gpt_gui_matched_matches"], 1)
            self.assertEqual(doc["phase2_2to6"]["match_count"], 5)
            self.assertFalse(doc["phase2_2to6"]["phase2_accepted"])
            self.assertFalse(doc["device_and_shadow"]["phase2_accepted"])

    def test_incomplete_input_cannot_be_misreported_as_device_pass(self):
        from champions_assist.local_strategy.roster_mapping_readiness import (
            RosterMappingReadiness)
        from champions_assist.local_strategy.device_shadow_evidence_link import (
            DeviceShadowLinkAudit)
        report = consolidated_phase2_report(
            roster=RosterMappingReadiness(
                "STRUCTURAL_IDS_PRESENT_UNATTESTED", "ONLY_FORMAT",
                worker_id_fields_structurally_complete=True,
                inspected_members=5, checked_move_slots=24),
            device=DeviceShadowLinkAudit(
                "EVIDENCE_LINK_REFUSED", "CAPTURE_INVALID",
                match_count=1),
            expected_match_ids=("match-A",))
        self.assertEqual(report.status, "CONSOLIDATED_AUDIT_REFUSED")
        self.assertFalse(report.phase2_accepted)

    def test_aggregator_refuses_unrelated_and_duplicated_gui_matches(self):
        from champions_assist.local_strategy.roster_mapping_readiness import (
            RosterMappingReadiness)
        from champions_assist.local_strategy.device_shadow_evidence_link import (
            DeviceShadowLinkAudit)
        roster = RosterMappingReadiness("MAPPING_FIELDS_INCOMPLETE", "TEST_ONLY")
        device = DeviceShadowLinkAudit("STRUCTURALLY_LINKED_UNVERIFIED",
                                       "TEST_ONLY", match_count=1)
        valid_gui = audit_gui_timing_export(scene(turns=1))
        denied = consolidated_phase2_report(
            roster=roster, device=device, expected_match_ids=("match-A",),
            gui_clocks=(valid_gui,))
        self.assertEqual(denied.status, "CONSOLIDATED_AUDIT_REFUSED")
        wrong_count = consolidated_phase2_report(
            roster=roster, device=device,
            expected_match_ids=("match-A", "match-B"))
        self.assertEqual(wrong_count.status, "CONSOLIDATED_AUDIT_REFUSED")


if __name__ == "__main__":
    unittest.main()
