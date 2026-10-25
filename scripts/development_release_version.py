"""Monotonic, human-readable semantic version labels for Windows DEVELOPMENT runs.

The checked-in VERSION is the release-line base (e.g. 0.1.0).
GitHub Actions' run_number is monotonically increasing for this workflow.
A rerun retains the *same* version, and the publishing script verifies its
original master commit SHA before reusing any existing release.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re

BASE_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
LABEL_PATTERN = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-dev\.([1-9]\d*)$")
RELEASE_PREFIX = "development-windows-"
DISPLAY_PREFIX = "Windows DEVELOPMENT "
ZIP_APPS = ("PokemonChampionsAssistant", "PokemonChampionsScreenDataCollector")


@dataclass(frozen=True)
class DevelopmentVersion:
    label: str
    tag: str
    title: str
    zip_names: tuple[str, ...]


def from_label(label: str) -> DevelopmentVersion:
    if LABEL_PATTERN.fullmatch(label) is None:
        raise ValueError("Expected semantic prerelease label vMAJOR.MINOR.PATCH-dev.RUN")
    return DevelopmentVersion(
        label=label,
        tag=RELEASE_PREFIX + label,
        title=DISPLAY_PREFIX + label,
        zip_names=tuple(f"{app}-{label}-windows-x64.zip" for app in ZIP_APPS),
    )


def resolve(version_file: Path, run_number: int) -> DevelopmentVersion:
    base = version_file.read_text(encoding="utf-8-sig").strip()
    if BASE_PATTERN.fullmatch(base) is None:
        raise ValueError("VERSION must be three non-negative SemVer components, e.g. 0.1.0")
    if not isinstance(run_number, int) or isinstance(run_number, bool) or run_number < 1:
        raise ValueError("GitHub workflow run_number must be a positive integer")
    return from_label(f"v{base}-dev.{run_number}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-file", type=Path, default=Path("VERSION"))
    parser.add_argument("--run-number", required=True, type=int)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    result = resolve(args.version_file, args.run_number)
    print(f"Development release: {result.title} (tag {result.tag})")
    if args.github_output is not None:
        # GitHub Actions output file is provided by the runner, not by user data.
        with args.github_output.open("a", encoding="utf-8") as f:
            f.write(f"version={result.label}\n")
            f.write(f"tag={result.tag}\n")
            f.write(f"title={result.title}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
