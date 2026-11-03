"""Preloaded selection -> conditional PREP in ONE six-row matrix lookup.

Ordinal selection and registered-action draft are NOT a game recommendation.
Both remain unsupported for final KO, damage, opponent policy and safe switch.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..preload import FrozenMatchAssets
from .conditional_prep_builder import (
    ConditionalPrepDraft, build_conditional_prep_draft,
)
from .selector import LocalSelection, rank_local_selection


@dataclass(frozen=True)
class ConditionalPreviewPlan:
    status: str
    reason: str
    selection: LocalSelection | None = None
    prep: ConditionalPrepDraft | None = None
    ranking_status: str = "NO_RANKING"
    ready_for_live: bool = False


def plan_conditional_preview(
    assets: FrozenMatchAssets,
    opponents: tuple[str, str, str, str, str, str],
) -> ConditionalPreviewPlan:
    """Propose 3 + lead first; then 18 registered actions from cached six.

    Every source is already match-frozen in memory. Never open a file during
    selection, re-query six opponent rows for a second player, or infer a
    certain KO from a matrix ordinal.
    """
    selected = rank_local_selection(assets, opponents)
    if selected.status != "MODEL_PROVISIONAL":
        return ConditionalPreviewPlan("BLOCKED", selected.reason,
                                      selection=selected)
    draft = build_conditional_prep_draft(
        assets, tuple(selected.selected_three), selected.lead,
        tuple(opponents), cached_matrix_rows_six=selected.matrix_rows_six)
    if draft.status != "CONDITIONAL_18_DRAFT":
        return ConditionalPreviewPlan("BLOCKED", draft.reason,
                                      selection=selected, prep=draft)
    return ConditionalPreviewPlan(
        "CONDITIONAL_PREVIEW_ONLY",
        "SELECTION_AND_18_REGISTERED_ACTIONS_NOT_CERTIFIED_GAME_TACTICS",
        selected, draft)
