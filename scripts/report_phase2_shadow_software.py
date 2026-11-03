"""Off-match report of Stage1 *software-only* GPT/Shadow capture quality.

Never accepts game actions, claims a win rate, imports raw OCR/video, or runs
during a battle. Optional GUI timing export is only a local Qt clock. #203
requires independent real device recording/video before Phase 2 acceptance.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import re
from typing import Any

from champions_assist.local_strategy.shadow_comparison_store import (
    ShadowComparisonRecord, read_shadow_comparisons,
)
from champions_assist.local_strategy.shadow_quality_gate import audit_shadow_quality
from champions_assist.local_strategy.gui_timing_trace_audit import (
    audit_gui_timing_export,
)

_SHADOW_ID = re.compile(r"shadow-(\d+)-(\d+)-(req-[0-9]+)\Z")


def stage1_software_shadow_report(
    rows: tuple[ShadowComparisonRecord, ...],
    gui_export: dict[str, Any] | None = None,
) -> dict[str, Any]:
    quality = audit_shadow_quality(rows, ())
    gui_status = "NOT_SUPPLIED"
    joined = 0
    if gui_export is not None:
        if not isinstance(gui_export, dict):
            raise ValueError("GUI_EXPORT_MUST_BE_OBJECT")
        clock = audit_gui_timing_export(gui_export)
        gui_status = clock.status
        packet = gui_export.get("timing_observations")
        raw_events = packet.get("events") if isinstance(packet, dict) else None
        events = raw_events if isinstance(raw_events, list) else []
        dispatches, displays = set(), set()
        for event in events:
            if not isinstance(event, dict):
                continue
            kind = event.get("event")
            if kind not in ("AI_REQUEST_DISPATCH", "AI_RESULT_DISPLAYED"):
                continue
            key = (event.get("match_id"), event.get("request_id"),
                   event.get("turn_epoch"), event.get("state_revision"))
            if kind == "AI_REQUEST_DISPATCH":
                dispatches.add(key)
            elif (event.get("recommendation_present") is True and
                  event.get("status") in ("ADVICE", "PROVISIONAL_ADVICE")):
                displays.add(key)
        if gui_status == "GUI_MONOTONIC_TIMING_ONLY":
            for row in rows:
                match = _SHADOW_ID.fullmatch(row.decision_window_id)
                if match is None or row.gpt_status != "GPT_UNVERIFIED":
                    continue
                key = (row.match_id, match.group(3),
                       int(match.group(1)), int(match.group(2)))
                if (row.state_revision == key[3]
                        and key in dispatches and key in displays):
                    joined += 1
    return {
        "status": ("SOFTWARE_QT_PAIR_COMPLETE_NOT_GAME_CERTIFIED"
                   if rows and joined == len(rows)
                   and gui_status == "GUI_MONOTONIC_TIMING_ONLY"
                   else "SOFTWARE_EVIDENCE_PARTIAL"),
        "reason": "GAME_LEGALITY_TACTICS_AND_DISPLAY_PAINT_NOT_ATTESTED",
        "sqlite_window_count": len(rows),
        "qt_gpt_pair_matched": joined,
        "qt_gpt_pair_unmatched": max(0, len(rows)-joined),
        "gui_timing_status": gui_status,
        "quality": asdict(quality),
        "game_capture_independently_authenticated": False,
        "champions_mechanics_verified": False,
        "real_tactical_correctness_verified": False,
        "live_local_action_verified": False,
        "phase2_accepted": False,
        "phase3_local_recommendation_enabled": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadow-sqlite", required=True, type=Path)
    parser.add_argument("--gui-export", type=Path)
    arguments = parser.parse_args()
    try:
        if not arguments.shadow_sqlite.is_file():
            raise ValueError("MISSING_SHADOW_DATABASE")
        rows = read_shadow_comparisons(arguments.shadow_sqlite)
        gui = None
        if arguments.gui_export is not None:
            raw = arguments.gui_export.read_bytes()
            if not 0 < len(raw) <= 3_000_000:
                raise ValueError("GUI_EXPORT_SIZE_INVALID")
            gui = json.loads(raw.decode("utf-8"))
        result = stage1_software_shadow_report(rows, gui)
    except (ValueError, TypeError, OSError, UnicodeError,
            json.JSONDecodeError, KeyError):
        result = {
            "status": "INVALID_STAGE1_SOFTWARE_EVIDENCE",
            "phase2_accepted": False,
            "reason": "MISSING_OR_MALFORMED_LOCAL_EVIDENCE",
        }
        print(json.dumps(result, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
