"""Explicit scripted mock provider for offline Phase 2 simulations and tests."""

from __future__ import annotations

from collections import deque
from typing import Iterable

from .fsm import DecisionRequest, MockDecision


class ScriptedMockDecisionProvider:
    """Returns caller-supplied fixture responses in order; never selects an action."""

    def __init__(self, responses: Iterable[MockDecision]) -> None:
        self._responses = deque(responses)
        self.requests: list[DecisionRequest] = []

    def decide(self, request: DecisionRequest) -> MockDecision:
        self.requests.append(request)
        if not self._responses:
            raise RuntimeError("no scripted mock response remains")
        return self._responses.popleft()
