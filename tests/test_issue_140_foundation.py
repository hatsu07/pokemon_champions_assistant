"""Phase 1 isolated contract tests. No real-game or Showdown-compatibility claim."""
from __future__ import annotations

from dataclasses import replace
import csv
import hashlib
import io
import json
from pathlib import Path
from types import MappingProxyType
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
from scripts.stage_issue140_runtime import stage, PINNED_SHOWDOWN_SHA, _engine_files
from champions_assist.preload import FrozenMatchAssets, REQUIRED_MASTER_FILES, _parse_team


class FakeAssets:
    own_team = (
        {"name": "ガブリアス", "move1": "じしん", "move2": "ドラゴンクロー",
         "move3": "いわなだれ", "move4": "つるぎのまい"},
        {"name": "アシレーヌ", "move1": "ムーンフォース"},
        {"name": "ミミッキュ", "move1": "じゃれつく"},
    )
    move_class_index = {"つるぎのまい": "変化"}


ASSETS = FakeAssets()

class FakeAssetsWrongMove:
    own_team = (
        {"name": "ガブリアス", "move1": "ドラゴンクロー",
         "move2": "じしん", "move3": "いわなだれ", "move4": "つるぎのまい"},
        *FakeAssets.own_team[1:],
    )
    move_class_index = FakeAssets.move_class_index

MEMBERS = tuple(m["name"] for m in ASSETS.own_team)


def _fake_preloaded_assets():
    """Real FrozenMatchAssets type with synthetic, internally consistent bytes."""
    species_ids = {
        "ガブリアス": "garchomp", "アシレーヌ": "primarina",
        "ミミッキュ": "mimikyu", "カイリュー": "dragonite",
        "サーフゴー": "gholdengo", "ドドゲザン": "kingambit",
    }
    move_ids = {
        "じしん": "earthquake", "ドラゴンクロー": "dragonclaw",
        "いわなだれ": "rockslide", "つるぎのまい": "swordsdance",
        "ムーンフォース": "moonblast", "じゃれつく": "playrough",
    }
    columns = ["record", "name"] + [f"move{i}" for i in range(1, 5)] + [
        "showdown_species_id"] + [f"showdown_move{i}_id" for i in range(1, 5)]
    rows = []
    for member in list(ASSETS.own_team) + [
        {"name": "カイリュー", "move1": "じしん"},
        {"name": "サーフゴー", "move1": "ムーンフォース"},
        {"name": "ドドゲザン", "move1": "じゃれつく"},
    ]:
        row = {name: "" for name in columns}
        row.update({"record": "MON", "name": member["name"],
                    "showdown_species_id": species_ids[member["name"]]})
        for i in range(1, 5):
            move = member.get(f"move{i}", "")
            row[f"move{i}"] = move
            row[f"showdown_move{i}_id"] = move_ids.get(move, "")
        rows.append(row)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    contents = {name: (output.getvalue() if i == 3 else f"synthetic-master-{i}")
                for i, name in enumerate(REQUIRED_MASTER_FILES)}
    sha = {name: hashlib.sha256(body.encode("utf-8")).hexdigest()
           for name, body in contents.items()}
    return FrozenMatchAssets(
        contents=MappingProxyType(contents),
        sha256=MappingProxyType(sha),
        matrix_index=MappingProxyType({}),
        lookup_index=MappingProxyType({}),
        own_team=_parse_team(contents[REQUIRED_MASTER_FILES[3]]),
        frozen_at="synthetic-not-game",
        move_class_index=MappingProxyType(ASSETS.move_class_index),
    )


VERIFIED_ASSETS = _fake_preloaded_assets()


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
        "showdown_party_order": list(MEMBERS),
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
        visual_ui, active, VERIFIED_ASSETS.sha256[REQUIRED_MASTER_FILES[3]],
        tuple((name, VERIFIED_ASSETS.sha256[name]) for name in REQUIRED_MASTER_FILES),
        "M-C", "ccfaa20f32af363569cd21a9d501b2bc1ded26b1",
        action_digest(verified), "req-1", time.monotonic()+60)
    return FrozenBattleState.freeze(
        window, state, obs, verified_action_ids=verified,
        constraints_confirmed=constraints)


