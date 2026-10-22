"""Responses API reasoning effort selection; defaults stay model-defined."""
from __future__ import annotations

EFFORT_OPTIONS = (
    ("自動（モデルの既定値）", "auto"),
    ("推論なし（対応モデルのみ）", "none"),
    ("最小（対応モデルのみ）", "minimal"),
    ("軽量・高速", "low"),
    ("標準・バランス", "medium"),
    ("高精度", "high"),
    ("最大級", "xhigh"),
    ("最深（対応モデルのみ）", "max"),
)
VALID_EFFORTS = frozenset(value for _, value in EFFORT_OPTIONS)


def reasoning_request_kwargs(effort: str = "auto") -> dict:
    """Return API kwargs without silently downgrading unsupported model values."""
    if not isinstance(effort, str) or effort not in VALID_EFFORTS:
        raise ValueError("invalid reasoning effort")
    return {} if effort == "auto" else {"reasoning": {"effort": effort}}
