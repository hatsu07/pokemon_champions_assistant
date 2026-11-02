"""Phase 2 partial Showdown turn ranking: only synthetic labelled fixtures."""
from __future__ import annotations

from dataclasses import replace
import json
import unittest

from champions_assist.local_strategy.models import (
    CapabilityStatus, Hypothesis, SimulationResult, fingerprint, PROTOCOL_VERSION,
)
from champions_assist.local_strategy.one_turn import (
    BranchObservation, compare_shadow_turn,
)
from tests.test_issue_140_foundation import (
    VERIFIED_ASSETS, frozen, move_id,
)

EARTH = move_id("じしん")
CLAW = move_id("ドラゴンクロー")
OWN_SPECIES = ("garchomp", "primarina", "mimikyu")
OTHER_SPECIES = ("dragonite", "gholdengo", "kingambit")


def current():
    s = frozen(verified=(EARTH, CLAW))
    state = s.state
    state["battle_evidence"]["pp_by_pokemon"]["own"]["ガブリアス"]["ドラゴンクロー"] = 5
    return replace(s, observed_state_json=json.dumps(state))


def make_branch(snapshot, action, opponent_choice, *, ours=(100, 100, 100),
                theirs=(60, 100, 100), nonce="", hypothesis="", corrupt=False,
                actual_p2="", initial_battle=None, branch_sha="",
                actual_p1="", label_override=""):
    own = [{"species": species, "hp": hp, "fainted": hp == 0}
           for species, hp in zip(OWN_SPECIES, ours)]
    opp = [{"species": species, "hp": hp, "fainted": hp == 0}
           for species, hp in zip(OTHER_SPECIES, theirs)]
    # Original submitted request, not synthesized by the comparison function.
    # This fixture exercises the Phase 1 adapter's new provenance contract;
    # the real worker stores this only after checking its echoed branch ID.
    roster = [
        {"name": "ガブリアス", "species_id": "garchomp",
         "moves": ["earthquake", "dragonclaw", "rockslide", "swordsdance"]},
        {"name": "アシレーヌ", "species_id": "primarina", "moves": ["moonblast"]},
        {"name": "ミミッキュ", "species_id": "mimikyu", "moves": ["playrough"]},
    ]
    if initial_battle is None:
        initial_battle = {"state": "full-synthetic-only",
                          "rng": {"seed": [1, 2, 3, 4]},
                          "turn": 5}
    p2 = actual_p2 or {"attack": "move 1", "switch": "switch 2"}.get(
        opponent_choice, opponent_choice)
    action_choice = actual_p1 or ("move 1" if action == EARTH else "move 2")
    payload = {
        "source": "FULL_REPLAY_SYNTHETIC",
        "battle": initial_battle,
        "p1_choice": action_choice,
        "p2_choice": p2,
        "hypothesis_id": hypothesis,
        "candidate_action_id": action,
        "expected_p1_roster": roster,
        "expected_p1_active": snapshot.window.active_pokemon,
    }
    payload["branch_input_sha256"] = fingerprint(payload)
    if branch_sha:
        payload["branch_input_sha256"] = branch_sha
    branch_id = nonce or f"branch-{action[:8]}-{opponent_choice}"
    submitted = {
        "protocol": PROTOCOL_VERSION, "op": "SIMULATE",
        "request_id": snapshot.window.request_id,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "identity": snapshot.window.identity(),
        "branch_request_id": branch_id,
        "payload": payload,
    }
    result = SimulationResult.from_worker(
        snapshot.window.request_id,
        snapshot.window.identity(), snapshot.snapshot_sha256, hypothesis,
        CapabilityStatus.PARTIAL,
        {"next": {"side_hp": [own, opp]}, "synthetic_only": True},
        branch_request_id=branch_id,
        branch_input_sha256=payload["branch_input_sha256"],
        candidate_action_id=action if not corrupt else "unbound",
        submitted_request=submitted,
    )
    return BranchObservation(action, hypothesis, label_override or p2, result)