def successful_result(s: FrozenBattleState) -> SimulationResult:
    return SimulationResult.from_worker(
        s.window.request_id, s.window.identity(), s.snapshot_sha256, "",
        CapabilityStatus.CONFIRMED, {"test": "synthetic-contract-only"},
        branch_request_id="test-only-branch-nonce",
        branch_input_sha256="a"*64,
        candidate_action_id=move_id("じしん"))


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

    def test_changed_deadline_invalidates_same_window_id(self):
        s = frozen()
        candidate = next(c for c in generate_candidates(s, ASSETS) if c.move == "じしん")
        result = successful_result(s)
        for delta in (-10, 10):
            current = replace(s, window=replace(s.window,
                      deadline_monotonic=s.window.deadline_monotonic+delta))
            r = accept_result(s, current, result, candidate, ASSETS,
                              now=s.window.deadline_monotonic-20)
            self.assertEqual(r.status, "NO_RECOMMENDATION")
            self.assertEqual(r.reason, "STALE_WINDOW_OR_REVISION")

    def test_snapshot_bound_to_hypothesis_contents_and_constraint_proof(self):
        s = frozen()
        base = Hypothesis.from_assumptions("H1", {"item": "Choice Scarf"},
                                           evidence_refs=("frame-1",), likelihood=0.4)
        variant = Hypothesis.from_assumptions("H1", {"item": "Choice Band"},
                                              evidence_refs=("frame-1",), likelihood=0.4)
        changed_evidence = Hypothesis.from_assumptions("H1", {"item": "Choice Scarf"},
                                                       evidence_refs=("frame-2",),
                                                       likelihood=0.4)
        changed_likelihood = Hypothesis.from_assumptions("H1", {"item": "Choice Scarf"},
                                                         evidence_refs=("frame-1",),
                                                         likelihood=0.5)
        original = replace(s, hypotheses=(base,))
        candidate = next(c for c in generate_candidates(s, ASSETS) if c.move == "じしん")
        resp = SimulationResult.from_worker(
            s.window.request_id, s.window.identity(), original.snapshot_sha256,
            "H1", CapabilityStatus.CONFIRMED, {},
            branch_request_id="bound-test-branch", branch_input_sha256="c"*64,
            candidate_action_id=candidate.action_id)
        for current in (
            replace(original, hypotheses=(variant,)),
            replace(original, hypotheses=(changed_evidence,)),
            replace(original, hypotheses=(changed_likelihood,)),
            replace(original, constraints_confirmed=False),
            replace(original, verified_action_ids=()),
        ):
            self.assertNotEqual(original.snapshot_sha256, current.snapshot_sha256)
            r=accept_result(original,current,resp,candidate,ASSETS)
            self.assertEqual(r.status,"NO_RECOMMENDATION")

    def test_confirmed_result_must_be_bound_to_matching_candidate_branch(self):
        s = frozen()
        candidate = next(c for c in generate_candidates(s, ASSETS) if c.move == "じしん")
        result = successful_result(s)
        for wrong in (
            replace(result, branch_request_id=""),
            replace(result, branch_input_sha256=""),
            replace(result, candidate_action_id="another-action"),
        ):
            outcome = accept_result(s,s,wrong,candidate,ASSETS)
            self.assertEqual(outcome.status,"NO_RECOMMENDATION")
            self.assertEqual(outcome.reason,"UNBOUND_BRANCH_OR_ACTION")

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
        # Windows Python's redirected stdin may otherwise use the ANSI code
        # page. The Node worker protocol is UTF-8, including Japanese names in
        # DecisionWindow.identity(); a mojibake echo is correctly discarded
        # by the adapter and can otherwise wait until the full turn deadline.
        script.write_text(
            "import sys\n"
            "sys.stdin.reconfigure(encoding='utf-8', errors='strict')\n"
            "sys.stdout.reconfigure(encoding='utf-8', errors='strict')\n"
            + code, encoding="utf-8")
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
            "  wrong_identity=dict(r); wrong_identity['identity']=dict(r['identity'])\n"
            "  wrong_identity['identity']['active_pokemon']='ミミッキュ'\n"
            "  wrong_identity.update(capability_status='CONFIRMED',ok=True)\n"
            "  print(json.dumps(wrong_identity),flush=True)\n"
            " if r['op']=='SIMULATE':\n"
            "  r['branch_request_id']=r['branch_request_id']\n"
            "  r['hypothesis_id']=r['payload']['hypothesis_id']\n"
            "  r['branch_input_sha256']=r['payload']['branch_input_sha256']\n"
            "  r['candidate_action_id']=r['payload']['candidate_action_id']\n"
            " r.update(capability_status='PARTIAL',ok=True,payload={})\n"
            " print(json.dumps(r),flush=True)\n"
        )
        tmp, w = self.make_fake_worker(script)
        try:
            w.start(timeout_seconds=1)
            s = frozen()
            result = w.simulate(s, {"synthetic": True},
                                "move 1", "move 1",
                                candidate_action_id=move_id("じしん"), assets=VERIFIED_ASSETS)
            self.assertEqual(result.capability_status, CapabilityStatus.PARTIAL)
        finally:
            w.close()
            tmp.cleanup()

    def test_candidate_id_must_match_actual_move_slot_and_verified_assets(self):
        s=frozen()
        tmp,w=self.make_fake_worker("import sys\nfor line in sys.stdin: pass\n")
        try:
            for choice in ("move 2", "move 3", "switch 2", "move 1 mega"):
                with self.assertRaises(WorkerUnavailable):
                    w.simulate(s, {"synthetic":True},choice,"move 1",
                               candidate_action_id=move_id("じしん"),assets=VERIFIED_ASSETS)
            with self.assertRaises(WorkerUnavailable):
                w.simulate(s, {"synthetic":True},"move 1","move 1",
                           candidate_action_id=move_id("じしん"))
            with self.assertRaises(WorkerUnavailable):
                w.simulate(s, {"synthetic":True},"move 1","move 1",
                           candidate_action_id=move_id("じしん"),
                           assets=FakeAssetsWrongMove())
        finally:
            w.close()
            tmp.cleanup()

    def test_switch_target_maps_only_from_exact_frozen_party_order(self):
        s=frozen(window_kind=WindowKind.FAINT_REPLACEMENT,visual_ui="PARTY_MENU",
                 verified=(switch_id("アシレーヌ"),),active="",awaiting=True)
        candidate=next(x for x in generate_candidates(s,ASSETS)
                       if x.switch_target=="アシレーヌ")
        from champions_assist.local_strategy.legal_actions import showdown_choice_for_candidate
        state=s.state
        state.pop("showdown_party_order")
        missing=replace(s,observed_state_json=json.dumps(state))
        with self.assertRaises(ValueError):
            showdown_choice_for_candidate(candidate,missing,ASSETS)
        state["showdown_party_order"]=["ガブリアス","アシレーヌ","ミミッキュ"]
        s2=replace(s,observed_state_json=json.dumps(state))
        self.assertEqual(showdown_choice_for_candidate(candidate,s2,ASSETS),"switch 2")
        state["showdown_party_order"]=["ミミッキュ","アシレーヌ","unknown"]
        s3=replace(s,observed_state_json=json.dumps(state))
        with self.assertRaises(ValueError):
            showdown_choice_for_candidate(candidate,s3,ASSETS)

    def test_preload_sha_and_registered_roster_must_match_current_window(self):
        s = frozen()
        tmp,w = self.make_fake_worker("import sys\nfor line in sys.stdin: pass\n")
        try:
            bad_member = dict(VERIFIED_ASSETS.own_team[0])
            bad_member["move1"],bad_member["move2"] = (
                bad_member["move2"],bad_member["move1"])
            changed_index = replace(
                VERIFIED_ASSETS,
                own_team=(bad_member,*VERIFIED_ASSETS.own_team[1:]))
            with self.assertRaisesRegex(WorkerUnavailable,"team rows differ"):
                w.simulate(s,{"synthetic":True},"move 2","move 1",
                           candidate_action_id=move_id("じしん"),
                           assets=changed_index)
            # A completely self-consistent *different* preloaded CSV cannot
            # borrow the prior match's SHA merely by matching its move ID.
            records=list(csv.DictReader(io.StringIO(
                VERIFIED_ASSETS.contents[REQUIRED_MASTER_FILES[3]])))
            records[0]["move1"],records[0]["move2"]=(
                records[0]["move2"],records[0]["move1"])
            records[0]["showdown_move1_id"],records[0]["showdown_move2_id"]=(
                records[0]["showdown_move2_id"],records[0]["showdown_move1_id"])
            output=io.StringIO()
            writer=csv.DictWriter(output,fieldnames=records[0].keys())
            writer.writeheader();writer.writerows(records)
            contents=dict(VERIFIED_ASSETS.contents)
            contents[REQUIRED_MASTER_FILES[3]]=output.getvalue()
            shas=dict(VERIFIED_ASSETS.sha256)
            shas[REQUIRED_MASTER_FILES[3]]=hashlib.sha256(
                output.getvalue().encode("utf-8")).hexdigest()
            new_assets=replace(VERIFIED_ASSETS,
                               contents=MappingProxyType(contents),
                               sha256=MappingProxyType(shas),
                               own_team=_parse_team(output.getvalue()))
            with self.assertRaisesRegex(WorkerUnavailable,"SHA differs"):
                w.simulate(s,{"synthetic":True},"move 2","move 1",
                           candidate_action_id=move_id("じしん"),
                           assets=new_assets)
            # Updated bytes with an old SHA also fail even when the
            # selected members in the in-memory index look identical.
            stale=replace(VERIFIED_ASSETS,
                          contents=MappingProxyType(contents))
            with self.assertRaisesRegex(WorkerUnavailable,"bytes do not match"):
                w.simulate(s,{"synthetic":True},"move 1","move 1",
                           candidate_action_id=move_id("じしん"),assets=stale)
            for key in REQUIRED_MASTER_FILES:
                stale_shas=dict(VERIFIED_ASSETS.sha256)
                stale_shas[key]="0"*64
                with self.assertRaisesRegex(WorkerUnavailable,"SHA differs"):
                    w.simulate(s,{"synthetic":True},"move 1","move 1",
                               candidate_action_id=move_id("じしん"),
                               assets=replace(VERIFIED_ASSETS,
                                              sha256=MappingProxyType(stale_shas)))
        finally:
            w.close()
            tmp.cleanup()

    def test_showdown_species_or_move_mapping_missing_is_not_guessed(self):
        s=frozen()
        tmp,w=self.make_fake_worker("import sys\nfor line in sys.stdin: pass\n")
        try:
            rows=list(csv.DictReader(io.StringIO(
                VERIFIED_ASSETS.contents[REQUIRED_MASTER_FILES[3]])))
            for field in ("showdown_species_id","showdown_move1_id"):
                mod=[dict(row) for row in rows]
                mod[0][field]=""
                output=io.StringIO()
                writer=csv.DictWriter(output,fieldnames=rows[0].keys())
                writer.writeheader();writer.writerows(mod)
                content=dict(VERIFIED_ASSETS.contents)
                content[REQUIRED_MASTER_FILES[3]]=output.getvalue()
                shas=dict(VERIFIED_ASSETS.sha256)
                shas[REQUIRED_MASTER_FILES[3]]=hashlib.sha256(
                    output.getvalue().encode("utf-8")).hexdigest()
                asset=replace(VERIFIED_ASSETS,
                              contents=MappingProxyType(content),
                              sha256=MappingProxyType(shas),
                              own_team=_parse_team(output.getvalue()))
                bound=replace(s,window=replace(s.window,
                    team_sha256=shas[REQUIRED_MASTER_FILES[3]],
                    four_master_sha256=tuple((name,shas[name])
                                            for name in REQUIRED_MASTER_FILES)))
                with self.assertRaisesRegex(WorkerUnavailable,"ID mapping missing"):
                    w.simulate(bound,{"synthetic":True},"move 1","move 1",
                               candidate_action_id=move_id("じしん"),assets=asset)
        finally:
            w.close()
            tmp.cleanup()

    def test_stalled_stdin_write_cannot_block_gui_thread_past_deadline(self):
        # HELLO is answered, then child stays alive without reading stdin.
        code = (
            "import sys,json,time\n"
            "line=sys.stdin.readline()\n"
            "r=json.loads(line)\n"
            "r.update(capability_status='PARTIAL',ok=True,payload={})\n"
            "print(json.dumps(r),flush=True)\n"
            "time.sleep(10)\n"
        )
        tmp,w=self.make_fake_worker(code)
        try:
            w.start(timeout_seconds=1.5)
            s=frozen()
            s=replace(s,window=replace(s.window,
                      deadline_monotonic=time.monotonic()+0.45))
            before=time.monotonic()
            with self.assertRaisesRegex(WorkerUnavailable,
                                        "stdin write timed out|worker timed out"):
                w.simulate(s,{"synthetic": "Z"*850_000},"move 1","move 1",
                           candidate_action_id=move_id("じしん"),assets=VERIFIED_ASSETS)
            self.assertLess(time.monotonic()-before, 2.0)
            self.assertTrue(w._failed)
            self.assertIsNotNone(w._proc)
            w._proc.wait(timeout=2)
        finally:
            w.close()
            tmp.cleanup()

    def test_branch_replies_out_of_order_same_window_are_never_adopted(self):
        # Same window/request/snapshot, but an old response with the wrong
        # branch nonce, hypothesis or payload hash must not substitute.
        script = (
            "import json,sys\n"
            "previous=None\n"
            "for line in sys.stdin:\n"
            " r=json.loads(line)\n"
            " if r['op']=='SIMULATE':\n"
            "  r['hypothesis_id']=r['payload']['hypothesis_id']\n"
            "  r['branch_input_sha256']=r['payload']['branch_input_sha256']\n"
            "  r['candidate_action_id']=r['payload']['candidate_action_id']\n"
            "  if previous is not None:\n"
            "   print(json.dumps(previous),flush=True)\n"
            "  previous=dict(r)\n"
            " r.update(capability_status='PARTIAL',ok=True,payload={})\n"
            " print(json.dumps(r),flush=True)\n"
        )
        tmp, worker = self.make_fake_worker(script)
        try:
            worker.start(timeout_seconds=2)
            s = frozen()
            one = worker.simulate(s, {"synthetic": True}, "move 1", "move 1",
                                  candidate_action_id=move_id("じしん"), assets=VERIFIED_ASSETS)
            two = worker.simulate(s, {"synthetic": True}, "move 1", "move 2",
                                  candidate_action_id=move_id("じしん"), assets=VERIFIED_ASSETS)
            self.assertEqual(one.capability_status, CapabilityStatus.PARTIAL)
            self.assertEqual(two.capability_status, CapabilityStatus.PARTIAL)
            self.assertNotEqual(one.branch_request_id, two.branch_request_id)
            self.assertNotEqual(one.branch_input_sha256, two.branch_input_sha256)
            self.assertEqual(one.candidate_action_id, move_id("じしん"))
            self.assertEqual(two.candidate_action_id, move_id("じしん"))
        finally:
            worker.close()
            tmp.cleanup()

    def test_wrong_branch_echo_never_pretends_success(self):
        # A malicious/buggy worker echo with every general identity correct
        # but an old or forged branch hash must time out, not be relabeled.
        script = (
            "import sys,json\n"
            "for line in sys.stdin:\n"
            " r=json.loads(line)\n"
            " if r['op']=='SIMULATE':\n"
            "  r['hypothesis_id']='wrong-hypothesis'\n"
            "  r['branch_input_sha256']='0'*64\n"
            "  r['candidate_action_id']=r['payload']['candidate_action_id']\n"
            " r.update(capability_status='PARTIAL',ok=True,payload={})\n"
            " print(json.dumps(r),flush=True)\n"
        )
        tmp, worker = self.make_fake_worker(script)
        try:
            worker.start(timeout_seconds=2)
            s=frozen()
            # Test has a tight explicit deadline rather than 60s turn limit.
            s=replace(s,window=replace(s.window,
                  deadline_monotonic=time.monotonic()+0.3))
            with self.assertRaises(WorkerUnavailable):
                worker.simulate(s, {"synthetic":True}, "move 1", "move 1",
                                candidate_action_id=move_id("じしん"), assets=VERIFIED_ASSETS)
        finally:
            worker.close()
            tmp.cleanup()

    def test_unregistered_hypothesis_or_action_is_rejected_before_ipc(self):
        tmp, worker = self.make_fake_worker("import time\ntime.sleep(2)\n")
        try:
            with self.assertRaises(WorkerUnavailable):
                worker.simulate(frozen(), {"synthetic": True},
                                "move 1", "move 1", candidate_action_id="fake")
            with self.assertRaises(WorkerUnavailable):
                worker.simulate(frozen(), {"synthetic": True},
                                "move 1", "move 1", hypothesis_id="unbound",
                                candidate_action_id=move_id("じしん"), assets=VERIFIED_ASSETS)
        finally:
            worker.close()
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
                w.simulate(frozen(), {"synthetic": True}, "move 1", "move 1",
                           candidate_action_id=move_id("じしん"), assets=VERIFIED_ASSETS)
            with self.assertRaises(WorkerUnavailable):
                w.start()
        finally:
            w.close()
            tmp.cleanup()


