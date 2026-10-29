"""P1 #114: synthetic capture-time evidence, no real match/API/images."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from champions_assist.offmatch_evidence import (
    freeze_team_context, MatchEvidenceBuffer, MatchEvidenceArchive,
    MAX_EVENT_BYTES,
)
from champions_assist.matchup_learning_store import MatchupLearningStore
from champions_assist.app_config import ConfigurationError

TEAM = "p1-test-team"
SHA = "sha256:p1-fixed-team"


class EvidenceArchiveTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = MatchupLearningStore(self.root)
        self.archive = MatchEvidenceArchive(self.root)
        self.context = freeze_team_context(
            team_id=TEAM, team_sha=SHA, tactical_version="live-matrix-hash-v1",
            model="synthetic", regulation="M-C",
            regulation_source="REGISTERED_TEAM_CSV_UNVERIFIED",
            environment_source="USER_SNAPSHOT",
            environment_observed_at="2026-10-01T09:00:00+09:00",
            frozen_assets_sha={"matrix":"sha256:abc", "lookup":"sha256:def"},
            own_members=[{"name":"ガブリアス", "move1":"じしん","move2":"げきりん",
                          "move3":"アイアンヘッド","move4":"まもる",
                          "ability":"さめはだ","item":"こだわりハチマキ"}])
        self.state = {
            "phase":"BATTLE", "step_number":1, "our_active":"ガブリアス",
            "opponent_active":"カバルドン", "selected_three":["ガブリアス", "ロトム", "サーフゴー"],
            "lead":"ガブリアス", "selection_assumed":True,
            "observed_hp":{"own":"100%", "opponent":"85%"},
            "battlefield":{"weather":"すなあらし", "secrets":"do not store"},
            "history":[{"access_token":"not-for-persistence"}],
            "image_observations_unverified":[{"ui":"MOVE_MENU", "our_hp":"100%",
                                             "messages":["secret OCR transcript"]}],
            "last_event":"NONE",
        }
        self.report = {
            "status":"ADVICE",
            "action":{"kind":"MOVE", "move":"じしん"},
            "observation":{"visible_moves":["じしん","げきりん"]},
            "access_token":"secret credential in model report",
            "frame_id":"f10",
        }

    def finish_match(self, mid="match-1", *, sha=SHA):
        self.assertTrue(self.store.record_finished(
            match_id=mid,team_id=TEAM,team_signature=sha,
            state={"phase":"ENDED", "result":"WIN", "opponent_six":["カバルドン"],
                   "selected_three":["ガブリアス"], "history":[]},
            source="AI_VISUAL_UNVERIFIED"))

    def test_not_written_mid_battle_and_never_confirm_advised_move(self):
        self.finish_match()
        buf = MatchEvidenceBuffer(match_id="match-1", context=self.context)
        self.assertTrue(buf.capture(self.state, self.report,
                                    captured_at="2026-10-29T01:00:00+00:00"))
        self.assertFalse(self.archive.read("match-1"))
        self.assertFalse(buf.capture(self.state, self.report))
        event = buf.events[0]
        self.assertEqual(event["proposal"]["action"]["move"], "じしん")
        self.assertEqual(event["proposal"]["displayed_at"],
                         "2026-10-29T01:00:00+00:00")
        self.assertEqual(event["execution"], {"action":{}, "certainty":"UNKNOWN"})
        self.assertEqual(event["selection"]["certainty"],"AI_VISUAL_CANDIDATE")
        self.assertEqual(event["legal_options"]["registered_moves"][0],"じしん")
        self.assertEqual(event["legal_options"]["certainty"],
                         "REGISTERED_NOT_CURRENTLY_LEGALITY_VERIFIED")
        self.assertFalse(buf.ended)
        completed=buf.finish()
        self.assertFalse(buf.capture(self.state,{**self.report,"status":"MATCH_END"}))
        self.assertEqual(self.archive.save_ended(completed)["status"],"STORED_UNVERIFIED")
        self.assertEqual(self.archive.save_ended(completed)["status"],"ALREADY_STORED")
        saved=self.archive.read("match-1")
        self.assertEqual(saved["context"]["team_sha"],SHA)
        self.assertEqual(saved["events"][0]["frame_id"],"f10")
        self.assertEqual(self.archive.verify("match-1")["status"],"VALID")
        with closing(sqlite3.connect(self.store.db_path)) as db:
            raw=json.dumps(db.execute(
                "SELECT event_json FROM p1_evidence_events").fetchall(),ensure_ascii=False)
        self.assertNotIn("secret",raw)
        self.assertNotIn("access_token",raw)
        self.assertNotIn("OCR transcript",raw)

    def test_explicit_game_action_is_separate_from_ai_recommendation(self):
        buf=MatchEvidenceBuffer(match_id="x", context=self.context)
        alternate={**self.report, "actual_action":{"kind":"SWITCH","target":"ロトム"},
                   "actual_action_source":"GAME_EVENT_CONFIRMED"}
        self.assertTrue(buf.capture(self.state,alternate))
        self.assertEqual(buf.events[0]["proposal"]["action"]["move"], "じしん")
        self.assertEqual(buf.events[0]["execution"]["action"]["target"],"ロトム")
        self.assertEqual(buf.events[0]["execution"]["certainty"],"GAME_EVENT_CONFIRMED")
        ignored={**alternate,"actual_action_source":"AI_VISUAL_CANDIDATE"}
        ignored["frame_id"]="f11"
        self.assertTrue(buf.capture(self.state,ignored))
        self.assertEqual(buf.events[1]["execution"]["certainty"],"UNKNOWN")

    def test_posthoc_mutation_is_rejected_instead_of_overwriting_evidence(self):
        self.finish_match()
        buf=MatchEvidenceBuffer(match_id="match-1",context=self.context)
        buf.capture(self.state,self.report)
        snapshot=buf.finish()
        self.archive.save_ended(snapshot)
        altered=json.loads(json.dumps(snapshot))
        altered["context"]["tactical_version"]="posthoc-changed-tactics"
        with self.assertRaises(ConfigurationError):
            self.archive.save_ended(altered)
        with closing(sqlite3.connect(self.store.db_path)) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE p1_evidence_match SET status='LIVE_READY'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM p1_evidence_events")
        self.assertEqual(self.archive.verify("match-1")["status"],"VALID")

    def test_verify_rejects_metadata_only_corruption(self):
        # Simulate offline disk/backup corruption. Production UPDATE triggers
        # normally forbid edits; bypass them ONLY in this synthetic test to
        # prove verify() validates the archived SHA independently.
        self.finish_match()
        buf = MatchEvidenceBuffer(match_id="match-1", context=self.context)
        self.assertTrue(buf.capture(self.state, self.report))
        self.archive.save_ended(buf.finish())
        self.assertEqual(self.archive.verify("match-1")["status"], "VALID")
        with closing(sqlite3.connect(self.store.db_path)) as db:
            original = db.execute(
                "SELECT metadata_json, status, dropped_count, metadata_sha "
                "FROM p1_evidence_match WHERE match_id='match-1'"
            ).fetchone()
            db.execute("DROP TRIGGER no_p1_match_update")
            db.commit()
            changed_context = json.loads(original[0])
            changed_context["tactical_version"] = "tampered-after-match"
            mutations = [
                ("metadata_json", json.dumps(changed_context, ensure_ascii=False)),
                ("status", "PARTIAL_CAPTURE"),
                ("dropped_count", 10),
                ("metadata_sha", "0" * 64),
            ]
            columns = ("metadata_json", "status", "dropped_count", "metadata_sha")
            for column, mutation in mutations:
                with self.subTest(column=column):
                    db.execute(
                        f"UPDATE p1_evidence_match SET {column}=? WHERE match_id=?",
                        (mutation, "match-1"))
                    db.commit()
                    self.assertEqual(
                        self.archive.verify("match-1")["status"], "CORRUPT")
                    db.execute(
                        f"UPDATE p1_evidence_match SET {column}=? WHERE match_id=?",
                        (original[columns.index(column)], "match-1"))
                    db.commit()
                    self.assertEqual(self.archive.verify("match-1")["status"],
                                     "VALID")

    def test_verify_rejects_corrupt_event_sha_even_with_unchanged_json(self):
        self.finish_match()
        buf = MatchEvidenceBuffer(match_id="match-1", context=self.context)
        self.assertTrue(buf.capture(self.state, self.report))
        self.archive.save_ended(buf.finish())
        with closing(sqlite3.connect(self.store.db_path)) as db:
            db.execute("DROP TRIGGER no_p1_event_update")
            db.execute(
                "UPDATE p1_evidence_events SET event_sha=? WHERE match_id=?",
                ("0" * 64, "match-1"))
            db.commit()
        self.assertEqual(self.archive.verify("match-1")["status"], "CORRUPT")

    def test_legacy_database_migration_is_non_destructive_and_backup_exists(self):
        self.finish_match()
        with closing(sqlite3.connect(self.store.db_path)) as db:
            count_before=db.execute("SELECT COUNT(*) FROM match_history").fetchone()[0]
        buf=MatchEvidenceBuffer(match_id="match-1",context=self.context)
        buf.capture(self.state,self.report)
        self.archive.save_ended(buf.finish())
        self.assertTrue(self.archive.backup_path.is_file())
        with closing(sqlite3.connect(self.archive.backup_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM match_history").fetchone()[0],
                             count_before)
            self.assertIsNone(db.execute("""SELECT name FROM sqlite_master
                WHERE name='p1_evidence_match'""").fetchone())
        with closing(sqlite3.connect(self.store.db_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM match_history").fetchone()[0],
                             count_before)

    def test_mismatched_team_version_never_enters_archive(self):
        self.finish_match(sha="sha256:newer-team")
        buf=MatchEvidenceBuffer(match_id="match-1",context=self.context)
        buf.capture(self.state,self.report)
        self.assertEqual(self.archive.save_ended(buf.finish())["status"],
                         "VERSION_MISMATCH")
        self.assertIsNone(self.archive.read("match-1"))

    def test_bounded_events_mark_incomplete_with_source_preserved(self):
        self.finish_match()
        buf=MatchEvidenceBuffer(match_id="match-1",context=self.context)
        with patch("champions_assist.offmatch_evidence.MAX_EVENTS",3):
            for idx in range(7):
                state={**self.state,"step_number":idx}
                report={**self.report,"frame_id":f"frame-{idx}"}
                self.assertTrue(buf.capture(state,report))
        self.assertEqual(len(buf.events),3)
        self.assertEqual(buf.dropped_events,4)
        final=buf.finish()
        self.assertTrue(final["incomplete"])
        self.assertEqual(final["status"],"PARTIAL_CAPTURE")
        result=self.archive.save_ended(final)
        self.assertEqual(result["status"],"STORED_UNVERIFIED")
        self.assertEqual(self.archive.read("match-1")["dropped_count"],4)

    def test_no_half_archive_on_validation_error_or_interrupt(self):
        self.finish_match()
        buf=MatchEvidenceBuffer(match_id="match-1",context=self.context)
        buf.capture(self.state,self.report)
        partial=buf.finish()
        partial["events"][0]["step"]=1234  # after-the-fact tampering
        with self.assertRaises(ConfigurationError):
            self.archive.save_ended(partial)
        self.assertIsNone(self.archive.read("match-1"))
        self.assertIsNotNone(self.store.pending_match("match-1"))

    def test_foreign_match_not_archived(self):
        buf=MatchEvidenceBuffer(match_id="unknown",context=self.context)
        buf.capture(self.state,self.report)
        self.assertEqual(self.archive.save_ended(buf.finish())["status"],"NOT_FINISHED")

    def test_selection_proposal_keeps_unconfirmed_bench(self):
        buf=MatchEvidenceBuffer(match_id="x",context=self.context)
        st={**self.state,"phase":"SELECTION","selected_three":[],
            "selection_assumed":False,"lead":None}
        rep={"status":"OK", "selected_three":["ガブリアス","ロトム","サーフゴー"],
             "lead":"ガブリアス"}
        self.assertTrue(buf.capture(st,rep))
        ev=buf.events[0]
        self.assertEqual(ev["selection"]["certainty"],"UNKNOWN")
        self.assertEqual(ev["selection_proposal"]["certainty"],"AI_HYPOTHESIS_UNVERIFIED")
        self.assertEqual(len(ev["selection_proposal"]["our_three"]),3)


if __name__ == "__main__":
    unittest.main()
