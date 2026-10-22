"""Responses Structured Outputs schemas derived from, never replacing, v2.3.

The API accepts a strict subset of JSON Schema.  A separately transformed copy
is sent to Responses; every returned object is subsequently validated against
the complete local canonical v2.3 schema in :mod:`contract_validation`.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .contract_validation import canonical_schema, response_kind_for_phase


def _adapt(node: Any) -> Any:
    if isinstance(node, list):
        return [_adapt(item) for item in node]
    if not isinstance(node, dict):
        return node
    result = {key: _adapt(value) for key, value in node.items() if key not in {"$schema", "$id", "title", "default", "examples"}}
    node_type = result.get("type")
    if isinstance(node_type, list):
        rest = {key: value for key, value in result.items() if key != "type"}
        return {"anyOf": [{"type": item} for item in node_type], **rest}
    if node_type == "object" or "properties" in result:
        properties = result.get("properties", {})
        result["properties"] = properties
        # Strict Structured Outputs requires closed objects and all listed keys.
        result["additionalProperties"] = False
        result["required"] = list(properties)
    return result


def responses_schema_for_phase(phase: str) -> dict[str, Any]:
    """Return a detached strict-schema copy for the four response shapes."""
    kind = response_kind_for_phase(phase)
    return _adapt(deepcopy(canonical_schema(kind)))


def responses_text_format_for_phase(phase: str) -> dict[str, Any]:
    kind = response_kind_for_phase(phase)
    return {
        "type": "json_schema",
        "name": f"champions_{kind}_v230",
        "strict": True,
        "schema": responses_schema_for_phase(phase),
    }
