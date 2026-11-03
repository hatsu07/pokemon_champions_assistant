"""Source-bound static 24-move fields (not Champions engine/game parity)."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from champions_assist.preload import MasterPreloader
from champions_assist.local_strategy.champions_master_key_candidates import (
    audit_champions_master_key_candidates,
)
from champions_assist.local_strategy.static_move_compatibility import (
    build_static_move_fields,
)

MASTER = Path(__file__).resolve().parents[1] / "masters"


def actual():
    assets = MasterPreloader(MASTER).load_for_new_match()
    move = (MASTER / "Pokemon_Champions_MC_move_master.csv").read_bytes()
    candidate = audit_champions_master_key_candidates(
        assets, move_master_bytes=move,
        learnsets_bytes=(MASTER / "Pokemon_Champions_MC_pokemon_learnsets_linked.csv").read_bytes(),
        usable_pokemon_bytes=(MASTER / "Pokemon_Champions_MC_usable_pokemon.csv").read_bytes(),
    )
    return assets, move, candidate


class StaticMoveCompatibilityTests(unittest.TestCase):
    def test_actual_registered_24_static_values_match_champions_masters(self):
        assets, move, candidates = actual()
        out = build_static_move_fields(assets, candidates, move)
        self.assertEqual(out.status, "CHAMPIONS_STATIC_24_SOURCE_BOUND", out.reason)
        self.assertTrue(out.source_fields_verified)
        self.assertFalse(out.champions_engine_parity_verified)
        self.assertEqual(out.ranking_status, "NO_RANKING")
        self.assertEqual(len(out.slots), 24)
        self.assertEqual(len({(s.registered_member, s.slot) for s in out.slots}),24)
        ten = [s for s in out.slots if s.champions_name == "10まんボルト"]
        self.assertEqual(len(ten), 1)
        self.assertEqual(ten[0].move_id, "thunderbolt")
        self.assertEqual(ten[0].type, "Electric")
        jet = next(s for s in out.slots if s.champions_name == "アクアジェット")
        self.assertEqual(jet.priority, 1)
        sphere = next(s for s in out.slots if s.champions_name == "はどうだん")
        self.assertIs(sphere.accuracy, True)

    def test_changed_source_or_candidate_is_refused(self):
        assets, move, candidates = actual()
        for modified, raw in (
            (replace(candidates, team_sha256="0"*64), move),
            (candidates, move.replace(b"aurasphere", b"bogussphere")),
            (replace(candidates, candidate_move_keys=23), move),
        ):
            with self.subTest(changed=modified is not candidates):
                out = build_static_move_fields(assets, modified, raw)
                self.assertEqual(out.status, "SOURCE_STATIC_FIELDS_REFUSED")
                self.assertFalse(out.source_fields_verified)
                self.assertEqual(out.ranking_status, "NO_RANKING")


if __name__ == "__main__":
    unittest.main()
