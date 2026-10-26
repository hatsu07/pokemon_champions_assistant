"""Exact Pokémon Champions species/form mapping for the fixed roster.

The roster and linked learnsets identify forms by (ポケモン名, フォーム/姿).
Matrix labels put the form in full-width parentheses. Do NOT fall back to
a different form, especially when the roster has more than one entry.
"""
from __future__ import annotations
from collections import Counter
import unicodedata

def _norm(s: object) -> str:
    return unicodedata.normalize("NFKC",str(s or "")).strip()

# Explicit aliases appearing in the shipped 358-row LIVE matrix. Each
# destination pair exists separately in BOTH Champions fixed masters.
# Never infer suffixes such as "ヒスイ" for unknown species.
FORM_ALIASES = {
    "アローラキュウコン": ("キュウコン","アローラのすがた"),
    "ウォッシュロトム": ("ロトム","ウォッシュロトム"),
    "ヒートロトム": ("ロトム","ヒートロトム"),
    "ヒスイウインディ": ("ウインディ","ヒスイのすがた"),
    "ヒスイダイケンキ": ("ダイケンキ","ヒスイのすがた"),
    "ヒスイヌメルゴン": ("ヌメルゴン","ヒスイのすがた"),
    "ヒスイゾロアーク": ("ゾロアーク","ヒスイのすがた"),
    "ガラルヤドキング": ("ヤドキング","ガラルのすがた"),
}

def form_key(name: str) -> tuple[str,str]:
    s=_norm(name).replace("(", "（").replace(")", "）")
    if s in FORM_ALIASES:
        return FORM_ALIASES[s]
    if "（" not in s or not s.endswith("）"):
        return _norm(s),""
    base,form=s[:-1].rsplit("（",1)
    return _norm(base),_norm(form)

def roster_form_key(row: dict) -> tuple[str,str]:
    return _norm(row.get("ポケモン名")),_norm(row.get("フォーム/姿"))

def build_form_index(records: list[dict]) -> dict:
    """Omit duplicate / ambiguous keys rather than silently overwriting."""
    lookup={}
    duplicates=set()
    for row in records:
        key=roster_form_key(row)
        if key in lookup:
            duplicates.add(key)
        lookup[key]=row
    for key in duplicates:
        lookup.pop(key,None)
    return lookup

def get_exact_form(name: str, index: dict) -> dict | None:
    """Names with an explicit form NEVER resolve to the default form."""
    key=form_key(name)
    return index.get(key)

def validate_catalog_form(cell: dict, *, master_species: dict,
                          master_learnsets: dict) -> bool:
    """Older v28 catalog is safe to retain, but should not be trusted for
    an exact-form inference without a matching key in its fixed masters."""
    opp=get_exact_form(cell["opponent"],master_species)
    ls=get_exact_form(cell["opponent"],master_learnsets)
    return opp is not None and ls is not None
