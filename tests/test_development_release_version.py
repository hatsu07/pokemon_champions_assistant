"""Semantic Windows development release labels; no GitHub writes."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts import development_release_version as subject

ROOT = Path(__file__).resolve().parents[1]


class SemanticDevelopmentVersionTests(unittest.TestCase):
    def test_checked_in_base_and_monotonic_release_sequence(self):
        first = subject.resolve(ROOT / "VERSION", 17)
        second = subject.resolve(ROOT / "VERSION", 18)
        self.assertEqual(first.label, "v0.1.0-dev.17")
        self.assertEqual(second.label, "v0.1.0-dev.18")
        self.assertEqual(first.tag, "development-windows-v0.1.0-dev.17")
        self.assertEqual(first.title, "Windows DEVELOPMENT v0.1.0-dev.17")
        self.assertEqual(first.zip_names, (
            "PokemonChampionsAssistant-v0.1.0-dev.17-windows-x64.zip",
            "PokemonChampionsScreenDataCollector-v0.1.0-dev.17-windows-x64.zip",
        ))
        self.assertEqual(first, subject.resolve(ROOT / "VERSION", 17))

    def test_semver_base_bump_keeps_version_shape(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "VERSION"
            path.write_text("1.2.3\n", encoding="utf-8")
            self.assertEqual(subject.resolve(path, 75).label, "v1.2.3-dev.75")

    def test_bad_versions_fail_closed(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "VERSION"
            for bad in ("", "1.2", "01.2.3", "1.02.3", "v1.2.3",
                        "1.2.3-dev.5", "1.2.3\nanything"):
                path.write_text(bad, encoding="utf-8")
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    subject.resolve(path, 12)
            path.write_text("1.2.3", encoding="utf-8")
            for bad in (0, -1, True):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    subject.resolve(path, bad)
            for bad in ("v0.1.0", "v0.1.0-dev.0", "v0.1.0-dev.01",
                        "v01.1.0-dev.3", "v0.1.0-dev.9-evil"):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    subject.from_label(bad)

    def test_github_outputs_are_exact_and_without_hash_names(self):
        with TemporaryDirectory() as directory:
            dest = Path(directory) / "outputs.txt"
            rc = subject.main([
                "--version-file", str(ROOT / "VERSION"),
                "--run-number", "12", "--github-output", str(dest),
            ])
            self.assertEqual(rc, 0)
            self.assertEqual(dest.read_text(encoding="utf-8").splitlines(), [
                "version=v0.1.0-dev.12",
                "tag=development-windows-v0.1.0-dev.12",
                "title=Windows DEVELOPMENT v0.1.0-dev.12",
            ])

    def test_gpu_diagnostic_cmd_is_excluded_but_developer_cli_is_retained(self):
        """Issue #110: assert both Windows packaging paths enforce exclusion."""
        pr_workflow = (ROOT / ".github/workflows/windows-validation-artifact.yml").read_text(
            encoding="utf-8")
        release_workflow = (ROOT / ".github/workflows/windows-development-release.yml").read_text(
            encoding="utf-8")
        for workflow in (pr_workflow, release_workflow):
            with self.subTest(workflow="development" if workflow == release_workflow else "PR"):
                self.assertNotIn(
                    "Copy-Item -LiteralPath \"scripts/Run-GPU-Diagnostics.cmd\"",
                    workflow)
                self.assertIn("Verify developer-only GPU diagnostics launcher is NOT packaged",
                              workflow)
                self.assertIn('-Filter "Run-GPU-Diagnostics.cmd" -Recurse -File',
                              workflow)
                self.assertIn("Developer-only Run-GPU-Diagnostics.cmd must not be included",
                              workflow)
                # GPU-free CLI contract is intentionally retained as a release gate.
                self.assertIn("Smoke bundled GPU diagnostic CLI without actual GPU inference",
                              workflow)
                self.assertIn("'--gpu-diagnostic-smoke'", workflow)
        # Archive entries must be checked in addition to the PyInstaller output.
        self.assertIn('$app -eq "PokemonChampionsAssistant"', release_workflow)
        self.assertIn('$zip.Entries', release_workflow)
        self.assertIn('-ieq "Run-GPU-Diagnostics.cmd"', release_workflow)
        self.assertIn('GPU diagnostic launcher found inside release ZIP',
                      release_workflow)

        entry = (ROOT / "scripts/windows_desktop_entry.py").read_text(
            encoding="utf-8")
        self.assertIn('"--gpu-diagnostics"', entry)
        self.assertIn('"--gpu-diagnostic-smoke"', entry)
        self.assertTrue((ROOT / "scripts/Run-GPU-Diagnostics.cmd").is_file())

        notes = (ROOT / "scripts/publish_windows_development_release.ps1").read_text(
            encoding="utf-8")
        self.assertIn("Run-GPU-Diagnostics.cmd is excluded", notes)
        self.assertIn("opt-in GPU diagnostic CLI remains available", notes)

    def test_windows_workflow_and_publisher_use_versioned_name_everywhere(self):
        yaml = (ROOT / ".github/workflows/windows-development-release.yml").read_text(encoding="utf-8")
        publisher = (ROOT / "scripts/publish_windows_development_release.ps1").read_text(encoding="utf-8")
        self.assertIn("id: release_version", yaml)
        self.assertIn("--run-number $env:GITHUB_RUN_NUMBER", yaml)
        self.assertIn('steps.release_version.outputs.version', yaml)
        self.assertIn('$name = "$app-$releaseVersion-windows-x64.zip"', yaml)
        self.assertIn('version=${{ steps.release_version.outputs.version }}', yaml)
        self.assertIn('-ReleaseVersion "${{ steps.release_version.outputs.version }}"', yaml)
        self.assertIn('$tag = "development-windows-$ReleaseVersion"', publisher)
        self.assertIn('$title = "Windows DEVELOPMENT $ReleaseVersion"', publisher)
        self.assertIn('"PokemonChampionsAssistant-$ReleaseVersion-windows-x64.zip"', publisher)
        self.assertIn('"PokemonChampionsScreenDataCollector-$ReleaseVersion-windows-x64.zip"', publisher)
        self.assertNotIn("PokemonChampionsGPUDiagnostics",publisher)
        self.assertEqual(len(subject.ZIP_APPS),2)
        self.assertIn("Release must contain exactly two approved ZIP assets",publisher)
        self.assertIn("$paths[0], $paths[1]",publisher)
        self.assertNotIn("$paths[2]",publisher)
        self.assertNotIn("PokemonChampionsGPUDiagnostics",yaml)
        self.assertNotIn("Package standalone GPU-debug ZIP",yaml)
        self.assertNotIn("windows_qt_test_runner.py",yaml)
        self.assertNotIn("python -m unittest",yaml)
        self.assertNotIn("tests/test_prune_actions_artifacts.sh",yaml)
        self.assertNotIn("Test release lifecycle scripts",yaml)
        self.assertIn("Verify master derives from a successful PR",yaml)
        self.assertIn("commits/$sha/pulls",yaml)
        self.assertIn("actions/runs?event=pull_request&head_sha=$head",yaml)
        self.assertIn("actions/runs/$($run.id)/jobs",yaml)
        self.assertIn("Smoke bundled GPU diagnostic CLI",yaml)
        self.assertIn("Smoke frozen persistent OCR child",yaml)
        self.assertIn("Smoke dataset collector GUI offline",yaml)
        self.assertIn("Stage and verify pinned PP-OCRv6",yaml)
        self.assertIn("desktop-windows-validation",yaml)
        self.assertIn("Unverified master file tree",yaml)
        self.assertNotIn('development-windows-$ExpectedSha', publisher)
        self.assertNotIn('DEVELOPMENT-windows-x64.zip', publisher)
        self.assertIn('$Release.target_commitish -ne $ExpectedSha', publisher)
        self.assertIn("--latest=false", publisher)
        # Catch accidentally concatenating the legacy SHA publisher after
        # the new semantic-version publisher: PowerShell would parse invalidly.
        self.assertEqual(publisher.count('$tag = "development-windows-$ReleaseVersion"'), 1)
        self.assertEqual(publisher.count("param("), 2)  # top-level and Assert-DevRelease
        self.assertTrue(publisher.rstrip().endswith(
            'Write-Host "Published and verified Windows DEVELOPMENT prerelease: $tag"'
        ))


if __name__ == "__main__":
    unittest.main()
