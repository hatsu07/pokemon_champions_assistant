"""Verify ChatGPT-interpreted OCR deltas; never parse OCR prose here."""
from __future__ import annotations
from copy import deepcopy
import re
from .battle_evidence import empty_battle_evidence, update_battle_evidence
from .terminology import japanese_name

_KINDS=("SWITCH","MOVE","FAINT","HP","STATUS","STATUS_CLEAR","BOOST",
        "ITEM","ITEM_SPENT","ITEM_EXCHANGED","ABILITY",
        "ABILITY_CHANGED_UNKNOWN","TYPE","MEGA","WEATHER","TERRAIN",
        "TEMP_START","TEMP_END","SIDE_EFFECT")
_TYPES=("ノーマル","ほのお","みず","でんき","くさ","こおり","かくとう",
        "どく","じめん","ひこう","エスパー","むし","いわ","ゴースト",
        "ドラゴン","あく","はがね","フェアリー")
_STATS=("A","B","C","D","S","ACC","EVA")

UPDATE_SCHEMA={
    "type":"object","additionalProperties":False,
    "properties":{
        "kind":{"type":"string","enum":list(_KINDS)},
        "side":{"type":"string","enum":["own","opponent","field"]},
        "pokemon":{"type":"string"},"from_pokemon":{"type":"string"},
        "value":{"type":"string"},"stat":{"type":"string"},
        "delta":{"type":"integer"},
        "certainty":{"type":"string","enum":["CONFIRMED","UNCERTAIN"]},
        "evidence_frames":{"type":"array","items":{"type":"string"}},
    },
    "required":["kind","side","pokemon","from_pokemon","value","stat",
                "delta","certainty","evidence_frames"],
}

def _canon(s):
    return japanese_name("pokemon",str(s or "").split("→")[0].strip())

def _proof(event,candidates):
    """Validate *evidence strength*, not the meaning of OCR prose.

    For a switch, a weak send-out frame can be corroborated by a later
    distinct message showing effects on the newly active Pokémon. Neither
    frame alone proves a switch. ChatGPT must still interpret both.
    """
    frames=event.get("evidence_frames")
    if (not isinstance(frames,list) or not 1<=len(frames)<=4
            or any(not isinstance(f,str) or not f for f in frames)
            or len(set(frames))!=len(frames)):
        return "EVIDENCE_FRAMES_INVALID"
    confidences=[]
    captured=[]
    switch_fields=("battle_message","system_message","own_active_name",
                   "opponent_active_name","own_item_popup",
                   "opponent_item_popup","own_effect_popup",
                   "opponent_effect_popup")
    has_actor_text=False
    for frame in frames:
        rows=[row for row in candidates if isinstance(row,dict)
              and row.get("frame_id")==frame and
              str(row.get("field","")).startswith("screen_text.") and
              str(row.get("raw_text") or "") and
              type(row.get("confidence")) in (int,float) and
              0<=row["confidence"]<=1]
        if event["kind"]=="SWITCH":
            rows=[row for row in rows if
                  str(row["field"]).endswith(switch_fields)]
            has_actor_text=has_actor_text or any(
                str(row["field"]).endswith((
                    "battle_message","system_message",
                    "own_active_name","opponent_active_name"))
                for row in rows)
        if not rows:
            return "EVIDENCE_FRAME_NOT_SENT"
        chosen=max(rows,key=lambda r:r["confidence"])
        confidences.append(float(chosen["confidence"]))
        if type(chosen.get("observed_at")) in (int,float):
            captured.append(float(chosen["observed_at"]))
    if event["kind"]=="SWITCH":
        if not has_actor_text:
            return "SWITCH_ACTOR_TEXT_REQUIRED"
        if len(frames)==1 and confidences[0]<0.80:
            return "SWITCH_NEEDS_SECOND_FRAME"
        if min(confidences)<0.55 or max(confidences)<0.68:
            return "WEAK_SWITCH_EVIDENCE"
        # If one OCR frame is weak, require a much stronger second reading.
        if min(confidences)<0.60 and max(confidences)<0.78:
            return "WEAK_SWITCH_EVIDENCE"
        if len(captured)==len(frames) and len(captured)>1:
            if max(captured)-min(captured)>45:
                return "SWITCH_EVIDENCE_TOO_OLD"
    elif max(confidences)<0.72:
        return "WEAK_OCR_EVIDENCE"
    return None

