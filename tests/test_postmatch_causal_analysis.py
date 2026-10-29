"""Synthetic P2 multi-match GPT O/X review tests; no network or private files."""
from __future__ import annotations

from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from champions_assist.autonomous_improvement import AutonomousImprovementStore
from champions_assist.matchup_learning_store import MatchupLearningStore
from champions_assist.offmatch_evidence import (
    MatchEvidenceArchive, MatchEvidenceBuffer, freeze_team_context)
from champions_assist.postmatch_causal_analysis import (
    PostmatchCausalStore, SCHEMA, _sha, MIN_MATCHES)

TEAM = "p2-test-team"
SIGNATURE = "sha256:p2-version"
GATE_TEXT = "R02 choice_item: move_use=>LOCK(move); switch=>UNLOCK\nR07 process_capability != safe_switch\n"


class _Stream:
    def __init__(self, response, *, fail=False):
        self.response = response
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        if self.fail:
            raise RuntimeError("synthetic interruption")
        yield SimpleNamespace(type="response.output_text.delta",
                              delta=json.dumps(self.response,ensure_ascii=False))
        yield SimpleNamespace(type="response.completed")


class _Client:
    def __init__(self, args, *, finding_factory=None, fail=False):
        self.calls = []
        self.args = args
        self.finding_factory = finding_factory
        self.fail = fail
        self.responses = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload=json.loads(kwargs["input"][0]["content"][0]["text"])
        source=payload["source"]
        response={
            "schema":SCHEMA, "research_sha":payload["research_sha"],
            "findings": (self.finding_factory(source) if self.finding_factory else []),
            "summary":"合成記録：データ不足のため保留",
        }
        return _Stream(response,fail=self.fail)


def finding(source, *, gate_rule="", gate_coverage="UNKNOWN",
            cause="PLAY", owner="USER", event_ordinal=0,
            match_id=None, opponent_type="HUMAN"):
    return {
        "match_id": match_id or source["matches"][0]["match_id"],
        "event_ordinal":event_ordinal,
        "cause":cause, "owner":owner,
        "opponent_control":opponent_type,
        "observation":"凍結された候補だけ",
        "issue":"行動の再現性が未確認",
        "gate_rule_id":gate_rule,"gate_coverage":gate_coverage,
        "o_options":"どちらも合法性未確認",
        "x_conditions":"読みと手順依存は未検証",
        "safe_choice":"UNKNOWN",
        "expected_value_choice":"UNKNOWN",
        "read_choice":"UNKNOWN",
        "loss_route":"UNKNOWN",
        "option_status":"UNKNOWN",
        "uncertainty":"選択と実行が未確認",
    }


class CausalResearchTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.learning=MatchupLearningStore(self.root)
        self.archive=MatchEvidenceArchive(self.root)
        self.p1=AutonomousImprovementStore(self.root)
        self.p2=PostmatchCausalStore(self.root)
        self.gate=self.root/"gate.txt"
        self.gate.write_text(GATE_TEXT,encoding="utf-8")
        self.gate_sha=sha256(self.gate.read_bytes()).hexdigest()
        self.clients=[]

    def factory(self, *, finding_factory=None, fail=False):
        def make(**kwargs):
            client=_Client(kwargs,finding_factory=finding_factory,fail=fail)
            self.clients.append(client)
            return client
        return make

    def record(self, mid, *, signature=SIGNATURE, gate_sha=None,
               tactical_version="tactical-v1", environment_at="2026-10-29",
               with_p1=True, confirmed=False, control="UNKNOWN",
               incomplete=False):
        state={
            "phase":"ENDED","result":"LOSS",
            "opponent_six":["カバルドン"],
            "selected_three":["ガブリアス"],
            "lead":"ガブリアス","history":[],
        }
        self.assertTrue(self.learning.record_finished(
            match_id=mid,team_id=TEAM,state=state,
            team_signature=signature,source="AI_VISUAL_UNVERIFIED"))
        self.assertTrue(self.learning.save_postmatch_review(
            match_id=mid,review={
                "schema":"champions-postmatch-review-v1",
                "match_id":mid,
                "findings":[{
                    "opponent":"カバルドン","our_pokemon":"ガブリアス",
                    "observation":"画面候補",
                    "issue":"初手の見直し",
                    "suggestion":"対面選択を再比較",
                    "certainty":"HYPOTHESIS"}],
                "next_match_notes":"保留",
            }))
        if with_p1:
            context=freeze_team_context(
                team_id=TEAM,team_sha=signature,model="test",
                tactical_version=tactical_version,
                regulation="REG-C",regulation_source="SYNTHETIC",
                regulation_observed_at="2026-10-28",
                environment_source="SYNTHETIC",
                environment_observed_at=environment_at,
                frozen_assets_sha={"gate":gate_sha if gate_sha is not None else self.gate_sha},
                own_members=[{"name":"ガブリアス","move1":"じしん"}])
            buffer=MatchEvidenceBuffer(match_id=mid,context=context)
            report={
                "status":"ADVICE","action":{"kind":"MOVE","move":"じしん"},
                "frame_id":"f1",
                "observation":{"visual_ui":"MOVE_MENU"},
            }
            if confirmed:
                report["actual_action"]={"kind":"MOVE","move":"じしん"}
                report["actual_action_source"]="USER_CONFIRMED"
            st={"phase":"BATTLE","step_number":1,"turn_id":"turn1",
                "our_active":"ガブリアス","opponent_active":"カバルドン",
                "selected_three":["ガブリアス"],
                "opponent_control_classification":control}
            self.assertTrue(buffer.capture(st,report))
            if incomplete:
                buffer.dropped_events=1
            self.assertEqual(
                self.archive.save_ended(buffer.finish())["status"],
                "STORED_UNVERIFIED")

    def ready(self, *, confirmed=False, gate_sha=None, control="UNKNOWN",
              environment_at="2026-10-29"):
        for index in range(3):
            self.record(f"match-{index}",confirmed=confirmed,
                        gate_sha=gate_sha,control=control,
                        environment_at=environment_at)
        p1=self.p1.aggregate_after_review(match_id="match-2")
        self.assertEqual(p1["status"],"STORED_PROVISIONAL_UNVERIFIED")
        return p1

    def test_no_call_until_three_independent_reviews_with_valid_p1(self):
        self.record("a")
        self.record("b")
        result=self.p2.analyze_after_review(
            match_id="b",model="m",access_token="test",gate_file=self.gate,
            client_factory=self.factory())
        self.assertEqual(result["calls_used"],0)
        self.assertEqual(result["status"],"NO_REPEATED_PATTERN")
        self.record("c",with_p1=False)
        self.p1.aggregate_after_review(match_id="c")
        result=self.p2.analyze_after_review(
            match_id="c",model="m",access_token="test",gate_file=self.gate,
            client_factory=self.factory())
        self.assertEqual(result["status"],"P1_EVIDENCE_UNAVAILABLE")
        self.assertFalse(self.clients)

    def test_bounded_gpt_once_holds_ox_no_live_and_no_raw_history(self):
        self.ready()
        result=self.p2.analyze_after_review(
            match_id="match-2",model="gpt-synthetic",access_token="mock",
            gate_file=self.gate,
            client_factory=self.factory(finding_factory=lambda s:[
                finding(s, gate_rule="R07",
                        gate_coverage="EXISTING_GATE_CANDIDATE")]))
        self.assertEqual(result["status"],"STORED_HOLD_UNVERIFIED")
        self.assertEqual(result["calls_used"],1)
        self.assertFalse(result["live_applied"])
        self.assertEqual(len(self.clients),1)
        kwargs=self.clients[0].calls[0]
        self.assertFalse(kwargs["store"])
        self.assertFalse(self.clients[0].args["max_retries"])
        self.assertEqual(kwargs["max_output_tokens"],3500)
        payload=kwargs["input"][0]["content"][0]["text"]
        self.assertNotIn("access_token",payload)
        self.assertNotIn("history",payload)
        self.assertNotIn("screenshot",payload)
        audit=self.p2.read(result["research_sha"])
        self.assertEqual(audit["status"],"STORED_HOLD_UNVERIFIED")
        self.assertEqual(audit["analysis"]["sample_size"],3)
        row=audit["analysis"]["findings"][0]
        self.assertEqual(row["certainty"],"GPT_HYPOTHESIS_UNVERIFIED")
        self.assertEqual(row["gate_status"],"RULE_COVERAGE_UNKNOWN")
        self.assertEqual(row["option_legality"],"NOT_VERIFIED")
        self.assertEqual(row["status"],"HOLD_GATE_OR_ACTION_UNVERIFIED")
        self.assertEqual(row["opponent_control"],"UNKNOWN")
        again=self.p2.analyze_after_review(
            match_id="match-2",model="gpt-synthetic",access_token="mock",
            gate_file=self.gate,client_factory=self.factory())
        self.assertEqual(again["status"],"STORED_HOLD_UNVERIFIED")
        self.assertEqual(len(self.clients),1)

    def test_existing_gate_with_proven_execution_is_not_a_new_rule(self):
        self.ready(confirmed=True,control="HUMAN")
        result=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,client_factory=self.factory(
                finding_factory=lambda s:[
                    finding(s,gate_rule="R02",
                            gate_coverage="EXISTING_GATE_CANDIDATE")]))
        row=self.p2.read(result["research_sha"])["analysis"]["findings"][0]
        self.assertEqual(row["gate_status"],"EXISTING_GATE_EXECUTION_CANDIDATE")
        self.assertEqual(row["status"],"EXISTING_RULE_EXECUTION_HYPOTHESIS")
        self.assertEqual(row["opponent_control"],"HUMAN")
        self.assertEqual(self.p2.read(result["research_sha"])["analysis"]["status"],"HOLD")

    def test_gate_mismatch_holds_even_if_gpt_claims_preventable(self):
        self.ready(confirmed=True,gate_sha="0"*64)
        result=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",gate_file=self.gate,
            client_factory=self.factory(finding_factory=lambda s:[
                finding(s,gate_rule="R02",
                        gate_coverage="EXISTING_GATE_CANDIDATE")]))
        row=self.p2.read(result["research_sha"])["analysis"]["findings"][0]
        self.assertEqual(row["gate_status"],"RULE_COVERAGE_UNKNOWN")
        self.assertEqual(row["gate_rule_id"],"")
        self.assertEqual(self.p2.read(result["research_sha"])["gate_sha"],"UNKNOWN")

    def test_output_referencing_unobserved_match_fails_closed_and_exhausts_budget(self):
        self.ready()
        factory=self.factory(finding_factory=lambda s:[
            finding(s,match_id="invented-match")])
        bad=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,client_factory=factory)
        self.assertEqual(bad["status"],"FAILED_HOLD")
        self.assertEqual(bad["reason"],"ConfigurationError")
        self.assertEqual(bad["calls_used"],1)
        second=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,client_factory=self.factory())
        self.assertEqual(second["status"],"FAILED_HOLD")
        self.assertEqual(len(self.clients),1)

    def test_cancel_and_network_error_do_not_retry_or_modify_old_review(self):
        self.ready()
        result=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,client_factory=self.factory(fail=True))
        self.assertEqual(result["status"],"FAILED_HOLD")
        self.assertEqual(self.p2.read(result["research_sha"])["calls_used"],1)
        self.assertIsNotNone(self.learning.postmatch_review("match-2"))

    def test_explicit_retry_once_then_budget_exhausted(self):
        self.ready()
        first=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,client_factory=self.factory(fail=True))
        self.assertEqual(first["status"],"FAILED_HOLD")
        no_auto=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,client_factory=self.factory())
        self.assertEqual(no_auto["status"],"FAILED_HOLD")
        self.assertEqual(len(self.clients),1)
        second=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,allow_retry=True,
            client_factory=self.factory(fail=True))
        self.assertEqual(second["status"],"FAILED_HOLD")
        self.assertEqual(second["calls_used"],2)
        exhausted=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,allow_retry=True,
            client_factory=self.factory())
        self.assertEqual(exhausted["status"],"BUDGET_EXHAUSTED")
        self.assertEqual(exhausted["calls_used"],2)
        self.assertEqual(len(self.clients),2)

    def test_mixed_environment_or_partial_archive_holds_without_gpt(self):
        self.record("x0",environment_at="old")
        self.record("x1",environment_at="new")
        self.record("x2",environment_at="old")
        self.p1.aggregate_after_review(match_id="x2")
        result=self.p2.analyze_after_review(
            match_id="x2",model="m",access_token="mock",
            gate_file=self.gate,client_factory=self.factory())
        self.assertEqual(result["status"],"ENVIRONMENT_VERSION_MISMATCH")
        self.assertFalse(self.clients)

    def test_never_attempt_when_cancelled_before_reservation(self):
        self.ready()
        result=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,cancel=lambda:True,client_factory=self.factory())
        self.assertEqual(result,{"status":"CANCELLED","calls_used":0})
        self.assertFalse(self.clients)


if __name__=="__main__":
    unittest.main()
