"""Evidence-aware live state reducer for local OCR, independent of network calls.

OCR is an observation, NOT proof that a recommendation was executed. Only
explicit, uniquely attributable, high-confidence phrases can affect the live
snapshot. Unclear messages remain recorded as candidates. Timed effects do
not expire by guessing Pokémon SV/other-game durations.
"""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata

from .terminology import japanese_name

_CONFIDENCE_MIN = 0.72
_STATS = {"こうげき":"A","ぼうぎょ":"B","とくこう":"C",
          "とくぼう":"D","すばやさ":"S","めいちゅう":"ACC","かいひ":"EVA"}
_STATUSES = {"まひ":"まひ","やけど":"やけど","どく":"どく",
             "もうどく":"もうどく","ねむり":"ねむり","こおり":"こおり"}
_TEMPORARY = {"アンコール":"encore","ちょうはつ":"taunt",
              "かなしばり":"disable","みがわり":"substitute"}
_WEATHER = {"あめ":"あめ","すなあらし":"すなあらし",
            "ゆき":"ゆき","ひざし":"ひざし"}
_TERRAIN = ("エレキフィールド","グラスフィールド",
            "サイコフィールド","ミストフィールド")


def _flat(text: object) -> str:
    return re.sub(r"\s+","",unicodedata.normalize("NFKC",str(text or ""))).replace("！","!").replace("。","")


def _pokemon(name: str) -> str:
    return japanese_name("pokemon",name.split("→")[-1].strip())


def _names(assets, state):
    selected=set(state.get("selected_three") or ())
    own={}
    for member in assets.own_team:
        label=member.get("name","")
        if not label or (selected and label not in selected):
            continue
        original=label.split("→",1)[0].strip()
        evolved=label.split("→")[-1].strip()
        own[_flat(_pokemon(original))]=_pokemon(original)
        own[_flat(_pokemon(evolved))]=_pokemon(evolved)
    opponent={_flat(_pokemon(n)):_pokemon(n)
              for n in getattr(assets,"matrix_index",{})
              if n and not n.startswith("未判明")}
    opponent.update({_flat(_pokemon(n)):_pokemon(n)
                     for n in state.get("opponent_six",[])
                     if n and not n.startswith("未判明")})
    return own,opponent


def _owner(raw: str, own: dict, opponents: dict, *, explicit_opponent=False):
    key=_flat(raw)
    if explicit_opponent:
        return ("opponent",opponents[key]) if key in opponents else (None,None)
    if key in own:
        # Unprefixed in-game Japanese player messages identify the player's
        # selected member. A same-species mirror requires the explicit side
        # in a future calibration; never promote an opponent preview guess.
        return "own",own[key]
    if key in opponents:
        # No explicit side => do not bind opponent to an actor.
        return None,None
    return None,None


def _actor(message: str, own: dict, opponents: dict):
    # Only strings that begin with an actor are accepted as actor-anchored.
    m=re.match(r"^(相手の)?(.+?)(?:は|の)(.+)$",message)
    if not m:
        return None,None,""
    side,name=_owner(m.group(2),own,opponents,
                     explicit_opponent=bool(m.group(1)))
    return side,name,m.group(3)


