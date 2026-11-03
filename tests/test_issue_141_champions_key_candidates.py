"""#141: Champions-only registered species/move key candidates, NOT engine IDs."""
from __future__ import annotations

from dataclasses import replace
import csv
from hashlib import sha256
from io import StringIO
from pathlib import Path
from types import MappingProxyType
import unittest

from champions_assist.preload import MasterPreloader, REQUIRED_MASTER_FILES, _parse_team
from champions_assist.local_strategy.champions_master_key_candidates import (
    audit_champions_master_key_candidates,
)

MASTERS = Path(__file__).resolve().parents[1] / "masters"
MOVES = MASTERS / "Pokemon_Champions_MC_move_master.csv"
LEARNSETS = MASTERS / "Pokemon_Champions_MC_pokemon_learnsets_linked.csv"
POKEMON = MASTERS / "Pokemon_Champions_MC_usable_pokemon.csv"


def run(*, assets=None, move=None, learnsets=None, pokemon=None):
    frozen = assets or MasterPreloader(MASTERS).load_for_new_match()
    return audit_champions_master_key_candidates(
        frozen,
        move_master_bytes=MOVES.read_bytes() if move is None else move,
        learnsets_bytes=LEARNSETS.read_bytes() if learnsets is None else learnsets,
        usable_pokemon_bytes=POKEMON.read_bytes() if pokemon is None else pokemon,
    )


def edited_csv(original: bytes, edit) -> bytes:
    original = original.decode("utf-8-sig")
    reader = csv.DictReader(StringIO(original))
    cols = list(reader.fieldnames)
    rows = list(reader)
    edit(rows)
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=cols, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


