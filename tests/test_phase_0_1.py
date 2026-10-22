from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from champions_assist.__main__ import main
from champions_assist.history_inspect import inspect_workbook
from champions_assist.store import BattleStore


class BattleStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "battle.sqlite3"
        self.store = BattleStore(self.db_path)
        self.match_id = "match-1"

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_correction_replays_without_rewriting_frozen_snapshot(self) -> None:
        self.assertTrue(
            self.store.append_evidence(
                self.match_id, "weather-a", field="weather", value="sun", source_kind="BATTLE_TEXT", quality="CONFIRMED"
            )
        )
        self.store.freeze_api_snapshot(self.match_id, "request-1", {"weather": "sun"})
        self.assertTrue(
            self.store.append_evidence(
                self.match_id, "weather-b", field="weather", value="rain", source_kind="UI_DIRECT", quality="CONFIRMED"
            )
        )
        weather = self.store.replay(self.match_id)["fields"]["weather"]
        self.assertEqual(weather["value"], "rain")
        self.assertTrue(weather["disagreement"])
        self.assertTrue(
            self.store.append_evidence(
                self.match_id,
                "weather-c",
                field="weather",
                value="rain",
                source_kind="MANUAL_VERIFIED",
                quality="CONFIRMED",
                supersedes=("weather-a",),
            )
        )
        weather = self.store.replay(self.match_id)["fields"]["weather"]
        self.assertEqual(weather["value"], "rain")
        self.assertFalse(weather["disagreement"])
        self.assertEqual(self.store.load_api_snapshot(self.match_id, "request-1"), {"weather": "sun"})

    def test_events_and_snapshots_are_append_only(self) -> None:
        self.store.append_evidence(
            self.match_id, "e1", field="hp.own-1", value=75, source_kind="UI_DIRECT", quality="CONFIRMED"
        )
        self.store.freeze_api_snapshot(self.match_id, "request-1", {"test": "original"})
        with self.assertRaises(sqlite3.IntegrityError):
            self.store._connection.execute("UPDATE battle_events SET event_type = 'NOTE'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store._connection.execute("UPDATE api_snapshots SET payload_json = '{}' ")
        self.assertEqual(self.store.load_api_snapshot(self.match_id, "request-1"), {"test": "original"})

    def test_turn_end_and_confirmed_effect_are_idempotent(self) -> None:
        self.assertTrue(
            self.store.append_event(
                self.match_id,
                "wall-start",
                "DURATION_STARTED",
                {"duration_id": "wall-1", "field": "our.screen", "remaining_candidates": [4, 7]},
            )
        )
        self.assertTrue(self.store.append_event(self.match_id, "turn-1", "TURN_END_CONFIRMED", {"turn_id": "turn-1"}))
        self.assertFalse(self.store.append_event(self.match_id, "turn-1-repeat", "TURN_END_CONFIRMED", {"turn_id": "turn-1"}))
        self.assertTrue(
            self.store.append_event(
                self.match_id, "effect-1", "CONFIRMED_EFFECT", {"effect_instance_id": "activation-1:rule-0"}
            )
        )
        self.assertFalse(
            self.store.append_event(
                self.match_id, "effect-duplicate", "CONFIRMED_EFFECT", {"effect_instance_id": "activation-1:rule-0"}
            )
        )
        projection = self.store.replay(self.match_id)
        self.assertEqual(projection["durations"]["wall-1"]["remaining_candidates"], [3, 6])
        self.assertEqual(projection["turn_end_ids"], ["turn-1"])
        self.assertEqual(projection["confirmed_effect_ids"], ["activation-1:rule-0"])

    def test_restart_replays_identical_projection(self) -> None:
        self.store.append_raw_observation(self.match_id, "frame-1", {"text": "untrusted OCR"}, capture_seq=5)
        self.store.append_evidence(
            self.match_id, "boost-candidate", field="own.atk_stage", value=1, source_kind="BATTLE_TEXT", quality="CANDIDATE"
        )
        self.store.append_event(self.match_id, "pending-1", "PENDING_EFFECT", {"effect_instance_id": "pending-effect"})
        before = self.store.replay(self.match_id)
        self.store.close()
        self.store = BattleStore(self.db_path)
        self.assertEqual(self.store.replay(self.match_id), before)
        self.assertEqual(before["fields"]["own.atk_stage"]["certainty"], "CANDIDATE")
        self.assertEqual(before["pending_effect_ids"], ["pending-effect"])

    def test_same_rank_disagreement_remains_conflict(self) -> None:
        self.store.append_evidence(
            self.match_id, "status-a", field="own.status", value="poison", source_kind="BATTLE_TEXT", quality="CONFIRMED"
        )
        self.store.append_evidence(
            self.match_id, "status-b", field="own.status", value="burn", source_kind="BATTLE_TEXT", quality="CONFIRMED"
        )
        self.assertEqual(self.store.replay(self.match_id)["fields"]["own.status"]["certainty"], "CONFLICT")

    def test_pending_effect_resolution_advances_replay_version(self) -> None:
        self.store.append_event(self.match_id, "pending-1", "PENDING_EFFECT", {"effect_instance_id": "effect-1"})
        self.store.append_event(self.match_id, "resolved-1", "RESOLVED_EFFECT", {"effect_instance_id": "effect-1"})
        projection = self.store.replay(self.match_id)
        self.assertEqual(projection["pending_effect_ids"], [])
        self.assertEqual(projection["observation_revision"], 2)
        self.assertEqual(projection["tactical_state_version"], 2)

    def test_api_call_reservation_is_unique_per_wire_key(self) -> None:
        self.store.freeze_api_snapshot(self.match_id, "request-1", {"request_id": "r1"})
        details = {
            "decision_window_id": "window-1",
            "tactical_state_version": 4,
            "allowed_action_digest": "sha256:abc",
            "phase": "NORMAL_COMMAND",
            "request_snapshot_id": "request-1",
        }
        self.assertTrue(self.store.reserve_api_call(self.match_id, **details))
        self.assertFalse(self.store.reserve_api_call(self.match_id, **details))
        self.assertTrue(self.store.reserve_api_call(self.match_id, **{**details, "tactical_state_version": 5}))


class OfflineCliAndHistoryTests(unittest.TestCase):
    def test_dry_run_status_declares_unconfigured_live_components(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--dry-run"]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["mode"], "OFFLINE_DRY_RUN")
        self.assertEqual(result["live_capture"], "NOT_CONFIGURED")
        self.assertEqual(result["game_control"], "NOT_IMPLEMENTED")

    def test_history_inspection_is_structure_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.xlsx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "xl/workbook.xml",
                    '''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>
                      <sheet name="RawLog" sheetId="1" r:id="rId1"/></sheets></workbook>''',
                )
                archive.writestr(
                    "xl/_rels/workbook.xml.rels",
                    '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                      <Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="worksheet"/>
                      </Relationships>''',
                )
                archive.writestr(
                    "xl/worksheets/sheet1.xml",
                    '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                      <dimension ref="A1:B2"/><sheetData><row r="1">
                      <c r="A1" t="inlineStr"><is><t>Recorded At</t></is></c>
                      <c r="B1"><f>SUM(A2:A2)</f><v>1</v></c>
                      </row></sheetData></worksheet>''',
                )
            result = inspect_workbook(path)
        self.assertEqual(result["mode"], "READ_ONLY_STRUCTURE_ONLY")
        self.assertEqual(result["semantic_mapping"], "NOT_INFERRED")
        self.assertEqual(result["sheets"][0]["dimension"], "A1:B2")
        self.assertEqual(result["sheets"][0]["first_row_cells"][1]["kind"], "formula")


if __name__ == "__main__":
    unittest.main(verbosity=2)
