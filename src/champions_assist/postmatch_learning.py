"""Bounded post-match ChatGPT review; NEVER run in live selection/PREP/turn.

The model can recommend new *hypotheses* backed by observed context but
cannot mark a safe switch/KO/processor as game-confirmed.
"""
from __future__ import annotations

import json
from pathlib import Path

from .app_config import ConfigurationError
from .matchup_learning_store import MatchupLearningStore
from .reasoning_config import reasoning_request_kwargs

REVIEW_SCHEMA = "champions-postmatch-review-v1"
_RESPONSE = {
    "type":"object","additionalProperties":False,
    "properties":{
        "schema":{"type":"string"},
        "match_id":{"type":"string"},
        "findings":{"type":"array","items":{
            "type":"object","additionalProperties":False,
            "properties":{k:{"type":"string"} for k in
                          ("opponent","our_pokemon","observation","issue",
                           "suggestion","certainty")},
            "required":["opponent","our_pokemon","observation","issue",
                        "suggestion","certainty"]}},
        "next_match_notes":{"type":"string"},
    },
    "required":["schema","match_id","findings","next_match_notes"],
}


def request_review(*, data_root: str | Path, match_id: str, access_token: str,
                   model: str, reasoning_effort: str = "auto",
                   client_factory=None) -> dict:
    store=MatchupLearningStore(data_root)
    pending=store.pending_match(match_id)
    if pending is None:
        return {"status":"NO_PENDING_MATCH","match_id":match_id}
    if client_factory is None:
        from openai import OpenAI
        client_factory=OpenAI
    prompt={
        "schema":REVIEW_SCHEMA,
        "match_id":match_id,
        "team_id":pending["team_id"],
        "observed_game_data":pending["snapshot"],
    }
    instructions=(
        "Review an ended Pokémon Champions singles match ONLY, using the "
        "provided observation candidate data and no other game's rules. "
        "All images were read by AI, not human verified. Winning/losing by itself "
        "does not prove a species matchup or safe switch. Separate what screen "
        "observations suggest from tactical hypotheses, and do NOT invent "
        "opponent moves, exact damage, speed order, abilities, KO, battle mechanics "
        "or actions absent from the observation. The suggestions concern "
        "future matches and are NOT live instructions. "
        "Give at most 5 concise findings for material deviations/risks, "
        "each labeled OBSERVED_CANDIDATE or HYPOTHESIS. "
        "Only include opponent names from opponent_six and user names from "
        "selected_three; if none identifiable use empty findings. "
        "Use no more than 180 characters in any finding field or 360 in "
        "next_match_notes. Provide cautious Japanese tactical review. "
        "Do not assume the AI's recommendation was actually executed. "
        "Return exact JSON fields and match id, no extra fields.")
    client=client_factory(api_key=access_token,
                          base_url="https://api.openai.com/v1",timeout=90,max_retries=0)
    stream=client.responses.create(
        model=model,store=False,stream=True,
        **reasoning_request_kwargs(reasoning_effort),
        instructions=instructions,
        input=[{"role":"user","content":[{
            "type":"input_text","text":json.dumps(prompt,ensure_ascii=False)}]}],
        text={"format":{"type":"json_schema","name":"champions_postmatch_review",
                        "strict":True,"schema":_RESPONSE}},
    )
    parts=[]
    ended=False
    if hasattr(stream,"__enter__"):
        with stream:
            for event in stream:
                if event.type=="response.output_text.delta":
                    parts.append(str(event.delta))
                elif event.type=="response.completed":
                    ended=True
                elif event.type in ("response.failed","response.incomplete","error"):
                    raise ConfigurationError("対戦後AI評価が完了しませんでした")
    else:
        raise ConfigurationError("対戦後AIの通信形式が不正です")
    if not ended or not parts:
        raise ConfigurationError("対戦後AIの出力が空または未完了です")
    review=json.loads("".join(parts))
    allowed_opponents=set(pending["snapshot"].get("opponent_six") or ())
    allowed_own=set(pending["snapshot"].get("selected_three") or ())
    if not isinstance(review,dict) or not isinstance(review.get("findings"),list):
        raise ConfigurationError("対戦後AI回答の形式が不正です")
    for item in review["findings"]:
        if (not isinstance(item,dict)
                or (item.get("opponent") and item["opponent"] not in allowed_opponents)
                or (item.get("our_pokemon") and item["our_pokemon"] not in allowed_own)):
            raise ConfigurationError("対戦後AIが未出場の対面を作り出しました")
    stored=store.save_postmatch_review(match_id=match_id,review=review)
    return {"status":"STORED_UNVERIFIED_REVIEW" if stored else "MATCH_GONE",
            "match_id":match_id,"findings":len(review["findings"])}
