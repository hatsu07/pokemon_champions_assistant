"""Immutable effect-rule catalog access for observer confirmation bookkeeping.

The catalog can label a possible activation with its Champions rule ID.  It
does not calculate or assert an effect: every activation still needs screen
evidence and a configured confirmation rule.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class EffectRule:
    rule_id: str
    source_type: str
    source_key: str
    verification_status: str
    confirmation_required: tuple[str, ...]


class FrozenEffectCatalog:
    """In-memory-only rules passed to a live observer; it never rereads a path."""

    def __init__(self, rules: Iterable[EffectRule]) -> None:
        self._by_rule_id = {rule.rule_id: rule for rule in rules}
        self._by_source = {(rule.source_type, rule.source_key): rule for rule in rules}

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "FrozenEffectCatalog":
        """Load before a match/calibration session; do not call this in live processing."""
        rules: list[EffectRule] = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record: dict[str, Any] = json.loads(line)
            rules.append(
                EffectRule(
                    rule_id=str(record["rule_id"]),
                    source_type=str(record["source_type"]),
                    source_key=str(record["source_key"]),
                    verification_status=str(record["verification_status"]),
                    confirmation_required=tuple(str(item) for item in record.get("confirmation_required", [])),
                )
            )
        return cls(rules)

    def rule_for(self, source_type: str, source_key: str) -> EffectRule | None:
        return self._by_source.get((source_type, source_key))

    def by_rule_id(self, rule_id: str) -> EffectRule | None:
        return self._by_rule_id.get(rule_id)
