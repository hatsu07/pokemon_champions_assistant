"""Safely prune ONLY old commit-tagged Windows DEVELOPMENT GitHub Releases.

By default this is a dry run. No Actions Artifacts quota is used.
Requires GitHub CLI with release deletion rights for --apply.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import re
import subprocess
import sys

REPOSITORY = "hatsu07/pokemon_champions_assistant"
TAG_PATTERN = re.compile(r"^development-windows-([0-9a-f]{40})$")
DEFAULT_KEEP = 3
DEFAULT_MIN_AGE_DAYS = 14


def _date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def list_releases(repo: str) -> list[dict]:
    completed = subprocess.run(
        ["gh", "release", "list", "--repo", repo, "--limit", "1000",
         "--json", "tagName,name,isPrerelease,isDraft,isImmutable,createdAt"],
        capture_output=True, text=True, check=True, encoding="utf-8",
    )
    data = json.loads(completed.stdout)
    if not isinstance(data, list):
        raise ValueError("gh release list did not return a JSON array")
    # Do not assume newest/oldest ordering or silently prune a truncated result.
    if len(data) >= 1000:
        raise ValueError("At least 1000 releases found: refuse to prune a potentially truncated list")
    return data


def select_candidates(
    releases: list[dict],
    *,
    now: datetime,
    min_age_days: int = DEFAULT_MIN_AGE_DAYS,
    keep: int = DEFAULT_KEEP,
) -> list[dict]:
    if not 0 <= min_age_days <= 3650 or not 2 <= keep <= 100:
        raise ValueError("min_age_days must be 0..3650; keep must be 2..100")
    matching = []
    for item in releases:
        if not isinstance(item, dict):
            raise ValueError("Unexpected release entry")
        tag = item.get("tagName", "")
        match = TAG_PATTERN.fullmatch(tag) if isinstance(tag, str) else None
        if not match:
            continue
        sha = match.group(1)
        if (item.get("name") != f"Windows DEVELOPMENT {sha[:12]}"
                or item.get("isPrerelease") is not True
                or item.get("isDraft") is not False):
            continue
        if not isinstance(item.get("createdAt"), str):
            raise ValueError("Development release missing createdAt")
        matching.append(item)
    matching.sort(key=lambda i: _date(i["createdAt"]), reverse=True)
    cutoff = now - timedelta(days=min_age_days)
    return [
        item
        for position, item in enumerate(matching)
        if position >= keep
        and item.get("isImmutable") is False
        and _date(item["createdAt"]) <= cutoff
    ]


def execute_deletion(repo: str, items: list[dict]) -> None:
    for item in items:
        tag = item["tagName"]
        # GitHub CLI deletes BOTH the development prerelease and its matching
        # generated commit tag. No unrelated release names are accepted.
        subprocess.run(
            ["gh", "release", "delete", tag, "--repo", repo,
             "--yes", "--cleanup-tag"],
            check=True,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=REPOSITORY)
    parser.add_argument("--min-age-days", type=int, default=DEFAULT_MIN_AGE_DAYS)
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    parser.add_argument("--apply", action="store_true", help="Actually delete (dry-run by default)")
    args = parser.parse_args(argv)
    if args.repo != REPOSITORY:
        parser.error("Cleanup is restricted to the configured private repository")
    if not (0 <= args.min_age_days <= 3650 and 2 <= args.keep <= 100):
        parser.error("Expected min-age-days 0..3650 and keep 2..100")
    try:
        releases = list_releases(args.repo)
        candidates = select_candidates(
            releases, now=datetime.now(timezone.utc),
            min_age_days=args.min_age_days, keep=args.keep,
        )
    except (OSError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as exc:
        print(f"Release listing/validation failed; nothing deleted: {exc}", file=sys.stderr)
        return 1
    print(f"{'APPLY' if args.apply else 'DRY RUN'} repo={args.repo}"
          f" keep={args.keep} min_age_days={args.min_age_days}"
          f" candidates={len(candidates)}")
    for item in candidates:
        print(f"  delete {item['tagName']} createdAt={item['createdAt']}")
    if not args.apply:
        print("No releases deleted. Add --apply after reviewing the list.")
        return 0
    try:
        execute_deletion(args.repo, candidates)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Deletion stopped on GitHub API error: {exc}", file=sys.stderr)
        return 1
    print(f"Deleted {len(candidates)} eligible old DEVELOPMENT releases and generated tags.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