class Phase2TurnTests(unittest.TestCase):
    def score(self, snapshot, branches):
        return compare_shadow_turn(
            snapshot, VERIFIED_ASSETS, tuple(branches),
            preserve="ガブリアス", stopper="ミミッキュ",
            now=snapshot.window.deadline_monotonic - 10,
        )

    def test_dominates_without_assuming_clairvoyant_opponent(self):
        s = current()
        branches = []
        for p2 in ("attack", "switch"):
            # Simulated earthquake is strictly better than claw regardless
            # of which of the common opponent choices is taken.
            branches.append(make_branch(s, EARTH, p2, ours=(90,100,100),
                                        theirs=(20,100,100)))
            branches.append(make_branch(s, CLAW, p2, ours=(90,100,100),
                                        theirs=(50,100,100)))
        ranked = self.score(s, branches)
        self.assertEqual(ranked.status, "MODEL_PROVISIONAL")
        self.assertEqual(ranked.best_action_id, EARTH)
        self.assertEqual(ranked.evaluated_branch_count, 4)
        self.assertNotIn("%", ranked.reason)

    def test_non_dominant_tradeoff_requires_opponent_policy_not_fake_probability(self):
        s = current()
        rows = (
            make_branch(s, EARTH, "attack", theirs=(5,100,100)),
            make_branch(s, CLAW, "attack", theirs=(60,100,100)),
            make_branch(s, EARTH, "switch", theirs=(90,100,100)),
            make_branch(s, CLAW, "switch", theirs=(15,100,100)),
        )
        ranked = self.score(s, rows)
        self.assertEqual(ranked.status, "AMBIGUOUS_OPPONENT_POLICY")
        self.assertEqual(ranked.best_action_id, "")
        self.assertEqual(set(ranked.nondominated_action_ids), {EARTH, CLAW})

    def test_incomplete_scenario_set_never_prefers_favorable_subset(self):
        s = current()
        rows = (
            make_branch(s, EARTH, "attack", theirs=(1,100,100)),
            make_branch(s, CLAW, "attack", theirs=(60,100,100)),
            make_branch(s, CLAW, "switch", theirs=(60,100,100)),
        )
        self.assertEqual(self.score(s, rows).reason,
                         "UNEQUAL_OR_INCOMPLETE_SCENARIO_COVERAGE")

    def test_fainted_last_mon_is_loss_not_illusory_damage_win(self):
        s = current()
        # Own last remaining also faints: not a victory over a zero-HP foe.
        losing = make_branch(s, EARTH, "attack",
                             ours=(0,0,0), theirs=(0,0,0))
        winning = make_branch(s, CLAW, "attack",
                              ours=(1,0,0), theirs=(0,0,0))
        out = self.score(s, (losing, winning))
        self.assertEqual(out.best_action_id, CLAW)

    def test_preserve_stopper_and_three_member_hp_not_just_active(self):
        s = current()
        # Both actions cause equal damage, but one sacrifices the stopper.
        a = make_branch(s, EARTH, "attack", ours=(100,100,0))
        b = make_branch(s, CLAW, "attack", ours=(90,100,100))
        self.assertEqual(self.score(s, (a,b)).best_action_id, CLAW)

    def test_stale_identity_duplicate_branch_and_invalid_hp_fail_closed(self):
        s = current()
        a = make_branch(s, EARTH, "attack")
        b = make_branch(s, CLAW, "attack")
        self.assertEqual(self.score(s, (a, replace(b,result=replace(
            b.result, request_id="old")))).reason, "STALE_OR_UNBOUND_BRANCH")
        self.assertEqual(self.score(s, (a,a,b)).reason,
                         "INVALID_OR_DUPLICATE_SCENARIO")
        self.assertEqual(self.score(s, (a, replace(b,result=replace(
            b.result, branch_request_id=a.result.branch_request_id)))).reason,
            "MISSING_OR_UNBOUND_SUBMISSION")
        corrupt = make_branch(s, CLAW, "attack", corrupt=True)
        self.assertEqual(self.score(s, (a,corrupt)).reason,
                         "STALE_OR_UNBOUND_BRANCH")
        hp_payload=json.loads(b.result.payload_json)
        hp_payload["next"]["side_hp"][0][0]["hp"] = -20
        changed=replace(b,result=replace(b.result,
                        payload_json=json.dumps(hp_payload)))
        self.assertEqual(self.score(s,(a,changed)).reason,
                         "MALFORMED_OR_CONTRADICTORY_HP")

    def test_same_label_but_different_submitted_p2_choice_is_rejected(self):
        s = current()
        a = make_branch(s, EARTH, "attack", actual_p2="move 1",
                        theirs=(10, 100, 100))
        b = make_branch(s, CLAW, "attack", actual_p2="switch 2",
                        label_override="move 1",
                        theirs=(80, 100, 100))
        self.assertEqual(self.score(s, (a, b)).reason,
                         "OPPONENT_CHOICE_NOT_SUBMITTED")

    def test_switch_name_cannot_mask_actual_attack_command(self):
        s = current()
        a = make_branch(s, EARTH, "attack")
        b = make_branch(s, CLAW, "switch",
                        actual_p2="move 1", label_override="switch")
        self.assertEqual(self.score(s, (a, b)).reason,
                         "OPPONENT_CHOICE_NOT_SUBMITTED")

    def test_same_label_but_different_original_battle_or_rng_is_rejected(self):
        s = current()
        a = make_branch(s, EARTH, "attack")
        for altered in (
            {"state": "other-full-synthetic", "rng": {"seed": [1,2,3,4]}, "turn": 5},
            {"state": "full-synthetic-only", "rng": {"seed": [5,6,7,8]}, "turn": 5},
        ):
            with self.subTest(altered=altered):
                b = make_branch(s, CLAW, "attack", initial_battle=altered)
                self.assertEqual(self.score(s, (a, b)).reason,
                                 "SCENARIO_INPUT_MISMATCH")

    def test_false_64_character_digest_or_missing_submitted_request_rejected(self):
        s = current()
        a = make_branch(s, EARTH, "attack")
        for b in (
            make_branch(s, CLAW, "attack", branch_sha="0"*64),
            replace(make_branch(s, CLAW, "attack"), result=replace(
                make_branch(s, CLAW, "attack").result,
                submitted_request_json="")),
            replace(make_branch(s, CLAW, "attack"), result=replace(
                make_branch(s, CLAW, "attack").result,
                branch_input_sha256="b"*64)),
        ):
            with self.subTest(result=b.result):
                self.assertEqual(self.score(s, (a, b)).status, "NO_RANKING")

    def test_nonce_and_p1_choice_bound_to_actual_submission(self):
        s = current()
        a = make_branch(s, EARTH, "attack")
        b = make_branch(s, CLAW, "attack")
        # A different actor or a new nonce may not reuse an earlier worker
        # result, even when the hash is otherwise a valid digest.
        changed_nonce = replace(b, result=replace(
            b.result, branch_request_id="different-branch-id"))
        self.assertEqual(self.score(s, (a, changed_nonce)).reason,
                         "MISSING_OR_UNBOUND_SUBMISSION")
        incorrect_move = make_branch(s, CLAW, "attack", actual_p1="move 1")
        self.assertEqual(self.score(s, (a, incorrect_move)).reason,
                         "ACTION_SLOT_NOT_BOUND")

    def test_hypothesis_cannot_use_different_initial_rng_across_choices(self):
        s = current()
        a = make_branch(s, EARTH, "attack")
        b = make_branch(s, EARTH, "switch", initial_battle={
            "state": "full-synthetic-only", "rng": {"seed": [0,0,0,0]}, "turn": 5
        })
        c = make_branch(s, CLAW, "attack")
        d = make_branch(s, CLAW, "switch")
        self.assertEqual(self.score(s, (a, b, c, d)).reason,
                         "SCENARIO_INPUT_MISMATCH")

    def test_direct_comparator_rejects_duplicate_snapshot_hypothesis_ids(self):
        # Calling compare_shadow_turn directly must not bypass the runner's
        # duplicate-ID gate. Both synthetically distinct item states share H1.
        two = (
            Hypothesis.from_assumptions("H1", {"item": "A"}),
            Hypothesis.from_assumptions("H1", {"item": "B"}),
        )
        for hypotheses in (
            two,
            (replace(two[0], hypothesis_id=""),),
            (replace(two[0], hypothesis_id=" H1 "),),
        ):
            snapshot = replace(current(), hypotheses=hypotheses)
            branches = (
                make_branch(snapshot, EARTH, "move 1", hypothesis="H1"),
                make_branch(snapshot, CLAW, "move 1", hypothesis="H1"),
            )
            with self.subTest(ids=[h.hypothesis_id for h in hypotheses]):
                result = self.score(snapshot, branches)
                self.assertEqual(result.status, "NO_RANKING")
                self.assertEqual(result.reason,
                                 "DUPLICATE_OR_INVALID_HYPOTHESIS_ID")
                self.assertEqual(result.best_action_id, "")
                self.assertEqual(result.evaluated_branch_count, 0)

    def test_no_unknown_success_on_expiry_or_fake_assets(self):
        s = current()
        rows=(make_branch(s, EARTH, "attack"),
              make_branch(s, CLAW, "attack"))
        expired=compare_shadow_turn(
            s, VERIFIED_ASSETS, rows, preserve="ガブリアス",
            stopper="ミミッキュ", now=s.window.deadline_monotonic)
        self.assertEqual(expired.reason,"INPUT_DEADLINE_PASSED")
        nonmatched=compare_shadow_turn(
            s, {}, rows, preserve="ガブリアス", stopper="ミミッキュ",
            now=s.window.deadline_monotonic-1)
        self.assertEqual(nonmatched.status,"NO_RANKING")


if __name__ == "__main__":
    unittest.main()
