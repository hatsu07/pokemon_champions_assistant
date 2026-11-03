"""OFF-MATCH ONLY: propose candidate keys from Champions frozen master sources.

Never auto-edit a registered team or promote these strings into battle advice.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from champions_assist.preload import MasterPreloader, PreloadError
from champions_assist.local_strategy.champions_master_key_candidates import (
    audit_champions_master_key_candidates,
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--masters-dir", type=Path, default=Path("masters"))
    p.add_argument("--team-csv", type=Path, default=None)
    args = p.parse_args()
    try:
        assets = MasterPreloader(
            args.masters_dir, team_source=args.team_csv).load_for_new_match()
        report = audit_champions_master_key_candidates(
            assets,
            move_master_bytes=(
                args.masters_dir / "Pokemon_Champions_MC_move_master.csv").read_bytes(),
            learnsets_bytes=(
                args.masters_dir /
                "Pokemon_Champions_MC_pokemon_learnsets_linked.csv").read_bytes(),
            usable_pokemon_bytes=(
                args.masters_dir / "Pokemon_Champions_MC_usable_pokemon.csv").read_bytes(),
        )
    except (OSError, ValueError, PreloadError):
        # No raw CSV, OCR, paths, model secrets or user-specific data in errors.
        print(json.dumps({"status": "INVALID_SOURCE_OR_FROZEN_TEAM",
                          "reason": "OFF_MATCH_SOURCE_LOAD_FAILED",
                          "usable_in_live": False}))
        return 2
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    # Successful *inventory* only. Never signifies an executable move.
    return 0 if report.status == "CHAMPIONS_KEY_CANDIDATES_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
