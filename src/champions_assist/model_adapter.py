"""Mock-first OpenAI Responses adapter with v2.3 safety gates.

This module never chooses a move, switch target, team, or review conclusion.  It
only builds a frozen request, sends it through a transport, and permits display
when the original v2.3 contract, freshness, deadline, and legal-action checks
all still hold.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any, Callable, Iterable, Protocol

from .contract_validation import ValidationResult, validate_response_for_current_request, validate_wire_request
from .preload import FrozenMatchAssets
from .prompts import ModelPrompt, PromptBuilder
from .settings import ModelSettings
from .store import BattleStore
from .structured_schema import responses_text_format_for_phase


class TransportFailure(RuntimeError):
    """A transport failure that intentionally contains no request secret."""


@dataclass(frozen=True)
class ProviderResult:
    response: dict[str, Any]
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    provider_response_id: str | None = None


@dataclass(frozen=True)
class TransportRequest:
    payload: dict[str, Any]
    model: str
    prompt: ModelPrompt
    text_format: dict[str, Any]
    timeout_seconds: float


class ModelTransport(Protocol):
    name: str
    external: bool

    def execute(self, request: TransportRequest) -> ProviderResult: ...


Fixture = ProviderResult | dict[str, Any] | Exception | Callable[[TransportRequest], ProviderResult | dict[str, Any]]


class MockResponsesTransport:
    """Scripted no-network transport for tests, simulation, and default operation."""

    name = "mock"
    external = False

    def __init__(self, responses: Iterable[Fixture]) -> None:
        self._responses = deque(responses)
        self.requests: list[TransportRequest] = []

    def execute(self, request: TransportRequest) -> ProviderResult:
        self.requests.append(request)
        if not self._responses:
            raise TransportFailure("mock response queue exhausted")
        fixture = self._responses.popleft()
        if callable(fixture):
            fixture = fixture(request)
        if isinstance(fixture, Exception):
            raise fixture
        if isinstance(fixture, ProviderResult):
            return fixture
        return ProviderResult(response=fixture)


class OpenAIResponsesTransport:
    """Explicitly opt-in Responses API transport; SDK retries are disabled."""

    name = "openai_responses"
    external = True

    def __init__(self, settings: ModelSettings) -> None:
        self._settings = settings

    def execute(self, request: TransportRequest) -> ProviderResult:
        if not self._settings.api_key:
            raise TransportFailure("runtime API key is not configured")
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=self._settings.api_key,
                max_retries=0,
                timeout=request.timeout_seconds,
            )
            response = client.responses.create(
                model=request.model,
                instructions=request.prompt.system,
                input=request.prompt.user,
                text={"format": request.text_format},
                store=False,
            )
            output_text = getattr(response, "output_text", None)
            if not isinstance(output_text, str) or not output_text:
                raise TransportFailure("Responses API returned no structured output")
            decoded = json.loads(output_text)
            if not isinstance(decoded, dict):
                raise TransportFailure("Responses API structured output is not an object")
            usage = getattr(response, "usage", None)
            return ProviderResult(
                response=decoded,
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None),
                provider_response_id=getattr(response, "id", None),
            )
        except TransportFailure:
            raise
        except Exception as exc:
            # Avoid serializing SDK exception strings because they can contain request details.
            if type(exc).__name__ in {"APITimeoutError", "TimeoutException"}:
                raise TimeoutError("OpenAI Responses API timed out") from exc
            raise TransportFailure(f"OpenAI Responses API failed: {type(exc).__name__}") from exc


class ModelOutcomeStatus(str, Enum):
    DISPLAYABLE = "DISPLAYABLE"
    BLOCKED = "BLOCKED"
    DUPLICATE_SUPPRESSED = "DUPLICATE_SUPPRESSED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    STALE = "STALE"
    REJECTED = "REJECTED"
    NO_DECISION = "NO_DECISION"


@dataclass(frozen=True)
class ModelOutcome:
    status: ModelOutcomeStatus
    reason: str
    response: dict[str, Any] | None = None
    sent: bool = False


def _utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _cost_to_microusd(value: float) -> int:
    return int(round(value * 1_000_000))


class ModelAdapter:
    """Validate, de-duplicate, budget, transport, and safely accept model work."""

    def __init__(
        self,
        *,
        store: BattleStore,
        transport: ModelTransport,
        settings: ModelSettings | None = None,
        prompt_builder: PromptBuilder | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._store = store
        self._transport = transport
        self._settings = settings or ModelSettings.from_environment()
        self._prompt_builder = prompt_builder or PromptBuilder()
        self._now_ms = now_ms or (lambda: 0)

    def _external_budget_reason(self, match_id: str) -> str | None:
        setting_reason = self._settings.real_transport_block_reason()
        if setting_reason is not None:
            return setting_reason
        totals = self._store.api_budget_totals(match_id, day_utc=_utc_day())
        estimate = _cost_to_microusd(self._settings.estimated_request_usd)
        cap = _cost_to_microusd(self._settings.spend_cap_usd)
        if totals["match_calls"] >= self._settings.max_requests_per_match:
            return "MATCH_API_REQUEST_CAP_EXCEEDED"
        if totals["day_calls"] >= self._settings.max_requests_per_day:
            return "DAILY_API_REQUEST_CAP_EXCEEDED"
        if totals["day_cost_microusd"] + estimate > cap:
            return "API_SPEND_CAP_EXCEEDED"
        return None

    @staticmethod
    def _current_matches_request(request: dict[str, Any], current: dict[str, Any]) -> bool:
        keys = ("match_id", "decision_window_id", "tactical_state_version", "allowed_action_digest")
        return bool(current.get("window_open", True)) and all(current.get(key) == request[key] for key in keys)

    def _record_terminal(
        self,
        request: dict[str, Any],
        *,
        disposition: str,
        reason: str,
        response: dict[str, Any] | None,
        provider_result: ProviderResult | None,
    ) -> None:
        self._store.record_api_response(
            request["match_id"],
            request_id=request["request_id"],
            transport=self._transport.name,
            disposition=disposition,
            reason=reason,
            response=response,
            provider_response_id=None if provider_result is None else provider_result.provider_response_id,
        )
        self._store.record_api_usage(
            request["match_id"],
            request_id=request["request_id"],
            transport=self._transport.name,
            model=self._settings.model,
            external=self._transport.external,
            input_tokens=None if provider_result is None else provider_result.input_tokens,
            output_tokens=None if provider_result is None else provider_result.output_tokens,
            total_tokens=None if provider_result is None else provider_result.total_tokens,
            estimated_cost_microusd=_cost_to_microusd(self._settings.estimated_request_usd) if self._transport.external else 0,
            outcome=disposition,
        )

    def decide(
        self,
        payload: dict[str, Any],
        assets: FrozenMatchAssets,
        *,
        current_supplier: Callable[[], dict[str, Any]],
        lookup_keys: Iterable[str] = (),
    ) -> ModelOutcome:
        """Perform at most one transport request for one immutable decision key."""
        request_validation = validate_wire_request(payload)
        if not request_validation.accepted:
            return ModelOutcome(ModelOutcomeStatus.BLOCKED, request_validation.reason)
        current_before = current_supplier()
        if not self._current_matches_request(payload, current_before):
            return ModelOutcome(ModelOutcomeStatus.STALE, "STALE_BEFORE_SEND")
        remaining_ms = payload["deadline_ms_remaining"]
        if remaining_ms <= self._settings.safety_margin_ms:
            return ModelOutcome(ModelOutcomeStatus.TIMEOUT, "DEADLINE_TOO_CLOSE")
        if self._transport.external:
            budget_reason = self._external_budget_reason(payload["match_id"])
            if budget_reason is not None:
                return ModelOutcome(ModelOutcomeStatus.BUDGET_EXCEEDED, budget_reason)
        timeout_seconds = min(
            self._settings.timeout_seconds,
            max(0.0, (remaining_ms - self._settings.safety_margin_ms) / 1000),
        )
        if timeout_seconds <= 0:
            return ModelOutcome(ModelOutcomeStatus.TIMEOUT, "DEADLINE_TOO_CLOSE")
        try:
            prompt = self._prompt_builder.build(payload, assets, lookup_keys=lookup_keys)
        except ValueError as exc:
            return ModelOutcome(ModelOutcomeStatus.BLOCKED, f"PROMPT_CONTEXT_INVALID:{exc}")
        reserved = self._store.reserve_model_request(
            payload["match_id"],
            decision_window_id=payload["decision_window_id"],
            tactical_state_version=payload["tactical_state_version"],
            allowed_action_digest=payload["allowed_action_digest"],
            phase=payload["phase"],
            request_snapshot_id=f"{payload['request_id']}:request",
            request_payload=payload,
        )
        if not reserved:
            return ModelOutcome(ModelOutcomeStatus.DUPLICATE_SUPPRESSED, "DUPLICATE_REQUEST_KEY")
        sent_at_ms = self._now_ms()
        transport_request = TransportRequest(
            payload=payload,
            model=self._settings.model,
            prompt=prompt,
            text_format=responses_text_format_for_phase(payload["phase"]),
            timeout_seconds=timeout_seconds,
        )
        try:
            provider_result = self._transport.execute(transport_request)
        except TimeoutError:
            self._record_terminal(payload, disposition="TIMEOUT", reason="TRANSPORT_TIMEOUT", response=None, provider_result=None)
            return ModelOutcome(ModelOutcomeStatus.TIMEOUT, "TRANSPORT_TIMEOUT", sent=True)
        except Exception as exc:
            self._record_terminal(payload, disposition="UNAVAILABLE", reason=f"TRANSPORT_FAILURE:{type(exc).__name__}", response=None, provider_result=None)
            return ModelOutcome(ModelOutcomeStatus.UNAVAILABLE, "TRANSPORT_FAILURE", sent=True)
        if self._now_ms() - sent_at_ms >= remaining_ms:
            self._record_terminal(payload, disposition="TIMEOUT", reason="RESPONSE_AFTER_DEADLINE", response=provider_result.response, provider_result=provider_result)
            return ModelOutcome(ModelOutcomeStatus.TIMEOUT, "RESPONSE_AFTER_DEADLINE", sent=True)
        current_after = current_supplier()
        validation = validate_response_for_current_request(payload, provider_result.response, current=current_after)
        if not validation.accepted:
            status = ModelOutcomeStatus.NO_DECISION if validation.reason == "NO_DECISION" else (
                ModelOutcomeStatus.STALE if validation.reason == "STALE_DECISION" else ModelOutcomeStatus.REJECTED
            )
            self._record_terminal(payload, disposition=status.value, reason=validation.reason, response=provider_result.response, provider_result=provider_result)
            return ModelOutcome(status, validation.reason, sent=True)
        self._record_terminal(payload, disposition="DISPLAYABLE", reason="OK", response=provider_result.response, provider_result=provider_result)
        return ModelOutcome(ModelOutcomeStatus.DISPLAYABLE, "OK", response=provider_result.response, sent=True)
