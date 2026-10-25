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
    structured: bool = False,
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
    if structured:
        return _run_structured(
            candidate_path,model=model,access_token=access_token,
            reasoning_effort=reasoning_effort,max_batches=max_batches,
            interrupted=interrupted,progress=progress,analyze=analyze,
            priority_opponents=priority_opponents)
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


def _run_structured(candidate_path: Path, *, model: str, access_token: str,
                    reasoning_effort: str, max_batches: int, interrupted,
                    progress, analyze, priority_opponents: list[str] | None) -> dict:
    """Adaptive 24 -> 36-cell batches; rejected oversized replies halve size.

    Count attempted requests against max_batches, never retry a broken 6-cell
    batch forever. Each successful batch is saved before moving on, so an
    interrupted or failed analysis resumes without losing validated v2 cells.
    """
    import json
    from .team_matchup_v2 import eligible_v2_request, validate_v2_reply
    count = attempts = successful = failures = 0
    size = 24
    small_successes = 0
    catalog = json.loads(candidate_path.read_text(encoding="utf-8"))
    source_gaps=sum(bool(x.get("missing_source")) and x.get("evaluation")=="UNKNOWN"
                    for x in catalog["cells"])
    def result(status):
        return {"status":status,"count":count,"batches":successful,
                "attempts":attempts,"retries":failures,
                "source_gaps":source_gaps,"batch_size":size}
    while attempts < max_batches:
        if interrupted():
            return result("INTERRUPTED")
        request=eligible_v2_request(candidate_path,max_cells=size,
                                    priority_opponents=priority_opponents)
        if not request["cells"]:
            return result("SOURCE_GAPS" if source_gaps else "COMPLETE")
        attempts += 1
        try:
            reply=analyze(request,model=model,access_token=access_token,
                          reasoning_effort=reasoning_effort)
            if (not isinstance(reply,dict)
                    or reply.get("status")!="AI_PROPOSAL_UNVERIFIED"
                    or reply.get("live_approved") is not False
                    or reply.get("processor_certified") is not False
                    or reply.get("safe_switch_certified") is not False):
                raise ConfigurationError("構造化AI評価は未検証候補のみ受け付けます")
            validated=validate_v2_reply(request,reply)
        except (ConfigurationError, ValueError):
            # No result from this attempt can be trusted or persisted.
            failures += 1
            if size <= 6:
                raise
            size=max(6,size//2)
            small_successes=0
            progress({"count":count,"batches":successful,"attempts":attempts,
                      "status":"RETRY_SMALLER","batch_size":size})
            continue
        filename="ai-proposal-"+uuid.uuid4().hex+".json"
        save_proposals(candidate_path.parent/filename,candidate_path,validated)
        successful+=1
        count+=len(validated["proposals"])
        small_successes+=1
        progress({"count":count,"batches":successful,"attempts":attempts,
                  "last_batch_count":len(validated["proposals"]),
                  "status":"IN_PROGRESS","batch_size":size})
        if small_successes>=2 and size<36:
            size=min(36,size+12)
            small_successes=0
    return result("BATCH_LIMIT")
