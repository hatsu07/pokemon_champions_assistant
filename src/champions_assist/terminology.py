"""Names-only bilingual translation for the frozen Pokémon Champions catalogue.

This file reads the packaged JSON *once* when imported (before model work).
It never imports another game's mechanics, move legality, stats or formats.
Unrecognized English names remain untouched: no fuzzy or AI guesswork.
"""
from __future__ import annotations

import json
from importlib.resources import files
import re
import unicodedata


def _key(value: str) -> str:
    return re.sub(r"[\\s_\\-‐–—’']", "", unicodedata.normalize("NFKC", value).strip().casefold())


def _read_catalogue() -> dict:
    path = files("champions_assist").joinpath("data/champions_name_aliases.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise RuntimeError("Champions bilingual name data version mismatch")
    return data


_CATALOGUE = _read_catalogue()
_ALIASES = _CATALOGUE["aliases"]
_ALLOWED_CATEGORIES = frozenset(_ALIASES)


def japanese_name(category: str, value: str) -> str:
    """Return a known canonical Japanese name, or the original if unknown."""
    if category not in _ALLOWED_CATEGORIES:
        raise ValueError("unknown Champions name category")
    if not isinstance(value, str):
        raise ValueError("name must be a string")
    return _ALIASES[category].get(_key(value), value.strip())


def mapped_name(category: str, value: str) -> bool:
    """True only if the exact name is included in the frozen name dictionary."""
    if category not in _ALLOWED_CATEGORIES:
        raise ValueError("unknown Champions name category")
    return isinstance(value, str) and _key(value) in _ALIASES[category]


def name_catalogue_stats() -> dict:
    return dict(_CATALOGUE["counts"])


def translate_extraction_fields(fields: dict) -> dict:
    """Translate structured names only; never rewrite unparsed battle messages."""
    value = dict(fields)
    for field, category in (("pokemon", "pokemon"), ("moves", "move"),
                            ("items", "item"), ("abilities", "ability"),
                            ("natures", "nature"), ("types", "type")):
        names = value.get(field)
        if isinstance(names, list):
            value[field] = [japanese_name(category, name) if isinstance(name, str)
                            else name for name in names]
    return value
