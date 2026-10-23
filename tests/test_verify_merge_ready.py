"""Offline regression checks for the PR CI gate and local pre-merge auditor."""
from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path

from scripts.verify_merge_ready import REQUIRED_JOB, REQUIRED_WORKFLOW_PATH, verify


ROOT = Path(__file__).resolve().parents[1]


def valid_snapshot():
    sha = "a" * 40
    pr = {"state": "open", "draft": False, "head": {"sha": sha},
          "base": {"ref": "master"}}
    branch = {"protected": True}
    protection = {
        "required_status_checks": {
            "strict": True, "contexts": [REQUIRED_JOB], "checks": []},
        "required_pull_request_reviews": {"required_approving_review_count": 0},
        "enforce_admins": {"enabled": True},
    }
    run = {"id": 123, "head_sha": sha, "event": "pull_request",
           "path": REQUIRED_WORKFLOW_PATH, "created_at": "2026-10-23T12:00:00Z",
           "status": "completed", "conclusion": "success"}
    job = {"name": REQUIRED_JOB, "status": "completed", "conclusion": "success"}
    return pr, branch, protection, [run], {123: [job]}


class MergeReadinessTests(unittest.TestCase):
    def test_clean_fully_protected_latest_head_passes(self):
        self.assertEqual(verify(*valid_snapshot()), [])

    def test_failed_ci_blocks_even_when_other_checks_pass(self):
        pr, branch, protection, runs, jobs = valid_snapshot()
        runs[0]["conclusion"] = "failure"
        self.assertTrue(verify(pr, branch, protection, runs, jobs))

    def test_partial_success_does_not_override_latest_failure(self):
        pr, branch, protection, runs, jobs = valid_snapshot()
        old = deepcopy(runs[0])
        old["id"] = 122
        old["created_at"] = "2026-10-23T11:00:00Z"
        runs[0]["conclusion"] = "failure"
        runs.append(old)
        jobs[122] = deepcopy(jobs[123])
        self.assertTrue(verify(pr, branch, protection, runs, jobs))

    def test_stale_success_blocks(self):
        pr, branch, protection, runs, jobs = valid_snapshot()
        pr["head"]["sha"] = "b" * 40
        self.assertTrue(verify(pr, branch, protection, runs, jobs))

    def test_skipped_or_pending_check_blocks(self):
        pr, branch, protection, runs, jobs = valid_snapshot()
        jobs[123][0]["conclusion"] = "skipped"
        self.assertTrue(verify(pr, branch, protection, runs, jobs))
        jobs[123][0]["status"] = "in_progress"
        jobs[123][0]["conclusion"] = None
        self.assertTrue(verify(pr, branch, protection, runs, jobs))

    def test_unprotected_repository_blocks(self):
        pr, branch, protection, runs, jobs = valid_snapshot()
        branch["protected"] = False
        self.assertTrue(verify(pr, branch, protection, runs, jobs))

    def test_no_admin_enforcement_or_missing_required_check_blocks(self):
        pr, branch, protection, runs, jobs = valid_snapshot()
        protection["enforce_admins"]["enabled"] = False
        self.assertTrue(verify(pr, branch, protection, runs, jobs))
        protection["enforce_admins"]["enabled"] = True
        protection["required_status_checks"]["contexts"] = []
        self.assertTrue(verify(pr, branch, protection, runs, jobs))

    def test_draft_or_wrong_base_blocks(self):
        pr, branch, protection, runs, jobs = valid_snapshot()
        pr["draft"] = True
        self.assertTrue(verify(pr, branch, protection, runs, jobs))
        pr["draft"] = False
        pr["base"]["ref"] = "development"
        self.assertTrue(verify(pr, branch, protection, runs, jobs))

    def test_ci_workflow_keeps_upload_optional_but_checks_required(self):
        workflow = (ROOT / REQUIRED_WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertIn("publish_artifacts:", workflow)
        self.assertIn("type: boolean", workflow)
        self.assertIn("default: false", workflow)
        self.assertEqual(
            workflow.count(
                "if: github.event_name == 'workflow_dispatch' && inputs.publish_artifacts == true"),
            2)
        self.assertIn("  desktop-windows-validation:", workflow)
        self.assertIn("Run offline battle and settings tests on Windows", workflow)
        self.assertIn("Smoke dataset collector GUI offline", workflow)
        self.assertIn("Verify PR head is still current", workflow)
        self.assertNotIn("continue-on-error: true", workflow)


if __name__ == "__main__":
    unittest.main()
