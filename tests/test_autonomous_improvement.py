"""Issue #92 stage 1: completely offline, synthetic, no private logs/API."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from champions_assist.autonomous_improvement import (
    AutonomousImprovementStore, SCHEMA,
)
from champions_assist.matchup_learning_store import MatchupLearningStore
from champions_assist.postmatch_learning import REVIEW_SCHEMA


TEAM = "synthetic-team"
SIGNATURE = "sha256:synthetic-configuration-v1"
MON = "ガブリアス"
OPP = "カバルドン"


class AutonomousResearchTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.learning = MatchupLearningStore(self.root)
        self.research = AutonomousImprovementStore(self.root)

    def record(self, match_id: str, *, signature: str = SIGNATURE,
               issue: str = "初手を見直す", suggestion: str = "相手初手への切り返しを検証",
               result: str = "LOSS", findings: int = 1):
        state = {
            "phase": "ENDED", "result": result,
            "opponent_six": [OPP], "selected_three": [MON], "lead": MON,
            "history": [{"ui": "RESULT_SCREEN"}],
            "battle_evidence": {}, "observed_hp": {},
        }
        self.assertTrue(self.learning.record_finished(
            match_id=match_id, team_id=TEAM,
            state=state, team_signature=signature, source="AI_VISUAL_UNVERIFIED"))
        finding = {
            "opponent": OPP, "our_pokemon": MON,
            "observation": "画像OCR候補のみ",
            "issue": issue, "suggestion": suggestion,
            "certainty": "HYPOTHESIS",
        }
        review = {
            "schema": REVIEW_SCHEMA, "match_id": match_id,
            "findings": [dict(finding) for _ in range(findings)],
            "next_match_notes": "未検証の候補",
        }
        self.assertTrue(self.learning.save_postmatch_review(
            match_id=match_id, review=review))

    def test_no_cycle_before_three_independent_same_team_reviews(self):
        self.record("m1")
        self.assertEqual(self.research.aggregate_after_review(match_id="m1")["status"],
                         "INSUFFICIENT_MATCHES")
        self.record("m2", findings=3)
        self.assertEqual(self.research.aggregate_after_review(match_id="m2")["status"],
                         "INSUFFICIENT_MATCHES")
        self.assertEqual(self.research.history(team_id=TEAM), [])
        self.record("other-version", signature="sha256:another-team")
        self.assertEqual(self.research.aggregate_after_review(match_id="m2")["status"],
                         "INSUFFICIENT_MATCHES")
        self.assertEqual(self.research.history(team_id=TEAM), [])

    def test_repeated_pattern_journal_is_idempotent_private_and_non_live(self):
        for match_id in ("m1", "m2", "m3"):
            self.record(match_id)
        original = {path.name: path.read_bytes() for path in self.root.glob("*.csv")}
        result = self.research.aggregate_after_review(match_id="m3")
        self.assertEqual(result["status"], "STORED_PROVISIONAL_UNVERIFIED")
        self.assertEqual(result["candidate_count"], 1)
        self.assertFalse(result["live_applied"])
        batch = self.research.history(team_id=TEAM)
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]["promotion_status"], "NOT_AUTHORIZED")
        self.assertEqual(batch[0]["status"], "PROVISIONAL_UNVERIFIED")
        self.assertEqual(len(batch[0]["match_ids"]), 3)
        candidates = self.research.candidates(batch_sha=result["batch_sha"])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["evidence_match_ids"], ["m1", "m2", "m3"])
        self.assertEqual(candidates[0]["validation"], "DEFERRED_UNVERIFIED")
        self.assertEqual(candidates[0]["certainty"], "GPT_HYPOTHESIS_UNVERIFIED")
        self.assertEqual(candidates[0]["legality"], "NOT_CHECKED")
        self.assertEqual(self.research.aggregate_after_review(match_id="m3")["status"],
                         "NO_NEW_INDEPENDENT_EVIDENCE")
        self.assertEqual(len(self.research.history(team_id=TEAM)), 1)
        self.assertEqual(
            {path.name: path.read_bytes() for path in self.root.glob("*.csv")},
            original)
        self.assertFalse(any(self.root.glob("**/live_decision_gate*")))

    def test_non_repeating_findings_are_audited_but_not_proposed(self):
        for index in range(3):
            self.record(f"m{index}", issue=f"異なる未確認項目{index}")
        result = self.research.aggregate_after_review(match_id="m2")
        self.assertEqual(result["status"], "STORED_NO_REPEATED_PATTERN")
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(self.research.history(team_id=TEAM)[0]["candidate_count"],0)
        self.assertEqual(self.research.candidates(batch_sha=result["batch_sha"]), [])

    def test_three_additional_reviews_make_new_cycle_not_per_match(self):
        for index in range(3):
            self.record(f"m{index}")
        self.assertEqual(self.research.aggregate_after_review(match_id="m2")["candidate_count"], 1)
        self.record("m3")
        self.assertEqual(self.research.aggregate_after_review(match_id="m3")["status"],
                         "NO_NEW_INDEPENDENT_EVIDENCE")
        self.record("m4")
        self.assertEqual(self.research.aggregate_after_review(match_id="m4")["status"],
                         "NO_NEW_INDEPENDENT_EVIDENCE")
        self.record("m5")
        self.assertEqual(self.research.aggregate_after_review(match_id="m5")["candidate_count"], 1)
        self.assertEqual(len(self.research.history(team_id=TEAM)), 2)

    def test_missing_review_and_invalid_match_mismatch_is_noop(self):
        self.assertEqual(self.research.aggregate_after_review(match_id="missing")["status"],
                         "NO_FINISHED_MATCH")
        state = {"phase":"ENDED","result":"WIN"}
        self.learning.record_finished(match_id="pending",team_id=TEAM,state=state)
        self.assertEqual(self.research.aggregate_after_review(match_id="pending")["status"],
                         "NO_SAVED_REVIEW")
        self.assertEqual(self.research.aggregate_after_review(
            match_id="pending",team_id="different")["status"], "TEAM_ID_MISMATCH")

    def test_suggestions_disagree_must_not_fabricate_consensus_fact(self):
        self.record("m1", suggestion="交代を見直す")
        self.record("m2", suggestion="技選択を見直す")
        self.record("m3", suggestion="交代を見直す")
        result = self.research.aggregate_after_review(match_id="m3")
        suggestion = self.research.candidates(batch_sha=result["batch_sha"])[0]
        self.assertEqual(suggestion["proposed_change"], "交代を見直す")
        self.assertIn("differ", suggestion["risk"])
        self.assertEqual(suggestion["kind"], "TACTICAL_HYPOTHESIS")

    def test_production_postmatch_worker_aggregates_after_review_without_extra_api(self):
        from unittest.mock import patch
        from champions_assist.desktop import _PostmatchReviewWorker
        for index in range(3):
            self.record(f"m{index}")
        captured=[]
        worker=_PostmatchReviewWorker(self.root, "m2", self.root, "fake-model", "auto")
        worker.result_ready.connect(captured.append)
        with patch("champions_assist.siwc_auth.ProtectedSessionStore"), patch(
            "champions_assist.siwc_auth.ChatGPTSignIn") as auth, patch(
            "champions_assist.postmatch_learning.request_review",
            return_value={"status":"STORED_UNVERIFIED_REVIEW","match_id":"m2"}
        ) as request:
            auth.return_value.access_token.return_value="token-for-mock-only"
            worker.run()
        request.assert_called_once()
        self.assertEqual(len(captured),1)
        self.assertEqual(captured[0]["improvement_research"]["candidate_count"],1)
        self.assertEqual(len(self.research.history(team_id=TEAM)),1)

    def test_research_exception_does_not_change_successful_saved_review(self):
        from unittest.mock import patch
        from champions_assist.desktop import _PostmatchReviewWorker
        self.record("m1")
        worker=_PostmatchReviewWorker(self.root, "m1", self.root, "fake-model", "auto")
        captured=[]
        worker.result_ready.connect(captured.append)
        with patch("champions_assist.siwc_auth.ProtectedSessionStore"), patch(
            "champions_assist.siwc_auth.ChatGPTSignIn") as auth, patch(
            "champions_assist.postmatch_learning.request_review",
            return_value={"status":"STORED_UNVERIFIED_REVIEW","match_id":"m1"}
        ), patch(
            "champions_assist.autonomous_improvement.AutonomousImprovementStore.aggregate_after_review",
            side_effect=RuntimeError("research unavailable")
        ):
            auth.return_value.access_token.return_value="token-for-mock-only"
            worker.run()
        self.assertEqual(captured[0]["status"],"STORED_UNVERIFIED_REVIEW")
        self.assertEqual(captured[0]["improvement_research"]["status"],
                         "RESEARCH_FAILED_REVIEW_RETAINED")
        self.assertIsNotNone(self.learning.postmatch_review("m1"))

    def test_provisional_never_modifies_legacy_review_rows(self):
        for index in range(3):
            self.record(f"m{index}")
        with closing(sqlite3.connect(self.learning.db_path)) as db:
            before = db.execute("SELECT review_json FROM postmatch_reviews ORDER BY match_id").fetchall()
            before_matches = db.execute("SELECT payload FROM match_history ORDER BY match_id").fetchall()
        self.research.aggregate_after_review(match_id="m2")
        with closing(sqlite3.connect(self.learning.db_path)) as db:
            after = db.execute("SELECT review_json FROM postmatch_reviews ORDER BY match_id").fetchall()
            after_matches = db.execute("SELECT payload FROM match_history ORDER BY match_id").fetchall()
            self.assertEqual(before, after)
            self.assertEqual(before_matches, after_matches)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM autonomous_improvement_candidates").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT DISTINCT promotion_status FROM autonomous_improvement_batches").fetchall(),
                             [("NOT_AUTHORIZED",)])
        self.assertEqual(self.research.history(team_id=TEAM)[0]["candidate_count"], 1)


if __name__ == "__main__":
    unittest.main()
