"""Conservative battle observations for the live image-to-AI replay.

Only explicit screen evidence is promoted. Never infer a mechanic from a
different Pokémon game, nor treat a recommendation as an executed move.
"""
from __future__ import annotations

from .terminology import japanese_name


def _key(name: str) -> str:
    return japanese_name("pokemon", name.split("→", 1)[0].strip())


def _move_key(name: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKC", japanese_name("move", name).strip())


def _clean(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value[:160] if value and value.upper() not in ("UNKNOWN", "NONE", "?") else ""


# These strings are visible-evidence fields, not interpreted game mechanics.
OBSERVATION_FACT_FIELDS = (
    "our_used_move", "our_item_revealed", "opponent_item_revealed",
    "our_ability_revealed", "opponent_ability_revealed",
    "our_current_form", "opponent_current_form",
    "our_stat_changes", "opponent_stat_changes",
    "our_status", "opponent_status",
)


def empty_battle_evidence() -> dict:
    return {
        "hp_by_pokemon": {"own": {}, "opponent": {}},
        "item_by_pokemon": {"own": {}, "opponent": {}},
        "ability_by_pokemon": {"own": {}, "opponent": {}},
        "form_by_pokemon": {"own": {}, "opponent": {}},
        "status_by_pokemon": {"own": {}, "opponent": {}},
        "boosts": {"own": {}, "opponent": {}},
        "choice_lock": {"pokemon": "", "move": "", "source": ""},
        "last_confirmed_our_move": "",
    }


def update_battle_evidence(state: dict, observation: dict, own_team: tuple[dict, ...]) -> None:
    """Apply explicitly visible observations without making them tactical facts.

    CHOICE lock activates only on an observed completed move *and* a registered
    choice user. Lock clears on observed own active change. No implicit move
    acknowledgement, item activation, ability or Mega transformation.
    """
    memory = state.setdefault("battle_evidence", empty_battle_evidence())
    previous_own = _clean(state.get("our_active"))
    previous_opponent = _clean(state.get("opponent_active"))
    own = _clean(observation.get("our_active")) or previous_own
    opponent = _clean(observation.get("opponent_active")) or previous_opponent
    # An observed switch clears the user's move lock, not the registered item.
    if own and previous_own and _key(own) != _key(previous_own):
        memory["choice_lock"] = {"pokemon": "", "move": "", "source": ""}
        memory["boosts"]["own"].pop(previous_own, None)
    if opponent and previous_opponent and _key(opponent) != _key(previous_opponent):
        memory["boosts"]["opponent"].pop(previous_opponent, None)
    for team, active in (("own", own), ("opponent", opponent)):
        if not active:
            continue
        prefix = "our" if team == "own" else "opponent"
        hp = _clean(observation.get(prefix + "_hp"))
        if hp:
            memory["hp_by_pokemon"][team][active] = hp
        for field, source in (
            ("item_by_pokemon", prefix + "_item_revealed"),
            ("ability_by_pokemon", prefix + "_ability_revealed"),
            ("form_by_pokemon", prefix + "_current_form"),
            ("status_by_pokemon", prefix + "_status"),
            ("boosts", prefix + "_stat_changes"),
        ):
            value = _clean(observation.get(source))
            if value:
                memory[field][team][active] = value
    if _clean(observation.get("our_item_revealed")) and own:
        registered = next((m for m in own_team if _key(m.get("name", "")) == _key(own)), None)
        # If an explicitly revealed item differs from the registered choice
        # item, never keep a potentially invalid lock.
        if registered and _clean(observation["our_item_revealed"]) != _clean(registered.get("item")):
            memory["choice_lock"] = {"pokemon": "", "move": "", "source": ""}
    if observation.get("observed_event") == "OUR_FAINTED":
        memory["choice_lock"] = {"pokemon": "", "move": "", "source": ""}
        # Faint is a directly observed event. Record this actor as 0 HP
        # even when the numeric HP overlay was not visible in that frame.
        # Never transfer it to a different or unconfirmed Pokémon.
        fainted_actor=previous_own or own
        if fainted_actor:
            memory["hp_by_pokemon"]["own"][fainted_actor] = "0"
    move = _clean(observation.get("our_used_move"))
    if move and own and observation.get("visual_ui") == "ACTION_RESOLUTION":
        memory["last_confirmed_our_move"] = move
        member = next((m for m in own_team if _key(m.get("name", "")) == _key(own)), None)
        if member and member.get("choice_lock") == "Y":
            revealed_item = memory["item_by_pokemon"]["own"].get(own, "")
            if not revealed_item or revealed_item == member.get("item"):
                # Do not invent a lock on a move absent from the actual set.
                moves = [member.get("move" + str(n), "") for n in range(1, 5)]
                if _move_key(move) in {_move_key(x) for x in moves if x}:
                    memory["choice_lock"] = {
                        "pokemon": own, "move": move,
                        "source": "AI_VISUAL_RESOLVED_MOVE_AND_REGISTERED_CHOICE_ITEM",
                    }
    # The screen parser may not distinguish temporary form from permanent
    # Mega state. Retain text verbatim; do not silently infer types or stats.
