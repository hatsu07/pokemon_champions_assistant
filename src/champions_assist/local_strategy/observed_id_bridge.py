"""Read-only Champions display-evidence -> *staged* Showdown ID projection.

This adapter uses the CURRENT WINDOW and actually frozen four-master team bytes.
It never translates unknown Japanese names or invents enemy team slot, RNG,
event ordering, moveset, held items or status IDs. Output is NOT a Battle JSON
and MUST NOT be passed to synthetic replay auditor or LIVE recommendation.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .models import FrozenBattleState
from .simulator_adapter import WorkerUnavailable, _verified_roster_binding

_EXACT_HP = re.compile(r"([0-9]{1,6})/([0-9]{1,6})\Z")
_ZERO_HP = re.compile(r"0\Z")
_BOOST_NAMES = {
    "A": "atk", "B": "def", "C": "spa", "D": "spd",
    "S": "spe", "ACC": "accuracy", "EVA": "evasion",
}


@dataclass(frozen=True)
class VisibleMember:
    # Own team IDs have provenance in the *current frozen team CSV*.
    # Opponent IDs and its three-party slot are intentionally absent.
    display_name: str
    side: str
    own_party_slot: int | None
    registered_species_id: str | None
    registered_move_ids: tuple[tuple[str, str], ...]
    revealed_move_ids: tuple[str, ...]
    observed_moves_display: tuple[str, ...]
    hp: int | None
    maxhp: int | None
    fainted: bool | None
    boosts: tuple[tuple[str, int], ...]
    raw_status: str | None
    raw_current_type: str | None
    raw_item: str | None
    raw_ability: str | None
    raw_form: str | None


@dataclass(frozen=True)
class ObservationIdStage:
    status: str
    reason: str
    snapshot_sha256: str = ""
    match_id: str = ""
    decision_window_id: str = ""
    state_revision: int = -1
    own_members: tuple[VisibleMember, ...] = ()
    opponent: VisibleMember | None = None
    other_observed_opponents: tuple[VisibleMember, ...] = ()
    own_active_slot: int | None = None
    unbound: tuple[str, ...] = ()
    # These flags may not be promoted by any caller on this stage alone.
    full_state_reconstructed: bool = False
    opponent_joint_set_verified: bool = False
    ready_for_engine: bool = False


def _refuse(reason: str) -> ObservationIdStage:
    return ObservationIdStage("NO_OBSERVATION_ID_STAGE", reason)


def _value(value: Any) -> str:
    """Only exact, non-placeholder visible literals; no loose coercion."""
    if (not isinstance(value, str) or not value
            or value != value.strip()
            or value.upper() in {"UNKNOWN", "NONE", "?"}
            or len(value) > 160):
        raise ValueError("UNVERIFIED_VISIBLE_VALUE")
    return value


def _section(memory: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    raw = memory.get(key, {"own": {}, "opponent": {}})
    if (not isinstance(raw, dict) or
            any(k not in ("own", "opponent") for k in raw)):
        raise ValueError("MALFORMED_VISIBLE_EVIDENCE")
    for side in ("own", "opponent"):
        if side in raw and not isinstance(raw[side], dict):
            raise ValueError("MALFORMED_VISIBLE_EVIDENCE")
    return {"own": raw.get("own", {}), "opponent": raw.get("opponent", {})}


def _hp(value: Any, *, allow_own_faint_marker: bool) -> tuple[int, int | None, bool]:
    raw = _value(value)
    # battle_evidence.py writes literal "0" for an observed own KO.
    # No positive bare integer or percentage is interpreted as exact HP.
    if _ZERO_HP.fullmatch(raw):
        if not allow_own_faint_marker:
            raise ValueError("OPPONENT_ZERO_HP_NEEDS_EXPLICIT_MAXIMUM")
        return (0, None, True)
    m = _EXACT_HP.fullmatch(raw)
    if m is None:
        raise ValueError("AMBIGUOUS_HP_SCALE_OR_FORMAT")
    hp, maximum = map(int, m.groups())
    if maximum < 1 or hp > maximum:
        raise ValueError("CONTRADICTORY_VISIBLE_HP")
    return (hp, maximum, hp == 0)


def _boosts(raw: Any) -> tuple[tuple[str, int], ...]:
    if (not isinstance(raw, dict) or not raw
            or any(k not in _BOOST_NAMES or not isinstance(v, int)
                   or isinstance(v, bool) or not -6 <= v <= 6
                   for k, v in raw.items())):
        raise ValueError("UNSUPPORTED_OR_MALFORMED_BOOSTS")
    return tuple((_BOOST_NAMES[k], raw[k]) for k in _BOOST_NAMES if k in raw)


def _current_member(name: str, side: str,
                    *, slot: int | None,
                    registered: dict[str, Any] | None,
                    sections: dict[str, dict[str, dict[str, Any]]],
                    ) -> VisibleMember:
    observations = {}
    for field, section_name in (
        ("hp", "hp_by_pokemon"), ("boosts", "boosts"),
        ("status", "status_by_pokemon"), ("type", "current_type"),
        ("moves", "moves_by_pokemon"),
        ("item","item_by_pokemon"),("ability","ability_by_pokemon"),
        ("form","form_by_pokemon"),
    ):
        section = sections[section_name][side]
        if name in section:
            observations[field] = section[name]
    hp, maximum, fainted = (None, None, None)
    if "hp" in observations:
        hp, maximum, fainted = _hp(
            observations["hp"],allow_own_faint_marker=(side=="own"))
    stages = _boosts(observations["boosts"]) if "boosts" in observations else ()
    raw_status = _value(observations["status"]) if "status" in observations else None
    raw_type = _value(observations["type"]) if "type" in observations else None
    raw_item = _value(observations["item"]) if "item" in observations else None
    raw_ability = (_value(observations["ability"])
                   if "ability" in observations else None)
    raw_form = _value(observations["form"]) if "form" in observations else None
    moves = observations.get("moves", [])
    if (not isinstance(moves, list) or len(moves) > 4
            or len(set(str(x) for x in moves)) != len(moves)):
        raise ValueError("MALFORMED_OBSERVED_MOVE_LIST")
    observed_moves = tuple(_value(move) for move in moves)
    registered_moves = tuple(registered["move_pairs"]) if registered else ()
    resolved = ()
    if registered is not None:
        by_name = dict(registered_moves)
        if any(move not in by_name for move in observed_moves):
            raise ValueError("OWN_MOVE_NOT_IN_FROZEN_TEAM")
        resolved = tuple(by_name[move] for move in observed_moves)
    return VisibleMember(
        name,side,slot,registered["species_id"] if registered else None,
        registered_moves,resolved,observed_moves,
        hp,maximum,fainted,stages,raw_status,raw_type,
        raw_item,raw_ability,raw_form,
    )


def stage_observed_champions_ids(snapshot: FrozenBattleState,
                                assets: Any) -> ObservationIdStage:
    """Stage precise facts, never certify any actual Showdown Battle state.

    The required own team IDs are checked against CURRENT match-frozen
    contents and FOUR source SHA. Neither the current opponent actor nor
    visible move names are automatically treated as Showdown IDs.
    Missing HP/status/type/boost never imply 0, healthy or base form.
    """
    if not isinstance(snapshot, FrozenBattleState):
        return _refuse("FROZEN_SNAPSHOT_REQUIRED")
    try:
        roster = _verified_roster_binding(snapshot, assets)
    except (WorkerUnavailable, ValueError, TypeError) as exc:
        return _refuse("FROZEN_OWN_TEAM_IDS_NOT_VERIFIED")
    try:
        state = snapshot.state
        if not isinstance(state, dict):
            raise ValueError("INVALID_OBSERVED_STATE")
        own_name = _value(state.get("our_active"))
        if own_name != snapshot.window.active_pokemon:
            raise ValueError("STALE_OWN_ACTOR")
        order = [x["name"] for x in roster]
        if own_name not in order:
            raise ValueError("OWN_ACTIVE_NOT_SELECTED")
        opponent_name = state.get("opponent_active", "")
        if opponent_name not in (None, ""):
            opponent_name = _value(opponent_name)
        else:
            opponent_name = ""
        preview = state.get("opponent_six", [])
        if (not isinstance(preview, list) or len(preview) not in (0, 6)
                or len(set(str(v) for v in preview)) != len(preview)
                or any(not isinstance(v, str) for v in preview)):
            raise ValueError("INVALID_OPPONENT_PREVIEW")
        preview = [_value(x) for x in preview]
        if opponent_name and preview and opponent_name not in preview:
            raise ValueError("OPPONENT_ACTIVE_NOT_IN_PREVIEW")
        memory = state.get("battle_evidence", {})
        if not isinstance(memory, dict):
            raise ValueError("MALFORMED_VISIBLE_EVIDENCE")
        sections = {
            field: _section(memory, field) for field in (
                "hp_by_pokemon", "boosts", "status_by_pokemon",
                "current_type", "moves_by_pokemon",
                "item_by_pokemon", "ability_by_pokemon",
                "form_by_pokemon",
            )
        }
        # This is the *selected* three, not the full registered six. A
        # stray own-team name could belong to a different battle or selection.
        known_own = set(order)
        allowed_opponent = set(preview) if preview else (
            {opponent_name} if opponent_name else set())
        for field, section in sections.items():
            for side, allowed in (("own", known_own),
                                  ("opponent", allowed_opponent)):
                if any(_value(name) not in allowed
                       for name in section[side]):
                    raise ValueError("EVIDENCE_ACTOR_NOT_IN_CURRENT_PARTY")
        # Retain only the active opponent as a display-name-only record:
        # the order of the chosen three and a full replay are unobserved.
        own_members = []
        for i, member in enumerate(roster, start=1):
            own = next(row for row in assets.own_team
                       if row["name"] == member["name"])
            ids = member["moves"]
            source_moves = [own[f"move{k}"] for k in range(1, 5)
                            if own.get(f"move{k}", "")]
            if (len(ids) != len(source_moves)
                    or len(set(source_moves)) != len(source_moves)):
                raise ValueError("FROZEN_TEAM_MOVE_BINDING_CHANGED")
            info = {"species_id": member["species_id"],
                    "move_pairs": tuple(zip(source_moves, ids))}
            own_members.append(_current_member(
                member["name"], "own", slot=i,
                registered=info, sections=sections))
        opponent = (_current_member(
            opponent_name, "opponent", slot=None,
            registered=None, sections=sections
        ) if opponent_name else None)
        # Preserve previously revealed opponents even if no longer active;
        # do not silently erase a known move/item/ability when switching.
        known_opponents = set().union(*(set(v["opponent"])
                                       for v in sections.values()))
        if opponent_name:
            known_opponents.add(opponent_name)
        other_names = (name for name in preview if name in known_opponents
                       and name != opponent_name) if preview else ()
        other_opponents = tuple(_current_member(
            name,"opponent",slot=None,registered=None,sections=sections)
            for name in other_names)
        visible = (*own_members, *((opponent,) if opponent else ()),
                   *other_opponents)
        unbound = [
            "OPPONENT_SELECTED_THREE_AND_PARTY_SLOT_UNKNOWN",
            "OPPONENT_SPECIES_AND_MOVE_SHOWDOWN_IDS_UNMAPPED",
            "HIDDEN_OPPONENT_SET_AND_RNG_UNKNOWN",
            "SHOWDOWN_EVENT_QUEUE_AND_EFFECT_TIMERS_UNKNOWN",
            "CHAMPIONS_MECHANICS_PARITY_UNVERIFIED",
        ]
        if any(m.raw_status is not None for m in visible):
            unbound.append("STATUS_TRANSLATION_NOT_VERIFIED")
        if any(m.raw_current_type is not None for m in visible):
            unbound.append("CURRENT_TYPE_TRANSLATION_NOT_VERIFIED")
        if any(m.observed_moves_display for m in
               (*((opponent,) if opponent else ()), *other_opponents)):
            unbound.append("REVEALED_OPPONENT_MOVE_IDS_UNMAPPED")
        if any(m.raw_item is not None or m.raw_ability is not None
               or m.raw_form is not None for m in visible):
            unbound.append("ITEM_ABILITY_AND_FORM_IDS_UNVERIFIED")
        return ObservationIdStage(
            "PARTIAL_OBSERVATION_ID_STAGE",
            "FROZEN_OWN_IDS_AND_ONLY_EXACT_VISIBLE_VALUES",
            snapshot.snapshot_sha256, snapshot.window.match_id,
            snapshot.window.decision_window_id,
            snapshot.window.state_revision, tuple(own_members),
            opponent, other_opponents, order.index(own_name) + 1,
            tuple(unbound)
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return _refuse("INVALID_OR_CONTRADICTORY_VISIBLE_EVIDENCE")
