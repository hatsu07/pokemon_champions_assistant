"""Offline guardrails for development prerelease retention. No GitHub calls."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from pathlib import Path
from unittest import mock

from scripts import prune_development_releases as subject

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def release(days_old: int, index: int, **overrides) -> dict:
    sha = f"{index:040x}"
    item = {
        "tagName": f"development-windows-{sha}",
        "name": f"Windows DEVELOPMENT {sha[:12]}",
        "isPrerelease": True,
        "isDraft": False,
        "isImmutable": False,
        "createdAt": (NOW - timedelta(days=days_old)).isoformat(),
    }
    item.update(overrides)
    return item


class ReleasePruningSafetyTests(unittest.TestCase):
    def test_only_old_releases_beyond_latest_three(self):
        entries = [release(age, i + 1) for i, age in enumerate([1, 2, 3, 15, 30, 45])]
        got = subject.select_candidates(entries, now=NOW)
        self.assertEqual([item["tagName"] for item in got], [entries[3]["tagName"], entries[4]["tagName"], entries[5]["tagName"]])

    def test_never_delete_other_projects_tags_or_normal_releases(self):
        unrelated = [
            release(200, 20, tagName="v1.0.0"),
            release(200, 21, isPrerelease=False),
            release(200, 22, isDraft=True),
            release(200, 23, name="Official production release"),
            release(200, 24, isImmutable=True),
            release(200, 25, tagName="development-windows-123"),
            release(200, 26, tagName="development-windows-" + ("0" * 40), name="Manually created"),
        ]
        protected = [release(1, 1), release(2, 2), release(3, 3)]
        self.assertEqual(subject.select_candidates(protected + unrelated, now=NOW), [])

    def test_age_and_keep_bounds_are_enforced(self):
        with self.assertRaises(ValueError):
            subject.select_candidates([], now=NOW, keep=0)
        with self.assertRaises(ValueError):
            subject.select_candidates([], now=NOW, min_age_days=-1)

    def test_preview_never_issues_deletion(self):
        with mock.patch.object(subject, "list_releases", return_value=[release(30, 1)]), \
             mock.patch.object(subject, "execute_deletion") as delete:
            self.assertEqual(subject.main([]), 0)
            delete.assert_not_called()

    def test_apply_deletes_only_expired_old_dev_releases(self):
        entries = [release(i + 20, i + 1) for i in range(6)]
        with mock.patch.object(subject, "list_releases", return_value=entries), \
             mock.patch.object(subject, "execute_deletion") as delete:
            self.assertEqual(subject.main(["--apply"]), 0)
            self.assertEqual(len(delete.call_args.args[1]), 3)

    def test_listing_failure_fails_closed(self):
        with mock.patch.object(subject, "list_releases", side_effect=ValueError("broken JSON")), \
             mock.patch.object(subject, "execute_deletion") as delete:
            self.assertEqual(subject.main(["--apply"]), 1)
            delete.assert_not_called()

    def test_execute_cleanup_uses_gh_release_delete_and_cleanup_tag(self):
        with mock.patch.object(subject.subprocess, "run") as run:
            subject.execute_deletion(subject.REPOSITORY, [release(40, 99)])
            args = run.call_args.args[0]
            self.assertEqual(args[:3], ["gh", "release", "delete"])
            self.assertIn("--cleanup-tag", args)
            self.assertIn("--yes", args)

    def test_pr_validation_never_uploads_artifacts_or_publishes_releases(self):
        yaml = (ROOT / ".github/workflows/windows-validation-artifact.yml").read_text(encoding="utf-8")
        self.assertNotIn("actions/upload-artifact@", yaml)
        self.assertNotIn("gh release create", yaml)
        self.assertIn("  desktop-windows-validation:", yaml)
        self.assertIn("Verify PR head is still current", yaml)
        self.assertIn("Smoke dataset collector GUI offline", yaml)

    def test_publisher_passes_both_zips_at_release_create_time(self):
        publisher = (ROOT / "scripts/publish_windows_development_release.ps1").read_text(encoding="utf-8")
        self.assertIn("'release', 'create', $tag,", publisher)
        self.assertIn("$paths[0], $paths[1],", publisher)
        self.assertNotIn("'--draft'", publisher)
        self.assertIn("Assert-DevRelease -Release $release -RequireComplete $true", publisher)
        self.assertIn("if ($release.draft -ne $false)", publisher)

    def test_master_release_and_cleanup_are_not_triggered_by_untrusted_pr(self):
        release_yaml = (ROOT / ".github/workflows/windows-development-release.yml").read_text(encoding="utf-8")
        cleanup_yaml = (ROOT / ".github/workflows/windows-development-release-cleanup.yml").read_text(encoding="utf-8")
        self.assertIn("branches: [master]", release_yaml)
        self.assertNotIn("pull_request:", release_yaml)
        self.assertIn("contents: write", release_yaml)
        self.assertIn("scripts/publish_windows_development_release.ps1", release_yaml)
        self.assertIn("Smoke dataset collector GUI offline", release_yaml)
        self.assertNotIn("actions/upload-artifact@", release_yaml)
        self.assertIn("schedule:", cleanup_yaml)
        self.assertIn("scripts/prune_development_releases.py --min-age-days 14 --keep 3 --apply", cleanup_yaml)
        self.assertNotIn("pull_request:", cleanup_yaml)


if __name__ == "__main__":
    unittest.main()
