"""Verify 100% name-only coverage of the packaged Champions fixed masters.

The bilingual source may use external language tables, but ONLY the bundled
Champions roster/legality masters define the names that the app may trust.
"""
from __future__ import annotations

import csv
import json
from importlib.resources import files
from pathlib import Path
import unittest

from champions_assist.terminology import (
    japanese_name, mapped_name, name_catalogue_stats, translate_extraction_fields,
)

ROOT = Path(__file__).resolve().parents[1] / "masters"
DATA = json.loads(files("champions_assist").joinpath(
    "data/champions_name_aliases.json").read_text(encoding="utf-8"))


def rows(name):
    with (ROOT / ("Pokemon_Champions_MC_" + name + ".csv")).open(
        encoding="utf-8-sig", newline=""
    ) as source:
        return list(csv.DictReader(source))


class TerminologyCoverageTests(unittest.TestCase):
    def test_every_champions_species_and_form_is_in_dictionary(self):
        roster = rows("usable_pokemon")
        self.assertEqual(len(roster), 350)
        self.assertEqual(len(DATA["roster_entries"]), len(roster))
        self.assertEqual(len({r["dex"] for r in DATA["roster_entries"]}), 231)
        for source, compiled in zip(roster, DATA["roster_entries"]):
            with self.subTest(name=source["ポケモン名"], form=source["フォーム/姿"]):
                self.assertEqual(compiled["jp_name"], source["ポケモン名"])
                self.assertEqual(compiled["form"], source["フォーム/姿"])
                self.assertTrue(mapped_name("pokemon", compiled["english_base"]))
                if source["フォーム/姿"]:
                    form = source["ポケモン名"] + "（" + source["フォーム/姿"] + "）"
                    self.assertTrue(mapped_name("pokemon", form))

    def test_every_move_has_direct_english_alias(self):
        all_rows = rows("move_master")
        self.assertEqual(len(all_rows), 510)
        for record in all_rows:
            self.assertEqual(japanese_name("move", record["英語名"]), record["技名"])
            self.assertEqual(japanese_name("move", record["MoveKey"]), record["技名"])

    def test_all_abilities_have_english_and_japanese_aliases(self):
        all_rows = rows("ability_master")
        self.assertEqual(len(all_rows), 216)
        lookup = DATA["aliases"]["ability"]
        for record in all_rows:
            label = record["特性名"]
            self.assertEqual(japanese_name("ability", label), label)
            self.assertTrue(any(name.isascii() and target == label
                                for name, target in lookup.items()),
                            "English mapping missing for: " + label)
        self.assertEqual(japanese_name("ability", "Aura Guard"), "はどうのぼうご")
        self.assertEqual(japanese_name("ability", "Eelevate"), "うなぎのぼり")
        self.assertEqual(japanese_name("ability", "Fire Mane"), "ほのおのたてがみ")

    def test_all_items_and_natures_types_covered(self):
        all_rows = rows("usable_held_items")
        self.assertEqual(len(all_rows), 166)
        for record in all_rows:
            self.assertEqual(japanese_name("item", record["英語名"]), record["道具名"])
        self.assertEqual(len(DATA["aliases"]["nature"]), 50)
        self.assertEqual(len(DATA["aliases"]["type"]), 36)
        self.assertEqual(japanese_name("nature", "Jolly"), "ようき")
        self.assertEqual(japanese_name("type", "Fairy"), "フェアリー")
        self.assertEqual(japanese_name("battle_term", "Stealth Rock"), "ステルスロック")

    def test_structured_output_translated_without_rewriting_messages(self):
        value = translate_extraction_fields({
            "pokemon": ["Pikachu", "Kingambit", "Garchomp"],
            "moves": ["Thunderbolt", "Sucker Punch"],
            "items": ["Air Balloon"],
            "abilities": ["Intimidate"],
            "natures": ["Jolly"],
            "types": ["Electric"],
            "messages": ["Pikachu used Thunderbolt!"],
        })
        self.assertEqual(value["pokemon"], ["ピカチュウ", "ドドゲザン", "ガブリアス"])
        self.assertEqual(value["items"], ["ふうせん"])
        self.assertEqual(value["abilities"], ["いかく"])
        self.assertEqual(value["natures"], ["ようき"])
        self.assertEqual(value["types"], ["でんき"])
        self.assertEqual(value["messages"], ["Pikachu used Thunderbolt!"])
        self.assertEqual(japanese_name("pokemon", "Unknown New Monster"), "Unknown New Monster")
        self.assertFalse(mapped_name("pokemon", "Unknown New Monster"))

    def test_package_metadata_count_matches_fixed_masters(self):
        stats = name_catalogue_stats()
        self.assertEqual(stats["roster_rows"], 350)
        self.assertEqual(stats["move_rows"], 510)
        self.assertEqual(stats["ability_rows"], 216)
        self.assertEqual(stats["item_rows"], 166)
        self.assertEqual(stats["unmapped_abilities"], [])
        self.assertEqual(stats["collisions"], [])


if __name__ == "__main__":
    unittest.main()
