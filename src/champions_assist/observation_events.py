"""Declarative OCR-event normalization with no cross-game mechanics inference."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
import unicodedata
from typing import Any, Iterable, Mapping

from .ocr import OcrRead


class EventCertainty(str, Enum):
    CONFIRMED = "CONFIRMED"
    CANDIDATE = "CANDIDATE"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"


class ObservedEventKind(str, Enum):
    MOVE_USED = "MOVE_USED"
    MOVE_RESOLVED = "MOVE_RESOLVED"
    MOVE_FAILED = "MOVE_FAILED"
    MOVE_MISSED = "MOVE_MISSED"
    STAT_STAGE_CHANGED = "STAT_STAGE_CHANGED"
    STATUS_APPLIED = "STATUS_APPLIED"
    STATUS_CLEARED = "STATUS_CLEARED"
    HAZARD_SET = "HAZARD_SET"
    HAZARD_REMOVED = "HAZARD_REMOVED"
    HAZARD_LAYER_CHANGED = "HAZARD_LAYER_CHANGED"
    WEATHER_SET = "WEATHER_SET"
    WEATHER_ENDED = "WEATHER_ENDED"
    WEATHER_OVERRIDDEN = "WEATHER_OVERRIDDEN"
    TERRAIN_SET = "TERRAIN_SET"
    TERRAIN_ENDED = "TERRAIN_ENDED"
    TERRAIN_OVERRIDDEN = "TERRAIN_OVERRIDDEN"
    SCREEN_SET = "SCREEN_SET"
    SCREEN_ENDED = "SCREEN_ENDED"
    SCREEN_BROKEN = "SCREEN_BROKEN"
    SCREEN_SIDE_CHANGED = "SCREEN_SIDE_CHANGED"
    MEGA_EVOLVED = "MEGA_EVOLVED"
    FORM_CHANGED = "FORM_CHANGED"
    TYPE_CHANGED = "TYPE_CHANGED"
    ABILITY_ACTIVATED = "ABILITY_ACTIVATED"
    ITEM_ACTIVATED = "ITEM_ACTIVATED"
    ITEM_CONSUMED = "ITEM_CONSUMED"
    ITEM_REVEALED = "ITEM_REVEALED"
    HP_CHANGED = "HP_CHANGED"
    SWITCH_OCCURRED = "SWITCH_OCCURRED"
    ACTIVE_CHANGED = "ACTIVE_CHANGED"
    FAINTED = "FAINTED"
    TURN_END_CONFIRMED = "TURN_END_CONFIRMED"
    MATCH_FINISHED = "MATCH_FINISHED"
    EFFECT_SWITCH_CHOICE_AVAILABLE = "EFFECT_SWITCH_CHOICE_AVAILABLE"
    RANDOM_FORCED_SWITCH = "RANDOM_FORCED_SWITCH"
    EFFECT_AUTO_SWITCH = "EFFECT_AUTO_SWITCH"


@dataclass(frozen=True)
class ScreenLexicon:
    """Exact observed terms only; no fuzzy correction or game-rule completion."""

    pokemon_names: tuple[str, ...] = ()
    move_names: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    hp_pattern: str | None = None
    remaining_turn_pattern: str | None = None

    @classmethod
    def from_frozen_assets(cls, assets: Any, *, opponent_names: Iterable[str] = ()) -> "ScreenLexicon":
        """Build an exact OCR vocabulary from already frozen match data only."""
        own_names = tuple(str(member["name"]) for member in getattr(assets, "own_team", ()) if member.get("name"))
        own_moves = tuple(
            str(member[key])
            for member in getattr(assets, "own_team", ())
            for key in ("move1", "move2", "move3", "move4")
            if member.get(key)
        )
        return cls(
            pokemon_names=tuple(dict.fromkeys((*own_names, *(str(item) for item in opponent_names)))),
            move_names=tuple(dict.fromkeys(own_moves)),
        )


@dataclass(frozen=True)
class EventPattern:
    """A calibration-supplied text rule, never a claim about real game wording."""

    pattern_id: str
    regex: str
    event_kind: ObservedEventKind
    certainty: EventCertainty = EventCertainty.CANDIDATE
    facts: Mapping[str, Any] = field(default_factory=dict)
    effect_instance_template: str | None = None
    confirm_effect: bool = False
    duration: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ObservedEvent:
    event_id: str
    kind: ObservedEventKind
    certainty: EventCertainty
    raw_observation_id: str
    roi_name: str
    text: str
    ocr_confidence: float | None
    facts: dict[str, Any]
    effect_instance_id: str | None = None
    confirm_effect: bool = False
    duration: dict[str, Any] | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "certainty": self.certainty.value,
            "raw_observation_id": self.raw_observation_id,
            "roi_name": self.roi_name,
            "text": self.text,
            "ocr_confidence": self.ocr_confidence,
            "facts": self.facts,
            "effect_instance_id": self.effect_instance_id,
            "confirm_effect": self.confirm_effect,
            "duration": self.duration,
        }


@dataclass(frozen=True)
class ParsedOcr:
    text: str
    events: tuple[ObservedEvent, ...]
    detected_pokemon: tuple[str, ...]
    detected_moves: tuple[str, ...]
    detected_statuses: tuple[str, ...]
    hp_values: tuple[tuple[int, int], ...]
    remaining_turns: tuple[tuple[int, int], ...]


def _expand(value: Any, match: re.Match[str]) -> Any:
    if isinstance(value, str):
        return re.sub(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", lambda item: match.groupdict().get(item.group(1), item.group(0)), value)
    if isinstance(value, list):
        return [_expand(item, match) for item in value]
    if isinstance(value, dict):
        return {str(key): _expand(item, match) for key, item in value.items()}
    return value


class TextObservationParser:
    """Normalize only configured phrases and explicit lexical matches."""

    def __init__(self, patterns: Iterable[EventPattern] = (), *, lexicon: ScreenLexicon | None = None) -> None:
        self.patterns = tuple(patterns)
        if len({pattern.pattern_id for pattern in self.patterns}) != len(self.patterns):
            raise ValueError("event pattern ids must be unique")
        self.lexicon = lexicon or ScreenLexicon()

    def parse(self, reading: OcrRead, *, roi_name: str, raw_observation_id: str, episode_id: int) -> ParsedOcr:
        text = reading.text.strip()
        # Japanese OCR may insert whitespace within a Pokémon/move name.
        # Normalize both the reading and the frozen Champions lexicon
        # before matching. These are *candidate* spans, not action proof.
        normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
        def has_term(term: str) -> bool:
            canonical=re.sub(r"\s+", "", unicodedata.normalize("NFKC",term))
            return bool(canonical) and canonical in normalized
        pokemon = tuple(item for item in self.lexicon.pokemon_names if has_term(item))
        moves = tuple(item for item in self.lexicon.move_names if has_term(item))
        statuses = tuple(item for item in self.lexicon.statuses if has_term(item))
        hp_values = self._numbers(self.lexicon.hp_pattern, text)
        remaining_turns = self._numbers(self.lexicon.remaining_turn_pattern, text)
        events: list[ObservedEvent] = []
        for pattern in self.patterns:
            match = re.search(pattern.regex, text)
            if match is None:
                continue
            facts = _expand(dict(pattern.facts), match)
            event_id = f"{raw_observation_id}:episode:{episode_id}:{pattern.pattern_id}"
            effect_id = None if pattern.effect_instance_template is None else str(_expand(pattern.effect_instance_template, match))
            duration = None if pattern.duration is None else _expand(dict(pattern.duration), match)
            events.append(
                ObservedEvent(
                    event_id=event_id,
                    kind=pattern.event_kind,
                    certainty=pattern.certainty,
                    raw_observation_id=raw_observation_id,
                    roi_name=roi_name,
                    text=text,
                    ocr_confidence=reading.confidence,
                    facts=facts,
                    effect_instance_id=effect_id,
                    confirm_effect=pattern.confirm_effect,
                    duration=duration,
                )
            )
        return ParsedOcr(text, tuple(events), pokemon, moves, statuses, hp_values, remaining_turns)

    @staticmethod
    def _numbers(pattern: str | None, text: str) -> tuple[tuple[int, int], ...]:
        if pattern is None:
            return ()
        values: list[tuple[int, int]] = []
        for match in re.finditer(pattern, text):
            try:
                values.append((int(match.group(1)), int(match.group(2))))
            except (IndexError, ValueError):
                continue
        return tuple(values)


class MessageEpisodeTracker:
    """Deduplicate a continuously displayed message but permit a later repeat."""

    def __init__(self) -> None:
        self._active_signatures: dict[str, str] = {}
        self._episodes: dict[str, int] = {}

    def observe(self, *, roi_name: str, image_signature: str, text: str) -> int | None:
        if not text.strip():
            self._active_signatures.pop(roi_name, None)
            return None
        if self._active_signatures.get(roi_name) == image_signature:
            return None
        self._active_signatures[roi_name] = image_signature
        self._episodes[roi_name] = self._episodes.get(roi_name, 0) + 1
        return self._episodes[roi_name]
