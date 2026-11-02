"""Conservative P2 choice extraction from an explicit Showdown ChoiceRequest.

These are *request-derived candidates*, not a proof of Showdown legality.
The upstream Battle.fromJSON/p2.activeRequest extraction and per-choice
`makeChoices` acceptance will be added in a separate pinned-engine PR.
Never build a ChoiceRequest from an OCR snapshot or guessed move set.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from .models import FrozenBattleState, canonical_json, fingerprint

_MAX_P2_CHOICES = 24
_ID = re.compile(r"[a-z0-9]+\Z")
# Bound both decimal tokens before int() to keep hostile/malformed request
# fields from leaking CPython's integer digit-limit ValueError. These are
# synthetic request parsing limits, not assumptions about Champions HP caps.
_HP = re.compile(r"([0-9]{1,9})/([0-9]{1,9})(?: ([a-z]+))?\Z")
_FNT = re.compile(r"0 fnt\Z")

@dataclass(frozen=True)
class P2RequestEnvelope:
    # Detached serialized JSON from a synthetic, COMPLETE Showdown Battle
    # and the p2.activeRequest captured from that exact engine state.
    battle_json: str
    request_json: str
    battle_sha256: str
    snapshot_sha256: str
    simulator_sha: str
    provenance: str = "EXPLICIT_SYNTHETIC_REQUEST_UNATTESTED"


@dataclass(frozen=True)
class P2RequestChoices:
    status: str
    reason: str
    commands: tuple[str, ...] = ()
    battle_sha256: str = ""
    # No strategy may upgrade this result to legally complete without engine
    # validation on the exact pinned Battle. False for every output here.
    exhaustive_legal_choices_verified: bool = False


def _blocked(reason: str) -> P2RequestChoices:
    return P2RequestChoices("NO_CHOICES", reason)


def _condition(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    if _FNT.fullmatch(value):
        return (0, 0)
    match = _HP.fullmatch(value)
    if not match:
        return None
    try:
        hp, maximum = int(match[1]), int(match[2])
    except ValueError:
        return None
    status = match[3]
    if (maximum < 1 or hp < 0 or hp > maximum
            or (status == "fnt" and hp != 0)
            or (hp == 0 and status not in (None, "fnt"))):
        # A "fnt" flag and positive HP conflict, as do other statuses
        # on a zero-HP Pokémon. Never treat either as a live switch option.
        return None
    return hp, maximum


def _pokemon(request: dict[str, Any]) -> tuple[list[int], int, int] | None:
    side = request.get("side")
    if not isinstance(side, dict) or side.get("id") != "p2":
        return None
    roster = side.get("pokemon")
    if not isinstance(roster, list) or len(roster) != 3:
        return None
    bench = []
    active: list[tuple[int, int]] = []
    for index, item in enumerate(roster, 1):
        if not isinstance(item, dict) or not isinstance(item.get("active"), bool):
            return None
        hp = _condition(item.get("condition"))
        if hp is None:
            return None
        if item["active"]:
            active.append((index, hp[0]))
        elif hp[0] > 0:
            bench.append(index)
    if len(active) != 1:
        return None
    return bench, active[0][0], active[0][1]


def enumerate_simple_p2_request(
    snapshot: FrozenBattleState,
    envelope: P2RequestEnvelope,
) -> P2RequestChoices:
    """Return at most 24 conservative single-actor option candidates.

    Supported request states: simple singles MOVE (no form/choice uncertainty)
    and a single forced switch. Requires the supplied request to be explicitly
    tied by hash to a detached FULL synthetic Battle, **but cannot attest the
    request actually came from that engine**. Never directly pass commands to
    `run_shadow_turn` until an engine-side legal-choice verification exists.
    """
    if not isinstance(snapshot, FrozenBattleState) or not isinstance(envelope, P2RequestEnvelope):
        return _blocked("INVALID_P2_REQUEST_INPUT")
    try:
        if (envelope.provenance != "EXPLICIT_SYNTHETIC_REQUEST_UNATTESTED"
                or envelope.snapshot_sha256 != snapshot.snapshot_sha256
                or envelope.simulator_sha != snapshot.window.simulator_sha):
            return _blocked("STALE_OR_UNTRUSTED_REQUEST_BINDING")
        battle = json.loads(envelope.battle_json)
        request = json.loads(envelope.request_json)
        if (not isinstance(battle, dict) or not isinstance(request, dict)
                or fingerprint(battle) != envelope.battle_sha256
                or canonical_json(battle) != envelope.battle_json
                or canonical_json(request) != envelope.request_json):
            return _blocked("P2_REQUEST_BINDING_INVALID")
    except (TypeError, ValueError, AttributeError):
        return _blocked("P2_REQUEST_BINDING_INVALID")
    if not request or request.get("wait") or request.get("teamPreview"):
        return _blocked("UNSUPPORTED_P2_REQUEST_STATE")
    detail = _pokemon(request)
    if detail is None:
        return _blocked("P2_SINGLES_ROSTER_UNVERIFIED")
    bench, active_slot, active_hp = detail
    if active_slot not in (1, 2, 3):
        return _blocked("P2_SINGLES_ROSTER_UNVERIFIED")

    if "forceSwitch" in request:
        if (request.get("forceSwitch") != [True]
                or "active" in request):
            return _blocked("UNSUPPORTED_P2_REQUEST_STATE")
        commands = tuple(f"switch {n}" for n in bench)
        if not commands:
            return _blocked("P2_NO_VERIFIED_REQUEST_OPTIONS")
        return P2RequestChoices(
            "PROVISIONAL_REQUEST_OPTIONS", "FORCED_SWITCH_REQUEST_ONLY",
            commands, envelope.battle_sha256,
        )

    if "active" not in request or "forceSwitch" in request:
        return _blocked("UNSUPPORTED_P2_REQUEST_STATE")
    # A fainted active actor may be awaiting a replacement, but can never
    # supply an ordinary attack in a simple singles move request.
    if active_hp == 0:
        return _blocked("P2_SINGLES_ROSTER_UNVERIFIED")
    actors = request["active"]
    if not isinstance(actors, list) or len(actors) != 1 or not isinstance(actors[0], dict):
        return _blocked("UNSUPPORTED_P2_REQUEST_STATE")
    a = actors[0]
    # Any hidden disabled/locked/trapped state or form operation makes a
    # request-only list unsafe to treat as complete. Do not invent handling
    # based on older game mechanics.
    for flag in ("maybeDisabled", "maybeLocked", "maybeTrapped",
                 "canMegaEvo", "canMegaEvoX", "canMegaEvoY",
                 "canUltraBurst", "canZMove", "canDynamax",
                 "maxMoves", "canTerastallize"):
        if a.get(flag):
            return _blocked("P2_COMPLEX_CHOICE_REQUIRES_ENGINE_VALIDATION")
    if "trapped" in a and not isinstance(a["trapped"], bool):
        return _blocked("P2_COMPLEX_CHOICE_REQUIRES_ENGINE_VALIDATION")
    moves = a.get("moves")
    if not isinstance(moves, list) or not 1 <= len(moves) <= 4:
        return _blocked("P2_MOVE_REQUEST_INVALID")
    found = set()
    moves_allowed = []
    for i, item in enumerate(moves, 1):
        if not isinstance(item, dict):
            return _blocked("P2_MOVE_REQUEST_INVALID")
        move = item.get("id")
        pp = item.get("pp")
        disabled = item.get("disabled", False)
        if (not isinstance(move, str) or not _ID.fullmatch(move)
                or move in found
                or not isinstance(pp, int) or isinstance(pp, bool) or pp < 0
                or not isinstance(disabled, bool)
                or (disabled is False and pp == 0)):
            return _blocked("P2_MOVE_REQUEST_INVALID")
        found.add(move)
        if not disabled:
            moves_allowed.append(f"move {i}")
    if not moves_allowed:
        return _blocked("P2_NO_VERIFIED_REQUEST_OPTIONS")
    commands = tuple(moves_allowed + ([] if a.get("trapped", False) else [
        f"switch {n}" for n in bench
    ]))
    if len(commands) > _MAX_P2_CHOICES:
        return _blocked("P2_CHOICE_LIMIT_EXCEEDED")
    return P2RequestChoices(
        "PROVISIONAL_REQUEST_OPTIONS", "REQUEST_DERIVED_NOT_ENGINE_ATTESTED",
        commands, envelope.battle_sha256,
    )
