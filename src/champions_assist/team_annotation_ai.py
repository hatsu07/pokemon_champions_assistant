"""Optional AI drafts for role/form_change/runtime_note.

Never silently mutate a registered team: returns editable suggestions only.
The form field may only echo a form already explicitly registered in the
team's own name; an AI guess cannot invent a new Champions form.
"""
from __future__ import annotations

import json
from .app_config import ConfigurationError
from .reasoning_config import reasoning_request_kwargs

SCHEMA = "champions-team-annotation-draft-v1"
_RESPONSE={
    "type":"object","additionalProperties":False,
    "properties":{
        "schema":{"type":"string"},
        "suggestions":{"type":"array","items":{
            "type":"object","additionalProperties":False,
            "properties":{
                "slot":{"type":"integer"},
                "role":{"type":"string"},
                "form_change":{"type":"string"},
                "runtime_note":{"type":"string"},
            },
            "required":["slot","role","form_change","runtime_note"]}},
    },
    "required":["schema","suggestions"],
}


def validate_annotation_draft(members: list[dict], value: dict) -> list[dict]:
    if (not isinstance(value,dict) or set(value)!={"schema","suggestions"}
            or value["schema"]!=SCHEMA or not isinstance(value["suggestions"],list)
            or len(value["suggestions"])!=len(members)):
        raise ConfigurationError("構築AI入力案の形式が不正です")
    by_slot={}
    for item in value["suggestions"]:
        if (not isinstance(item,dict)
                or set(item)!={"slot","role","form_change","runtime_note"}
                or type(item["slot"]) is not int
                or not 0<=item["slot"]<len(members)
                or item["slot"] in by_slot):
            raise ConfigurationError("構築AI入力案の行が不正です")
        for k in ("role","form_change","runtime_note"):
            field=item[k]
            if (not isinstance(field,str) or len(field)>180
                    or any(c in field for c in ("\r","\n","<",">"))):
                raise ConfigurationError("構築AI入力案に不正な文字列があります")
        own_name=members[item["slot"]].get("name","")
        allowed_form=("" if "→" not in own_name else
                      own_name.replace(" → ","=>").replace("→","=>"))
        if item["form_change"] not in ("",allowed_form):
            raise ConfigurationError("固定マスター外のフォーム変化を入力案に採用できません")
        by_slot[item["slot"]]=item
    return [by_slot[i] for i in range(len(members))]


def request_annotation_draft(members: list[dict], *,
                             model: str, access_token: str,
                             reasoning_effort: str="auto",
                             client_factory=None) -> list[dict]:
    if len(members)!=6:
        raise ConfigurationError("自軍6匹の登録が必要です")
    if client_factory is None:
        from openai import OpenAI
        client_factory=OpenAI
    only=("name","item","ability","nature","ap_h_a_b_c_d_s",
          "move1","move2","move3","move4","form_change")
    prompt={"schema":SCHEMA,"registered_members":[{
        "slot":i,**{k:(m.get(k) or "") for k in only}} for i,m in enumerate(members)]}
    instructions=(
        "Pokémon Champions singles only. Suggest concise 6 member annotations "
        "for the three optional CSV fields role, form_change, runtime_note. "
        "Roles are flexible hints (LEAD, PIVOT, STOPPER, ENDGAME etc), NEVER fixed "
        "team selection constraints. Notes are brief caution flags about "
        "registered item/choice lock, form change and move use. "
        "Never invent unavailable moves, unseen abilities, KO or safe switches. "
        "For form_change output empty string unless Pokémon name contains '→', "
        "then use exactly that original name with arrow converted to '=>'. "
        "Keep every field <=180 chars, concise, no newline, avoid unsupported "
        "Champions-specific mechanics; user reviews all proposals before save. "
        "Return exactly slots 0..5 and schema, nothing else.")
    client=client_factory(api_key=access_token,base_url="https://api.openai.com/v1",
                          timeout=90,max_retries=0)
    stream=client.responses.create(
        model=model,store=False,stream=True,
        **reasoning_request_kwargs(reasoning_effort),
        instructions=instructions,
        input=[{"role":"user","content":[{
            "type":"input_text","text":json.dumps(prompt,ensure_ascii=False)}]}],
        text={"format":{"type":"json_schema","name":"champions_team_annotation",
                        "strict":True,"schema":_RESPONSE}})
    chunks=[]
    completed=False
    with stream:
        for event in stream:
            if event.type=="response.output_text.delta":
                chunks.append(str(event.delta))
            elif event.type=="response.completed":
                completed=True
            elif event.type in ("response.failed","response.incomplete","error"):
                raise ConfigurationError("構築AI入力案が完了しませんでした")
    if not completed or not chunks:
        raise ConfigurationError("構築AI入力案が空か未完了です")
    return validate_annotation_draft(members,json.loads("".join(chunks)))
