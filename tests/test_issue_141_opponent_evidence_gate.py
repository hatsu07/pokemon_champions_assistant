"""Synthetic observed-evidence vs explicit joint hypothesis contract tests."""
from __future__ import annotations

from dataclasses import replace
import json
import unittest

from champions_assist.local_strategy.models import (
    Hypothesis, canonical_json,
)
from champions_assist.local_strategy.opponent_evidence_gate import (
    check_observed_opponent_hypotheses,
)
from champions_assist.local_strategy.shadow_runner import run_shadow_turn
from champions_assist.local_strategy.one_turn import compare_shadow_turn
from tests.test_issue_141_one_turn import current, make_branch, EARTH, CLAW
from tests.test_issue_141_shadow_runner import FakePhase1Worker, scenario
from tests.test_issue_140_foundation import VERIFIED_ASSETS


NAME = "カイリュー"
GOLD = "サーフゴー"


def observed(*, active=NAME, move="しんそく", item="ラムのみ",
             ability="マルチスケイル", form="カイリュー", other=False,
             malformed=False, current_type="", status=""):
    snapshot = current()
    state = snapshot.state
    state["opponent_active"] = active
    evidence = state.setdefault("battle_evidence", {})
    evidence["moves_by_pokemon"] = {
        "own": {}, "opponent": {NAME: [move]}}
    evidence["item_by_pokemon"] = {
        "own": {}, "opponent": {NAME: item}}
    evidence["ability_by_pokemon"] = {
        "own": {}, "opponent": {NAME: ability}}
    evidence["form_by_pokemon"] = {
        "own": {}, "opponent": {NAME: form}}
    if current_type:
        evidence["current_type"] = {
            "own": {}, "opponent": {NAME: current_type}}
    if status:
        evidence["status_by_pokemon"] = {
            "own": {}, "opponent": {NAME: status}}
    if other:
        evidence["moves_by_pokemon"]["opponent"][GOLD] = ["ゴールドラッシュ"]
    if malformed:
        evidence["moves_by_pokemon"]["opponent"][NAME] = {"not": "moves"}
    return replace(snapshot, observed_state_json=canonical_json(state))


def assumptions(*, item="ラムのみ", ability="マルチスケイル",
                move="しんそく", form="カイリュー", other=False,
                include_item=True, current_type="", status=""):
    member = {"moves": [move, "じしん"], "ability": ability, "form": form}
    if include_item:
        member["item"] = item
    if current_type:
        member["current_type"] = current_type
    if status:
        member["status"] = status
    sets = {NAME: member}
    if other:
        sets[GOLD] = {"moves": ["ゴールドラッシュ"]}
    return {"opponent_sets": sets, "metadata": {
        "reference": "synthetic fixture only", "regulation": "TEST"}}


def with_hypotheses(snapshot, *sets):
    return replace(snapshot, hypotheses=tuple(
        Hypothesis.from_assumptions(f"H{i+1}", row)
        for i, row in enumerate(sets)
    ))


