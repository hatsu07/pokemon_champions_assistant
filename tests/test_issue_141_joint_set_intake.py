"""Joint-set intake synthetic contracts. Never asserts real set prevalence."""
from __future__ import annotations

from dataclasses import replace
import unittest

from champions_assist.local_strategy.joint_set_intake import (
    JointCandidateIntake, SourcedJointSetCandidate,
    stage_sourced_joint_candidates,
)
from champions_assist.local_strategy.models import canonical_json
from tests.test_issue_141_one_turn import current
from tests.test_issue_141_opponent_evidence_gate import (
    NAME, GOLD, observed,
)

KING = "ドドゲザン"
TRIO = (NAME, GOLD, KING)
SOURCE = "CHAMPIONS_COOCCURRING_JOINT_RECORD"
RETRIEVED = "2026-11-02T14:00:00+09:00"


def members():
    return {
        NAME: {
            "moves": ["しんそく", "じしん"],
            "item": "ラムのみ", "ability": "マルチスケイル",
            "form": "カイリュー",
        },
        GOLD: {
            "moves": ["ゴールドラッシュ", "シャドーボール"],
            "item": "ふうせん", "ability": "おうごんのからだ",
            "form": "サーフゴー",
        },
        KING: {
            "moves": ["ドゲザン", "ふいうち"],
            "item": "くろいメガネ", "ability": "そうだいしょう",
            "form": "ドドゲザン",
        },
    }


def record(candidate_id="Joint-1", *, source=SOURCE, source_ref="synthetic://joint-set-1",
           retrieved_at=RETRIEVED, regulation="M-C", trio=TRIO, party=None):
    return SourcedJointSetCandidate(
        candidate_id=candidate_id, source_kind=source,
        source_ref=source_ref, retrieved_at=retrieved_at,
        regulation=regulation, selected_three=trio,
        opponent_sets_json=canonical_json({
            "opponent_sets": members() if party is None else party,
        }))


