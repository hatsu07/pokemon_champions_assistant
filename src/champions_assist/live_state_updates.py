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
from .battle_evidence import _visual_stages

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
# Type literals are validated against Pokémon Champions' fixed type domain.
_TYPES = ("ノーマル","ほのお","みず","でんき","くさ","こおり",
          "かくとう","どく","じめん","ひこう","エスパー",
          "むし","いわ","ゴースト","ドラゴン","あく",
          "はがね","フェアリー")


def _flat(text: object) -> str:
    return re.sub(r"\s+","",unicodedata.normalize("NFKC",str(text or ""))).replace("！","!").replace("。","")


def _pokemon(name: str) -> str:
    return japanese_name("pokemon",name.split("→")[-1].strip())


def _names(assets, state):
    selected=set(state.get("selected_three") or ())
    own={}
    for member in assets.own_team:
        label=member.get("name","")
        if not label:
            continue
        original=label.split("→",1)[0].strip()
        evolved=label.split("→")[-1].strip()
        if selected and not ({label,original,evolved} & selected):
            continue
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
        if re.fullmatch(r"(?:メガシンカした|メガ進化した)!?",tail):
            return {"kind":"MEGA","side":side,"pokemon":name}
        current_type=re.fullmatch(r"タイプが(.+?)になった!?",tail)
        if current_type and current_type.group(1) in _TYPES:
            return {"kind":"TYPE","side":side,"pokemon":name,
                    "current_type":current_type.group(1)}
        if re.fullmatch(r"(?:たおれた|倒れた|ひんしになった)!?",tail):
            return {"kind":"FAINT","side":side,"pokemon":name}
        # Berry activation has the same つかった verb as a move.
        # A known berry name is an item, never a fifth move.
        berry=re.fullmatch(r"(.+?のみ)を(?:つかった|使った)!?",tail)
        if berry:
            return {"kind":"ITEM_SPENT","side":side,"pokemon":name,
                    "item":berry.group(1)}
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
        item=re.fullmatch(r"(.+?)を(?:消費した|なくした)",tail)
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



def fill_unknown_preview(state: dict, assets, pokemon: str) -> bool:
    """Complete exactly one unresolved opponent slot from OBSERVED species.

    This never claims a matchup grade: only corresponding frozen cache rows
    are reused. All other preview slots stay unchanged.
    """
    roster=state.get("opponent_six") or []
    missing=[i for i,entry in enumerate(roster)
             if str(entry).startswith("未判明相手")]
    if (len(missing)!=1 or pokemon in roster
            or pokemon not in getattr(assets,"matrix_index",{})):
        return False
    roster[missing[0]]=pokemon
    state["visual_unknown_opponents"]=[]
    state["matrix_rows_six"]=[
        assets.matrix_index[entry] for entry in roster
        if entry in assets.matrix_index]
    prep=state.get("prep")
    if isinstance(prep,dict):
        prep["unknown_filled_during_match"]=pokemon
        from .prep_cache import cached_pairs
        selected=tuple(state.get("selected_three") or ())
        lead=state.get("lead")
        recovered=(cached_pairs(assets,selected,(pokemon,),lead)
                   if len(selected)==3 and lead in selected and
                   getattr(assets,"matchup_cache_index",{}) else [])
        if len(recovered)==3:
            pairs=prep.get("matchups_18") or prep.get("matchups_known") or []
            keys={(p.get("our_pokemon"),p.get("opponent_pokemon"))
                  for p in pairs}
            for pair in recovered:
                key=(pair["our_pokemon"],pair["opponent_pokemon"])
                if key not in keys:
                    pairs.append(pair)
            prep["matchups_known"]=pairs
            prep["cached_matchups"]=len(pairs)
        else:
            unresolved=prep.setdefault("unrated_new_opponents",[])
            if pokemon not in unresolved:
                unresolved.append(pokemon)
    return True


