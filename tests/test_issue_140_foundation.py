"""Phase 1 isolated contract tests. No real-game or Showdown-compatibility claim."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch
import sys
import tempfile
import time
import unittest

from champions_assist.local_strategy.models import (
    ActionKind, CandidateAction, CapabilityStatus, DecisionWindow,
    FrozenBattleState, Hypothesis, LegalStatus, ObservedFact,
    SimulationResult, WindowKind, action_digest, fingerprint,
)
from champions_assist.local_strategy.legal_actions import (
    generate_candidates, revalidate_candidate,
)
from champions_assist.local_strategy.decision_service import accept_result
from champions_assist.local_strategy.simulator_adapter import (
    LocalSimulatorWorker, WorkerUnavailable,
)
from scripts.stage_issue140_runtime import stage, PINNED_SHOWDOWN_SHA


class FakeAssets:
    own_team = (
        {"name": "ガブリアス", "move1": "じしん", "move2": "ドラゴンクロー",
         "move3": "いわなだれ", "move4": "つるぎのまい"},
        {"name": "アシレーヌ", "move1": "ムーンフォース"},
        {"name": "ミミッキュ", "move1": "じゃれつく"},
    )
    move_class_index = {"つるぎのまい": "変化"}


ASSETS = FakeAssets()
MEMBERS = tuple(m["name"] for m in ASSETS.own_team)


def move_id(move: str) -> str:
    return fingerprint({"action_kind": "MOVE", "move": move, "switch_target": ""})


def switch_id(target: str) -> str:
    return fingerprint({"action_kind": "SWITCH", "move": "", "switch_target": target})


def frozen(*, window_kind: WindowKind = WindowKind.MOVE_COMMAND,
           visual_ui: str = "MOVE_MENU",
           verified: tuple[str, ...] = (move_id("じしん"),),
           constraints: bool = True,
           pp: int | None = 5, state_revision: int = 2,
           decision_window_id: str = "window-1",
           active: str = "ガブリアス", awaiting: bool = False,
           choice_lock: dict | None = None) -> FrozenBattleState:
    pp_by = ({"own": {"ガブリアス": {"じしん": pp}}}
             if pp is not None else {"own": {}})
    state = {
        "our_active": active, "selected_three": list(MEMBERS),
        "awaiting_replacement": awaiting,
        "battle_evidence": {
            "pp_by_pokemon": pp_by,
            "choice_lock": choice_lock or {},
            "hp_by_pokemon": {"own": {}},
        },
    }
    obs = {"visual_ui": visual_ui,
           "visible_moves": ["じしん", "ドラゴンクロー", "いわなだれ", "つるぎのまい"],
           "visible_party": ["アシレーヌ", "ミミッキュ"]}
    window = DecisionWindow(
        "match-1", decision_window_id, window_kind, 4, state_revision,
        visual_ui, active, "team-sha",
        tuple((x, "sha-"+x) for x in ("gate", "matrix", "lookup", "team")),
        "M-C", "ccfaa20f32af363569cd21a9d501b2bc1ded26b1",
        action_digest(verified), "req-1", time.monotonic()+60)
    return FrozenBattleState.freeze(
        window, state, obs, verified_action_ids=verified,
        constraints_confirmed=constraints)


def successful_result(s: FrozenBattleState) -> SimulationResult:
    return SimulationResult.from_worker(
        s.window.request_id, s.window.identity(), s.snapshot_sha256, "",
        CapabilityStatus.CONFIRMED, {"test": "synthetic-contract-only"})


class ContractTests(unittest.TestCase):
    def test_observation_and_hypothesis_are_disjoint(self):
        fact = ObservedFact.from_value("HP", "35%", "visible_ui", "2026-10-31T00:00Z")
        hyp = Hypothesis.from_assumptions("H1", {"item": "Choice Scarf"})
        self.assertEqual(json.loads(fact.value_json), "35%")
        self.assertEqual(json.loads(hyp.assumptions_json)["item"], "Choice Scarf")
        s = frozen()
        changed = s.state
        changed["our_active"] = "ミミッキュ"
        self.assertEqual(s.state["our_active"], "ガブリアス")

    def test_freeze_rejects_mismatched_actor_or_screen(self):
        s = frozen()
        bad = s.state
        bad["our_active"] = "ミミッキュ"
        with self.assertRaises(ValueError):
            FrozenBattleState.freeze(s.window, bad, s.observation,
                                     verified_action_ids=s.verified_action_ids)
        wrong = s.observation
        wrong["visual_ui"] = "PARTY_MENU"
        with self.assertRaises(ValueError):
            FrozenBattleState.freeze(s.window, s.state, wrong,
                                     verified_action_ids=s.verified_action_ids)

    def test_snapshot_rejects_digest_mismatch(self):
        s = frozen()
        with self.assertRaises(ValueError):
            FrozenBattleState.freeze(s.window, s.state, s.observation,
                                     verified_action_ids=("an-unseen-id",))

    def test_verified_move_stays_legal_and_revalidated(self):
        s = frozen()
        candidates = generate_candidates(s, ASSETS)
        earth = next(c for c in candidates if c.move == "じしん")
        self.assertEqual(earth.legal_status, LegalStatus.LEGAL)
        self.assertEqual(revalidate_candidate(earth, s, ASSETS).legal_status,
                         LegalStatus.LEGAL)

    def test_unknown_pp_and_constraints_are_not_legal(self):
        for s in (frozen(pp=None), frozen(constraints=False)):
            earth = next(c for c in generate_candidates(s, ASSETS)
                         if c.move == "じしん")
            self.assertEqual(earth.legal_status, LegalStatus.UNKNOWN)
        s = frozen(pp=0)
        earth = next(c for c in generate_candidates(s, ASSETS) if c.move == "じしん")
        self.assertEqual(earth.legal_status, LegalStatus.ILLEGAL)

    def test_command_menu_not_legality_proof(self):
        s = frozen(visual_ui="COMMAND_MENU")
        earth = next(c for c in generate_candidates(s, ASSETS) if c.move == "じしん")
        self.assertEqual(earth.legal_status, LegalStatus.UNKNOWN)

    def test_action_id_cannot_be_reused_for_another_move(self):
        s = frozen()
        fake = CandidateAction(move_id("じしん"), ActionKind.MOVE,
                               "ドラゴンクロー", window_id=s.window.decision_window_id)
        self.assertEqual(revalidate_candidate(fake, s, ASSETS).legal_status,
                         LegalStatus.ILLEGAL)

    def test_known_choice_lock_violation(self):
        s = frozen(choice_lock={"pokemon": "ガブリアス", "move": "ドラゴンクロー"})
        earth = next(c for c in generate_candidates(s, ASSETS) if c.move == "じしん")
        self.assertEqual(earth.legal_status, LegalStatus.ILLEGAL)
        self.assertEqual(earth.reason, "CHOICE_LOCK_VIOLATION")

    def test_faint_replacement_uses_party_window(self):
        s = frozen(window_kind=WindowKind.FAINT_REPLACEMENT, visual_ui="PARTY_MENU",
                   verified=(switch_id("アシレーヌ"),), active="", awaiting=True)
        candidates = generate_candidates(s, ASSETS)
        self.assertEqual(next(c for c in candidates if c.switch_target == "アシレーヌ")
                         .legal_status, LegalStatus.LEGAL)
        self.assertFalse(any(c.kind == ActionKind.MOVE for c in candidates))

    def test_future_pivot_target_never_certified(self):
        s = frozen()
        candidate = CandidateAction(
            fingerprint({"action_kind": "PIVOT", "move": "とんぼがえり",
                         "switch_target": "アシレーヌ"}),
            ActionKind.PIVOT, "とんぼがえり", "アシレーヌ",
            window_id=s.window.decision_window_id)
        self.assertEqual(revalidate_candidate(candidate, s, ASSETS).legal_status,
                         LegalStatus.UNKNOWN)

    def test_current_confirmed_contract_can_be_adopted(self):
        s = frozen()
        earth = next(c for c in generate_candidates(s, ASSETS) if c.move == "じしん")
        adopted = accept_result(s, s, successful_result(s), earth, ASSETS)
        self.assertEqual(adopted.status, "LOCAL_VERIFIED")

    def test_stale_revisions_and_ABA_windows_are_rejected(self):
        s = frozen()
        earth = next(c for c in generate_candidates(s, ASSETS) if c.move == "じしん")
        for changed in (frozen(state_revision=3),
                        frozen(decision_window_id="window-2")):
            outcome = accept_result(s, changed, successful_result(s), earth, ASSETS)
            self.assertEqual(outcome.status, "NO_RECOMMENDATION")

    def test_changed_observation_or_expired_deadline_rejected(self):
        s = frozen()
        earth = next(c for c in generate_candidates(s, ASSETS) if c.move == "じしん")
        changed = replace(s, observation_json=json.dumps({"visual_ui": "PARTY_MENU"}))
        self.assertEqual(accept_result(s, changed, successful_result(s),
                                       earth, ASSETS).status, "NO_RECOMMENDATION")
        self.assertEqual(accept_result(s, s, successful_result(s), earth,
                                       ASSETS, now=s.window.deadline_monotonic).status,
                         "NO_RECOMMENDATION")

    def test_unknown_capability_cannot_become_certified_advice(self):
        s = frozen()
        earth = next(c for c in generate_candidates(s, ASSETS) if c.move == "じしん")
        result = SimulationResult.from_worker(
            s.window.request_id, s.window.identity(), s.snapshot_sha256, "",
            CapabilityStatus.PARTIAL, {})
        self.assertEqual(accept_result(s, s, result, earth, ASSETS).status,
                         "NO_RECOMMENDATION")

    def test_malformed_worker_identity_returns_no_recommendation(self):
        s = frozen()
        candidate = next(c for c in generate_candidates(s, ASSETS) if c.move == "じしん")
        r = replace(successful_result(s), identity_json="{broken")
        self.assertEqual(accept_result(s, s, r, candidate, ASSETS).status,
                         "NO_RECOMMENDATION")

    def test_response_wrong_request_or_hypothesis_rejected(self):
        s = frozen()
        earth = next(c for c in generate_candidates(s, ASSETS) if c.move == "じしん")
        wrong = SimulationResult.from_worker(
            "old-req", s.window.identity(), s.snapshot_sha256, "",
            CapabilityStatus.CONFIRMED, {})
        self.assertEqual(accept_result(s, s, wrong, earth, ASSETS).status,
                         "NO_RECOMMENDATION")
        wrong_h = SimulationResult.from_worker(
            s.window.request_id, s.window.identity(), s.snapshot_sha256,
            "unobserved", CapabilityStatus.CONFIRMED, {})
        self.assertEqual(accept_result(s, s, wrong_h, earth, ASSETS).status,
                         "NO_RECOMMENDATION")


class WorkerTests(unittest.TestCase):
    def make_fake_worker(self, code: str):
        tmp = tempfile.TemporaryDirectory()
        script = Path(tmp.name, "fake_worker.py")
        script.write_text(code, encoding="utf-8")
        worker = LocalSimulatorWorker(
            node_executable=sys.executable, worker_script=str(script),
            showdown_root=tmp.name,
            simulator_sha="ccfaa20f32af363569cd21a9d501b2bc1ded26b1")
        return tmp, worker

    def test_worker_roundtrip_rejects_real_simulation_certainty(self):
        script = (
            "import sys,json\n"
            "for line in sys.stdin:\n"
            " r=json.loads(line)\n"
            " if r['op']=='SIMULATE':\n"
            "  old=dict(r); old['snapshot_sha256']='stale-snapshot'\n"
            "  old.update(capability_status='CONFIRMED',ok=True)\n"
            "  print(json.dumps(old),flush=True)\n"
            " r.update(capability_status='PARTIAL',ok=True,payload={})\n"
            " print(json.dumps(r),flush=True)\n"
        )
        tmp, w = self.make_fake_worker(script)
        try:
            w.start(timeout_seconds=1)
            s = frozen()
            result = w.simulate(s, {"synthetic": True},
                                "move 1", "move 1")
            self.assertEqual(result.capability_status, CapabilityStatus.PARTIAL)
        finally:
            w.close()
            tmp.cleanup()

    def test_worker_start_timeout_fails_closed(self):
        script = "import time\ntime.sleep(3)\n"
        tmp, w = self.make_fake_worker(script)
        try:
            with self.assertRaises(WorkerUnavailable):
                w.start(timeout_seconds=0.05)
        finally:
            w.close()
            tmp.cleanup()

    def test_missing_runtime_cannot_start(self):
        w = LocalSimulatorWorker(
            node_executable="/not/a/real/node", worker_script="/missing/worker",
            showdown_root="/missing/engine", simulator_sha="sha")
        with self.assertRaises(WorkerUnavailable):
            w.start()


    def test_worker_crash_never_restarts_or_returns_old_advice(self):
        script = (
            "import sys,json\n"
            "for line in sys.stdin:\n"
            " r=json.loads(line)\n"
            " if r['op']=='HELLO':\n"
            "  r.update(capability_status='PARTIAL',ok=True,payload={})\n"
            "  print(json.dumps(r),flush=True)\n"
            " else:\n"
            "  sys.exit(2)\n"
        )
        tmp, w = self.make_fake_worker(script)
        try:
            w.start(timeout_seconds=1)
            with self.assertRaises(WorkerUnavailable):
                w.simulate(frozen(), {"synthetic": True}, "move 1", "move 1")
            with self.assertRaises(WorkerUnavailable):
                w.start()
        finally:
            w.close()
            tmp.cleanup()


class StagingTests(unittest.TestCase):
    def test_pinned_runtime_stage_requires_sha_and_licenses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            showdown = root / "showdown"
            (showdown / "dist/sim").mkdir(parents=True)
            (showdown / "node_modules").mkdir()
            (showdown / "dist/sim/index.js").write_text("module.exports = {};")
            (showdown / "LICENSE").write_text("test-only-upstream-license")
            (showdown / "package.json").write_text("{}")
            node = root / "node.exe"
            node.write_bytes(b"synthetic-node-not-real-executable")
            node_license = root / "NODE_LICENSE"
            node_license.write_text("node-license-placeholder")
            notices = root / "notices"
            notices.write_text("third-party-notices-placeholder")
            worker = root / "worker.cjs"
            worker.write_text("// synthetic worker")
            target = root / "output"
            opts = dict(showdown_root=showdown, node_exe=node,
                        node_license=node_license,
                        third_party_notices=notices, worker_script=worker,
                        output=target)
            with patch("scripts.stage_issue140_runtime.subprocess.check_output",
                       return_value="wrong-SHA\n"):
                with self.assertRaises(ValueError):
                    stage(**opts)
            self.assertFalse(target.exists())
            with patch("scripts.stage_issue140_runtime.subprocess.check_output",
                       return_value=PINNED_SHOWDOWN_SHA + "\n"):
                manifest = stage(**opts)
            self.assertEqual(manifest["engine_sha"], PINNED_SHOWDOWN_SHA)
            self.assertFalse(manifest["production_enabled"])
            self.assertEqual((target / "showdown/.champions-source-sha")
                             .read_text().strip(), PINNED_SHOWDOWN_SHA)
            with self.assertRaises(FileExistsError):
                stage(**opts)

    def test_stage_rejects_empty_dependency_notices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "engine").mkdir()
            (root / "node.exe").touch()
            (root / "LICENSE").write_text("license")
            (root / "NOTICES").touch()
            (root / "worker.cjs").write_text("// worker")
            with patch("scripts.stage_issue140_runtime.subprocess.check_output",
                       return_value=PINNED_SHOWDOWN_SHA + "\n"):
                with self.assertRaises(FileNotFoundError):
                    stage(showdown_root=root / "engine",
                          node_exe=root / "node.exe",
                          node_license=root / "LICENSE",
                          third_party_notices=root / "NOTICES",
                          worker_script=root / "worker.cjs",
                          output=root / "out")


if __name__ == "__main__":
    unittest.main()
