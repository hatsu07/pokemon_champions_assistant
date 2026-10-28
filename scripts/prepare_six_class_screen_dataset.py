"""Copy unambiguous legacy 7-class PNGs into a NEW six-class dataset.

The SOURCE tree is NEVER modified. Dry-run by default. Requires review of
legacy BATTLE_EVENT and UNKNOWN images; they are NOT automatically mapped.
No screenshots or file contents leave the local computer.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import shutil
import sys

SAFE_MAPPING = {
    "MATCHMAKING": "OTHER",
    "TEAM_SELECTION": "TEAM_SELECTION",
    "MOVE_MENU": "TURN_ACTION",
    "PARTY_MENU": "SWITCH_SELECTION",
    "RESULT": "RESULT_SCREEN",
}
REVIEW_REQUIRED = ("BATTLE_EVENT", "UNKNOWN")
NEW_LABELS = ("TEAM_SELECTION", "TURN_ACTION", "SWITCH_SELECTION",
              "BATTLE_EVENT", "RESULT_SCREEN", "OTHER")
LEGACY_LABELS = (*SAFE_MAPPING, *REVIEW_REQUIRED)


def build_plan(source: Path, destination: Path):
    source = source.resolve(strict=True)
    destination = destination.resolve(strict=False)
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Source and destination must be separate, non-nested directories")
    if not source.is_dir():
        raise ValueError("Source must be a directory")
    # Existing destination labels may contain user-reviewed images; never mix or overwrite.
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Destination must be a NEW or empty directory")
    copied = []
    manual = Counter()
    destination_seen = set()
    for label in LEGACY_LABELS:
        folder = source / label
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.suffix.lower() != ".png":
                continue
            if path.is_symlink():
                raise ValueError(f"Refusing symlinked image: {path}")
            if label in REVIEW_REQUIRED:
                manual[label] += 1
                continue
            target = destination / SAFE_MAPPING[label] / path.name
            normalized = str(target).casefold()
            if normalized in destination_seen:
                raise ValueError(f"Collision in destination (same filename): {target}")
            destination_seen.add(normalized)
            if "__" not in path.stem:
                raise ValueError(f"Missing session prefix in image name: {path}")
            copied.append((path, target))
    return copied, manual


def apply_plan(copied, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for label in NEW_LABELS:
        (destination / label).mkdir(exist_ok=False)
    for old, new in copied:
        # Never overwrite even when a destination was unexpectedly created.
        with old.open("rb") as reader, new.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
        shutil.copystat(old, new)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True,
                        help="Existing seven-label screens directory (read-only)")
    parser.add_argument("--destination", type=Path, required=True,
                        help="New, empty six-label screens directory")
    parser.add_argument("--apply", action="store_true",
                        help="Create NEW directory and copy only unambiguous PNGs")
    args = parser.parse_args(argv)
    try:
        if args.destination.is_symlink():
            raise ValueError("Refusing symlinked destination")
        plan, manual = build_plan(args.source, args.destination)
        # Fail before applying if an ambiguous folder is hidden behind symlinks.
        if any((args.source / label).is_symlink() for label in LEGACY_LABELS):
            raise ValueError("Refusing symlinked source label directory")
        print(f"{'APPLY' if args.apply else 'DRY RUN'}: {len(plan)} unambiguous PNGs -> {args.destination}")
        for label in REVIEW_REQUIRED:
            print(f"MANUAL REVIEW (NOT COPIED): {label}: {manual[label]} PNGs")
        print("Original seven-class dataset is never changed.")
        if not args.apply:
            return 0
        if args.destination.exists():
            # Empty existing destination may be user-owned; do not delete it.
            raise ValueError("For --apply, destination must not exist yet")
        apply_plan(plan, args.destination)
        print("Copied safe classes. Review BATTLE_EVENT/UNKNOWN, then validate the new dataset.")
        return 0
    except (OSError, ValueError) as exc:
        print(f"MIGRATION ABORTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
