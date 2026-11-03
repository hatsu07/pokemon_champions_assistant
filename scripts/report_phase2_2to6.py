"""#141: ONE post-match report for all Phase 2 ②〜⑥ prerequisites.

Use saved GUI exports, 5-match device packet + independent annotations,
read-only SQLite and the currently registered team index/source assets.
Never execute on a live battle, access network or enable LOCAL_SHADOW advice.
A structurally valid report is NOT Champions-game acceptance.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from champions_assist.local_strategy.device_shadow_evidence_link import (
    CapturedTurnLink, GoldenReviewClaim, audit_offline_device_shadow_link,
)
from champions_assist.local_strategy.registered_team_source_binding import (
    RegisteredSourceContext, bind_registered_team_sources,
)
from champions_assist.local_strategy.gui_timing_trace_audit import (
    audit_gui_timing_export,
)
from champions_assist.local_strategy.roster_mapping_readiness import (
    RosterMappingReadiness, audit_frozen_roster_mapping,
)
from champions_assist.local_strategy.consolidated_phase2_audit import (
    consolidated_phase2_report,
)
from champions_assist.local_strategy.shadow_comparison_store import (
    read_shadow_comparisons,
)
from champions_assist.preload import MasterPreloader


def _json_file(path: Path, max_size: int) -> dict:
    if not path.is_file() or not 1 <= path.stat().st_size <= max_size:
        raise ValueError("MISSING_OR_OVERSIZED_SOURCE_JSON")
    item = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(item, dict):
        raise ValueError("SOURCE_JSON_OBJECT_REQUIRED")
    return item


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-packet", required=True, type=Path)
    parser.add_argument("--annotations", required=True, type=Path)
    parser.add_argument("--shadow-sqlite", required=True, type=Path)
    parser.add_argument("--gui-export", action="append", type=Path, default=[])
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--masters-dir", required=True, type=Path)
    parser.add_argument("--team-id", required=True)
    parser.add_argument("--team-fingerprint", required=True)
    parser.add_argument("--frozen-team-sha256", required=True)
    parser.add_argument("--regulation", required=True)
    args = parser.parse_args()
    try:
        packet = _json_file(args.device_packet, 2_000_000)
        labels = _json_file(args.annotations, 1_000_000)
        if (set(labels) != {"frames", "goldens"}
                or not isinstance(labels["frames"], list)
                or not isinstance(labels["goldens"], list)
                or len(args.gui_export) > 100):
            raise ValueError("INVALID_ANNOTATIONS_OR_GUI_COUNT")
        frames = tuple(CapturedTurnLink(**item) for item in labels["frames"])
        goldens = tuple(GoldenReviewClaim(**item) for item in labels["goldens"])
        rows = read_shadow_comparisons(args.shadow_sqlite)
        context = RegisteredSourceContext(args.data_root, args.masters_dir)
        bound = bind_registered_team_sources(
            context, team_id=args.team_id,
            team_fingerprint=args.team_fingerprint,
            frozen_team_sha256=args.frozen_team_sha256,
            regulation=args.regulation)
        linked = audit_offline_device_shadow_link(
            packet, team_id=args.team_id,
            team_fingerprint=args.team_fingerprint,
            frozen_team_sha256=args.frozen_team_sha256,
            regulation=args.regulation, shadow_rows=rows,
            frames=frames, golden_claims=goldens,
            source_context=context)
        roster = RosterMappingReadiness(
            "INVALID_FROZEN_ASSETS", "REGISTERED_SOURCES_NOT_CROSS_BOUND")
        if bound.source_identity_cross_bound:
            cache = (args.data_root / "live-team-assets" /
                     args.team_id / args.team_fingerprint)
            own_csv = args.data_root / "teams" / (args.team_id + ".csv")
            assets = MasterPreloader(
                args.masters_dir, team_source=own_csv,
                matrix_source=cache / "matrix.txt",
                lookup_source=cache / "lookup.txt").load_for_new_match()
            roster = audit_frozen_roster_mapping(assets)
        gui = tuple(audit_gui_timing_export(_json_file(p, 3_000_000))
                    for p in args.gui_export)
        ids = tuple(m.get("match_id", "") for m in packet.get("matches", [])
                    if isinstance(m, dict))
        all_in_one = consolidated_phase2_report(
            roster=roster, device=linked, expected_match_ids=ids,
            gui_clocks=gui)
        print(json.dumps({
            "status": all_in_one.status,
            "registered_source_binding": asdict(bound),
            "own_roster": asdict(roster),
            "device_and_shadow": asdict(linked),
            "gui_timing": [asdict(x) for x in gui],
            "phase2_2to6": asdict(all_in_one),
        }, ensure_ascii=False, indent=2))
        # Incomplete game acceptance is reported explicitly, never silently
        # upgraded. Exit 0 indicates successful *offline report creation*.
        return 0
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
        print(json.dumps({
            "status": "CONSOLIDATED_AUDIT_REFUSED",
            "reason": "INVALID_OR_UNREADABLE_OFFLINE_INPUTS",
            "phase2_accepted": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