class StagingTests(unittest.TestCase):
    @staticmethod
    def make_stage_inputs(root):
        showdown = root / "showdown"
        (showdown / "dist/sim").mkdir(parents=True)
        (showdown / "node_modules/dependency").mkdir(parents=True)
        (showdown / "dist/sim/index.js").write_text("module.exports = {};")
        (showdown / "dist/sim/other.js").write_text("// trusted")
        (showdown / "node_modules/dependency/index.js").write_text("// trusted")
        (showdown / "LICENSE").write_text("test-only-upstream-license")
        (showdown / "package.json").write_text("{}")
        (showdown / "package-lock.json").write_text("{}")
        node = root / "node.exe"
        node.write_bytes(b"synthetic-node-not-real-executable")
        node_license = root / "NODE_LICENSE"
        node_license.write_text("node-license-placeholder")
        notices = root / "notices"
        notices.write_text("third-party-notices-placeholder")
        worker = root / "worker.cjs"
        worker.write_text("// synthetic worker")
        independent = root / "reviewed-manifest.json"
        independent.write_text(json.dumps({
            "engine_sha": PINNED_SHOWDOWN_SHA,
            "engine_file_sha256": _engine_files(showdown)
        }), encoding="utf-8")
        return dict(showdown_root=showdown, node_exe=node,
                    node_license=node_license, third_party_notices=notices,
                    worker_script=worker, verified_build_manifest=independent,
                    output=root / "out")

    @staticmethod
    def clean_mock(command, *args, **kwargs):
        return PINNED_SHOWDOWN_SHA + "\n" if "rev-parse" in command else ""

    def test_pinned_runtime_stage_requires_sha_and_licenses(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = self.make_stage_inputs(Path(tmp))
            with patch("scripts.stage_issue140_runtime.subprocess.check_output",
                       return_value="wrong-SHA\n"):
                with self.assertRaises(ValueError):
                    stage(**opts)
            self.assertFalse(opts["output"].exists())
            with patch("scripts.stage_issue140_runtime.subprocess.check_output",
                       side_effect=self.clean_mock):
                manifest = stage(**opts)
            self.assertEqual(manifest["engine_sha"], PINNED_SHOWDOWN_SHA)
            self.assertFalse(manifest["production_enabled"])
            self.assertIn("node_modules/dependency/index.js",
                          manifest["engine_file_sha256"])
            self.assertEqual((opts["output"] / "showdown/.champions-source-sha")
                             .read_text().strip(), PINNED_SHOWDOWN_SHA)
            with self.assertRaises(FileExistsError):
                stage(**opts)

    def test_dirty_checkout_and_unverified_dependency_files_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = self.make_stage_inputs(Path(tmp))
            def dirty(command, *args, **kwargs):
                return (PINNED_SHOWDOWN_SHA + "\n" if "rev-parse" in command
                        else " M src/sim/battle.ts\n")
            with patch("scripts.stage_issue140_runtime.subprocess.check_output",
                       side_effect=dirty):
                with self.assertRaisesRegex(ValueError, "uncommitted"):
                    stage(**opts)
            self.assertFalse(opts["output"].exists())
            dependency = opts["showdown_root"] / "node_modules/dependency/index.js"
            dependency.write_text("// modified post-review")
            with patch("scripts.stage_issue140_runtime.subprocess.check_output",
                       side_effect=self.clean_mock):
                with self.assertRaisesRegex(ValueError, "verified inventory"):
                    stage(**opts)
            self.assertFalse(opts["output"].exists())
            # Also catch modified dist output even when tracked git status
            # remains clean, as dist may be ignored by Git.
            dependency.write_text("// trusted")
            dist = opts["showdown_root"] / "dist/sim/other.js"
            dist.write_text("// modified ignored dist output")
            with patch("scripts.stage_issue140_runtime.subprocess.check_output",
                       side_effect=self.clean_mock):
                with self.assertRaisesRegex(ValueError, "verified inventory"):
                    stage(**opts)
            self.assertFalse(opts["output"].exists())

    def test_stage_rejects_empty_dependency_notices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "engine").mkdir()
            (root / "node.exe").touch()
            (root / "LICENSE").write_text("license")
            (root / "NOTICES").touch()
            (root / "worker.cjs").write_text("// worker")
            (root / "reviewed.json").write_text("{}")
            with patch("scripts.stage_issue140_runtime.subprocess.check_output",
                       side_effect=self.clean_mock):
                with self.assertRaises((FileNotFoundError,ValueError)):
                    stage(showdown_root=root / "engine",
                          node_exe=root / "node.exe",
                          node_license=root / "LICENSE",
                          third_party_notices=root / "NOTICES",
                          worker_script=root / "worker.cjs",
                          verified_build_manifest=root / "reviewed.json",
                          output=root / "out")


if __name__ == "__main__":
    unittest.main()
