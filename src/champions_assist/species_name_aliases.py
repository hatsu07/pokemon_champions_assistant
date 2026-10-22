"""Exact, reviewed aliases for Champions team-preview species names.

The LIVE assets, registered team and six-row lookup use the canonical Japanese
species names. Visual models may return English even for a Japanese screenshot.
Do not use fuzzy matching: one incorrect substitution changes all 18 matchups.

This is deliberately a small, audited set of unambiguous BASE species. Add
more entries only after reviewing their official name and any regional/form
distinctions. Unknown aliases are left unchanged and blocked by existing
team/matrix validation. No prior-game stats, mechanics or learnsets are used.
"""
from __future__ import annotations

import unicodedata

# Names observed in the user's 2026-10-22 saved TEAM_SELECTION diagnostic.
ENGLISH_TO_JAPANESE_BASE_SPECIES = {
    "pikachu": "ピカチュウ",
    "kingambit": "ドドゲザン",
    "garchomp": "ガブリアス",
    "azumarill": "マリルリ",
    "gyarados": "ギャラドス",
    "gengar": "ゲンガー",
    "meowscarada": "マスカーニャ",
    "primarina": "アシレーヌ",
    "lucario": "ルカリオ",
    "gholdengo": "サーフゴー",
    "mimikyu": "ミミッキュ",
}


def selection_base_name(value: str) -> str:
    """A canonical Japanese base name when recognized; otherwise unchanged.

    The registered team's 'ルカリオ → メガルカリオZ' describes a form
    change. The team-preview species is 'ルカリオ'. No other formes are
    collapsed, and aliases do not rewrite the user-stored CSV.
    """
    base = unicodedata.normalize("NFKC", value).split("→", 1)[0].strip()
    return ENGLISH_TO_JAPANESE_BASE_SPECIES.get(base.casefold(), base)