def _event_for_message(text: str, own: dict, opponents: dict):
    msg=_flat(text)
    # '相手はXをくりだした', 'ゆけっ!X!' and explicit named switch.
    outgoing=re.fullmatch(r"相手は(.+?)を(?:くりだした|繰り出した)!?",msg)
    if outgoing:
        side,name=_owner(outgoing.group(1),own,opponents,
                         explicit_opponent=True)
        if side:
            return {"kind":"SWITCH","side":side,"pokemon":name}
    outgoing=re.fullmatch(r"ゆけっ!?(.+?)!?",msg)
    if outgoing:
        side,name=_owner(outgoing.group(1),own,opponents)
        if side=="own":
            return {"kind":"SWITCH","side":side,"pokemon":name}
    side,name,tail=_actor(msg,own,opponents)
    if side and name:
        if re.fullmatch(r"(?:たおれた|倒れた|ひんしになった)!?",tail):
            return {"kind":"FAINT","side":side,"pokemon":name}
        move=re.fullmatch(r"(.+?)を(?:つかった|使った)!?",tail)
        if move:
            return {"kind":"MOVE","side":side,"pokemon":name,
                    "move":move.group(1)}
        for text,status in _STATUSES.items():
            if (tail in (text+"になった",text+"になってしまった",
                         text+"をおった",text+"になった!")):
                return {"kind":"STATUS","side":side,"pokemon":name,
                        "status":status}
            if (tail in (text+"がなおった",text+"が治った",text+"からかいふくした")):
                return {"kind":"STATUS_CLEAR","side":side,"pokemon":name,
                        "status":status}
        stage=re.fullmatch(r"(.+?)が(ぐーんと|がくっと)?(あがった|さがった)!?",tail)
        if stage and stage.group(1) in _STATS:
            delta=(2 if stage.group(2) else 1)*(1 if stage.group(3)=="あがった" else -1)
            return {"kind":"BOOST","side":side,"pokemon":name,
                    "stat":_STATS[stage.group(1)],"delta":delta}
        for phrase,kind in _TEMPORARY.items():
            if tail in (phrase+"をうけた",phrase+"になった",
                        phrase+"にかかった"):
                return {"kind":"TEMP_START","side":side,"pokemon":name,
                        "effect":kind}
            if tail in (phrase+"がとけた",phrase+"がきれた",
                        phrase+"からかいほうされた"):
                return {"kind":"TEMP_END","side":side,"pokemon":name,
                        "effect":kind}
        item=re.fullmatch(r"(.+?)を(?:つかった|消費した|なくした)",tail)
        if item:
            return {"kind":"ITEM_SPENT","side":side,"pokemon":name,
                    "item":item.group(1)}
        # Exact activation phrase only; activation does not mean current
        # held item ownership or an ability remains active after replacement.
        ability=re.fullmatch(r"(?:とくせい|特性)(.+?)が(?:はたらいた|発動した)",tail)
        if ability:
            return {"kind":"ABILITY","side":side,"pokemon":name,
                    "ability":ability.group(1)}
    if msg in ("あめがふりはじめた","あめが降り始めた"):
        return {"kind":"WEATHER","value":"あめ"}
    if msg in ("あめがやんだ","あめが止んだ"):
        return {"kind":"WEATHER","value":""}
    if msg in ("すなあらしがふきはじめた",):
        return {"kind":"WEATHER","value":"すなあらし"}
    if msg in ("すなあらしがおさまった",):
        return {"kind":"WEATHER","value":""}
    if msg in ("ゆきがふりはじめた",):
        return {"kind":"WEATHER","value":"ゆき"}
    if msg in ("ゆきがやんだ",):
        return {"kind":"WEATHER","value":""}
    if msg in ("ひざしがつよくなった",):
        return {"kind":"WEATHER","value":"ひざし"}
    if msg in ("ひざしがもとにもどった",):
        return {"kind":"WEATHER","value":""}
    for name in _TERRAIN:
        if msg in (name+"になった",name+"がひろがった"):
            return {"kind":"TERRAIN","value":name}
        if msg in (name+"がきえた",name+"がなくなった"):
            return {"kind":"TERRAIN","value":""}
    screen=re.fullmatch(
        r"^(相手の)?場に(リフレクター|ひかりのかべ|ステルスロック|まきびし|"
        r"どくびし|ねばねばネット)が(はられた|しかれた|なくなった|きえた)$",msg)
    if screen:
        return {"kind":"SIDE_EFFECT",
                "side":"opponent" if screen.group(1) else "own",
                "effect":screen.group(2),
                "active":screen.group(3) in ("はられた","しかれた")}
    return None


