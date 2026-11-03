"""Offline only: inspect the registered Champions team -> pinned worker IDs.

Run before matchmaking; NEVER invoke from the real-time selection/PREP/turn
callbacks. JSON output is diagnostic, not game authority or a remapping tool.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from champions_assist.preload import MasterPreloader, PreloadError
from champions_assist.local_strategy.roster_mapping_readiness import (
    audit_frozen_roster_mapping,
)


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--masters-dir",type=Path,default=Path("masters"),
                        help="directory of frozen local Champions master sources")
    parser.add_argument("--team-csv",type=Path,default=None,
                        help="registered team CSV to audit (optional)")
    args=parser.parse_args()
    try:
        assets=MasterPreloader(
            args.masters_dir,team_source=args.team_csv).load_for_new_match()
    except (PreloadError, ValueError, OSError) as exc:
        # Never print file contents, OCR, snapshots or user secrets.
        print(json.dumps({
            "status":"PRELOAD_FAILED",
            "reason":"FOUR_MASTER_PRELOAD_INCOMPLETE",
            "ready_for_live":False},ensure_ascii=False))
        return 2
    result=audit_frozen_roster_mapping(assets)
    print(json.dumps(asdict(result),ensure_ascii=False,indent=2))
    # The exit status indicates a well-formed inventory, not permission for
    # an actual LIVE action. Real-game state/legality remains unverified.
    return 0 if result.status=="STRUCTURAL_IDS_PRESENT_UNATTESTED" else 1


if __name__=="__main__":
    raise SystemExit(main())
