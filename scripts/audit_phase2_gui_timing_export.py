"""OFF-MATCH ONLY: quantify GUI-screen timing from explicit saved UI export.

The exported trace's monotonic timestamps are not independent video or
Champions action legality proof. No network, GUI or LIVE calls occur here.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from champions_assist.local_strategy.gui_timing_trace_audit import (
    audit_gui_timing_export,
)

MAX_JSON = 3_000_000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gui_match_export", type=Path,
                        help="JSON exported from the desktop match UI")
    args = parser.parse_args()
    try:
        path = args.gui_match_export
        if not path.is_file() or not 1 <= path.stat().st_size <= MAX_JSON:
            raise ValueError("MISSING_OR_OVERSIZED_GUI_EXPORT")
        export = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        print(json.dumps({"status": "GUI_TRACE_REFUSED",
                          "reason": "INVALID_GUI_EXPORT_FILE",
                          "phase2_accepted": False}, ensure_ascii=False))
        return 2
    result = audit_gui_timing_export(export)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    # Status 0 means a structurally auditable UI trace; never Phase 2 pass.
    return 0 if result.status == "GUI_MONOTONIC_TIMING_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
