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
            "PokemonChampionsGPUDiagnostics-v0.1.0-dev.17-windows-x64.zip",
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
        self.assertIn('"PokemonChampionsGPUDiagnostics-$ReleaseVersion-windows-x64.zip"', publisher)
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