class Phase2OpponentEvidenceTests(unittest.TestCase):
    def test_no_evidence_does_not_claim_hidden_set_verified(self):
        result = check_observed_opponent_hypotheses(current())
        self.assertEqual(result.status, "CLEAR")
        self.assertEqual(result.reason, "NO_REVEALED_OPPONENT_DETAILS")

    def test_all_joint_sets_must_preserve_revealed_values(self):
        snapshot = with_hypotheses(
            observed(other=True),
            assumptions(other=True),
            assumptions(other=True, move="しんそく"),
        )
        result = check_observed_opponent_hypotheses(snapshot)
        self.assertEqual(result.status, "CLEAR")
        self.assertEqual(result.checked_hypotheses, ("H1", "H2"))
        self.assertEqual(set(result.checked_opponents), {NAME, GOLD})

    def test_any_joint_set_contradicting_known_fact_blocks_everything(self):
        for altered, field in (
            (assumptions(item="こだわりハチマキ"), "item"),
            (assumptions(move="げきりん"), "moves"),
            (assumptions(ability="せいしんりょく"), "ability"),
            (assumptions(form="メガカイリュー"), "form"),
        ):
            with self.subTest(field=field):
                snapshot = with_hypotheses(
                    observed(), assumptions(), altered)
                worker = FakePhase1Worker()
                result = run_shadow_turn(
                    snapshot, VERIFIED_ASSETS, worker,
                    (scenario("H1"), scenario("H2")),
                    preserve="ガブリアス", stopper="ミミッキュ")
                self.assertEqual(result.status, "NO_RANKING")
                self.assertEqual(
                    result.reason, "JOINT_ASSUMPTIONS_CONTRADICT_OBSERVATION")
                self.assertEqual(result.best_action_id, "")
                self.assertEqual(worker.calls, [])
                gate = check_observed_opponent_hypotheses(snapshot)
                self.assertEqual(gate.offending_hypothesis_id, "H2")
                self.assertEqual(gate.offending_field, field)

    def test_unknown_missing_observed_member_and_missing_field_block(self):
        for h in (
            {"item": "ラムのみ"},  # Old hypothesis schema not a joint set
            {"opponent_sets": {}},
            assumptions(include_item=False),
            assumptions(other=False),
        ):
            snapshot = with_hypotheses(observed(other=True), h)
            result = check_observed_opponent_hypotheses(snapshot)
            self.assertEqual(result.status, "NO_RANKING")
            self.assertEqual(result.reason, "JOINT_ASSUMPTIONS_UNBOUND")

    def test_no_hypotheses_with_observed_opponent_cannot_guess_set(self):
        snapshot = observed()
        result = check_observed_opponent_hypotheses(snapshot)
        self.assertEqual(result.status, "NO_RANKING")
        self.assertEqual(result.reason,
                         "OBSERVED_OPPONENT_WITHOUT_JOINT_HYPOTHESES")

    def test_malformed_observation_never_infers_from_non_list_moves(self):
        snapshot = with_hypotheses(observed(malformed=True), assumptions())
        result = check_observed_opponent_hypotheses(snapshot)
        self.assertEqual(result.status, "NO_RANKING")
        self.assertEqual(result.reason, "INVALID_OBSERVED_OPPONENT_EVIDENCE")

    def test_standalone_comparator_must_not_bypass_evidence(self):
        snapshot = with_hypotheses(observed(), assumptions(item="別の道具"))
        branches = (
            make_branch(snapshot, EARTH, "attack", hypothesis="H1"),
            make_branch(snapshot, CLAW, "attack", hypothesis="H1"),
        )
        result = compare_shadow_turn(
            snapshot, VERIFIED_ASSETS, branches,
            preserve="ガブリアス", stopper="ミミッキュ",
            now=snapshot.window.deadline_monotonic-5)
        self.assertEqual(result.status, "NO_RANKING")
        self.assertEqual(result.reason,
                         "JOINT_ASSUMPTIONS_CONTRADICT_OBSERVATION")

    def test_revealed_current_type_and_status_are_not_silently_ignored(self):
        s = with_hypotheses(
            observed(current_type="ひこう", status="まひ"),
            assumptions(current_type="ひこう", status="まひ"),
            assumptions(current_type="ドラゴン", status="まひ"),
        )
        self.assertEqual(
            check_observed_opponent_hypotheses(s).offending_field,
            "current_type")
        self.assertEqual(
            check_observed_opponent_hypotheses(s).reason,
            "JOINT_ASSUMPTIONS_CONTRADICT_OBSERVATION")
        s = with_hypotheses(
            observed(current_type="ひこう", status="まひ"),
            assumptions(current_type="ひこう", status="どく"),
        )
        self.assertEqual(
            check_observed_opponent_hypotheses(s).offending_field,
            "status")
        good = with_hypotheses(
            observed(current_type="ひこう", status="まひ"),
            assumptions(current_type="ひこう", status="まひ"),
        )
        self.assertEqual(
            check_observed_opponent_hypotheses(good).status, "CLEAR")

    def test_unseen_move_is_not_inferred_from_marginal_usage_stats(self):
        snapshot = with_hypotheses(
            observed(move="しんそく"), assumptions(move="りゅうのまい"))
        result = check_observed_opponent_hypotheses(snapshot)
        self.assertEqual(result.status, "NO_RANKING")
        self.assertEqual(result.offending_field, "moves")


if __name__ == "__main__":
    unittest.main()
