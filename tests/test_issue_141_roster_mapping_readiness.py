"""A real-team blocker: unreviewed Champions→Showdown identifiers.

Tests check the frozen SOURCE CSV contract only, never game mechanics or a
real-game Battle. The mapped synthetic fixture is not authority to act.
"""
from __future__ import annotations

import csv
from dataclasses import replace
from hashlib import sha256
from io import StringIO
from pathlib import Path
from types import MappingProxyType
import unittest

from champions_assist.preload import (
    REQUIRED_MASTER_FILES, MasterPreloader, _parse_team,
)
from champions_assist.local_strategy.roster_mapping_readiness import (
    audit_frozen_roster_mapping,
)
from tests.test_issue_140_foundation import VERIFIED_ASSETS


TEAM = REQUIRED_MASTER_FILES[3]


def change_team(*, drop_columns=False, duplicate_species=False,
                bad_move=False, raw=None, bom_sha=False):
    contents=dict(VERIFIED_ASSETS.contents)
    original=contents[TEAM]
    reader=csv.DictReader(original.splitlines())
    columns=list(reader.fieldnames or ())
    rows=list(reader)
    if raw is not None:
        contents[TEAM]=raw
    else:
        if duplicate_species:
            rows[1]["showdown_species_id"]=rows[0]["showdown_species_id"]
        if bad_move:
            rows[0]["showdown_move1_id"]="LOOKS_PLAUSIBLE_BUT_UNVERIFIED"
        if drop_columns:
            columns=[x for x in columns if not x.startswith("showdown_")]
        stream=StringIO()
        writer=csv.DictWriter(stream,fieldnames=columns,extrasaction="ignore",
                              lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        contents[TEAM]=stream.getvalue()
    digest=sha256((b"\xef\xbb\xbf" if bom_sha else b"")+
                  contents[TEAM].encode("utf-8")).hexdigest()
    shas=dict(VERIFIED_ASSETS.sha256)
    shas[TEAM]=digest
    return replace(
        VERIFIED_ASSETS,contents=MappingProxyType(contents),
        sha256=MappingProxyType(shas),
        own_team=_parse_team(contents[TEAM]))


class RosterMappingReadinessTests(unittest.TestCase):
    def test_explicit_id_fields_are_only_structurally_accepted(self):
        audit=audit_frozen_roster_mapping(VERIFIED_ASSETS)
        self.assertEqual(audit.status,"STRUCTURAL_IDS_PRESENT_UNATTESTED")
        self.assertTrue(audit.worker_id_fields_structurally_complete)
        self.assertEqual(audit.inspected_members,6)
        self.assertEqual(audit.team_sha256,VERIFIED_ASSETS.sha256[TEAM])
        self.assertFalse(audit.mapping_independently_attested)
        self.assertFalse(audit.champions_mechanics_verified)
        self.assertFalse(audit.real_game_battle_reconstructed)
        self.assertFalse(audit.ready_for_live)

    def test_missing_columns_return_exact_requirements_not_guessed_ids(self):
        audit=audit_frozen_roster_mapping(change_team(drop_columns=True))
        self.assertEqual(audit.status,"MAPPING_FIELDS_INCOMPLETE")
        self.assertEqual(len([f for f in audit.missing_fields
                              if f.endswith(".showdown_species_id")]),6)
        self.assertGreaterEqual(len(audit.missing_fields),12)
        self.assertEqual(audit.inspected_members,6)
        self.assertFalse(audit.worker_id_fields_structurally_complete)
        self.assertFalse(audit.ready_for_live)

    def test_duplicates_invalid_ids_and_tampering_fail_closed(self):
        duplicate=audit_frozen_roster_mapping(change_team(duplicate_species=True))
        self.assertEqual(duplicate.status,"MAPPING_FIELDS_INCOMPLETE")
        self.assertTrue(any(f.endswith(".showdown_species_id")
                            for f in duplicate.invalid_fields))
        invalid=audit_frozen_roster_mapping(change_team(bad_move=True))
        self.assertEqual(invalid.status,"MAPPING_FIELDS_INCOMPLETE")
        self.assertIn("ガブリアス.showdown_move1_id",invalid.invalid_fields)
        original=VERIFIED_ASSETS
        modified=dict(original.contents)
        modified[TEAM]=modified[TEAM]+"\n# unauthorized edit"
        corrupted=replace(original,contents=MappingProxyType(modified))
        audit=audit_frozen_roster_mapping(corrupted)
        self.assertEqual(audit.status,"INVALID_FROZEN_ASSETS")
        self.assertEqual(audit.reason,"MASTER_SHA_DOES_NOT_MATCH_FROZEN_BYTES")

    def test_preloader_bom_sha_is_accepted_without_guessing(self):
        result=audit_frozen_roster_mapping(change_team(bom_sha=True))
        self.assertEqual(result.status,"STRUCTURAL_IDS_PRESENT_UNATTESTED")
        self.assertFalse(result.ready_for_live)

    def test_current_repository_team_is_audited_not_silently_remapped(self):
        # Repo's shipped master is deliberately the actual October 3 roster,
        # NOT the synthetic six with fake Showdown IDs used above. This test
        # states the current contract without freezing a future requirement
        # that a verified mapping must remain absent forever.
        masters=Path(__file__).resolve().parents[1]/"masters"
        assets=MasterPreloader(masters).load_for_new_match()
        audit=audit_frozen_roster_mapping(assets)
        self.assertIn(audit.status,(
            "MAPPING_FIELDS_INCOMPLETE","STRUCTURAL_IDS_PRESENT_UNATTESTED"))
        if "showdown_species_id" not in assets.own_team[0]:
            self.assertEqual(audit.status,"MAPPING_FIELDS_INCOMPLETE")
            self.assertEqual(len(audit.missing_fields),6+audit.checked_move_slots)
        self.assertFalse(audit.ready_for_live)


if __name__=="__main__":
    unittest.main()
