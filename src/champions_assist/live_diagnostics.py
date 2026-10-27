"""Bounded, explicitly selected live battle diagnostics.

Only these named fields are persisted. Never write screenshots, prompts,
network traffic, tokens or arbitrary nested model output to disk.
The source of a Pokémon identity remains evidence, not guaranteed truth.
"""
from __future__ import annotations

import re

_SECRET = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{8,}|bearer\s+[a-z0-9._-]{8,}"
    r"|(?:api[_ -]?key|access[_ -]?token|authorization)\s*[:=]\s*\S+"
    r"|[a-z0-9_.+-]+@[a-z0-9.-]+\.[a-z]{2,})"
)
_FIELDS = frozenset((
    "kind","status","reason","source","field","roi","frame_id","capture_epoch",
    "side","pokemon","move","item","ability","hp","pp","stat","delta",
    "current_type","confidence","captured_at","processed_at","actor",
    "our_active","opponent_active","our_active_source","opponent_active_source",
    "actor_confirmed","awaiting_replacement","state_revision","turn_epoch",
    "request_id","screen_kind","choice_kind","selected_three","selected_bench",
    "registered_moves","ocr_candidate_count","ocr_candidates","known_hp",
    "choice_lock","battlefield","observation","action","action_kind",
    "switch_target","warning","validation_code","recommendation","displayed",
    "display_source","target","previous","current","changes","before","after",
    "last_confirmed_turn","last_confirmed_frame","last_confirmed_source",
    "accepted","rejected","transport","elapsed_ms","queue_ms","revision",
    "previous_revision","event","evidence","active_own","active_opponent",
    "current_form","status_by_pokemon","item_by_pokemon","moves_by_pokemon",
    "boosts","source_conflict","age_ms","dedup","request_phase","result",
    "detected_pokemon","detected_moves","own","opponent","hp_by_pokemon","current","maximum",
    "percent_displayed","visual_ui","our_hp","opponent_hp",
    "observed_event","name","phase","selected_bench_unconfirmed",
    "selected_switch_options","verified","stale","display_turn","original_turn",
    "origin","raw_text","ocr_text","parsed","outcome",
    "state_updates","evidence_frames","certainty","from_pokemon","value",
    "interpreted_own_active","interpreted_opponent_active",
))
_TEXT_FIELDS = frozenset((
    "raw_text","ocr_text","warning","recommendation","displayed","evidence",
))
_MAX_DETAIL_BYTES = 6000


def _clean_value(value, *, key="", depth=0, actor_map=False):
    """Only named structured battle fields; redact and bound every string."""
    if depth>4:
        return None
    if value is None or type(value) in (bool,int,float):
        if isinstance(value,float) and not (-1e9<=value<=1e9):
            return None
        return value
    if isinstance(value,str):
        limit=800 if key in _TEXT_FIELDS else 160
        return _SECRET.sub("[REDACTED]",value[:limit])
    if isinstance(value,(tuple,list)):
        return [_clean_value(x,depth=depth+1,actor_map=actor_map)
                for x in value[:12] if isinstance(x,(str,int,float,bool,dict))]
    if isinstance(value,dict):
        return {str(k):_clean_value(
                    v,key=str(k),depth=depth+1,
                    actor_map=(actor_map or key in (
                        "known_hp","hp_by_pokemon","status_by_pokemon",
                        "item_by_pokemon","moves_by_pokemon","boosts")))
                for k,v in list(value.items())[:40]
                if isinstance(k,str) and (
                    k in _FIELDS or
                    (actor_map and re.fullmatch(
                        r"[ぁ-んァ-ヴー一-龯A-Za-z0-9・→ _-]{1,48}",k)))} 
    return None


def redact_battle_detail(detail: dict) -> dict:
    """Limit diagnostic payload without logging unknown keys or full prompts."""
    if not isinstance(detail,dict):
        return {}
    clean=_clean_value(detail)
    from json import dumps
    if len(dumps(clean,ensure_ascii=False).encode("utf-8"))>_MAX_DETAIL_BYTES:
        return {"status":"DETAIL_TRUNCATED","reason":"MAX_DETAIL_BYTES"}
    return clean


def current_state_digest(state: dict) -> dict:
    """Small, actionable facts; never include full frozen masters or images."""
    state=state or {}
    evidence=state.get("battle_evidence") or {}
    return redact_battle_detail({
        "our_active":state.get("our_active") or "",
        "opponent_active":state.get("opponent_active") or "",
        "awaiting_replacement":bool(state.get("awaiting_replacement")),
        "state_revision":state.get("_live_state_revision",0),
        "selected_three":(state.get("selected_three") or [])[:3],
        "known_hp":evidence.get("hp_by_pokemon",{}),
        "choice_lock":evidence.get("choice_lock",{}),
    })


def ai_input_digest(prompt: dict) -> dict:
    """Exactly the tactical values sent, not the full model prompt."""
    p=prompt or {}
    return redact_battle_detail({
        "screen_kind":p.get("screen_kind") or p.get("expected_screen_label"),
        "active_own":p.get("active_own") or
                     (p.get("replay_state") or {}).get("our_active"),
        "active_opponent":p.get("active_opponent") or
                     (p.get("replay_state") or {}).get("opponent_active"),
        "selected_three":p.get("selected_three"),
        "selected_bench":p.get("living_selected_bench_candidates") or
                     (p.get("registered_command_options") or {}).get(
                         "bench_candidates_from_current_state"),
        "registered_moves":p.get("our_registered_moves") or
                     (p.get("registered_command_options") or {}).get(
                         "moves_by_pokemon"),
        "known_hp":p.get("known_hp_by_pokemon") or
                     (p.get("registered_command_options") or {}).get(
                         "confirmed_hp_by_pokemon"),
        "choice_lock":p.get("battle_evidence",{}).get("choice_lock") if
                     isinstance(p.get("battle_evidence"),dict) else
                     (p.get("replay_state",{}).get("battle_evidence") or {}).get(
                         "choice_lock"),
        "battlefield":p.get("battlefield") or
                     (p.get("replay_state") or {}).get("battlefield"),
        "ocr_candidate_count":len(p.get("ocr_candidates_unverified") or
                     p.get("local_field_candidates_unverified") or []),
    })


def ai_response_digest(response: dict) -> dict:
    """A pre-validation, structured action and visual observation, not API body."""
    response=response or {}
    a=response.get("action") if isinstance(response.get("action"),dict) else response
    obs=response.get("observation") if isinstance(
        response.get("observation"),dict) else {}
    return redact_battle_detail({
        "action_kind":a.get("action_kind"),
        "move":a.get("move"),
        "switch_target":a.get("switch_target"),
        "warning":a.get("warning"),
        "interpreted_own_active":response.get("interpreted_own_active"),
        "interpreted_opponent_active":response.get("interpreted_opponent_active"),
        "state_updates":(response.get("state_updates") or [])[:8],
        "observation":{k:obs.get(k) for k in (
            "our_active","opponent_active","visual_ui","our_hp",
            "opponent_hp","observed_event")},
    })