class ChampionsKeyCandidateTests(unittest.TestCase):
    def test_real_team_24_champions_moves_and_six_base_candidates(self):
        report = run()
        self.assertEqual(report.status, "CHAMPIONS_KEY_CANDIDATES_ONLY", report)
        self.assertEqual(report.candidate_species_keys, 6)
        self.assertEqual(report.candidate_move_keys, 24)
        self.assertEqual(report.expected_move_slots, 24)
        self.assertEqual(len(report.members), 6)
        self.assertEqual(report.unmapped_slots, ())
        self.assertEqual(report.unverified_forms,
                         ("ルカリオ → メガルカリオZ",))
        self.assertEqual(report.source_sha256[0][0], "team_csv")
        self.assertEqual(len(report.source_sha256), 4)
        self.assertEqual(report.members[0].registered_name, "マスカーニャ")
        self.assertEqual(report.members[0].proposed_base_key, "meowscarada")
        self.assertEqual(report.members[0].moves[0].key_candidate, "flowertrick")
        self.assertEqual(report.members[3].base_species, "ルカリオ")
        self.assertEqual(report.members[3].proposed_base_key, "lucario")
        self.assertEqual(report.members[3].form_status,
                         "FORM_NOT_ENGINE_ATTESTED")
        self.assertFalse(report.showdown_mapping_verified)
        self.assertFalse(report.champions_mechanics_verified)
        self.assertFalse(report.usable_in_live)

    def test_fullwidth_numeric_move_matches_only_registered_move_key(self):
        report = run()
        move = report.members[4].moves[2]
        self.assertEqual(move.champions_move, "10まんボルト")
        self.assertEqual(move.key_candidate, "thunderbolt")
        self.assertEqual(move.match_type, "DIGIT_WIDTH_EQUIVALENT")
        self.assertEqual(move.learnset_membership, "IN_CHAMPIONS_LEARNSET")
        self.assertFalse(report.usable_in_live)

    def test_missing_move_from_champions_learnset_never_gets_candidate(self):
        original = LEARNSETS.read_bytes()
        def edit(rows):
            target = next(r for r in rows if r["ポケモン名"] == "サーフゴー")
            keys = target["習得MoveKey一覧"].split(", ")
            keys.remove("thunderbolt")
            target["習得MoveKey一覧"] = ", ".join(keys)
        report = run(learnsets=edited_csv(original, edit))
        self.assertEqual(report.status, "SOURCE_CANDIDATES_INCOMPLETE")
        self.assertIn("サーフゴー.move3", report.unmapped_slots)
        self.assertEqual(report.candidate_move_keys, 23)
        self.assertFalse(report.usable_in_live)

    def test_ambiguous_width_folded_species_or_move_name_rejects_source(self):
        original = MOVES.read_bytes()
        def edit(rows):
            matching = next(r for r in rows if r["技名"] == "１０まんボルト")
            rows.append({**matching, "技名": "10まんボルト"})
        report = run(move=edited_csv(original, edit))
        self.assertEqual(report.status, "INVALID_SOURCE_OR_FROZEN_TEAM")
        self.assertEqual(report.reason, "MALFORMED_OR_AMBIGUOUS_CHAMPIONS_MASTER")
        self.assertFalse(report.usable_in_live)

    def test_bare_multiform_species_is_not_silently_chosen_by_source_order(self):
        # The two Champions masters legitimately have six 'ロトム' rows.
        # A registered team with bare ロトム gives NO exact form identity.
        assets = MasterPreloader(MASTERS).load_for_new_match()
        team_name = REQUIRED_MASTER_FILES[3]
        source = assets.contents[team_name]
        reader = csv.DictReader(StringIO(source))
        columns = list(reader.fieldnames)
        rows = list(reader)
        member = next(r for r in rows if r.get("record") == "MON"
                      and r.get("name") == "サーフゴー")
        member["name"] = "ロトム"
        out = StringIO()
        writer = csv.DictWriter(out, fieldnames=columns,
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        modified = out.getvalue()
        contents = dict(assets.contents)
        contents[team_name] = modified
        shas = dict(assets.sha256)
        # Source file is UTF-8-SIG in repo; preserve its SHA contract.
        shas[team_name] = sha256(b"\xef\xbb\xbf" +
                                 modified.encode("utf-8")).hexdigest()
        frozen = replace(
            assets, contents=MappingProxyType(contents),
            sha256=MappingProxyType(shas),
            own_team=_parse_team(modified))
        report = run(assets=frozen)
        self.assertEqual(report.status, "SOURCE_CANDIDATES_INCOMPLETE")
        self.assertIn("ロトム.LearnsetKey", report.unmapped_slots)
        rotom = next(x for x in report.members if x.registered_name == "ロトム")
        self.assertEqual(rotom.proposed_base_key, "")
        self.assertNotIn(rotom.proposed_base_key,
                         ("rotomwash", "rotomheat", "rotom"))
        self.assertFalse(report.usable_in_live)

    def test_unsupported_mega_form_remains_explicit_even_when_names_match(self):
        report = run()
        lucario = next(x for x in report.members if x.base_species == "ルカリオ")
        self.assertEqual(lucario.form_target, "メガルカリオZ")
        self.assertEqual(lucario.form_status, "FORM_NOT_ENGINE_ATTESTED")
        self.assertNotEqual(lucario.proposed_base_key, "megalucario")
        self.assertNotEqual(lucario.proposed_base_key, "megalucarioz")
        self.assertFalse(report.showdown_mapping_verified)

    def test_malformed_source_and_invalid_frozen_team_fail_closed(self):
        malformed = run(move=b"not a Champions CSV")
        self.assertEqual(malformed.status, "INVALID_SOURCE_OR_FROZEN_TEAM")
        assets = MasterPreloader(MASTERS).load_for_new_match()
        team = REQUIRED_MASTER_FILES[3]
        tampered = dict(assets.contents)
        tampered[team] += "\nTAMPERED"
        frozen = replace(assets, contents=MappingProxyType(tampered))
        invalid = run(assets=frozen)
        self.assertEqual(invalid.status, "INVALID_SOURCE_OR_FROZEN_TEAM")
        self.assertEqual(invalid.reason, "FOUR_MASTER_SOURCE_INTEGRITY_FAILED")


if __name__ == "__main__":
    unittest.main()
