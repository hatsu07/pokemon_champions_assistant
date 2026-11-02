"""Real pinned Showdown → REAL pinned worker → Python IPC E2E.

Only run in Phase2 real-upstream CI after npm ci/build and fixture export.
The temporary file inventory is a TEST HARNESS, not independently reviewed
distributable runtime evidence. No Champions observation is invented.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from champions_assist.local_strategy.models import canonical_json
from champions_assist.local_strategy.simulator_adapter import LocalSimulatorWorker
from tests.test_issue_140_foundation import VERIFIED_ASSETS, frozen

PIN="ccfaa20f32af363569cd21a9d501b2bc1ded26b1"
REPO=Path(__file__).resolve().parents[1]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    return digest(path.read_bytes())


def make_ci_test_runtime(upstream: Path, root: Path) -> Path:
    """Copy actual pinned built dependencies: test-only inventory, no distro."""
    target=root / "showdown"
    target.mkdir()
    for directory in ("dist", "node_modules"):
        # npm .bin symlinks must be materialized as regular files before the
        # production worker rejects all links in its runtime inventory.
        shutil.copytree(upstream / directory, target / directory)
    for name in ("package.json","package-lock.json","LICENSE"):
        shutil.copy2(upstream / name, target / name)
    (target / ".champions-source-sha").write_text(PIN+"\n",encoding="utf-8")
    node=Path(shutil.which("node") or "")
    if not node.is_file():
        raise RuntimeError("actual Node executable is required")
    (root / "NODE_LICENSE.txt").write_text("synthetic-test-only-node-license",encoding="utf-8")
    (root / "THIRD_PARTY_NOTICES.txt").write_text(
        "synthetic-test-only-upstream-notice",encoding="utf-8")
    worker=REPO / "local_simulator/worker.cjs"
    p2=REPO / "local_simulator/p2_engine_choices.cjs"
    reader=REPO / "local_simulator/pinned_replay_readout.cjs"
    inventory={}
    for file in sorted(target.rglob("*")):
        if file.is_symlink():
            raise RuntimeError("test runtime contains a symlink")
        if file.is_file() and file.name!=".champions-source-sha":
            inventory[file.relative_to(target).as_posix()]=file_sha(file)
    manifest={
        "scope":"ISSUE_140_SYNTHETIC_ONLY",
        "engine_sha":PIN,
        "production_enabled":False,
        "showdown_entry_sha256":file_sha(target / "dist/sim/index.js"),
        "engine_file_sha256":inventory,
        "worker_sha256":file_sha(worker),
        "p2_engine_choices_sha256":file_sha(p2),
        "pinned_replay_readout_sha256":file_sha(reader),
        "node_exe_sha256":file_sha(node),
        "node_license_sha256":file_sha(root / "NODE_LICENSE.txt"),
        "third_party_notices_sha256":file_sha(root / "THIRD_PARTY_NOTICES.txt"),
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest,ensure_ascii=False),encoding="utf-8")
    return target


class RealPinnedReadoutPythonE2E(unittest.TestCase):
    def test_actual_pinned_showdown_worker_and_python_share_exact_full_battle_sha(self):
        fixture=os.environ.get("READOUT_E2E_FIXTURE_PATH","")
        upstream=os.environ.get("SHOWDOWN_PINNED_BUILD_ROOT","")
        self.assertTrue(fixture and upstream,
                        "This test requires the actual pinned-build CI environment")
        battle=json.loads(Path(fixture).read_text(encoding="utf-8"))
        self.assertIsInstance(battle,dict)
        self.assertIn("prng",canonical_json(battle))
        with tempfile.TemporaryDirectory() as tmp:
            target=make_ci_test_runtime(Path(upstream),Path(tmp))
            node=shutil.which("node")
            self.assertTrue(node)
            with LocalSimulatorWorker(
                node_executable=node,
                worker_script=str(REPO / "local_simulator/worker.cjs"),
                showdown_root=str(target),
                simulator_sha=PIN,
            ) as worker:
                snap=frozen()
                snap=replace(snap,window=replace(
                    snap.window,deadline_monotonic=time.monotonic()+30))
                outputs=[]
                for variant in (battle,{**battle,"turn":float(battle["turn"])}):
                    raw=canonical_json(variant)
                    receipt=worker.readout_synthetic_replay(
                        snap,variant,assets=VERIFIED_ASSETS)
                    sent=json.loads(receipt.submitted_request_json)
                    readout=json.loads(receipt.readout_json)
                    self.assertEqual(sent["op"],"READOUT_REPLAY")
                    self.assertEqual(sent["payload"]["battle_json"],raw)
                    self.assertEqual(receipt.snapshot_sha256,snap.snapshot_sha256)
                    self.assertEqual(receipt.branch_request_id,
                                     sent["branch_request_id"])
                    self.assertEqual(receipt.branch_input_sha256,
                                     sent["payload"]["branch_input_sha256"])
                    self.assertEqual(receipt.battle_sha256,
                                     digest(raw.encode("utf-8")))
                    self.assertEqual(readout["battle_sha256"],
                                     receipt.battle_sha256)
                    self.assertEqual(readout["status"],
                                     "SYNTHETIC_ENGINE_REPLAY_READOUT")
                    self.assertEqual(len(readout["members"]),6)
                    self.assertEqual(readout["active"],{"p1":1,"p2":1})
                    self.assertEqual(
                        [x["species_id"] for x in readout["members"]],
                        ["garchomp","primarina","mimikyu",
                         "dragonite","gholdengo","kingambit"])
                    self.assertEqual(
                        [x["move_ids"] for x in readout["members"][:3]],
                        [["earthquake","dragonclaw","rockslide","swordsdance"],
                         ["moonblast"],["playrough"]])
                    self.assertFalse(receipt.engine_battle_authenticated)
                    self.assertFalse(receipt.full_state_reconstructed)
                    self.assertFalse(receipt.opponent_slot_mapping_authenticated)
                    self.assertFalse(receipt.ready_for_engine)
                    outputs.append(receipt)
                self.assertNotEqual(outputs[0].branch_request_id,
                                    outputs[1].branch_request_id)
                self.assertNotEqual(outputs[0].battle_sha256,
                                    outputs[1].battle_sha256)


if __name__=="__main__":
    unittest.main()