def _ensure(evidence: dict):
    for key in ("moves_by_pokemon","pp_by_pokemon","item_state_by_pokemon",
                "ability_state_by_pokemon","temporary_effects","fainted"):
        evidence.setdefault(key,{"own":{},"opponent":{}})
    evidence.setdefault("update_provenance",[])
    evidence.setdefault("last_observed_own_move","")
    evidence.setdefault("last_move_by_pokemon",{"own":{},"opponent":{}})


def apply_local_ocr(state: dict, records, assets) -> tuple[dict, list[dict]]:
    """Fold bounded OCR results into a detached match state.

    Returns state, audit. Uncertain events are candidates, never overwritten
    certified facts. Caller must use the returned state before making a NEW
    tactical request; results from older revisions must be invalidated.
    """
    updated=deepcopy(state)
    evidence=updated.setdefault("battle_evidence",{})
    _ensure(evidence)
    battlefield=updated.setdefault("battlefield",{})
    battlefield.setdefault("weather","")
    battlefield.setdefault("terrain","")
    battlefield.setdefault("effects_by_side",{"own":{},"opponent":{}})
    audit=[]
    own,opponents=_names(assets,updated)
    seen=updated.setdefault("_ocr_applied_event_ids",[])
    seen_set=set(seen)
    # An HP HUD captured during switch animation may still show the outgoing
    # member. Only the same-frame name can attribute it to a new entrant.
    switching_frames=set()
    named_frames=set()
    for record in records:
        field=getattr(record,"field","")
        frame=str(getattr(record,"frame_id",""))
        if field in ("screen_text.own_active_name",
                     "screen_text.opponent_active_name"):
            named_frames.add((frame,"own" if field=="screen_text.own_active_name"
                              else "opponent"))
        elif field.endswith("battle_message"):
            event=_event_for_message(str(getattr(record,"text","")),own,opponents)
            if event and event.get("kind")=="SWITCH":
                switching_frames.add((frame,event.get("side")))
    for rec in records:
        text=str(getattr(rec,"text","") or "")
        field=getattr(rec,"field","")
        from hashlib import sha256
        digest=sha256(_flat(text).encode("utf-8")).hexdigest()[:20]
        ref=(str(getattr(rec,"frame_id","")),field,digest)
        if ref in seen_set or not text:
            continue
        seen.append(ref)
        seen_set.add(ref)
        if len(seen)>60:
            del seen[:-60]
        conf=getattr(rec,"confidence",None)
        trusted=(getattr(rec,"status","")=="CANDIDATE"
                 and isinstance(conf,(int,float)) and conf>=_CONFIDENCE_MIN)
        evt=None
        if field in ("screen_text.own_active_name","screen_text.opponent_active_name"):
            side="own" if "own_active" in field and not "opponent" in field else "opponent"
            corpus=own if side=="own" else opponents
            name=corpus.get(_flat(text))
            if name:
                evt={"kind":"ACTIVE","side":side,"pokemon":name}
        elif field.startswith("screen_text.own_pp_move") and field[-1:] in "1234":
            obj=getattr(rec,"value",None)
            if isinstance(obj,dict) and set(("current","maximum"))<=obj.keys():
                slot=int(field[-1:])
                actor=updated.get("our_active","")
                member=next((m for m in assets.own_team
                             if _pokemon(m["name"])==_pokemon(actor)),None)
                move=member.get("move"+str(slot),"") if member else ""
                if move:
                    evt={"kind":"PP","side":"own","pokemon":actor,
                         "move":move,"pp":int(obj["current"])}
        elif field=="screen_text.own_hp_fraction":
            obj=getattr(rec,"value",None)
            if isinstance(obj,dict) and set(("current","maximum"))<=obj.keys():
                evt={"kind":"HP","side":"own","hp":str(obj["current"])+"/"+str(obj["maximum"])}
        elif field=="screen_text.opponent_hp_percent":
            obj=getattr(rec,"value",None)
            if isinstance(obj,dict) and "percent_displayed" in obj:
                evt={"kind":"HP","side":"opponent","hp":str(obj["percent_displayed"])+"%"}
        elif field.startswith("screen_text.") and any(
                tag in field for tag in ("battle_message","effect_popup","item_popup")):
            evt=_event_for_message(text,own,opponents)
        if evt is None:
            continue
        if evt["kind"]=="HP" and (ref[0],evt.get("side")) in switching_frames and (
                ref[0],evt.get("side")) not in named_frames:
            audit.append({"kind":"HP","status":"CANDIDATE",
                          "reason":"SAME_FRAME_SWITCH_HUD_AMBIGUOUS",
                          "frame_id":ref[0]})
            continue
        if not trusted:
            audit.append({"kind":evt["kind"],"status":"CANDIDATE",
                          "reason":"LOW_CONFIDENCE_OR_CONFLICT","frame_id":ref[0]})
            continue
        kind,side=evt["kind"],evt.get("side")
        name=evt.get("pokemon")
        old=updated.get("our_active" if side=="own" else "opponent_active")
        if kind in ("ACTIVE","SWITCH"):
            key="our_active" if side=="own" else "opponent_active"
            if name!=old:
                # The same exact Pokémon-name ROI must be read after any
                # stale switch proposal; OCR message itself is execution proof.
                if name and old:
                    evidence["boosts"][side].pop(old,None)
                if side=="own":
                    evidence["choice_lock"]={"pokemon":"","move":"","source":""}
                    updated["awaiting_replacement"]=False
                updated[key]=name
                # If there is exactly ONE unresolved preview slot and this
                # species is not already among the five, fill it without
                # changing known slot assignments.
                if side=="opponent" and name not in updated.get("opponent_six",[]):
                    unknown=[i for i,n in enumerate(updated.get("opponent_six",[]))
                             if str(n).startswith("未判明相手")]
                    if len(unknown)==1:
                        updated["opponent_six"][unknown[0]]=name
                        updated["visual_unknown_opponents"]=[]
                        if name in getattr(assets,"matrix_index",{}):
                            updated["matrix_rows_six"]=[
                                assets.matrix_index[n] for n in updated["opponent_six"]
                                if n in assets.matrix_index]
                        # cached PREP is old preview evidence, not proof for
                        # the newly discovered opponent.
                        if updated.get("prep"):
                            prep=updated["prep"]
                            prep["unknown_filled_during_match"]=name
                            from .prep_cache import cached_pairs
                            three=tuple(updated.get("selected_three") or ())
                            lead=updated.get("lead")
                            recovered=(cached_pairs(assets,three,(name,),lead)
                                       if len(three)==3 and lead in three and
                                       getattr(assets,"matchup_cache_index",{})
                                       else [])
                            if len(recovered)==3:
                                existing=prep.get("matchups_18") or prep.get("matchups_known") or []
                                seen_pairs={(p.get("our_pokemon"),p.get("opponent_pokemon"))
                                            for p in existing}
                                for row in recovered:
                                    k=(row["our_pokemon"],row["opponent_pokemon"])
                                    if k not in seen_pairs:
                                        existing.append(row)
                                prep["matchups_known"]=existing
                                prep["cached_matchups"]=len(existing)
                            else:
                                # Missing cached matchup never becomes a
                                # guessed guaranteed processor/safe switch.
                                prep.setdefault("unrated_new_opponents",[])
                                if name not in prep["unrated_new_opponents"]:
                                    prep["unrated_new_opponents"].append(name)
        elif kind=="PP":
            actor=updated.get("our_active")
            if not actor or actor!=name:
                audit.append({"kind":kind,"status":"CANDIDATE",
                              "reason":"ACTIVE_NOT_CONFIRMED","frame_id":ref[0]})
                continue
            evidence["pp_by_pokemon"]["own"].setdefault(actor,{})[evt["move"]]=evt["pp"]
        elif kind=="HP":
            actor=updated.get("our_active" if side=="own" else "opponent_active")
            if not actor or (side=="own" and updated.get("awaiting_replacement")):
                audit.append({"kind":kind,"status":"CANDIDATE",
                              "reason":"ACTIVE_NOT_CONFIRMED","frame_id":ref[0]})
                continue
            evidence.setdefault("hp_by_pokemon",{"own":{},"opponent":{}})[side][actor]=evt["hp"]
            updated.setdefault("observed_hp",{})[side]=evt["hp"]
        elif name is not None:
            if kind=="FAINT":
                evidence.setdefault("hp_by_pokemon",{"own":{},"opponent":{}})[side][name]="0"
                evidence["fainted"][side][name]=True
                if side=="own":
                    updated["awaiting_replacement"]=True
                    evidence["choice_lock"]={"pokemon":"","move":"","source":""}
            elif kind=="MOVE":
                moves=evidence["moves_by_pokemon"][side].setdefault(name,[])
                if evt["move"] not in moves:
                    moves.append(evt["move"])
                evidence["last_move_by_pokemon"][side][name]=evt["move"]
                if side=="own":
                    evidence["last_confirmed_our_move"]=evt["move"]
                    evidence["last_observed_own_move"]=evt["move"]
                # Opponent PP is UNKNOWN. Own PP decreases only when an
                # independently observed prior numeric count was available.
                pp=evidence["pp_by_pokemon"][side].get(name,{})
                val=pp.get(evt["move"])
                if isinstance(val,int) and val>0:
                    pp[evt["move"]]=val-1
            elif kind=="STATUS":
                evidence.setdefault("status_by_pokemon",{"own":{},"opponent":{}})[side][name]=evt["status"]
            elif kind=="STATUS_CLEAR":
                statuses=evidence.setdefault("status_by_pokemon",{"own":{},"opponent":{}})[side]
                if statuses.get(name)==evt["status"]:
                    statuses.pop(name,None)
            elif kind=="BOOST":
                entry=evidence.setdefault("boosts",{"own":{},"opponent":{}})[side].get(name)
                if not isinstance(entry,dict):
                    entry={}
                entry[evt["stat"]]=entry.get(evt["stat"],0)+evt["delta"]
                evidence["boosts"][side][name]=entry
            elif kind in ("TEMP_START","TEMP_END"):
                active=evidence["temporary_effects"][side].setdefault(name,{})
                if kind=="TEMP_START":
                    active[evt["effect"]]={"active":True,"remaining_turns":None,
                                         "source":"LOCAL_OCR_EXPLICIT_MESSAGE"}
                else:
                    active.pop(evt["effect"],None)
            elif kind=="ITEM_SPENT":
                evidence["item_state_by_pokemon"][side][name]={
                    "item":evt["item"],"state":"SPENT_OR_LOST"}
            elif kind=="ABILITY":
                evidence.setdefault("ability_by_pokemon",{"own":{},"opponent":{}})[side][name]=evt["ability"]
                evidence["ability_state_by_pokemon"][side][name]={"active":True,"name":evt["ability"]}
        elif kind in ("WEATHER","TERRAIN"):
            key="weather" if kind=="WEATHER" else "terrain"
            battlefield[key]=evt["value"]
            battlefield[key+"_state"]={
                "active":bool(evt["value"]),
                "value":evt["value"],
                "remaining_turns":None,
                "source":"LOCAL_OCR_EXPLICIT_MESSAGE"}
        elif kind=="SIDE_EFFECT":
            effects=battlefield["effects_by_side"][side]
            if evt["active"]:
                effects[evt["effect"]]={"active":True,"turns_remaining":None,
                                        "source":"LOCAL_OCR_EXPLICIT_MESSAGE"}
            else:
                effects.pop(evt["effect"],None)
        else:
            continue
        updated["_live_state_revision"]=updated.get("_live_state_revision",0)+1
        audit.append({"kind":kind,"status":"APPLIED","side":side or "",
                      "frame_id":ref[0],"revision":updated.get("_live_state_revision",0)})
        provenance=evidence["update_provenance"]
        provenance.append({"kind":kind,"source":getattr(rec,"source","LOCAL_OCR"),
                           "frame_id":ref[0],"confidence":conf})
        del provenance[:-50]
    return updated,audit
