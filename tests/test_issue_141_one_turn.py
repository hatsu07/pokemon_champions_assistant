"""Phase 2 partial Showdown turn ranking: only synthetic labelled fixtures."""
from __future__ import annotations

from dataclasses import replace
import json
import unittest

from champions_assist.local_strategy.models import (
    CapabilityStatus, SimulationResult, action_digest,
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
                theirs=(60, 100, 100), nonce="", hypothesis="", corrupt=False):
    own = [{"species": species, "hp": hp, "fainted": hp == 0}
           for species, hp in zip(OWN_SPECIES, ours)]
    opp = [{"species": species, "hp": hp, "fainted": hp == 0}
           for species, hp in zip(OTHER_SPECIES, theirs)]
    result = SimulationResult.from_worker(
        snapshot.window.request_id,
        snapshot.window.identity(), snapshot.snapshot_sha256, hypothesis,
        CapabilityStatus.PARTIAL,
        {"next": {"side_hp": [own, opp]}, "synthetic_only": True},
        branch_request_id=nonce or f"branch-{action[:8]}-{opponent_choice}",
        branch_input_sha256="a"*64,
        candidate_action_id=action if not corrupt else "unbound",
    )
    return BranchObservation(action, hypothesis, opponent_choice, result)


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
            "STALE_OR_UNBOUND_BRANCH")
        corrupt = make_branch(s, CLAW, "attack", corrupt=True)
        self.assertEqual(self.score(s, (a,corrupt)).reason,
                         "STALE_OR_UNBOUND_BRANCH")
        hp_payload=json.loads(b.result.payload_json)
        hp_payload["next"]["side_hp"][0][0]["hp"] = -20
        changed=replace(b,result=replace(b.result,
                        payload_json=json.dumps(hp_payload)))
        self.assertEqual(self.score(s,(a,changed)).reason,
                         "MALFORMED_OR_CONTRADICTORY_HP")

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
