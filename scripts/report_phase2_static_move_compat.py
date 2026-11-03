"""Off-match 6 x 24 Champions fixed-master move fields for pinned Dex comparison.

No registered team rewrite and no LIVE recommendation/engine authorization.
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
from champions_assist.local_strategy.static_move_compatibility import (
    build_static_move_fields,
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--masters-dir", type=Path, default=Path("masters"))
    p.add_argument("--team-csv", type=Path, default=None)
    args = p.parse_args()
    try:
        assets = MasterPreloader(
            args.masters_dir, team_source=args.team_csv).load_for_new_match()
        move = (args.masters_dir / "Pokemon_Champions_MC_move_master.csv").read_bytes()
        candidates = audit_champions_master_key_candidates(
            assets,
            move_master_bytes=move,
            learnsets_bytes=(args.masters_dir /
                "Pokemon_Champions_MC_pokemon_learnsets_linked.csv").read_bytes(),
            usable_pokemon_bytes=(args.masters_dir /
                "Pokemon_Champions_MC_usable_pokemon.csv").read_bytes())
        out = build_static_move_fields(assets, candidates, move)
    except (OSError, ValueError, PreloadError):
        print(json.dumps({"status": "SOURCE_STATIC_FIELDS_REFUSED",
                          "reason": "OFF_MATCH_SOURCE_LOAD_FAILED",
                          "champions_engine_parity_verified": False,
                          "ranking_status": "NO_RANKING"}))
        return 2
    print(json.dumps(asdict(out), ensure_ascii=False, indent=2))
    return 0 if out.status == "CHAMPIONS_STATIC_24_SOURCE_BOUND" else 1


if __name__ == "__main__":
    raise SystemExit(main())