class SourcedJointSetIntakeTests(unittest.TestCase):
    def build(self, *records, snapshot=None):
        return stage_sourced_joint_candidates(
            current() if snapshot is None else snapshot,
            tuple(records))

    def test_complete_cooccurring_records_stage_without_inferred_probabilities(self):
        a = record()
        b = record("Joint-2", source_ref="synthetic://joint-set-2")
        out = self.build(a, b)
        self.assertEqual(out.status, "PROVISIONAL_JOINT_CANDIDATES")
        self.assertEqual(out.coverage, "NOT_PROVEN_EXHAUSTIVE")
        self.assertEqual(len(out.hypotheses), 2)
        self.assertEqual(tuple(h.hypothesis_id for h in out.hypotheses),
                         ("Joint-1", "Joint-2"))
        self.assertTrue(all(h.likelihood is None for h in out.hypotheses))
        self.assertIn("REGULATION:M-C", out.hypotheses[0].evidence_refs)
        self.assertIn("SOURCE_REF:synthetic://joint-set-1",
                      out.hypotheses[0].evidence_refs)
        self.assertIn("RETRIEVED_AT:"+RETRIEVED,
                      out.hypotheses[0].evidence_refs)
        self.assertEqual(current().hypotheses, ())  # No automatic promotion

    def test_observation_conflict_rejects_entire_set_not_just_one_candidate(self):
        bad = members()
        bad[NAME]["item"] = "こだわりハチマキ"
        snapshot = observed()
        result = self.build(record(), record("Joint-2", party=bad),
                            snapshot=snapshot)
        self.assertEqual(result.status, "NO_RANKING")
        self.assertEqual(result.reason,
                         "JOINT_ASSUMPTIONS_CONTRADICT_OBSERVATION")
        self.assertEqual(result.rejected_candidate_id, "Joint-2")
        self.assertEqual(result.hypotheses, ())

    def test_joint_bundles_not_cartesian_products_of_marginal_stats(self):
        for source in ("SINGLE_ITEM_USAGE_RATES", "MARGINAL_STATS",
                       "SCARLET_VIOLET_SETS", "OTHER_GAME"):
            with self.subTest(source=source):
                r=self.build(record(source=source))
                self.assertEqual(r.status, "NO_RANKING")
                self.assertEqual(r.reason, "JOINT_SOURCE_OR_REGULATION_UNVERIFIED")
                self.assertFalse(r.hypotheses)

    def test_regulation_timestamp_and_source_provenance_required(self):
        bad = (
            record(regulation="SV"),
            record(regulation=""),
            record(retrieved_at="2026-11-02"),  # lacks tz offset
            record(retrieved_at="date unknown"),
            record(source_ref=""),
        )
        for r in bad:
            with self.subTest(r=r):
                result=self.build(r)
                self.assertEqual(result.reason, "JOINT_SOURCE_OR_REGULATION_UNVERIFIED")
                self.assertEqual(result.hypotheses, ())

    def test_unregistered_or_partial_cooccurring_party_is_not_expanded(self):
        party = members()
        party.pop(KING)
        self.assertEqual(self.build(record(party=party)).reason,
                         "INVALID_JOINT_SET_MEMBERS")
        party = members()
        party[NAME].pop("ability")
        self.assertEqual(self.build(record(party=party)).reason,
                         "INCOMPLETE_OR_INFERRED_JOINT_MEMBER")
        party = members()
        party[NAME]["usage_rate"] = 0.60
        self.assertEqual(self.build(record(party=party)).reason,
                         "INCOMPLETE_OR_INFERRED_JOINT_MEMBER")

    def test_moves_are_explicit_not_guessed_or_duplicated(self):
        for invalid in ([], ["しんそく"]*2, ["しんそく"]*5, [""]):
            party=members()
            party[NAME]["moves"] = invalid
            with self.subTest(moves=invalid):
                self.assertEqual(self.build(record(party=party)).reason,
                                 "INVALID_EXPLICIT_JOINT_MOVES")

    def test_observed_preview_enforces_registered_three_membership(self):
        snapshot = current()
        state = snapshot.state
        state["opponent_six"] = [NAME, GOLD, "ガブリアス", "ミミッキュ",
                                 "ゲンガー", "ピカチュウ"]
        snapshot = replace(snapshot, observed_state_json=canonical_json(state))
        result = self.build(record(), snapshot=snapshot)
        self.assertEqual(result.reason, "JOINT_PARTY_CONTRADICTS_PREVIEW")
        state["opponent_six"][2] = KING
        snapshot = replace(snapshot, observed_state_json=canonical_json(state))
        self.assertEqual(self.build(record(), snapshot=snapshot).status,
                         "PROVISIONAL_JOINT_CANDIDATES")

    def test_reject_duplicate_ids_mixed_valid_invalid_and_existing_registration(self):
        self.assertEqual(self.build(record(), record()).reason,
                         "DUPLICATE_OR_INVALID_HYPOTHESIS_ID")
        self.assertEqual(self.build(record(), record("Joint-2",
                                                    source="MARGINAL_STATS")).status,
                         "NO_RANKING")
        self.assertEqual(self.build().reason,
                         "JOINT_CANDIDATE_BUDGET_OR_EMPTY")
        self.assertEqual(self.build(*[record(f"Joint-{i}") for i in range(17)]).reason,
                         "JOINT_CANDIDATE_BUDGET_OR_EMPTY")
        valid=self.build(record())
        s=replace(current(), hypotheses=valid.hypotheses)
        self.assertEqual(self.build(record("Other"),snapshot=s).reason,
                         "ALREADY_HAS_REGISTERED_HYPOTHESES")

    def test_incomplete_selected_three_or_form_is_not_extrapolated(self):
        self.assertEqual(
            self.build(record(trio=(NAME,GOLD,NAME))).reason,
            "INVALID_JOINT_SELECTED_THREE")
        party=members()
        party[NAME]["form"] = ""
        self.assertEqual(self.build(record(party=party)).reason,
                         "INCOMPLETE_OR_INFERRED_JOINT_MEMBER")

    def test_no_showdown_state_or_completeness_attestation_is_created(self):
        result=self.build(record())
        self.assertFalse(hasattr(result, "battle_json"))
        self.assertFalse(hasattr(result, "p2_choices"))
        self.assertEqual(result.reason, "EXPLICIT_SOURCE_SET_STAGED")
        self.assertEqual(result.coverage, "NOT_PROVEN_EXHAUSTIVE")


if __name__ == "__main__":
    unittest.main()
