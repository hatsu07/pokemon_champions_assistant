"""OFF-MATCH ONLY: pair device capture and SQLite Shadow logs by exact window.

No real-game correctness or action approval is implied by a successfully
parsed packet. External review/video verification is still mandatory.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from champions_assist.local_strategy.device_shadow_evidence_link import (
    CapturedTurnLink, GoldenReviewClaim, audit_offline_device_shadow_link,
)
from champions_assist.local_strategy.shadow_comparison_store import (
    read_shadow_comparisons,
)

MAX_PACKET = 2_000_000
MAX_ANNOTATIONS = 1_000_000


def _json_file(path: Path, limit: int) -> dict:
    if not path.is_file() or not 1 <= path.stat().st_size <= limit:
        raise ValueError("INVALID_EVIDENCE_JSON_SIZE")
    obj = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(obj, dict):
        raise ValueError("INVALID_EVIDENCE_JSON_OBJECT")
    return obj


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-packet", required=True, type=Path)
    parser.add_argument("--annotations", required=True, type=Path)
    parser.add_argument("--shadow-sqlite", required=True, type=Path)
    parser.add_argument("--team-id", required=True)
    parser.add_argument("--team-fingerprint", required=True)
    parser.add_argument("--frozen-team-sha256", required=True)
    parser.add_argument("--regulation", required=True)
    args = parser.parse_args()
    try:
        device = _json_file(args.device_packet, MAX_PACKET)
        labels = _json_file(args.annotations, MAX_ANNOTATIONS)
        if (set(labels) != {"frames", "goldens"}
                or not isinstance(labels["frames"], list)
                or not isinstance(labels["goldens"], list)):
            raise ValueError("INVALID_FRAME_ANNOTATIONS")
        frames = tuple(CapturedTurnLink(**r) for r in labels["frames"])
        goldens = tuple(GoldenReviewClaim(**r) for r in labels["goldens"])
        rows = read_shadow_comparisons(args.shadow_sqlite)
        audit = audit_offline_device_shadow_link(
            device, team_id=args.team_id,
            team_fingerprint=args.team_fingerprint,
            regulation=args.regulation,
            frozen_team_sha256=args.frozen_team_sha256,
            shadow_rows=rows, frames=frames, golden_claims=goldens)
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError):
        print(json.dumps({
            "status": "EVIDENCE_LINK_REFUSED",
            "reason": "INVALID_OR_UNREADABLE_OFFLINE_PACKET",
            "phase2_accepted": False}, ensure_ascii=False))
        return 2
    print(json.dumps(asdict(audit), ensure_ascii=False, indent=2))
    # Exit=0 means ONLY structurally linked evidence; no tactics verified.
    return 0 if audit.status == "STRUCTURALLY_LINKED_UNVERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
