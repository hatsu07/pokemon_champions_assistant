"""Runtime-only OpenAI settings.

These values deliberately use a ``CHAMPIONS_ASSIST_`` namespace and never read
Codex/IDE configuration.  The API key is kept out of ``repr`` and is never
accepted by persistence or logging APIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os


class SettingsError(ValueError):
    pass


def _bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    if value.lower() in {"1", "true", "yes", "on"}:
        return True
    if value.lower() in {"0", "false", "no", "off"}:
        return False
    raise SettingsError("boolean environment setting must be 0/1 or true/false")


def _integer(value: str | None, *, default: int, name: str) -> int:
    try:
        parsed = default if value is None else int(value)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise SettingsError(f"{name} cannot be negative")
    return parsed


def _decimal(value: str | None, *, default: float, name: str) -> float:
    try:
        parsed = default if value is None else float(value)
    except ValueError as exc:
        raise SettingsError(f"{name} must be numeric") from exc
    if parsed < 0:
        raise SettingsError(f"{name} cannot be negative")
    return parsed


@dataclass(frozen=True)
class ModelSettings:
    """Spend-safe settings for a runtime API client, not the Codex model."""

    api_enabled: bool = False
    model: str = "gpt-4.1-mini"
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 8.0
    safety_margin_ms: int = 1_500
    max_requests_per_match: int = 40
    max_requests_per_day: int = 200
    spend_cap_usd: float = 0.0
    estimated_request_usd: float = 0.0

    @classmethod
    def from_environment(cls, environ: dict[str, str] | None = None) -> "ModelSettings":
        env = os.environ if environ is None else environ
        from .api_credentials import get_api_key
        key = get_api_key() if environ is None else get_api_key(environ=environ)
        return cls(
            api_enabled=_bool(env.get("CHAMPIONS_ASSIST_ENABLE_OPENAI_API")),
            model=env.get("CHAMPIONS_ASSIST_OPENAI_MODEL", "gpt-4.1-mini"),
            api_key=key,
            timeout_seconds=_decimal(env.get("CHAMPIONS_ASSIST_API_TIMEOUT_SECONDS"), default=8.0, name="CHAMPIONS_ASSIST_API_TIMEOUT_SECONDS"),
            safety_margin_ms=_integer(env.get("CHAMPIONS_ASSIST_API_SAFETY_MARGIN_MS"), default=1_500, name="CHAMPIONS_ASSIST_API_SAFETY_MARGIN_MS"),
            max_requests_per_match=_integer(env.get("CHAMPIONS_ASSIST_API_MAX_REQUESTS_PER_MATCH"), default=40, name="CHAMPIONS_ASSIST_API_MAX_REQUESTS_PER_MATCH"),
            max_requests_per_day=_integer(env.get("CHAMPIONS_ASSIST_API_MAX_REQUESTS_PER_DAY"), default=200, name="CHAMPIONS_ASSIST_API_MAX_REQUESTS_PER_DAY"),
            spend_cap_usd=_decimal(env.get("CHAMPIONS_ASSIST_API_SPEND_CAP_USD"), default=0.0, name="CHAMPIONS_ASSIST_API_SPEND_CAP_USD"),
            estimated_request_usd=_decimal(env.get("CHAMPIONS_ASSIST_API_ESTIMATED_REQUEST_USD"), default=0.0, name="CHAMPIONS_ASSIST_API_ESTIMATED_REQUEST_USD"),
        )

    def real_transport_block_reason(self) -> str | None:
        """Fail closed until both explicit consent and a bounded budget exist."""
        if not self.api_enabled:
            return "EXTERNAL_API_DISABLED"
        if not self.api_key:
            return "OPENAI_API_KEY_MISSING"
        if not self.model:
            return "OPENAI_MODEL_MISSING"
        if self.spend_cap_usd <= 0:
            return "API_SPEND_CAP_REQUIRED"
        if self.estimated_request_usd <= 0:
            return "API_COST_ESTIMATE_REQUIRED"
        if self.max_requests_per_match <= 0 or self.max_requests_per_day <= 0:
            return "API_REQUEST_CAP_REQUIRED"
        return None