def _ensure(evidence: dict):
    for key in ("moves_by_pokemon","pp_by_pokemon","item_state_by_pokemon",
                "ability_state_by_pokemon","temporary_effects","fainted"):
        evidence.setdefault(key,{"own":{},"opponent":{}})
    evidence.setdefault("update_provenance",[])
    evidence.setdefault("boost_descriptions",{"own":{},"opponent":{}})
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
    trusted_names={}
    for record in records:
        field=getattr(record,"field","")
        frame=str(getattr(record,"frame_id",""))
        if field in ("screen_text.own_active_name",
                     "screen_text.opponent_active_name"):
            side="own" if field=="screen_text.own_active_name" else "opponent"
            names=own if side=="own" else opponents
            name=names.get(_flat(getattr(record,"text","")))
            conf=getattr(record,"confidence",None)
            if (name and getattr(record,"status","")=="CANDIDATE"
                    and isinstance(conf,(int,float)) and conf>=_CONFIDENCE_MIN):
                named_frames.add((frame,side))
                trusted_names[(frame,side)]=name
        elif field.endswith("battle_message"):
            event=_event_for_message(str(getattr(record,"text","")),own,opponents)
            if event and event.get("kind")=="SWITCH":
                switching_frames.add((frame,event.get("side")))
    names_seen=updated.setdefault("_ocr_name_attestations",{})
    transition_time=updated.setdefault("_ocr_actor_transition_at",{})
    # Order by capture time, then SWITCH and name before HUD.
    def _record_order(rec):
        field=getattr(rec,"field","")
        event=(_event_for_message(str(getattr(rec,"text","")),own,opponents)
               if field.endswith("battle_message") else None)
        priority=(0 if event and event.get("kind")=="SWITCH" else
                  1 if field in ("screen_text.own_active_name",
                                 "screen_text.opponent_active_name") else 2)
        return float(getattr(rec,"timestamp",0)),priority
    for rec in sorted(records,key=_record_order):
        text=str(getattr(rec,"text","") or "")
        field=getattr(rec,"field","")
        from hashlib import sha256
        digest=sha256(_flat(text).encode("utf-8")).hexdigest()[:20]
        ref=(str(getattr(rec,"frame_id","")),field,digest)
        observed_at=float(getattr(rec,"timestamp",0))
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
                             if any(_pokemon(part)==_pokemon(actor)
                                    for part in m["name"].split("→"))),None)
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
        if (evt["kind"] in ("ACTIVE","HP","PP") and
                observed_at<transition_time.get(evt["side"],float("-inf"))):
            audit.append({"kind":evt["kind"],"status":"CANDIDATE",
                          "reason":"PRE_SWITCH_STALE_HUD","frame_id":ref[0]})
            continue
        if evt["kind"] in ("HP","PP") and (ref[0],evt.get("side")) in switching_frames and (
                ref[0],evt.get("side")) not in named_frames:
            audit.append({"kind":evt["kind"],"status":"CANDIDATE",
                          "reason":"SAME_FRAME_SWITCH_HUD_AMBIGUOUS",
                          "frame_id":ref[0]})
            continue
        if not trusted:
            audit.append({"kind":evt["kind"],"status":"CANDIDATE",
                          "reason":"LOW_CONFIDENCE_OR_CONFLICT","frame_id":ref[0]})
            continue
        kind,side=evt["kind"],evt.get("side")
        state_delta=True
        name=evt.get("pokemon")
        old=updated.get("our_active" if side=="own" else "opponent_active")
        if kind in ("HP","PP"):
            same_frame=trusted_names.get((ref[0],side))
            attestation=names_seen.get(side,{})
            good=(same_frame==old or
                  (attestation.get("pokemon")==old and
                   0<=observed_at-attestation.get("timestamp",float("-inf"))<=2.0))
            if (not old or not good or
                    ((ref[0],side) in switching_frames and same_frame!=old)):
                audit.append({"kind":kind,"status":"CANDIDATE",
                              "reason":"ACTOR_HUD_NOT_ATTESTED","frame_id":ref[0]})
                continue
        if kind in ("ACTIVE","SWITCH"):
            state_delta=(name!=old)
            key="our_active" if side=="own" else "opponent_active"
            if name!=old:
                # The same exact Pokémon-name ROI must be read after any
                # stale switch proposal; OCR message itself is execution proof.
                if name and old:
                    evidence["boosts"][side].pop(old,None)
                    evidence["boost_descriptions"][side].pop(old,None)
                    for temporary in evidence["temporary_effects"][side].get(old,{}).values():
                        if isinstance(temporary,dict):
                            temporary["active"]=False
                            temporary["requires_reconfirmation_after_switch"]=True
                if side=="own":
                    evidence["choice_lock"]={"pokemon":"","move":"","source":""}
                    updated["awaiting_replacement"]=False
                updated[key]=name
                transition_time[side]=observed_at
                names_seen.pop(side,None)
                if side=="opponent":
                    fill_unknown_preview(updated,assets,name)
            if kind=="ACTIVE" and name:
                names_seen[side]={"pokemon":name,"timestamp":observed_at,
                                  "frame_id":ref[0]}
        elif kind=="PP":
            actor=updated.get("our_active")
            if not actor or actor!=name or updated.get("awaiting_replacement"):
                audit.append({"kind":kind,"status":"CANDIDATE",
                              "reason":"ACTIVE_NOT_CONFIRMED","frame_id":ref[0]})
                continue
            pps=evidence["pp_by_pokemon"]["own"].setdefault(actor,{})
            state_delta=pps.get(evt["move"])!=evt["pp"]
            pps[evt["move"]]=evt["pp"]
        elif kind=="HP":
            actor=updated.get("our_active" if side=="own" else "opponent_active")
            if not actor or (side=="own" and updated.get("awaiting_replacement")):
                audit.append({"kind":kind,"status":"CANDIDATE",
                              "reason":"ACTIVE_NOT_CONFIRMED","frame_id":ref[0]})
                continue
            hp=evidence.setdefault("hp_by_pokemon",{
                "own":{},"opponent":{}})[side]
            state_delta=(hp.get(actor)!=evt["hp"] or
                         updated.setdefault("observed_hp",{}).get(side)!=evt["hp"])
            hp[actor]=evt["hp"]
            updated["observed_hp"][side]=evt["hp"]
        elif name is not None:
            if kind=="FAINT":
                names_seen.pop(side,None)
                transition_time[side]=observed_at
                evidence.setdefault("hp_by_pokemon",{"own":{},"opponent":{}})[side][name]="0"
                evidence["fainted"][side][name]=True
                if side=="own":
                    updated["awaiting_replacement"]=True
                    evidence["choice_lock"]={"pokemon":"","move":"","source":""}
            elif kind=="MEGA":
                # The player's registered form is reliable only if exactly
                # one post-mega variant belongs to this registered Pokémon.
                form="MEGA_EVOLVED_FORM_UNCONFIRMED"
                if side=="own":
                    candidates=[m["name"].split("→")[-1].strip()
                                for m in assets.own_team
                                if len(m["name"].split("→"))==2 and
                                _pokemon(m["name"].split("→")[0])==name]
                    if len(candidates)==1:
                        form=candidates[0]
                # Use the frozen Champions matrix only. Unknown type stays unknown.
                if form!="MEGA_EVOLVED_FORM_UNCONFIRMED":
                    frozen=getattr(assets,"matrix_index",{}).get(form)
                    if isinstance(frozen,str):
                        row=frozen.split("|")
                        types=row[1].split("/") if len(row)>2 else []
                        if types and all(t in _TYPES for t in types):
                            evidence.setdefault("current_type",{
                                "own":{},"opponent":{}})[side][name]=row[1]
                updated["our_current_form" if side=="own"
                        else "opponent_current_form"]=form
                evidence.setdefault("form_by_pokemon",{
                    "own":{},"opponent":{}})[side][name]=form
            elif kind=="TYPE":
                evidence.setdefault("current_type",{
                    "own":{},"opponent":{}})[side][name]=evt["current_type"]
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
                if isinstance(entry,str):
                    parsed=_visual_stages(entry)
                    if parsed is not None:
                        entry=parsed
                    else:
                        evidence["boost_descriptions"][side][name]=entry
                        entry={}
                if not isinstance(entry,dict):
                    entry={}
                if name in evidence["boost_descriptions"][side]:
                    entry["_prior_stage_uncertain"]=True
                prior=entry.get(evt["stat"])
                if isinstance(prior,int):
                    entry[evt["stat"]]=max(-6,min(6,prior+evt["delta"]))
                elif not entry.get("_prior_stage_uncertain"):
                    entry[evt["stat"]]=max(-6,min(6,evt["delta"]))
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
                items=evidence.setdefault("item_by_pokemon",{
                    "own":{},"opponent":{}})[side]
                if items.get(name)==evt["item"]:
                    items.pop(name,None)
                if side=="own":
                    registered=next((m for m in assets.own_team
                        if any(_pokemon(part)==name
                               for part in m.get("name","").split("→"))),None)
                    if (registered and registered.get("item")==evt["item"]
                            and evidence.get("choice_lock",{}).get("pokemon")==name):
                        evidence["choice_lock"]={"pokemon":"","move":"","source":""}
            elif kind=="ABILITY":
                evidence.setdefault("ability_by_pokemon",{"own":{},"opponent":{}})[side][name]=evt["ability"]
                evidence["ability_state_by_pokemon"][side][name]={"active":True,"name":evt["ability"]}
        elif kind in ("WEATHER","TERRAIN"):
            key="weather" if kind=="WEATHER" else "terrain"
            state_delta=battlefield.get(key)!=evt["value"]
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
        if state_delta:
            updated["_live_state_revision"]=updated.get("_live_state_revision",0)+1
        audit.append({"kind":kind,"status":"APPLIED","side":side or "",
                      "frame_id":ref[0],"revision":updated.get("_live_state_revision",0)})
        provenance=evidence["update_provenance"]
        provenance.append({"kind":kind,"source":getattr(rec,"source","LOCAL_OCR"),
                           "frame_id":ref[0],"confidence":conf})
        del provenance[:-50]
    return updated,audit
