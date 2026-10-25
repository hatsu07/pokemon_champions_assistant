"""Bounded live context assembled only from four preloaded Champions masters.

The matrix is the primary source. Secondary lookup rows are never scanned for
all six opponents or promoted into certified processors/safe switches.
"""
from __future__ import annotations

WARNING_MARKERS = ("先制", "積み", "メガ", "天候", "壁", "高速", "優先度")
RISK_MARKERS = frozenset("PSGWF")


def critical_lookup_rows(assets, opponents: tuple[str, ...],
                         matrix_rows: tuple[str, ...] | list[str],
                         *, limit: int = 2) -> list[str]:
    """At most two exact-key 60sec rows for documented critical warnings.

    Select only if matrix warning/risk explicitly marks the threat. This is
    factual retrieval, not a Python choice of own team or a strategic ranking.
    Stable opponent order means unknown threats are not silently fabricated.
    """
    if not 0 <= limit <= 2 or len(opponents) != len(matrix_rows) or len(opponents) != 6:
        raise ValueError("LOOKUP_REQUIRES_SIX_MATRIX_ROWS_AND_LIMIT_0_TO_2")
    selected = []
    for opponent, text in zip(opponents, matrix_rows):
        columns = text.split("|")
        if len(columns) != 14 or columns[0] != opponent:
            raise ValueError("LOOKUP_MATRIX_ROWS_MISMATCH")
        warning, risk = columns[10], columns[13]
        if not (any(token in warning for token in WARNING_MARKERS)
                or any(flag in risk for flag in RISK_MARKERS)):
            continue
        row = assets.lookup_index.get(opponent)
        if row is not None:
            selected.append(row)
            if len(selected) == limit:
                break
    return selected[:limit]


SELECTION_CHECKLIST = (
    "Primary source: the SIX opponent matrix rows, not general usage rankings. "
    "Read all six rows in one batch and judge own 3+lead as a whole. "
    "Consider probable opposing lead and three, setup/speed threats, walls and "
    "opponent win conditions, our sweep route and preserve member. "
    "Require a matrix-explicit processor for key threats where verified. "
    "A mere favorable rate, insurance flag, or nominal type resistance is "
    "NOT a proven processor nor a safe hard switch. "
    "Compare lead direct attack versus pivot versus hard switch, both priority "
    "moves, speed order, KO evidence, Mega/form changes and choice locks. "
    "Do not use an AI_UNVERIFIED cell as proof of KO, speed, safe switch or a "
    "guaranteed processor. Treat it as a conditional hypothesis only. "
    "Secondary lookup may clarify no more than two clearly flagged threats; "
    "absence of a lookup row is not proof of safety. "
    "Select exactly three registered members and one of them as lead with "
    "the highest plausible whole-three win probability. No SV/ZA inference."
)
