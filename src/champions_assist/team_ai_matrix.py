"""Build a team-scoped *provisional* LIVE matrix from offline ChatGPT proposals.

This is an off-match publication step. It never approves KO claims, safe hard
switches, or processor roles. It modifies only generated private matrix.txt,
not the fixed masters, team, gate, or 60-second lookup.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from .app_config import ConfigurationError, atomic_write_text

TAG = "AI_UNVERIFIED:"
CATALOG_SCHEMA = "champions-matchup-evidence-v1"
PROPOSAL_SCHEMA = "champions-ai-matchup-proposals-v1"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_text(value: object, limit: int = 72) -> str:
    if not isinstance(value, str):
        return ""
    # Proposals are untrusted text, not instructions or verified game rules.
    clean = "".join(ch for ch in value if ch.isprintable() and ch not in "|\r\n<>")
    return " ".join(clean.split())[:limit]


def proposal_notes(catalog_path: Path) -> tuple[dict[str, dict[int, str]], int]:
    """Prefer validated v2 structured hints, fallback to previous v1 prose.

    Local JSON can be modified; validate each row again against the exact
    evidence catalog before exposing it in a match-specific matrix.
    """
    from .team_matchup_v2 import (
        SCHEMA_V2, FIELDS, validate_v2_reply, compact_v2_hint)
    catalog_path = Path(catalog_path)
    raw = catalog_path.read_bytes()
    catalog = json.loads(raw)
    if catalog.get("schema") != CATALOG_SCHEMA:
        raise ConfigurationError("Champions対面根拠が不正です")
    digest = sha256(raw).hexdigest()
    source = {(c["opponent"],c["slot"],c["member_signature"]):c
              for c in catalog["cells"]}
    notes: dict[str, dict[int, str]] = {}
    versions: dict[tuple[str,int],int] = {}
    for path in sorted(catalog_path.parent.glob("ai-proposal-*.json")):
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            data = _load(path)
            ver = 2 if data.get("schema") == SCHEMA_V2 else 1
            if (data.get("schema") not in (PROPOSAL_SCHEMA,SCHEMA_V2)
                    or data.get("catalog_sha256") != digest
                    or data.get("status") != "AI_PROPOSAL_UNVERIFIED"
                    or data.get("live_approved") is not False
                    or data.get("processor_certified") is not False
                    or data.get("safe_switch_certified") is not False
                    or not isinstance(data.get("proposals"), list)
                    or not data["proposals"]):
                continue
            rows = data["proposals"]
            keys = []
            for r in rows:
                key=(r["opponent"],r["slot"],r["member_signature"])
                if key not in source or key in keys:
                    raise ValueError("matchup key missing or duplicated")
                keys.append(key)
            if ver == 2:
                selected=[source[k] for k in keys]
                request={
                    "catalog_sha256":digest,
                    "cells":[{"opponent":c["opponent"],"slot":c["slot"],
                              "member_signature":c["member_signature"]}
                             for c in selected],
                    "own_members":[{"slot":c["slot"],"moves":c["registered_moves"],
                                    "traits":c["own_species"]}
                                   for c in selected],
                    "opponents":[{"name":c["opponent"],"traits":c["opponent_species"]}
                                 for c in selected],
                    "regulation":catalog["regulation"],
                    "member_signatures":catalog["member_signatures"]}
                # Reject edited files that bypassed fixed-master constraints.
                validate_v2_reply(request, {
                    "schema":SCHEMA_V2,"catalog_sha256":digest,
                    "proposals":rows})
                selected_notes=[compact_v2_hint(r) for r in rows]
            else:
                if any(not isinstance(r,dict)
                       or any(not isinstance(r.get(k),str) or not r[k].strip()
                              for k in ("assessment","risks","unknowns"))
                       for r in rows):
                    continue
                selected_notes=[
                    _safe_text(r["assessment"],52) +
                    "/注意:" + _safe_text(r["risks"],20) for r in rows]
            for key,note in zip(keys,selected_notes):
                opponent,slot,_signature=key
                current=(opponent,slot)
                if versions.get(current,0) > ver:
                    continue
                versions[current]=ver
                notes.setdefault(opponent,{})[slot]=note
        except (OSError,ValueError,UnicodeError,TypeError,KeyError,ConfigurationError):
            continue
    return notes, len(versions)


def publish_ai_matrix(*, matrix_path: str | Path,
                      manifest_path: str | Path,
                      catalog_path: str | Path) -> dict:
    """Atomically publish bounded notes in column 12 of a derived 14-column matrix.

    The per-opponent AI notes are frozen together with the normal four masters
    at match start. Existing 6 matchup cells and processors are NEVER changed.
    """
    matrix_path, manifest_path, catalog_path = map(
        Path, (matrix_path, manifest_path, catalog_path))
    manifest = _load(manifest_path)
    # read_text() normalizes CRLF on Windows, breaking hashes of the fixed
    # reviewed baseline; verify the exact on-disk bytes instead.
    original_bytes = matrix_path.read_bytes()
    old_text = original_bytes.decode("utf-8-sig")
    if sha256(original_bytes).hexdigest() != manifest.get("matrix_sha256"):
        raise ConfigurationError("LIVEマトリクスの内容が監査記録と一致しません")
    # An exactly matching old reviewed team needs no AI hypothesis matrix.
    if manifest.get("status") == "REVIEWED_REUSE":
        return {"ai_proposal_cells": 0, "ai_proposal_opponents": 0,
                "ai_proposal_coverage": 0.0,
                "ai_proposal_status": "EXACT_BASELINE_REUSED",
                "ai_proposal_live_certified": False}
    if (not old_text.startswith("# TEAM_SCOPED_MATRIX v1;")
            or "# DERIVED_STATUS=PROVISIONAL_UNVERIFIED;" not in old_text):
        raise ConfigurationError("暫定の構築別LIVEマトリクスだけを更新できます")
    raw_catalog = catalog_path.read_bytes()
    if sha256(raw_catalog).hexdigest() != manifest.get("candidate_sha256"):
        raise ConfigurationError("LIVEマトリクスとAI評価案の構築根拠が一致しません")
    notes, count = proposal_notes(catalog_path)
    rows = []
    opponent_count = 0
    for line in old_text.splitlines():
        if not line or line.startswith("#"):
            rows.append(line)
            continue
        parts = line.split("|")
        if len(parts) != 14:
            raise ConfigurationError("LIVEマトリクスの14列形式が不正です")
        opponent_count += 1
        # Column 12 was a placeholder plan in generated matrices. This is
        # ADVISORY-ONLY text, never a match grade or certified processor.
        base=parts[11].split(" POSTMATCH_UNVERIFIED:",1)[0]
        postmatch=parts[11][len(base):]
        if base != "-" and not base.startswith(TAG):
            raise ConfigurationError("既存のLIVE戦術計画を上書きできません")
        by_slot = notes.get(parts[0], {})
        hints = [chr(65 + slot) + ":" + by_slot[slot]
                 for slot in range(6) if slot in by_slot]
        generated_hint = (TAG + " ".join(hints)) if hints else "-"
        parts[11] = generated_hint[:max(1,720-len(postmatch))] + postmatch
        rows.append("|".join(parts))
    generated = "\n".join(rows) + "\n"
    status = {
        "ai_proposal_cells": count,
        "ai_proposal_opponents": len(notes),
        "ai_proposal_coverage": round(count / max(1, opponent_count * 6), 6),
        "ai_proposal_status": "AI_PROPOSAL_UNVERIFIED",
        "ai_proposal_live_certified": False,
    }
    if generated != old_text:
        # A mismatched SHA on an interrupted write regenerates from the fixed
        # source on the next prepare, never trusts a half-written manifest.
        atomic_write_text(matrix_path, generated)
    manifest.update(status)
    manifest["matrix_sha256"] = sha256(generated.encode("utf-8")).hexdigest()
    atomic_write_text(manifest_path, json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, indent=2))
    return status
