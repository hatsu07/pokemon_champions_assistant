"""Cross-build delta reuse for validated, *unverified* v2 AI hypotheses.

Executed only on an explicit off-match "make matrix" action. Old evaluation
cannot be re-used for changed own moves, held item, AP, regulation, opponent
form, or changed Champions master evidence.
"""
from __future__ import annotations
from pathlib import Path
from hashlib import sha256
import json

from .app_config import ConfigurationError
from .team_matchup_v2 import (
    SCHEMA_V2, evidence_key, eligible_v2_request, validate_v2_reply)
from .team_ai_analysis import save_proposals


def reuse_unchanged_matchups(catalog_path: str | Path,
                             assets_root: str | Path) -> dict:
    target_path = Path(catalog_path)
    assets_root = Path(assets_root)
    current_raw = target_path.read_bytes()
    current = json.loads(current_raw)
    current_sha = sha256(current_raw).hexdigest()
    index = {(c["opponent"],c["member_signature"],evidence_key(c,current)):c
             for c in current["cells"]
             if c.get("evaluation")=="UNKNOWN" and not c.get("missing_source")}
    by_cell={(c["opponent"],c["slot"],c["member_signature"]):c
             for c in index.values()}
    known = set()
    for file in target_path.parent.glob("ai-proposal-*.json"):
        try:
            stored=json.loads(file.read_text(encoding="utf-8"))
            if (stored.get("schema")==SCHEMA_V2
                    and stored.get("catalog_sha256")==current_sha
                    and stored.get("status")=="AI_PROPOSAL_UNVERIFIED"
                    and stored.get("live_approved") is False):
                known.update((r["opponent"],r["slot"],r["member_signature"])
                             for r in stored.get("proposals",[]))
        except (OSError,ValueError,TypeError,KeyError):
            continue
    reused={}
    directories=0
    scanned=0
    for old_path in sorted(assets_root.glob("*/*/matchup-evidence.json")):
        if old_path == target_path:
            continue
        directories+=1
        if directories > 150:
            break
        try:
            if old_path.stat().st_size > 5_000_000:
                continue
            old_raw=old_path.read_bytes()
            old=json.loads(old_raw)
            if (old.get("schema")!="champions-matchup-evidence-v1"
                    or old.get("master_sha256")!=current.get("master_sha256")
                    or old.get("regulation")!=current.get("regulation")):
                continue
            original_sha=sha256(old_raw).hexdigest()
            old_cells={(c["opponent"],c["slot"],c["member_signature"]):c
                       for c in old["cells"]}
            for file in sorted(old_path.parent.glob("ai-proposal-*.json")):
                scanned+=1
                if scanned > 1000:
                    break
                if file.stat().st_size > 2_000_000:
                    continue
                data=json.loads(file.read_text(encoding="utf-8"))
                if (data.get("schema")!=SCHEMA_V2
                        or data.get("catalog_sha256")!=original_sha
                        or data.get("status")!="AI_PROPOSAL_UNVERIFIED"
                        or data.get("live_approved") is not False
                        or data.get("processor_certified") is not False
                        or data.get("safe_switch_certified") is not False):
                    continue
                rows=data.get("proposals",[])
                evidence=[]
                for r in rows:
                    evidence.append(old_cells[(r["opponent"],r["slot"],r["member_signature"])])
                source_request={
                    "catalog_sha256":original_sha,
                    "cells":[{"opponent":c["opponent"],"slot":c["slot"],
                              "member_signature":c["member_signature"]} for c in evidence],
                    "own_members":[{"slot":c["slot"],"moves":c["registered_moves"],
                                    "traits":c["own_species"]} for c in evidence],
                    "opponents":[{"name":c["opponent"],"traits":c["opponent_species"]}
                                 for c in evidence],
                    "regulation":old["regulation"],
                    "member_signatures":old["member_signatures"]}
                validate_v2_reply(source_request,{
                    "schema":SCHEMA_V2,"catalog_sha256":original_sha,"proposals":rows})
                for c,r in zip(evidence,rows):
                    key=(c["opponent"],c["member_signature"],evidence_key(c,old))
                    target=index.get(key)
                    if target is None:
                        continue
                    target_key=(target["opponent"],target["slot"],target["member_signature"])
                    if target_key in known or target_key in reused:
                        continue
                    # Slot changes are allowed only for identical, signed
                    # members and byte-identical per-cell Champions evidence.
                    reused[target_key]={**r,"slot":target["slot"]}
                if scanned > 1000:
                    break
        except (OSError,ValueError,TypeError,KeyError,ConfigurationError):
            continue
        if scanned > 1000:
            break
    if not reused:
        return {"reused_cells":0,"reuse_status":"NONE"}
    ordered=sorted(reused.values(),key=lambda r:(r["opponent"],r["slot"]))
    for start in range(0,len(ordered),36):
        rows=ordered[start:start+36]
        request={
            "catalog_sha256":current_sha,
            "cells":[{"opponent":r["opponent"],"slot":r["slot"],
                      "member_signature":r["member_signature"]} for r in rows],
            "own_members":[{"slot":c["slot"],"moves":c["registered_moves"],
                            "traits":c["own_species"]}
                           for r in rows for c in (by_cell[(r["opponent"],r["slot"],
                                                             r["member_signature"])],)],
            "opponents":[{"name":c["opponent"],"traits":c["opponent_species"]}
                         for r in rows for c in (by_cell[(r["opponent"],r["slot"],
                                                           r["member_signature"])],)],
            "regulation":current["regulation"],
            "member_signatures":current["member_signatures"]}
        validated=validate_v2_reply(request,{
            "schema":SCHEMA_V2,"catalog_sha256":current_sha,
            "proposals":rows})
        payload=sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:20]
        destination=target_path.parent/("ai-proposal-reuse-"+payload+".json")
        if not destination.exists():
            save_proposals(destination,target_path,validated)
    return {"reused_cells":len(reused),"reuse_status":"EVIDENCE_MATCHED_UNVERIFIED"}