def apply_ai_ocr_updates(state,proposals,candidates,assets):
    """Apply only confirmed, source-linked AI updates in the model's order."""
    result=deepcopy(state)
    audit=[]
    if not isinstance(proposals,list):
        return result,[{"status":"REJECTED","reason":"INVALID_UPDATES"}]
    candidates=list(candidates or [])
    applied=set(tuple(row) for row in result.get("_ai_ocr_update_keys") or [])
    own={_canon(form) for member in assets.own_team
         for form in member["name"].split("→")
         if _canon(member["name"].split("→")[0]) in
            {_canon(n) for n in result.get("selected_three") or []}}
    opponent={_canon(n) for n in (
        list(result.get("opponent_six") or [])+
        list(getattr(assets,"matrix_index",{}))) if
        n and not str(n).startswith("未判明")}
    for event in proposals[:12]:
        if not isinstance(event,dict):
            audit.append({"status":"REJECTED","reason":"INVALID_EVENT"});continue
        kind=event.get("kind")
        side=event.get("side")
        name=_canon(event.get("pokemon"))
        prior=_canon(event.get("from_pokemon"))
        value=str(event.get("value") or "").strip()[:160]
        item={"kind":kind,"side":side,"pokemon":name}
        def fail(reason):
            audit.append({**item,"status":"REJECTED","reason":reason})
        if kind not in _KINDS or side not in ("own","opponent","field"):
            fail("UNKNOWN_EVENT");continue
        if event.get("certainty")!="CONFIRMED":
            fail("AI_UNCERTAIN");continue
        proof=_proof(event,candidates)
        if proof:
            fail(proof);continue
        if kind in ("WEATHER","TERRAIN"):
            if side!="field":fail("FIELD_SIDE_REQUIRED");continue
        elif kind=="SIDE_EFFECT":
            if side not in ("own","opponent"):
                fail("SIDE_EFFECT_SIDE_REQUIRED");continue
        elif side=="field" or (side=="own" and name not in own) or (
                side=="opponent" and name not in opponent):
            fail("POKEMON_NOT_IN_KNOWN_ROSTER");continue
        identity=(kind,side,name,prior,value,str(event.get("stat")),
                  str(event.get("delta")),*sorted(event["evidence_frames"]))
        # Replayed OCR history must not be misdiagnosed as a new illegal
        # self-switch on the following turn.
        if identity in applied:
            fail("ALREADY_APPLIED");continue
        key="our_active" if side=="own" else "opponent_active"
        current=_canon(result.get(key)) if side!="field" else ""
        if kind=="SWITCH":
            if prior and current and prior!=current:
                fail("STALE_FROM_ACTIVE");continue
            if current==name:fail("SELF_SWITCH");continue
        elif kind not in ("WEATHER","TERRAIN","SIDE_EFFECT") and current!=name:
            fail("ACTOR_NOT_CURRENT");continue
        memory=result.setdefault("battle_evidence",empty_battle_evidence())
        if kind=="SWITCH":
            update_battle_evidence(result,{
                "our_active":name if side=="own" else result.get("our_active"),
                "opponent_active":name if side=="opponent" else result.get("opponent_active"),
            },assets.own_team)
            result[key]=name
            if side=="own":
                result["awaiting_replacement"]=False
                memory["choice_lock"]={"pokemon":"","move":"","source":""}
            else:
                from .live_state_updates import fill_unknown_preview
                fill_unknown_preview(result,assets,name)
        elif kind=="FAINT":
            memory["hp_by_pokemon"][side][name]="0"
            memory["fainted"][side][name]=True
            if side=="own":
                result["awaiting_replacement"]=True
                memory["choice_lock"]={"pokemon":"","move":"","source":""}
        elif kind=="HP":
            if not re.fullmatch(r"(?:\d{1,4}/[1-9]\d{0,3}|\d{1,3}%)",value):
                fail("INVALID_HP");continue
            memory["hp_by_pokemon"][side][name]=value
            result.setdefault("observed_hp",{})[side]=value
        elif kind=="MOVE":
            if not value:fail("EMPTY_MOVE");continue
            if side=="own":
                member=next((m for m in assets.own_team if name in
                    {_canon(x) for x in m["name"].split("→")}),None)
                legal={japanese_name("move",member.get("move"+str(i),""))
                       for i in range(1,5)} if member else set()
                if japanese_name("move",value) not in legal:
                    fail("MOVE_NOT_REGISTERED");continue
            update_battle_evidence(result,{
                "our_active":result.get("our_active"),
                "opponent_active":result.get("opponent_active"),
                "visual_ui":"ACTION_RESOLUTION",
                ("our_used_move" if side=="own" else "opponent_used_move"):value
            },assets.own_team)
        elif kind=="STATUS_CLEAR":
            memory["status_by_pokemon"][side].pop(name,None)
        elif kind in ("STATUS","ITEM","ABILITY","TYPE"):
            if not value or (kind=="TYPE" and value not in _TYPES):
                fail("INVALID_STATE_VALUE");continue
            target={"STATUS":"status_by_pokemon","ITEM":"item_by_pokemon",
                    "ABILITY":"ability_by_pokemon","TYPE":"current_type"}[kind]
            memory.setdefault(target,{"own":{},"opponent":{}})[side][name]=value
        elif kind=="BOOST":
            stat=event.get("stat");delta=event.get("delta")
            if stat not in _STATS or type(delta)!=int or delta==0 or not -6<=delta<=6:
                fail("INVALID_BOOST");continue
            entry=memory["boosts"][side].setdefault(name,{})
            if not isinstance(entry,dict) or entry.get("_prior_stage_uncertain"):
                fail("UNKNOWN_BOOST_BASE");continue
            entry[stat]=max(-6,min(6,entry.get(stat,0)+delta))
        elif kind=="MEGA":
            memory.setdefault("form_by_pokemon",{"own":{},"opponent":{}})[side][name]="MEGA_EVOLVED_FORM_UNCONFIRMED"
            memory.setdefault("current_type",{"own":{},"opponent":{}})[side].pop(name,None)
            memory.setdefault("current_base_speed_by_pokemon",{"own":{},"opponent":{}})[side].pop(name,None)
        elif kind=="ITEM_SPENT":
            if not value:
                fail("ITEM_NAME_REQUIRED");continue
            memory["item_state_by_pokemon"][side][name]={
                "item":value,"state":"SPENT_OR_LOST"}
            memory["item_by_pokemon"][side].pop(name,None)
            if side=="own" and memory["choice_lock"].get("pokemon")==name:
                memory["choice_lock"]={"pokemon":"","move":"","source":""}
        elif kind=="ITEM_EXCHANGED":
            memory["item_by_pokemon"][side].pop(name,None)
            memory["item_state_by_pokemon"][side][name]={
                "item":"UNKNOWN","state":"CURRENT_UNKNOWN_AFTER_EXCHANGE"}
            if side=="own":
                memory["choice_lock"]={"pokemon":"","move":"","source":""}
        elif kind=="ABILITY_CHANGED_UNKNOWN":
            memory["ability_by_pokemon"][side].pop(name,None)
            memory.setdefault("ability_state_by_pokemon",{
                "own":{},"opponent":{}})[side][name]={
                "name":"UNKNOWN","active":False}
        elif kind in ("TEMP_START","TEMP_END"):
            if value not in ("encore","taunt","disable","substitute"):
                fail("UNKNOWN_TEMPORARY_EFFECT");continue
            active=memory["temporary_effects"][side].setdefault(name,{})
            if kind=="TEMP_START":
                active[value]={"active":True,"remaining_turns":None,
                               "source":"AI_INTERPRETED_OCR"}
            else:
                active.pop(value,None)
        elif kind=="SIDE_EFFECT":
            if value not in ("リフレクター","ひかりのかべ","ステルスロック",
                             "まきびし","どくびし","ねばねばネット"):
                fail("UNKNOWN_SIDE_EFFECT");continue
            delta=event.get("delta")
            if type(delta)!=int or delta not in (-1,1):
                fail("SIDE_EFFECT_DIRECTION_REQUIRED");continue
            effects=result.setdefault("battlefield",{}).setdefault(
                "effects_by_side",{"own":{},"opponent":{}})[side]
            if delta==1:
                effects[value]={"active":True,"turns_remaining":None,
                                "source":"AI_INTERPRETED_OCR"}
            else:
                effects.pop(value,None)
        elif kind in ("WEATHER","TERRAIN"):
            result.setdefault("battlefield",{})["weather" if kind=="WEATHER" else "terrain"]=value
        applied.add(identity)
        result["_ai_ocr_update_keys"]=list(applied)[-100:]
        result["_live_state_revision"]=result.get("_live_state_revision",0)+1
        audit.append({**item,"status":"APPLIED","reason":"AI_OCR_EVIDENCE_VERIFIED"})
        provenance=memory.setdefault("update_provenance",[])
        provenance.append({"kind":kind,"source":"AI_INTERPRETED_OCR",
                           "frame_ids":event["evidence_frames"][:4]})
        del provenance[:-50]
    return result,audit
