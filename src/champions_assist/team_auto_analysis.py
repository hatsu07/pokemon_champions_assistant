"""Bounded, resumable off-match ChatGPT matchup candidate analysis.

One explicit confirmation starts successive batches; this never certifies a
processor, safe switch, actual damage or game matchup grade.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable
import uuid

from .app_config import ConfigurationError
from .team_ai_analysis import build_request, analyze_request, save_proposals, validate_reply, SCHEMA

MAX_BATCHES = 180


def run_automatic_analysis(
    candidate_path: str | Path,
    *,
    model: str,
    access_token: str,
    reasoning_effort: str = "auto",
    max_batches: int = MAX_BATCHES,
    interrupted: Callable[[], bool] | None = None,
    progress: Callable[[dict], None] | None = None,
    analyze: Callable | None = None,
    priority_opponents: list[str] | None = None,
) -> dict:
    """Resume from existing proposal cache; stop cleanly at bounded limits.

    Each model response is strictly revalidated and saved atomically, never
    promoted into a LIVE matchup grade. Caller must block invocation in battle.
    """
    if type(max_batches) is not int or not 1 <= max_batches <= MAX_BATCHES:
        raise ConfigurationError("自動解析の一括上限は1～180です")
    if not model or not access_token:
        raise ConfigurationError("接続済みのChatGPTモデルが必要です")
    candidate_path = Path(candidate_path)
    analyze = analyze or analyze_request
    interrupted = interrupted or (lambda: False)
    progress = progress or (lambda _value: None)
    count = batches = 0
    while batches < max_batches:
        if interrupted():
            return {"status": "INTERRUPTED", "count": count, "batches": batches}
        request = build_request(candidate_path, priority_opponents=priority_opponents)
        if not request["cells"]:
            return {"status": "COMPLETE", "count": count, "batches": batches}
        result = analyze(request, model=model, access_token=access_token,
                         reasoning_effort=reasoning_effort)
        # Never trust a mocked/future adapter to return an already validated
        # result. Enforce the existing strict schema and exact matchup keys.
        if (not isinstance(result, dict) or result.get("status") != "AI_PROPOSAL_UNVERIFIED"
                or result.get("live_approved") is not False
                or result.get("processor_certified") is not False
                or result.get("safe_switch_certified") is not False):
            raise ConfigurationError("AIの返答は未検証の候補のみ受け付けます")
        validated = validate_reply(request, {
            "schema":result.get("schema"), "catalog_sha256":result.get("catalog_sha256"),
            "proposals":result.get("proposals"),
        })
        filename = "ai-proposal-" + uuid.uuid4().hex + ".json"
        save_proposals(candidate_path.parent / filename, candidate_path, validated)
        batches += 1
        count += len(validated["proposals"])
        progress({"count": count, "batches": batches,
                  "last_batch_count": len(validated["proposals"]),
                  "status": "IN_PROGRESS"})
    return {"status": "BATCH_LIMIT", "count": count, "batches": batches}
