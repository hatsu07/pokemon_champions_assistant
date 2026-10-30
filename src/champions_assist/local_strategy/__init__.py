"""Experimental Issue #140 local foundation; never a production tactical path."""
from .models import (
    ActionKind, CandidateAction, CapabilityStatus, DecisionWindow,
    FrozenBattleState, Hypothesis, LegalStatus, ObservedFact,
    SimulationResult, WindowKind,
)
from .legal_actions import generate_candidates, revalidate_candidate
from .decision_service import accept_result, DecisionOutcome

__all__ = [
    "ActionKind", "CandidateAction", "CapabilityStatus", "DecisionWindow",
    "FrozenBattleState", "Hypothesis", "LegalStatus", "ObservedFact",
    "SimulationResult", "WindowKind", "generate_candidates",
    "revalidate_candidate", "accept_result", "DecisionOutcome",
]
