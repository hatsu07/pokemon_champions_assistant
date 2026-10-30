# Issue 140 — offline Windows runtime staging, not production adoption

After Phase 0 approval, reproduce the pinned checkout (no upstream auto-follow),
build the Champions-capable simulator in an isolated Docker environment, and
assemble the full Node binary and dependency license inventory.

The opt-in staging script validates the **git HEAD** of the Showdown checkout
against `ccfaa20f32af363569cd21a9d501b2bc1ded26b1`, checks prebuilt
dist/node_modules, requires a Node license and third-party notices, and
generates a manifest with input SHA256 values.

Example (paths are operator supplied; not a command to run on Windows):

```bash
python scripts/stage_issue140_runtime.py \
  --showdown-root /isolated/pinned-showdown \
  --node-exe /offline/windows-node/node.exe \
  --node-license /offline/windows-node/LICENSE \
  --third-party-notices /offline/THIRD_PARTY_NOTICES.txt \
  --output /temporary/phase1-node-trial
```

No download, CI artifact publication, app-bundle installation, or production
enablement is performed. The output is a **trial staging directory** only.
Use a controlled Windows onedir/offline smoke test and actual license/SBOM
audit before integrating it into PyInstaller. The protocol supports explicit
local startup using the staged `node.exe`, `worker.cjs`, and `showdown/`
paths but does not provide live gameplay recommendations.

**Missing**: proven Champions calculation equivalence, arbitrary observed
midbattle reconstruction, Windows installer/runtime smoke, actual device E2E
and permission to switch GPT production advice. These remain Phase 0/2/3
gates, not achievements of this scaffold.
