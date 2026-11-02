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

from champions_assist.local_strategy.models import canonical_json, fingerprint
from champions_assist.local_strategy.simulator_adapter import LocalSimulatorWorker
from champions_assist.local_strategy.observed_synthetic_replay_bridge import (
    ExplicitSyntheticBattleBranch, compare_observed_to_synthetic_replays,
)
from champions_assist.local_strategy.observed_full_grid_diagnostic import (
    audit_observed_synthetic_turn,
)
from tests.test_issue_141_joint_set_intake import record
from tests.test_issue_141_observed_id_bridge import with_state
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

    def test_actual_pinned_worker_full_sourced_batch_matches_only_visible_constraints(self):
        """Real Battle/worker/Python joined to Stage②, without game attestation."""
        fixture=os.environ.get("READOUT_E2E_FIXTURE_PATH","")
        upstream=os.environ.get("SHOWDOWN_PINNED_BUILD_ROOT","")
        self.assertTrue(fixture and upstream)
        battle=json.loads(Path(fixture).read_text(encoding="utf-8"))
        # The actual engine fixture's p2.active is Dragonite (slot 1).
        # These display identities are USER-CLAIMED, not authenticated by
        # Showdown or OCR. No exact p2 HP is fabricated in the observation.
        snap=with_state(frozen(),edits=lambda s:s.update(
            opponent_active="カイリュー",
            opponent_six=["カイリュー","サーフゴー","ドドゲザン",
                          "ウルガモス","ミミッキュ","ロトム"]))
        snap=replace(snap,window=replace(
            snap.window,deadline_monotonic=time.monotonic()+30))
        sources=(
            record("Joint-1"),
            record("Joint-2",source_ref="synthetic://second-joint"),
        )
        entries=tuple(ExplicitSyntheticBattleBranch(
            c.candidate_id,battle,("カイリュー","サーフゴー","ドドゲザン"))
            for c in sources)
        with tempfile.TemporaryDirectory() as tmp:
            target=make_ci_test_runtime(Path(upstream),Path(tmp))
            with LocalSimulatorWorker(
                node_executable=shutil.which("node"),
                worker_script=str(REPO / "local_simulator/worker.cjs"),
                showdown_root=str(target),simulator_sha=PIN,
            ) as worker:
                out=compare_observed_to_synthetic_replays(
                    snap,VERIFIED_ASSETS,joint_candidates=sources,
                    complete_branches=entries,worker=worker)
        self.assertEqual(
            out.status,"SYNTHETIC_REPLAYS_MATCH_OBSERVED_CONSTRAINTS_ONLY",
            out.reason)
        self.assertEqual(out.hypothesis_ids,("Joint-1","Joint-2"))
        self.assertEqual(len(out.battle_sha256),2)
        self.assertTrue(all(v==out.battle_sha256[0]
                            for v in out.battle_sha256))
        self.assertIn("p1:1.species_id",out.checked_fields[0])
        self.assertEqual(out.ranking_status,"NO_RANKING")
        self.assertFalse(out.game_battle_authenticated)
        self.assertFalse(out.opponent_mapping_authenticated)
        self.assertFalse(out.full_state_reconstructed)
        self.assertFalse(out.ready_for_engine)
 
    def test_actual_pinned_worker_observed_readout_p2_and_simulation_same_battle(self):
        """Real worker E2E for the review P2 integrity fix, synthetic only.

        Three operations must see the same *original complete* Battle/RNG
        bytes for every hypothesis and p1/p2 branch. This is not proof that
        any such Battle came from an actual Champions match.
        """
        fixture=os.environ.get("READOUT_E2E_FIXTURE_PATH","")
        upstream=os.environ.get("SHOWDOWN_PINNED_BUILD_ROOT","")
        self.assertTrue(fixture and upstream)
        battle=json.loads(Path(fixture).read_text(encoding="utf-8"))
        expected_sha=digest(canonical_json(battle).encode("utf-8"))
        snap=with_state(frozen(),edits=lambda s:s.update(
            opponent_active="カイリュー",
            opponent_six=["カイリュー","サーフゴー","ドドゲザン",
                          "ウルガモス","ミミッキュ","ロトム"]))
        snap=replace(snap,window=replace(
            snap.window,deadline_monotonic=time.monotonic()+90))
        sources=(
            record("Joint-1"),
            record("Joint-2",source_ref="synthetic://second-joint"),
        )
        entries=tuple(ExplicitSyntheticBattleBranch(
            c.candidate_id,battle,("カイリュー","サーフゴー","ドドゲザン"))
            for c in sources)

        class TracingActualWorker(LocalSimulatorWorker):
            def __init__(self,**kwargs):
                super().__init__(**kwargs)
                self.readouts=[]
                self.enumerations=[]
                self.simulations=[]

            def readout_synthetic_replay(self,snap,battle_json,**kwargs):
                self.readouts.append((
                    kwargs["hypothesis_id"],
                    digest(canonical_json(dict(battle_json)).encode("utf-8"))))
                return super().readout_synthetic_replay(
                    snap,battle_json,**kwargs)

            def enumerate_p2_from_replay(self,snap,battle_json,**kwargs):
                self.enumerations.append((
                    kwargs["hypothesis_id"],fingerprint(dict(battle_json))))
                return super().enumerate_p2_from_replay(
                    snap,battle_json,**kwargs)

            def simulate(self,snap,battle_json,own_choice,opponent_choice,
                         **kwargs):
                self.simulations.append((
                    kwargs["hypothesis_id"],fingerprint(dict(battle_json)),
                    own_choice,opponent_choice))
                return super().simulate(
                    snap,battle_json,own_choice,opponent_choice,**kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            target=make_ci_test_runtime(Path(upstream),Path(tmp))
            with TracingActualWorker(
                node_executable=shutil.which("node"),
                worker_script=str(REPO / "local_simulator/worker.cjs"),
                showdown_root=str(target),simulator_sha=PIN,
            ) as worker:
                out=audit_observed_synthetic_turn(
                    snap,VERIFIED_ASSETS,joint_candidates=sources,
                    complete_branches=entries,worker=worker,
                    preserve="ガブリアス",stopper="ミミッキュ",
                    current_snapshot=lambda:snap,reserve_seconds=0.5)
        self.assertEqual(
            out.status,"SYNTHETIC_TURN_DIAGNOSTIC_ONLY",out.reason)
        self.assertEqual(out.ranking_status,"NO_RANKING")
        self.assertEqual(out.hypothesis_ids,("Joint-1","Joint-2"))
        self.assertEqual(out.synthetic_battle_sha256,
                         (expected_sha,expected_sha))
        self.assertEqual(worker.readouts,
                         [("Joint-1",expected_sha),("Joint-2",expected_sha)])
        self.assertEqual(worker.enumerations,
                         [("Joint-1",expected_sha),("Joint-2",expected_sha)])
        self.assertEqual(len(worker.simulations),
                         out.evaluated_branch_count)
        self.assertTrue(worker.simulations)
        self.assertEqual(
            {v for _,v,_,_ in worker.simulations},{expected_sha})
        self.assertEqual(out.evaluated_branch_count,
                         sum(out.engine_choice_counts))
        self.assertFalse(out.game_battle_authenticated)
        self.assertFalse(out.opponent_mapping_authenticated)
        self.assertFalse(out.exhaustive_opponent_policy)
        self.assertFalse(out.full_state_reconstructed)
        self.assertFalse(out.ready_for_engine)


if __name__=="__main__":
    unittest.main()
