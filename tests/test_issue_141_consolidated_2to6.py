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

    def test_review_p2_corrupt_live_manifest_counts_never_cross_bind(self):
        # team_readiness verifies file hashes before checking manifest count
        # invariants. The initial true team_match/matrix/lookup flags must
        # NOT override its final "生成物不整合" / "生成物確認不可" status.
        from champions_assist.team_prep_readiness import team_readiness

        with TemporaryDirectory() as d:
            root = Path(d)
            record, fp, digest, reg, csv, prepared = register_and_prepare(root)
            source = RegisteredSourceContext(root, MASTER)
            manifest_path = prepared.manifest_path
            pristine = json.loads(manifest_path.read_text(encoding="utf-8"))
            packet, rows, frames, goldens = fixture(root)
            packet["team_id"] = record.team_id
            packet["team_fingerprint"] = fp
            rows = tuple(replace(r, team_sha256=digest) for r in rows)

            def source_audit():
                return bind_registered_team_sources(
                    source, team_id=record.team_id, team_fingerprint=fp,
                    frozen_team_sha256=digest, regulation=reg)

            def device_audit():
                return audit_offline_device_shadow_link(
                    packet, team_id=record.team_id, team_fingerprint=fp,
                    frozen_team_sha256=digest, regulation=reg,
                    shadow_rows=rows, frames=frames, golden_claims=goldens,
                    source_context=source)

            self.assertTrue(source_audit().source_identity_cross_bound)
            self.assertEqual(device_audit().same_revision_shadow_turns, 10)
            for variant in ("negative_opponent_rows",
                            "nonnumeric_opponent_rows",
                            "missing_opponent_rows", "invalid_ai_counts"):
                with self.subTest(variant=variant):
                    manifest = json.loads(json.dumps(pristine))
                    if variant == "negative_opponent_rows":
                        manifest["opponent_rows"] = -1
                    elif variant == "nonnumeric_opponent_rows":
                        manifest["opponent_rows"] = "not-a-number"
                    elif variant == "missing_opponent_rows":
                        manifest.pop("opponent_rows", None)
                    else:
                        # Older generated manifests omit AI version
                        # counts; adding a malformed pair must still be
                        # detected instead of requiring v1/v2 to exist.
                        manifest["ai_v1_cells"] = -1
                        manifest["ai_v2_cells"] = 0
                    manifest_path.write_text(
                        json.dumps(manifest), encoding="utf-8")
                    ready = team_readiness(
                        root, record.team_id, csv, master_dir=MASTER)
                    self.assertIn(
                        ready["status"],
                        ("生成物不整合", "生成物確認不可"))
                    # The exploit is strongest when the early per-file flags
                    # already signal true; those flags alone prove too little.
                    self.assertTrue(ready["team_match"])
                    self.assertTrue(ready["matrix"])
                    self.assertTrue(ready["lookup"])
                    self.assertEqual(ready["stale_reasons"], [])

                    bound = source_audit()
                    self.assertEqual(
                        bound.reason,
                        "SOURCE_ASSET_MANIFEST_NOT_CURRENT_OR_MATCHED")
                    self.assertFalse(bound.source_identity_cross_bound)
                    self.assertFalse(bound.phase2_accepted)
                    device = device_audit()
                    self.assertEqual(device.status, "EVIDENCE_LINK_REFUSED")
                    self.assertIn(
                        "SOURCE_ASSET_MANIFEST_NOT_CURRENT_OR_MATCHED",
                        device.reason)
                    self.assertFalse(device.team_identity_cross_bound)
                    self.assertEqual(device.same_revision_shadow_turns, 0)
                    self.assertFalse(device.phase2_accepted)
            manifest_path.write_text(json.dumps(pristine), encoding="utf-8")
            self.assertTrue(source_audit().source_identity_cross_bound)

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

    def test_review_p2_1_blocked_wrong_window_or_team_not_counted(self):
        from champions_assist.local_strategy.phase2_match_audit import (
            Phase2MatchAudit)
        with TemporaryDirectory() as d:
            root = Path(d)
            registered, fp, team_sha, reg, csv, prepared = (
                register_and_prepare(root))
            packet, rows, frames, goldens = fixture(root)
            packet.update(team_id=registered.team_id, team_fingerprint=fp)
            rows = tuple(replace(row, team_sha256=team_sha) for row in rows)
            linked = audit_offline_device_shadow_link(
                packet, team_id=registered.team_id, team_fingerprint=fp,
                frozen_team_sha256=team_sha, regulation=reg,
                shadow_rows=rows, frames=frames, golden_claims=goldens,
                source_context=RegisteredSourceContext(root, MASTER))
            assets = MasterPreloader(
                MASTER, team_source=csv, matrix_source=prepared.matrix_path,
                lookup_source=prepared.lookup_path).load_for_new_match()
            roster = audit_frozen_roster_mapping(assets)
            rec = rows[0]
            valid = Phase2MatchAudit(
                status="OFFLINE_EVIDENCE_ONLY",
                reason="NO_REAL_GAME_ACCEPTANCE_AUTHORITY",
                match_id=rec.match_id,
                decision_window_id=rec.decision_window_id,
                state_revision=rec.state_revision,
                snapshot_sha256=rec.snapshot_sha256,
                observed_stage_status="PROVISIONAL_OBSERVED_BRANCH_CONSTRAINTS",
                synthetic_turn_status=rec.local_status,
                prep_status="ASSESSED_18_CONDITIONAL_ONLY",
                prep_assessed_pairs=18,
                prep_time_status="PREP_REPORTED_WITHIN_SHARED_90_SECONDS_UNVERIFIED",
                shadow_store_status="RECORDED",
                shadow_record=rec,
                opponent_preview_binding="FROZEN_SIX_MATCHED_NOT_GAME_AUTHENTICATED",
            )
            def check(report):
                return consolidated_phase2_report(
                    roster=roster, device=linked,
                    expected_match_ids=(p["match_id"] for p in packet["matches"]),
                    matches=(report,))
            structurally_linked = check(valid)
            self.assertEqual(structurally_linked.synthetic_one_turn_runs, 1)
            self.assertEqual(structurally_linked.prep18_structural_matches, 1)
            self.assertEqual(structurally_linked.prep18_assessed_pairs, 18)
            self.assertFalse(structurally_linked.phase2_accepted)
            bad_cases = (
                replace(valid, status="BLOCKED", reason="PREP_AFTER_OPPONENT_LEAD"),
                replace(valid, opponent_preview_binding="NOT_VERIFIED"),
                replace(valid, prep_time_status="PREP_AFTER_OPPONENT_LEAD"),
                replace(valid, observed_stage_status="NOT_RUN"),
                replace(valid, decision_window_id="different-window"),
                replace(valid, state_revision=100),
                replace(valid, snapshot_sha256="0" * 64),
                replace(valid, shadow_record=replace(
                    rec, team_sha256="f" * 64)),
                replace(valid, shadow_record=replace(
                    rec, master_manifest_sha256="f" * 64)),
                replace(valid, shadow_record=rows[1]),
                replace(valid, shadow_store_status="NOT_WRITTEN"),
                replace(valid, missing_acceptance_evidence=(
                    "PREP18_TACTICAL_FIELDS_NOT_ASSESSED",)),
            )
            for bad in bad_cases:
                with self.subTest(reason=bad.reason,
                                  window=bad.decision_window_id,
                                  rev=bad.state_revision,
                                  sha=bad.shadow_record.team_sha256 if
                                      bad.shadow_record else "missing"):
                    result = check(bad)
                    self.assertEqual(result.synthetic_one_turn_runs, 0)
                    self.assertEqual(result.prep18_structural_matches, 0)
                    self.assertEqual(result.prep18_assessed_pairs, 0)
                    self.assertIn("REJECTED_OR_UNLINKED_PHASE2_MATCH_AUDIT",
                                  result.missing_evidence)
                    self.assertFalse(result.phase2_accepted)
            # Same labels without authoritative LOCAL roster sources cannot
            # count an arbitrary Phase2MatchAudit as registered-team proof.
            unbound = audit_offline_device_shadow_link(
                packet, team_id=registered.team_id, team_fingerprint=fp,
                frozen_team_sha256=team_sha, regulation=reg,
                shadow_rows=rows, frames=frames, golden_claims=goldens)
            self.assertEqual(consolidated_phase2_report(
                roster=roster, device=unbound,
                expected_match_ids=(p["match_id"] for p in packet["matches"]),
                matches=(valid,)).synthetic_one_turn_runs, 0)

    def test_review_p2_2_gui_one_of_two_device_turns_not_complete(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            team, fp, team_sha, reg, _, _ = register_and_prepare(root)
            packet, rows, frames, goldens = fixture(root)
            packet.update(team_id=team.team_id, team_fingerprint=fp)
            rows = tuple(replace(row, team_sha256=team_sha) for row in rows)
            linked = audit_offline_device_shadow_link(
                packet, team_id=team.team_id, team_fingerprint=fp,
                frozen_team_sha256=team_sha, regulation=reg,
                shadow_rows=rows, frames=frames, golden_claims=goldens,
                source_context=RegisteredSourceContext(root, MASTER))
            self.assertEqual(dict(linked.expected_turns_by_match),
                             {f"SYNTHETIC-{i}": 2 for i in range(5)})
            clocks = []
            for i in range(5):
                trace = scene(turns=1)
                for event in trace["timing_observations"]["events"]:
                    if event.get("match_id") == "capture-actual-opaque":
                        event["match_id"] = f"SYNTHETIC-{i}"
                gui = audit_gui_timing_export(trace)
                self.assertEqual(gui.complete_gpt_display_turns, 1)
                clocks.append(gui)
            from champions_assist.local_strategy.roster_mapping_readiness import (
                RosterMappingReadiness)
            result = consolidated_phase2_report(
                roster=RosterMappingReadiness(
                    "MAPPING_FIELDS_INCOMPLETE", "UNKNOWN"),
                device=linked,
                expected_match_ids=(p["match_id"] for p in packet["matches"]),
                gui_clocks=tuple(clocks))
            self.assertEqual(result.gpt_gui_matched_matches, 0)
            self.assertEqual(result.gpt_gui_missing_device_turns, 5)
            self.assertIn("GUI_DEVICE_TURN_COUNT_MISMATCH",
                          result.missing_evidence)
            self.assertIn("INCOMPLETE_GUI_TIME_EVENTS_FOR_ALL_MATCHES",
                          result.missing_evidence)
            self.assertFalse(result.phase2_accepted)

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
