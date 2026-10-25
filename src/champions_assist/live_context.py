"""Bounded live context assembled only from four preloaded Champions masters.

The matrix is the primary source. Secondary lookup rows are never scanned for
all six opponents or promoted into certified processors/safe switches.
"""
from __future__ import annotations

WARNING_MARKERS = ("先制", "積み", "メガ", "天候", "壁", "高速", "優先度")
# Risk S (generic setup) alone is ubiquitous: not a sufficient trigger.
CRITICAL_FLAGS = frozenset("PGWF")


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
    ranked = []
    for index,(opponent, text) in enumerate(zip(opponents, matrix_rows)):
        columns = text.split("|")
        if len(columns) != 14 or columns[0] != opponent:
            continue
        warning, risk = columns[10], columns[13]
        lookup = assets.lookup_index.get(opponent)
        if lookup is None:
            continue
        # Prioritize potentially game-ending setup / priority / ability
        # threats, not whatever happened to appear first in the screenshot.
        # These are lookup retrieval hints, NEVER strategic move decisions.
        score = (3*any(x in warning for x in ("先制","優先度"))
                 + 3*any(x in warning for x in ("積み","からをやぶる","りゅうのまい"))
                 + 2*any(x in warning for x in ("天候","高速","壁"))
                 + int("メガ" in warning)
                 + 2*any(flag in risk for flag in ("P","G"))
                 + any(flag in risk for flag in ("W","F")))
        try:
            base_speed = int(columns[2])
        except (ValueError,TypeError):
            base_speed = 0
        if base_speed >= 120:
            score += 1
        if score:
            ranked.append((-score,index,lookup))
    ranked.sort(key=lambda r:(r[0],r[1]))
    return [value for _,_,value in ranked[:limit]]


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
