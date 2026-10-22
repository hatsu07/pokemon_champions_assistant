"""Backward-compatible Pokémon species normalizer from the complete roster.

English is allowed in internal vision output. Names are translated to
Champions-registered Japanese labels at the validation/display boundary.
"""
from __future__ import annotations
from .terminology import japanese_name


def selection_base_name(value: str) -> str:
    # Registered Mega labels after an arrow are not the preview species.
    return japanese_name("pokemon", value.split("→", 1)[0].strip())
