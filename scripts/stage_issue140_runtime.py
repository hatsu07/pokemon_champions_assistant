"""Offline, opt-in staging of a Windows Node/Showdown trial runtime.

NOT called by production builds. Requires locally verified pinned upstream and
explicit third-party license notices; never downloads files or uses live data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


PINNED_SHOWDOWN_SHA = "ccfaa20f32af363569cd21a9d501b2bc1ded26b1"


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _engine_files(root: Path) -> dict[str, str]:
    """Enumerate all packaged code and dependency files; never follow links."""
    names = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlink in Showdown runtime tree is prohibited")
        if path.is_file() and path.name != ".champions-source-sha":
            names[path.relative_to(root).as_posix()] = _digest(path)
    return names


def stage(*, showdown_root: Path, node_exe: Path, node_license: Path,
          third_party_notices: Path, worker_script: Path,
          verified_build_manifest: Path,
          output: Path) -> dict:
    """Copy only already-present inputs, with deterministic source checks."""
    for p in (showdown_root, node_exe, node_license,
              third_party_notices, worker_script, verified_build_manifest):
        if not p.exists():
            raise FileNotFoundError(str(p))
    if output.exists():
        raise FileExistsError("refusing to overwrite staged runtime")
    checkout = subprocess.check_output(
        ["git", "-C", str(showdown_root), "rev-parse", "HEAD"],
        text=True, timeout=10).strip()
    if checkout != PINNED_SHOWDOWN_SHA:
        raise ValueError("Showdown checkout SHA not the pinned Phase 0 version")
    # Tracked source modifications invalidate the fixed upstream revision.
    # Ignored build outputs and dependencies are checked against an EXTERNAL
    # reviewed file hash inventory; do not generate this inventory from
    # the current checkout in the staging command.
    dirty = subprocess.check_output(
        ["git", "-C", str(showdown_root), "status",
         "--porcelain", "--untracked-files=no"],
        text=True, timeout=10)
    if dirty.strip():
        raise ValueError("Showdown checkout has tracked uncommitted changes")
    reference = json.loads(verified_build_manifest.read_text(encoding="utf-8"))
    if (not isinstance(reference, dict)
            or reference.get("engine_sha") != PINNED_SHOWDOWN_SHA
            or not isinstance(reference.get("engine_file_sha256"), dict)
            or not reference["engine_file_sha256"]
            or any(not isinstance(k, str) or not isinstance(v, str)
                   or len(v) != 64 for k, v in reference["engine_file_sha256"].items())):
        raise ValueError("independent verified build file inventory required")
    observed = _engine_files(showdown_root)
    # Only the exact runtime files staged below, not unrelated repository
    # sources, are compared to the independent build inventory.
    included = {k: v for k, v in observed.items()
                if k.startswith(("dist/", "node_modules/"))
                or k in ("LICENSE", "package.json", "package-lock.json")}
    if included != reference["engine_file_sha256"]:
        raise ValueError("Showdown build/dependency tree differs from verified inventory")
    if node_exe.name.lower() != "node.exe":
        raise ValueError("Windows runtime must contain a node.exe binary")
    required = [
        showdown_root / "dist" / "sim" / "index.js",
        showdown_root / "node_modules",
        showdown_root / "LICENSE",
        showdown_root / "package.json",
    ]
    if any(not p.exists() for p in required):
        raise FileNotFoundError("Showdown build/dependencies/licenses incomplete")
    if node_license.stat().st_size == 0 or third_party_notices.stat().st_size == 0:
        raise ValueError("runtime license inventory must not be empty")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        target = Path(tmp) / "runtime"
        target.mkdir()
        shutil.copy2(node_exe, target / "node.exe")
        shutil.copy2(node_license, target / "NODE_LICENSE.txt")
        shutil.copy2(third_party_notices, target / "THIRD_PARTY_NOTICES.txt")
        shutil.copy2(worker_script, target / "worker.cjs")
        engine = target / "showdown"
        engine.mkdir()
        for folder in ("dist", "node_modules"):
            shutil.copytree(showdown_root / folder, engine / folder)
        for name in ("LICENSE", "package.json", "package-lock.json"):
            src = showdown_root / name
            if src.is_file():
                shutil.copy2(src, engine / name)
        if _engine_files(engine) != reference["engine_file_sha256"]:
            raise ValueError("staged Showdown dependency tree differs from verified inventory")
        (engine / ".champions-source-sha").write_text(
            PINNED_SHOWDOWN_SHA + "\n", encoding="utf-8")
        manifest = {
            "scope": "ISSUE_140_SYNTHETIC_ONLY",
            "engine_sha": PINNED_SHOWDOWN_SHA,
            "node_exe_sha256": _digest(target / "node.exe"),
            "worker_sha256": _digest(target / "worker.cjs"),
            "showdown_entry_sha256": _digest(engine / "dist/sim/index.js"),
            "engine_file_sha256": reference["engine_file_sha256"],
            "node_license_sha256": _digest(target / "NODE_LICENSE.txt"),
            "third_party_notices_sha256": _digest(target / "THIRD_PARTY_NOTICES.txt"),
            "production_enabled": False,
            "champions_game_compatibility": "UNKNOWN",
        }
        (target / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        target.rename(output)
    return manifest


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--showdown-root", type=Path, required=True)
    p.add_argument("--node-exe", type=Path, required=True)
    p.add_argument("--node-license", type=Path, required=True)
    p.add_argument("--third-party-notices", type=Path, required=True)
    p.add_argument("--verified-build-manifest", type=Path, required=True,
                   help="Independent reviewed exact file hash inventory")
    p.add_argument("--worker-script", type=Path,
                   default=Path("local_simulator/worker.cjs"))
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = stage(showdown_root=args.showdown_root, node_exe=args.node_exe,
                   node_license=args.node_license,
                   third_party_notices=args.third_party_notices,
                   worker_script=args.worker_script,
                   verified_build_manifest=args.verified_build_manifest,
                   output=args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
