"""Regression tests for the CI gate and separating artifact uploads."""
from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path

from scripts.verify_merge_ready import REQUIRED_JOB, REQUIRED_WORKFLOW_PATH, verify

ROOT = Path(__file__).resolve().parents[1]


def snapshot():
    sha = "a" * 40
    pr = {"state": "open", "draft": False, "head": {"sha": sha},
          "base": {"ref": "master"}}
    run = {"id": 123, "head_sha": sha, "event": "pull_request",
           "path": REQUIRED_WORKFLOW_PATH, "created_at": "2026-10-23T12:00:00Z",
           "status": "completed", "conclusion": "success"}
    job = {"name": REQUIRED_JOB, "status": "completed", "conclusion": "success"}
    return pr, [run], {123: [job]}


class MergeReadinessTests(unittest.TestCase):
    def test_clean_latest_head_succeeds_without_paid_branch_protection(self):
        self.assertEqual(verify(*snapshot()), [])

    def test_failed_workflow_blocks_even_if_required_job_succeeded(self):
        pr, runs, jobs = snapshot()
        runs[0]["conclusion"] = "failure"
        self.assertTrue(verify(pr, runs, jobs))

    def test_latest_failed_run_cannot_be_overridden_by_older_success(self):
        pr, runs, jobs = snapshot()
        old = deepcopy(runs[0])
        old["id"] = 122
        old["created_at"] = "2026-10-23T11:00:00Z"
        runs[0]["conclusion"] = "failure"
        runs.append(old)
        jobs[122] = deepcopy(jobs[123])
        self.assertTrue(verify(pr, runs, jobs))

    def test_old_head_succeeded_but_new_head_unchecked(self):
        pr, runs, jobs = snapshot()
        pr["head"]["sha"] = "b" * 40
        self.assertTrue(verify(pr, runs, jobs))

    def test_skipped_or_incomplete_job_blocks(self):
        pr, runs, jobs = snapshot()
        jobs[123][0]["conclusion"] = "skipped"
        self.assertTrue(verify(pr, runs, jobs))
        jobs[123][0]["status"] = "in_progress"
        jobs[123][0]["conclusion"] = None
        self.assertTrue(verify(pr, runs, jobs))

    def test_skipped_workflow_blocks(self):
        pr, runs, jobs = snapshot()
        runs[0]["conclusion"] = "skipped"
        self.assertTrue(verify(pr, runs, jobs))

    def test_missing_jobs_and_unrelated_jobs_block(self):
        pr, runs, jobs = snapshot()
        jobs[123] = []
        self.assertTrue(verify(pr, runs, jobs))
        jobs[123] = [{"name": "unrelated", "status": "completed", "conclusion": "success"}]
        self.assertTrue(verify(pr, runs, jobs))

    def test_draft_and_wrong_base_block(self):
        pr, runs, jobs = snapshot()
        pr["draft"] = True
        self.assertTrue(verify(pr, runs, jobs))
        pr["draft"] = False
        pr["base"]["ref"] = "dev"
        self.assertTrue(verify(pr, runs, jobs))

    def test_dispatch_run_cannot_serve_as_pr_ci(self):
        pr, runs, jobs = snapshot()
        runs[0]["event"] = "workflow_dispatch"
        self.assertTrue(verify(pr, runs, jobs))

    def test_workflow_builds_and_publishes_both_zip_artifacts(self):
        workflow = (ROOT / REQUIRED_WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertIn("  desktop-windows-validation:", workflow)
        self.assertIn("Run offline battle and settings tests on Windows", workflow)
        self.assertIn("Test artifact cleanup script offline", workflow)
        self.assertIn("Smoke dataset collector GUI offline", workflow)
        self.assertIn("Verify PR head is still current", workflow)
        self.assertIn("name: PokemonChampionsAssistant-DEVELOPMENT-windows-x64", workflow)
        self.assertIn("name: PokemonChampionsScreenDataCollector-DEVELOPMENT-windows-x64", workflow)
        self.assertEqual(workflow.count("uses: actions/upload-artifact@v4"), 2)
        self.assertEqual(workflow.count("retention-days: 2"), 2)
        self.assertNotIn("publish_artifacts:", workflow)
        self.assertNotIn("inputs.publish_artifacts", workflow)
        self.assertNotIn("continue-on-error: true", workflow)


if __name__ == "__main__":
    unittest.main()
