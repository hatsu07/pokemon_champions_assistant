"""Synthetic-only current-window evidence-binding tests (no real OCR proof)."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import unittest

from champions_assist.local_strategy.models import canonical_json, fingerprint
from champions_assist.local_strategy.observed_provenance_gate import (
    SOURCE, SECTIONS, WindowEvidenceRecord, audit_synthetic_window_evidence,
)
from tests.test_issue_140_foundation import frozen
from tests.test_issue_141_observed_id_bridge import details, with_state


def current_snapshot():
    return with_state(frozen(), edits=details)


def records_for(snapshot):
    w = snapshot.window
    state = snapshot.state
    memory = state["battle_evidence"]
    observation_sha = fingerprint(snapshot.observation)
    out = []
    for section in SECTIONS:
        for side, actor in (("own", state["our_active"]),
                            ("opponent", state["opponent_active"])):
            by_name = memory.get(section, {}).get(side, {})
            # Test helper must not silently hide *present* corrupt values.
            # Presence is determined by actor membership, not truthiness.
            if actor not in by_name:
                continue
            value = by_name[actor]
            out.append(WindowEvidenceRecord(
                f"synthetic.{len(out)+1}", SOURCE,
                w.match_id, w.decision_window_id, w.turn_epoch,
                w.state_revision, w.screen_kind, w.active_pokemon,
                observation_sha, "2026-11-02T12:00:00+00:00",
                section, side, actor, canonical_json(value),
            ))
    return tuple(out)


class ObservationWindowProvenanceTests(unittest.TestCase):
    def test_synthetic_bound_values_never_attest_real_capture(self):
        snapshot = current_snapshot()
        before = snapshot.observed_state_json
        stamps = records_for(snapshot)
        result = audit_synthetic_window_evidence(snapshot, stamps)
        self.assertEqual(result.status, "SYNTHETIC_WINDOW_FACTS_BOUND")
        self.assertEqual(result.snapshot_sha256, snapshot.snapshot_sha256)
        self.assertEqual(result.observation_sha256, fingerprint(snapshot.observation))
        self.assertIn("hp_by_pokemon:own:ガブリアス", result.checked_keys)
        self.assertIn("moves_by_pokemon:opponent:カイリュー", result.checked_keys)
        self.assertIn("hp_by_pokemon:opponent:サーフゴー",
                      result.historical_keys_unverified)
        self.assertIn("hp_by_pokemon:own:ミミッキュ",
                      result.historical_keys_unverified)
        self.assertFalse(result.capture_origin_authenticated)
        self.assertFalse(result.historical_events_authenticated)
        self.assertFalse(result.full_state_reconstructed)
        self.assertFalse(result.ready_for_engine)
        self.assertEqual(snapshot.observed_state_json, before)
        self.assertEqual(stamps, records_for(snapshot))

    def test_every_present_active_fact_requires_an_exact_record(self):
        s = current_snapshot()
        records = records_for(s)
        for tampered in (records[:-1], records[1:],
                         records + (records[0],),
                         records + (replace(records[0], record_id="extra"),)):
            self.assertEqual(
                audit_synthetic_window_evidence(s, tampered).status,
                "NO_WINDOW_EVIDENCE_BINDING",
            )

    def test_wrong_value_even_with_correct_snapshot_hash_is_rejected(self):
        s = current_snapshot()
        records = records_for(s)
        i = next(i for i, x in enumerate(records) if x.section == "hp_by_pokemon")
        for raw in ('"0/100"', '"99%"', "null", '"63/184"  ', "{ }"):
            altered = list(records)
            altered[i] = replace(records[i], value_json=raw)
            self.assertEqual(
                audit_synthetic_window_evidence(s, tuple(altered)).status,
                "NO_WINDOW_EVIDENCE_BINDING",
            )

    def test_stale_match_window_turn_revision_and_screen_are_rejected(self):
        s = current_snapshot()
        stamps = records_for(s)
        changes = (
            {"match_id": "old-match"},
            {"decision_window_id": "prior-window"},
            {"turn_epoch": s.window.turn_epoch - 1},
            {"state_revision": s.window.state_revision - 1},
            {"state_revision": True},
            {"screen_kind": "ACTION_RESOLUTION"},
            {"active_pokemon": "ミミッキュ"},
            {"observation_sha256": "0"*64},
        )
        for fields in changes:
            with self.subTest(fields=fields):
                altered = (replace(stamps[0], **fields),) + stamps[1:]
                self.assertEqual(
                    audit_synthetic_window_evidence(s, altered).status,
                    "NO_WINDOW_EVIDENCE_BINDING",
                )

    def test_changed_snapshot_observation_cannot_reuse_old_capture(self):
        s = current_snapshot()
        records = records_for(s)
        obs = s.observation
        obs["visual_ui"] = s.window.screen_kind
        obs["synthetic_capture_revision"] = "changed"
        altered = replace(s, observation_json=canonical_json(obs))
        self.assertEqual(audit_synthetic_window_evidence(
            altered, records).reason, "STALE_OR_UNBOUND_OBSERVATION_RECORD")

    def test_stale_actor_and_unsupported_history_fact_cannot_be_rebadged(self):
        s = current_snapshot()
        records = records_for(s)
        for patch in (
            {"actor": "サーフゴー"},
            {"actor": "ミミッキュ"},
            {"side": "opponent"},
            {"section": "unsupported_field"},
        ):
            altered = (replace(records[0], **patch),) + records[1:]
            self.assertEqual(
                audit_synthetic_window_evidence(s, altered).status,
                "NO_WINDOW_EVIDENCE_BINDING",
            )

    def test_source_is_not_self_attested_real_game_proof(self):
        s = current_snapshot()
        stamps = records_for(s)
        for source in ("REAL_GAME_CAPTURE", "OCR_CONFIRMED", "", "USER_VERIFIED"):
            altered = (replace(stamps[0], source=source),) + stamps[1:]
            self.assertEqual(audit_synthetic_window_evidence(
                s, altered).reason, "REAL_CAPTURE_SOURCE_NOT_ATTESTED")

    def test_timezone_required_for_every_capture_timestamp(self):
        s = current_snapshot()
        stamps = records_for(s)
        for timestamp in ("2026-11-02T12:00:00", "not-a-time", "", None,
                          "2026-11-02T12:00:00+00:00 "):
            altered = (replace(stamps[0], captured_at=timestamp),) + stamps[1:]
            self.assertEqual(
                audit_synthetic_window_evidence(s, altered).reason,
                "STALE_OR_UNBOUND_OBSERVATION_RECORD",
            )

    def test_missing_or_corrupt_battle_evidence_never_proves_empty(self):
        s = current_snapshot()
        stamps = records_for(s)
        for bad in (None, [], False, "malformed", {}):
            modified = with_state(s, field="battle_evidence", value=bad)
            self.assertEqual(audit_synthetic_window_evidence(
                modified, stamps).status, "NO_WINDOW_EVIDENCE_BINDING")

    def test_explicitly_empty_active_fact_is_never_silently_dropped(self):
        s = current_snapshot()
        clean_records = records_for(s)
        for section, values in (
            ("hp_by_pokemon", (None, "", [], {})),
            ("moves_by_pokemon", (None, "", [], {})),
            ("boosts", (None, "", [], {})),
            ("status_by_pokemon", (None, "", [], {})),
            ("item_by_pokemon", (None, "", [], {})),
        ):
            for bad in values:
                with self.subTest(section=section, value=repr(bad)):
                    state = s.state
                    state["battle_evidence"][section]["own"]["ガブリアス"] = bad
                    broken = replace(s, observed_state_json=canonical_json(state))
                    # Even when the record generator reflects the same
                    # corrupted snapshot, a self-consistent packet is invalid.
                    for stamps in (clean_records, records_for(broken)):
                        self.assertEqual(
                            audit_synthetic_window_evidence(broken, stamps).reason,
                            "MALFORMED_STORED_EVIDENCE_VALUE",
                        )
                    if section == "hp_by_pokemon" and bad is None:
                        self.assertTrue(any(
                            r.section == section and r.side == "own"
                            and r.actor == "ガブリアス" and r.value_json == "null"
                            for r in records_for(broken)
                        ))

    def test_invalid_historical_values_fail_even_without_history_stamps(self):
        s = current_snapshot()
        stamps = records_for(s)
        for section, side, actor, bad in (
            ("hp_by_pokemon", "opponent", "サーフゴー", None),
            ("moves_by_pokemon", "opponent", "サーフゴー", []),
            ("ability_by_pokemon", "opponent", "サーフゴー", ""),
            ("hp_by_pokemon", "own", "ミミッキュ", {}),
            ("boosts", "opponent", "サーフゴー", {}),
        ):
            with self.subTest(section=section, actor=actor):
                state = s.state
                state["battle_evidence"][section][side][actor] = bad
                broken = replace(s, observed_state_json=canonical_json(state))
                self.assertEqual(
                    audit_synthetic_window_evidence(broken, stamps).reason,
                    "MALFORMED_STORED_EVIDENCE_VALUE",
                )

    def test_nonempty_wrong_fact_shapes_and_placeholders_also_fail(self):
        s = current_snapshot()
        stamps = records_for(s)
        for section, bad in (
            ("hp_by_pokemon", 0),
            ("status_by_pokemon", True),
            ("status_by_pokemon", "UNKNOWN"),
            ("boosts", {"A": True}),
            ("boosts", {"A": 7}),
            ("boosts", {"wrong": 1}),
            ("moves_by_pokemon", [""]),
            ("moves_by_pokemon", ["じしん", "じしん"]),
            ("moves_by_pokemon", ["じしん", 0]),
        ):
            with self.subTest(section=section, value=repr(bad)):
                state = s.state
                state["battle_evidence"][section]["own"]["ガブリアス"] = bad
                broken = replace(s, observed_state_json=canonical_json(state))
                self.assertEqual(
                    audit_synthetic_window_evidence(broken, stamps).reason,
                    "MALFORMED_STORED_EVIDENCE_VALUE",
                )

    def test_no_stored_current_fact_cannot_certify_an_empty_event(self):
        s = frozen()
        self.assertEqual(audit_synthetic_window_evidence(s, ()).status,
                         "NO_WINDOW_EVIDENCE_BINDING")

    def test_bool_numeric_record_ids_and_duplicate_fields_fail_closed(self):
        s = current_snapshot()
        stamps = records_for(s)
        for change in (
            {"record_id": ""},
            {"record_id": "a b"},
            {"record_id": stamps[1].record_id},
            {"turn_epoch": True},
            {"value_json": 123},
        ):
            altered = (replace(stamps[0], **change),) + stamps[1:]
            self.assertEqual(audit_synthetic_window_evidence(
                s, altered).status, "NO_WINDOW_EVIDENCE_BINDING")
        extra = replace(stamps[0], record_id="different")
        self.assertEqual(audit_synthetic_window_evidence(
            s, stamps + (extra,)).reason, "DUPLICATE_OBSERVATION_FIELD")


if __name__ == "__main__":
    unittest.main()
