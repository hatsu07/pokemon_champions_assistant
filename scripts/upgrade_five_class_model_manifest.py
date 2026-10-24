"""Non-destructively upgrade a locally trained *five-class* v1 manifest to v2.

The old seven-label schema-v1 models are NOT compatible and are rejected.
The original model/manifest are never edited, even when --apply is supplied.
No retraining, network access, release promotion, or user-image processing.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from champions_assist.screen_classifier import LABELS, LABEL_SCHEMA_VERSION

MODEL_NAME = "champions_screen_classifier.onnx"
MANIFEST_NAME = "champions_screen_classifier.json"


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_legacy_five_class_model(source_dir: Path) -> dict[str, Any]:
    """Allow conversion only for a well-formed legacy five-class training pair."""
    source = Path(source_dir)
    if source.is_symlink() or not source.is_dir():
        raise ValueError("source must be an existing non-symlink directory")
    model = source / MODEL_NAME
    manifest = source / MANIFEST_NAME
    if any(path.is_symlink() or not path.is_file() for path in (model, manifest)):
        raise ValueError("source must contain a real ONNX and adjacent JSON manifest")
    info = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(info, dict):
        raise ValueError("manifest must be an object")
    if info.get("schema_version") != 1:
        raise ValueError("only schema v1 training manifests can be converted")
    if info.get("labels") != list(LABELS):
        raise ValueError("refusing legacy seven-class or reordered classifier labels")
    if info.get("input_size") != [160, 160]:
        raise ValueError("unexpected input shape; conversion is not safe")
    if not isinstance(info.get("metrics"), dict) or set(info["metrics"]) != set(LABELS):
        raise ValueError("five-class per-label evaluation metrics are required")
    if type(info.get("validated")) is not bool:
        raise ValueError("validated flag must be an explicit boolean")
    fingerprint = info.get("sha256")
    if not isinstance(fingerprint, str) or _hash_file(model) != fingerprint.lower():
        raise ValueError("model SHA-256 and manifest do not match")
    return info


def upgrade_legacy_five_class_model(
    source_dir: Path, destination_dir: Path, *, apply: bool = False,
) -> dict[str, Any]:
    """Generate a NEW sibling model folder, never modify or delete the archive."""
    source, destination = Path(source_dir), Path(destination_dir)
    info = inspect_legacy_five_class_model(source)
    real_source = source.resolve(strict=True)
    real_destination = destination.resolve(strict=False)
    if destination.is_symlink() or destination.exists():
        raise ValueError("destination must be a new, non-existing directory")
    if real_destination == real_source or real_destination.parent != real_source.parent:
        raise ValueError("destination must be a new sibling of the source folder")
    result = {
        "source": str(real_source),
        "destination": str(real_destination),
        "source_schema_version": 1,
        "new_schema_version": LABEL_SCHEMA_VERSION,
        "labels": list(LABELS),
        "model_sha256": info["sha256"],
        "validated": False,
        "status": "DRY_RUN" if not apply else "CREATED_EXPERIMENTAL_ONLY",
    }
    if not apply:
        return result
    updated = dict(info)
    updated["schema_version"] = LABEL_SCHEMA_VERSION
    # Schema repair does not prove competitive accuracy or release readiness,
    # even if a previous manifest said validated=true.
    updated["validated"] = False
    updated["manifest_migration"] = "legacy_five_class_schema_v1_to_v2"
    with tempfile.TemporaryDirectory(
        dir=real_destination.parent, prefix=f".{destination.name}-staging-"
    ) as working:
        staging = Path(working)
        shutil.copy2(real_source / MODEL_NAME, staging / MODEL_NAME)
        if _hash_file(staging / MODEL_NAME) != info["sha256"].lower():
            raise ValueError("copied ONNX checksum changed")
        (staging / MANIFEST_NAME).write_text(
            json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        # The source remains intact. Destination must not pre-exist.
        if real_destination.exists():
            raise FileExistsError("destination was created concurrently")
        os.rename(staging, real_destination)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--destination-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="write a new independent model directory")
    args = parser.parse_args(argv)
    try:
        result = upgrade_legacy_five_class_model(
            args.source_dir, args.destination_dir, apply=args.apply
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, f"MIGRATION_BLOCKED: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
