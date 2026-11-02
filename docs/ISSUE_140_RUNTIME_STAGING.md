# Issue 140 — offline Windows runtime staging, not production adoption

By explicit 2026-11-02 user decision (PR #174), the fixed Showdown engine is
the development assumption; waiting for proof of all game mechanics is no longer
required to build Phase 1. Reproduce the pinned checkout (no upstream auto-follow),
build the Champions-capable simulator in an isolated Docker environment, and
assemble the full Node binary and dependency license inventory.

The opt-in staging script validates the **git HEAD** of the Showdown checkout
against `ccfaa20f32af363569cd21a9d501b2bc1ded26b1`, rejects a dirty
tracked working tree, checks prebuilt `dist` and `node_modules` against
a **separately reviewed exact-file SHA256 inventory** provided using
`--verified-build-manifest`, requires Node and third-party notices, and
generates a runtime manifest containing SHA256 for **every** packaged engine
file. That inventory must come from a separately validated pinned build;
creating it from the same unverified staging inputs defeats the trust boundary.
The JSON has `engine_sha` (fixed pin) and `engine_file_sha256` (relative
path → 64-character SHA256 of each included file, including entrypoint, other
dist modules, dependencies, package manifest, and license). The worker
re-enumerates and hashes the entire packaged engine tree before `require()`,
rejecting added/deleted/modified files, including after staging.

Example (paths are operator supplied; not a command to run on Windows):

```bash
python scripts/stage_issue140_runtime.py \
  --showdown-root /isolated/pinned-showdown \
  --node-exe /offline/windows-node/node.exe \
  --node-license /offline/windows-node/LICENSE \
  --third-party-notices /offline/THIRD_PARTY_NOTICES.txt \
  --verified-build-manifest /offline/reviewed-pinned-tree.json \
  --output /temporary/phase1-node-trial
```

No download, CI artifact publication, app-bundle installation, or production
enablement is performed. The output is a **trial staging directory** only.
Use a controlled Windows onedir/offline smoke test and actual license/SBOM
audit before integrating it into PyInstaller. The protocol supports explicit
local startup using the staged `node.exe`, `worker.cjs`, and `showdown/`
paths but does not provide live gameplay recommendations.

**Still missing in this particular scaffold**: bundled Windows runtime with
real Node binary and complete dependency notices/SBOM, Windows installer/runtime
smoke, actual device E2E and tactical GUI integration. Game calculation parity
(Gate A UNKNOWN) and arbitrary observed midbattle reconstruction
(Gate B UNSUPPORTED) remain recorded risks but are **not** implementation
start/merge blockers under PR #174. No finished local strategy is claimed.
