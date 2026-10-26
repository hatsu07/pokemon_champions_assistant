"""Compact, strictly bounded *unverified* Champions matchup analysis v2.

The AI does not calculate damage or certify processors/safe hard switches.
Facts from the fixed masters are validated locally; model judgements stay
explicitly provisional.  Off-match only; never invoked during match/PREP.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json

from .app_config import ConfigurationError

SCHEMA_V2 = "champions-ai-matchup-proposals-v2"
LIMIT_V2 = 48
SPEED = ("UNKNOWN", "BASE_FASTER", "BASE_SLOWER", "BASE_TIE")
RISK = ("UNKNOWN", "POSSIBLE", "NOT_ESTABLISHED")
ROLE = ("UNKNOWN", "CONDITIONAL_UNVERIFIED", "NOT_ESTABLISHED")
DMG = ("UNKNOWN", "NOT_CALCULATED")
FIELDS = ("opponent", "slot", "member_signature", "attack_move",
          "base_speed", "damage", "priority_risk", "setup_risk",
          "direct_processor", "safe_switch", "note", "unknowns")
RESPONSE_SCHEMA_V2 = {
    "type":"object", "additionalProperties":False,
    "properties":{
        "schema":{"type":"string"}, "catalog_sha256":{"type":"string"},
        "proposals":{"type":"array", "items":{
            "type":"object", "additionalProperties":False,
            "properties":{key:{"type":"integer"} if key=="slot" else {"type":"string"}
                          for key in FIELDS},
            "required":list(FIELDS)}},
    },
    "required":["schema","catalog_sha256","proposals"],
}


def eligible_v2_request(catalog_path: str | Path, *, max_cells: int = 24,
                        priority_opponents: list[str] | None = None) -> dict:
    """One reusable own-team context plus grouped opponents (no 6x repetition).

    Saved v1 prose does NOT count as a structured evaluation. A changed catalog
    SHA invalidates the saved proposals, not the underlying master data.
    """
    if type(max_cells) is not int or not 1 <= max_cells <= LIMIT_V2:
        raise ConfigurationError("構造化AI解析は1～48対面までです")
    from .species_name_aliases import selection_base_name
    path = Path(catalog_path)
    raw = path.read_bytes()
    catalog = json.loads(raw)
    if catalog.get("schema") != "champions-matchup-evidence-v1":
        raise ConfigurationError("構築別根拠カタログの形式が不正です")
    digest = sha256(raw).hexdigest()
    covered = set()
    # SQLite is the authoritative evaluation ledger when available. A JSON
    # compatibility mirror may be missing after a crash between the DB commit
    # and legacy export; never analyze that already-committed matchup twice.
    if (path.name=="matchup-evidence.json"
            and path.parent.parent.parent.name=="live-team-assets"):
        root=path.parents[3]
        if (root/"matchup-learning.sqlite3").is_file():
            from .sqlite_primary import ChampionsPrimaryData
            for row in ChampionsPrimaryData(root).proposal_rows(path,only_v2=True):
                covered.add((row["opponent"],row["slot"],row["member_signature"]))
    for saved_file in sorted(path.parent.glob("ai-proposal-*.json")):
        try:
            if saved_file.stat().st_size > 2_000_000:
                continue
            saved = json.loads(saved_file.read_text(encoding="utf-8"))
            if (saved.get("schema") == SCHEMA_V2
                    and saved.get("catalog_sha256") == digest
                    and saved.get("status") == "AI_PROPOSAL_UNVERIFIED"
                    and saved.get("live_approved") is False
                    and saved.get("processor_certified") is False
                    and saved.get("safe_switch_certified") is False):
                covered.update((r["opponent"],r["slot"],r["member_signature"])
                               for r in saved.get("proposals",[])
                               if isinstance(r,dict) and all(
                                   k in r for k in ("opponent","slot","member_signature")))
        except (OSError, ValueError, TypeError):
            continue
    priority = {selection_base_name(x):i for i,x in enumerate(priority_opponents or [])
                if isinstance(x,str) and x.strip()}
    candidates = [c for c in catalog["cells"]
                  if c.get("evaluation") == "UNKNOWN" and not c.get("missing_source")
                  and (c["opponent"],c["slot"],c["member_signature"]) not in covered]
    candidates.sort(key=lambda c:(priority.get(selection_base_name(c["opponent"]),
                                                len(priority)), c["opponent"],c["slot"]))
    chosen = candidates[:max_cells]
    opponent_groups = {}
    own_members = {}
    key_cells = []
    for c in chosen:
        opponent_groups.setdefault(c["opponent"],{
            "name":c["opponent"], "traits":c["opponent_species"],
            "priority_candidates":c["opponent_priority_candidates"],
            "high_power_candidates":c["opponent_potential_high_power_moves"]})
        own_members.setdefault(str(c["slot"]),{
            "slot":c["slot"], "name":c["own"], "signature":c["member_signature"],
            "traits":c["own_species"], "moves":c["registered_moves"],
            "build":c["registered_build"]})
        key_cells.append({"opponent":c["opponent"],"slot":c["slot"],
                          "member_signature":c["member_signature"]})
    return {
        "schema":SCHEMA_V2, "catalog_sha256":digest,
        "regulation":catalog["regulation"],
        "member_signatures":catalog["member_signatures"],
        "master_sha256":catalog.get("master_sha256",{}),
        "own_members":list(own_members.values()),
        "opponents":list(opponent_groups.values()),
        "cells":key_cells,
    }


def _validated_speed(row: dict, own: dict, opp: dict) -> bool:
    actual = row["base_speed"]
    if actual == "UNKNOWN":
        return True
    o = own.get("base_speed")
    p = opp.get("base_speed")
    if type(o) is not int or type(p) is not int:
        return False
    return actual == ("BASE_FASTER" if o > p else "BASE_SLOWER" if o < p else "BASE_TIE")


def validate_v2_reply(request: dict, reply: dict) -> dict:
    if (not isinstance(reply,dict) or reply.get("schema") != SCHEMA_V2
            or reply.get("catalog_sha256") != request["catalog_sha256"]):
        raise ConfigurationError("構造化AI評価のスキーマまたは根拠が一致しません")
    rows = reply.get("proposals")
    if not isinstance(rows,list) or len(rows) != len(request["cells"]):
        raise ConfigurationError("構造化AI評価の対面件数が一致しません")
    expected={(c["opponent"],c["slot"],c["member_signature"]) for c in request["cells"]}
    own={m["slot"]:m for m in request["own_members"]}
    opponents={m["name"]:m for m in request["opponents"]}
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row) != set(FIELDS):
            raise ConfigurationError("構造化AI評価の必須項目に不整合があります")
        key=(row["opponent"],row["slot"],row["member_signature"])
        if key not in expected or key in seen or type(row["slot"]) is not int:
            raise ConfigurationError("構造化AI評価の対面キーが不正です")
        seen.add(key)
        for f in FIELDS:
            if f != "slot" and (not isinstance(row[f],str)
                                or len(row[f]) > (180 if f in ("note","unknowns") else 160)):
                raise ConfigurationError("構造化AI評価に過大・不正な文字列があります")
        member=own[row["slot"]]
        valid_moves={x["name"] for x in member["moves"]}
        if (row["attack_move"] not in valid_moves | {"UNKNOWN"}
                or row["base_speed"] not in SPEED
                or row["damage"] not in DMG
                or row["priority_risk"] not in RISK
                or row["setup_risk"] not in RISK
                or row["direct_processor"] not in ROLE
                or row["safe_switch"] not in ROLE
                or not _validated_speed(row,member["traits"],opponents[row["opponent"]]["traits"])
                or not row["note"].strip() or not row["unknowns"].strip()
                or any("|" in row[f] or "\n" in row[f] for f in FIELDS if f!="slot")):
            raise ConfigurationError("未検証AI評価とChampions固定根拠が矛盾します")
    return {
        "schema":SCHEMA_V2,"catalog_sha256":request["catalog_sha256"],
        "regulation":request["regulation"],
        "member_signatures":request["member_signatures"],
        "status":"AI_PROPOSAL_UNVERIFIED","live_approved":False,
        "processor_certified":False,"safe_switch_certified":False,
        "proposals":rows,
    }


def compact_v2_hint(row: dict) -> str:
    """Short tactical tags without manufacturing a verified LIVE cell."""
    speed={"UNKNOWN":"?", "BASE_FASTER":"基速", "BASE_SLOWER":"基遅",
           "BASE_TIE":"基同"}[row["base_speed"]]
    flags=[]
    if row["priority_risk"]=="POSSIBLE":flags.append("先")
    if row["setup_risk"]=="POSSIBLE":flags.append("積")
    processor="仮" if row["direct_processor"]=="CONDITIONAL_UNVERIFIED" else "?"
    switch="仮" if row["safe_switch"]=="CONDITIONAL_UNVERIFIED" else "?"
    attack=row["attack_move"] if row["attack_move"]!="UNKNOWN" else "?"
    # Conditional hints: even '仮' is explicitly unverified, never certified.
    return f"技={attack},S={speed},危={''.join(flags) or '?'},処={processor},受={switch}"


def evidence_key(cell: dict, catalog: dict) -> str:
    """Reject stale mechanics, regulation, members, moves and opponent forms."""
    fields=("opponent","member_signature","own","own_species","opponent_species",
            "registered_moves","registered_build","opponent_priority_candidates",
            "opponent_potential_high_power_moves","missing_source")
    data={k:cell.get(k) for k in fields}
    data["regulation"]=catalog.get("regulation")
    data["master_sha256"]=catalog.get("master_sha256")
    return sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode("utf-8")).hexdigest()
