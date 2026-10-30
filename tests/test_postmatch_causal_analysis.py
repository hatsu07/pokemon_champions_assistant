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
               incomplete=False, issue="初手の見直し",
               event_count=1, rich=False):
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
                    "issue":issue,
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
            for index in range(event_count):
                state_at_turn={
                    "phase":"BATTLE","step_number":index+1,
                    "turn_id":f"turn{index+1}",
                    "our_active":"ガブリアス","opponent_active":"カバルドン",
                    "selected_three":["ガブリアス"],
                    "opponent_control_classification":control,
                }
                candidate=dict(report)
                candidate["frame_id"]=f"f{index+1}"
                if rich:
                    state_at_turn.update({
                        "last_event":"観測候補だけ " + ("注意" * 15),
                        "observed_hp":{"own":"100/200", "opponent":"80/210"},
                        "battlefield":{"weather":"すなあらし",
                                       "terrain":"なし",
                                       "effects":"防御UPの候補"},
                    })
                    candidate["observation"]={
                        "visual_ui":"MOVE_MENU",
                        "visible_moves":["じしん","げきりん","まもる","アイアンヘッド"],
                        "visible_party":["ガブリアス","ロトム","サーフゴー",
                                         "ポケモン4","ポケモン5","ポケモン6"],
                    }
                self.assertTrue(buffer.capture(state_at_turn,candidate))
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

    def test_new_candidate_uses_new_three_matches_not_hash_first_old_pattern(self):
        # First batch: three reviews mention old issue A. Second batch:
        # another three reviews mention only issue B. P1 carries forward both
        # candidate groups, but P2 MUST choose the issue supported by new IDs.
        for index in range(3):
            self.record(f"old-{index}",issue="旧候補A")
        first=self.p1.aggregate_after_review(match_id="old-2")
        self.assertEqual(first["status"],"STORED_PROVISIONAL_UNVERIFIED")
        for index in range(3):
            self.record(f"new-{index}",issue="新候補B")
        second=self.p1.aggregate_after_review(match_id="new-2")
        self.assertEqual(second["status"],"STORED_PROVISIONAL_UNVERIFIED")
        cands=self.p1.candidates(batch_sha=second["batch_sha"])
        self.assertEqual({c["issue"] for c in cands},{"旧候補A","新候補B"})
        output=self.p2.analyze_after_review(
            match_id="new-2",model="m",access_token="mock",
            gate_file=self.gate,client_factory=self.factory())
        self.assertEqual(output["status"],"STORED_HOLD_UNVERIFIED")
        payload=json.loads(self.clients[0].calls[0]["input"][0]["content"][0]["text"])
        source=payload["source"]
        self.assertEqual(source["pattern"]["issue"],"新候補B")
        self.assertEqual({m["match_id"] for m in source["matches"]},
                         {"new-0","new-1","new-2"})
        self.assertIn("candidate_sha",source["pattern"])
        self.assertEqual(self.p2.read(output["research_sha"])["analysis"]["sample_size"],3)

    def test_no_repeated_new_pattern_does_not_recycle_old_issue_or_call_api(self):
        for index in range(3):
            self.record(f"old-{index}",issue="旧候補A")
        self.p1.aggregate_after_review(match_id="old-2")
        for index in range(3):
            self.record(f"new-{index}",issue=f"各回別件{index}")
        second=self.p1.aggregate_after_review(match_id="new-2")
        self.assertEqual(second["status"],"STORED_PROVISIONAL_UNVERIFIED")
        result=self.p2.analyze_after_review(
            match_id="new-2",model="m",access_token="mock",
            gate_file=self.gate,client_factory=self.factory())
        self.assertEqual(result["status"],"NO_RELEVANT_NEW_PATTERN")
        self.assertEqual(result["calls_used"],0)
        self.assertEqual(self.clients,[])

    def test_cancel_after_reservation_refunds_without_stuck_reserved_state(self):
        self.ready()
        checks=iter((False, True))
        first=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,cancel=lambda:next(checks),
            client_factory=self.factory())
        self.assertEqual(first["status"],"CANCELLED_HOLD")
        self.assertEqual(first["calls_used"],0)
        self.assertFalse(first["dispatched"])
        self.assertEqual(self.clients,[])
        self.assertEqual(self.p2.read(first["research_sha"])["status"],"CANCELLED_HOLD")
        self.assertEqual(self.p2.read(first["research_sha"])["calls_used"],0)
        # Re-entry is NOT automatic, but a deliberate explicit retry is safe.
        repeat=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,client_factory=self.factory())
        self.assertEqual(repeat["status"],"CANCELLED_HOLD")
        resumed=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,allow_retry=True,client_factory=self.factory())
        self.assertEqual(resumed["status"],"STORED_HOLD_UNVERIFIED")
        self.assertEqual(resumed["calls_used"],1)
        self.assertEqual(len(self.clients),1)

    def test_existing_unnumbered_gate_section_is_visible_and_hold_not_new_rule(self):
        self.gate.write_text(
            GATE_TEXT + "\n[SWITCH_GATE]\n"
            "TRIGGER iff candidate_action in {HARD_SWITCH,PIVOT}\n"
            "REJECT if switch_ok=N and no EXCEPTION\n"
            "[HP_LOW_GATE]\nDO_NOT_SWITCH until current_KO assessed\n"
            "[ROLE_END_GATE]\nIF role_end compare stay\n"
            "[ATTACK_GATE]\nASSESS current KO before switch\n"
            "[PRIORITY_GATE]\nCOMPARE speed and priority\n",
            encoding="utf-8")
        self.gate_sha=sha256(self.gate.read_bytes()).hexdigest()
        self.ready(confirmed=True,control="HUMAN")
        output=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,client_factory=self.factory(
                finding_factory=lambda source:[
                    finding(source,gate_rule="SWITCH_GATE",
                            gate_coverage="EXISTING_GATE_CANDIDATE")]))
        self.assertEqual(output["status"],"STORED_HOLD_UNVERIFIED")
        packet=json.loads(self.clients[0].calls[0]["input"][0]["content"][0]["text"])
        rules=packet["source"]["existing_gate_rules"]
        self.assertIn("SWITCH_GATE",rules)
        self.assertIn("REJECT if switch_ok=N",rules["SWITCH_GATE"])
        for section in ("HP_LOW_GATE","ROLE_END_GATE","ATTACK_GATE","PRIORITY_GATE"):
            self.assertIn(section,rules)
        finding_row=self.p2.read(output["research_sha"])["analysis"]["findings"][0]
        self.assertEqual(finding_row["gate_rule_id"],"SWITCH_GATE")
        self.assertEqual(finding_row["gate_status"],"EXISTING_GATE_EXECUTION_CANDIDATE")
        self.assertEqual(finding_row["certainty"],"GPT_HYPOTHESIS_UNVERIFIED")
        self.assertEqual(self.p2.read(output["research_sha"])["analysis"]["status"],"HOLD")

    def test_mismatched_gate_sections_cannot_be_used_for_historic_claim(self):
        # A postmatch gate that differs by one byte is NOT the frozen rules.
        self.ready(confirmed=True,gate_sha="0"*64)
        self.gate.write_text(GATE_TEXT+"\n[SWITCH_GATE]\nREJECT if unsafe\n",
                             encoding="utf-8")
        output=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,client_factory=self.factory(
                finding_factory=lambda source:[
                    finding(source,gate_rule="SWITCH_GATE",
                            gate_coverage="EXISTING_GATE_CANDIDATE")]))
        packet=json.loads(self.clients[0].calls[0]["input"][0]["content"][0]["text"])
        self.assertEqual(packet["source"]["existing_gate_rules"],{})
        stored=self.p2.read(output["research_sha"])["analysis"]["findings"][0]
        self.assertEqual(stored["gate_status"],"RULE_COVERAGE_UNKNOWN")

    def test_realistic_three_matches_five_events_preserve_all_event_windows(self):
        # 10k+ characters of SHA-matched gate rules must not overwhelm 15
        # independently frozen, evidence-rich turn records.
        sections = "".join(
            f"\n[SECTION_{idx:02}]\n" + ("rule details " * 28) + "\n"
            for idx in range(25))
        self.gate.write_text(
            GATE_TEXT + "\n[SWITCH_GATE]\nREJECT unsafe landing\n"
            + sections, encoding="utf-8")
        self.gate_sha=sha256(self.gate.read_bytes()).hexdigest()
        for index in range(3):
            self.record(f"match-{index}",event_count=5,rich=True)
        self.p1.aggregate_after_review(match_id="match-2")
        prepared=self.p2._prepare(match_id="match-2",gate_file=self.gate)
        self.assertEqual(prepared["status"],"READY",prepared)
        source=prepared["source"]
        self.assertEqual(source["sample_size"],3)
        self.assertEqual(len(source["matches"]),3)
        self.assertTrue(all(len(m["events"])==5 for m in source["matches"]))
        self.assertFalse(source["selection_audit"]["excluded_match_ids"])
        self.assertIn("SWITCH_GATE", source["existing_gate_rules"])
        self.assertLessEqual(len(json.dumps(source,ensure_ascii=False,
                        separators=(",",":"),sort_keys=True)),23000)
        # The gate selection is auditable and never sends partial rule bodies.
        all_rules=set(__import__(
            "champions_assist.postmatch_causal_analysis",
            fromlist=["_frozen_gate_rules"])._frozen_gate_rules(
                self.gate.read_text(encoding="utf-8")))
        self.assertEqual(
            set(source["existing_gate_rules"]) |
            set(source["gate_omitted_rule_ids"]), all_rules)
        self.assertFalse(set(source["existing_gate_rules"]) &
                         set(source["gate_omitted_rule_ids"]))

    def test_six_matches_five_events_reduce_extra_matches_with_audit(self):
        for index in range(6):
            self.record(f"match-{index}",event_count=5,rich=True)
        batch=self.p1.aggregate_after_review(match_id="match-5")
        self.assertEqual(batch["status"],"STORED_PROVISIONAL_UNVERIFIED")
        result=self.p2.analyze_after_review(
            match_id="match-5",model="m",access_token="mock",
            gate_file=self.gate,client_factory=self.factory())
        self.assertEqual(result["status"],"STORED_HOLD_UNVERIFIED",result)
        source=json.loads(
            self.clients[0].calls[0]["input"][0]["content"][0]["text"])["source"]
        self.assertGreaterEqual(source["sample_size"],3)
        self.assertLessEqual(source["sample_size"],6)
        self.assertEqual(len(source["selection_audit"]["eligible_match_ids"]),6)
        self.assertIn("match-5",[m["match_id"] for m in source["matches"]])
        self.assertEqual(set(source["selection_audit"]["eligible_match_ids"]),
                         {m["match_id"] for m in source["matches"]} |
                         set(source["selection_audit"]["excluded_match_ids"]))
        self.assertTrue(all(len(m["events"])==5 for m in source["matches"]))
        self.assertLessEqual(len(json.dumps(source,ensure_ascii=False,
                        separators=(",",":"),sort_keys=True)),23000)

    def test_gate_changed_cannot_restart_automatic_budget_for_same_p1_batch(self):
        self.ready()
        first=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,client_factory=self.factory())
        self.assertEqual(first["status"],"STORED_HOLD_UNVERIFIED")
        self.gate.write_text(GATE_TEXT+"\n[SWITCH_GATE]\nREJECT unsafe\n",
                             encoding="utf-8")
        again=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,client_factory=self.factory())
        self.assertEqual(again["status"],"SOURCE_CHANGED_HOLD")
        self.assertEqual(again["research_sha"],first["research_sha"])
        self.assertEqual(again["calls_used"],1)
        self.assertEqual(len(self.clients),1)
        forced=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,allow_retry=True,client_factory=self.factory())
        self.assertEqual(forced["status"],"SOURCE_CHANGED_HOLD")
        self.assertEqual(len(self.clients),1)
        with closing(sqlite3.connect(self.learning.db_path)) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM p2_causal_batches WHERE p1_batch_sha=?",
                (self.p1.history(team_id=TEAM)[0]["batch_sha"],)).fetchone()[0],1)

    def test_gate_changed_after_failure_does_not_allow_explicit_new_source(self):
        self.ready()
        first=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,client_factory=self.factory(fail=True))
        self.assertEqual(first["status"],"FAILED_HOLD")
        old_text=self.gate.read_text(encoding="utf-8")
        self.gate.write_text(old_text+"changed",encoding="utf-8")
        blocked=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,allow_retry=True,client_factory=self.factory())
        self.assertEqual(blocked["status"],"SOURCE_CHANGED_HOLD")
        self.assertEqual(len(self.clients),1)
        self.gate.write_text(old_text,encoding="utf-8")
        retry=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,allow_retry=True,client_factory=self.factory())
        self.assertEqual(retry["status"],"STORED_HOLD_UNVERIFIED")
        self.assertEqual(retry["calls_used"],2)
        self.assertEqual(len(self.clients),2)

    def test_p1_p2_repeated_pattern_cycles_3_6_9_12_15(self):
        # Both P1's bounded evidence IDs AND P2's new-3 filter must work
        # over long-running accounts; every cycle uses its own P1 budget.
        for index in range(15):
            mid=f"match-{index:03}"
            self.record(mid)
            if index % 3 != 2:
                continue
            research=self.p1.aggregate_after_review(match_id=mid)
            self.assertEqual(research["status"],"STORED_PROVISIONAL_UNVERIFIED")
            evidence=self.p1.candidates(batch_sha=research["batch_sha"])[0][
                "evidence_match_ids"]
            self.assertEqual(len(evidence),min(index+1,12))
            self.assertTrue({f"match-{n:03}"
                             for n in range(index-2,index+1)}.issubset(evidence))
            result=self.p2.analyze_after_review(
                match_id=mid,model="m",access_token="mock",
                gate_file=self.gate,client_factory=self.factory())
            self.assertEqual(result["status"],"STORED_HOLD_UNVERIFIED",
                             f"cycle {index+1}: {result}")
            sent=json.loads(
                self.clients[-1].calls[0]["input"][0]["content"][0]["text"])
            included={x["match_id"] for x in sent["source"]["matches"]}
            self.assertTrue({f"match-{n:03}" for n in range(index-2,index+1)}
                            .issubset(included))
        self.assertEqual(len(self.clients),5)
        with closing(sqlite3.connect(self.learning.db_path)) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM p2_batch_budget").fetchone()[0],5)

    def test_gate_omission_audit_covers_all_original_sections(self):
        # Upper-stage parser exclusions must not disappear from GPT's
        # visible rule inventory or P4's audit of what was not checked.
        import re
        gate=(GATE_TEXT + "\n[CORE]\nR02 registered choice lock\n"
              "[SWITCH_GATE]\nREJECT unsafe landing\n"
              "[PREP_SHARED_90S]\n" + "p"*1853 + "\n"
              "[CASE_REGRESSION]\n" + "q"*2335 + "\n"
              "[PRIORITY_GATE]\nASSESS priority before ordinary move\n")
        self.gate.write_text(gate,encoding="utf-8")
        self.gate_sha=sha256(self.gate.read_bytes()).hexdigest()
        self.ready()
        prepared=self.p2._prepare(match_id="match-2",gate_file=self.gate)
        self.assertEqual(prepared["status"],"READY")
        source=prepared["source"]
        omitted=set(source["gate_omitted_rule_ids"])
        shown=set(source["existing_gate_rules"])
        reasons=source["gate_omission_reasons"]
        for section in ("PREP_SHARED_90S","CASE_REGRESSION"):
            self.assertIn(section,omitted)
            self.assertEqual(reasons[section],"SECTION_TOO_LONG")
        self.assertEqual(reasons["CORE"],
                         "CORE_SECTION_ONLY_NUMBERED_RULES_EXTRACTED")
        self.assertIn("SWITCH_GATE",shown)
        self.assertIn("PRIORITY_GATE",shown)
        original_sections=set(re.findall(
            r"^\\[([A-Z][A-Z0-9_]{1,63})\\]$",gate,re.MULTILINE))
        self.assertEqual(original_sections,(
            shown|omitted)&original_sections)
        self.assertEqual(set(reasons),omitted)
        self.assertFalse(shown&omitted)
        result=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,client_factory=self.factory())
        self.assertEqual(result["status"],"STORED_HOLD_UNVERIFIED")

    def test_legacy_duplicate_p2_rows_migrate_non_destructively_and_hold(self):
        # A pre-fix database could contain multiple research SHA digests
        # for one P1 batch after gate changes. Never create a failing unique
        # index or delete audit. Seed one conservative no-more-calls budget.
        batch=self.ready()
        with closing(sqlite3.connect(self.learning.db_path)) as db:
            entries=[("legacy-1","FAILED_HOLD",1),
                     ("legacy-2","STORED_HOLD_UNVERIFIED",1)]
            for research,status,calls in entries:
                db.execute("""INSERT INTO p2_causal_batches(
                    research_sha,team_id,team_signature,p1_batch_sha,
                    evidence_sha,model,gate_sha,status,analysis_json,
                    calls_used,attempted_at,completed_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (research,TEAM,SIGNATURE,batch["batch_sha"],
                     "old-evidence","legacy-model","UNKNOWN",status,
                     '{"legacy":true}',calls,
                     "2026-10-01T00:00:00+00:00", ""))
            db.commit()
        with closing(self.p2._db()) as db:
            rows=db.execute("""SELECT canonical_research_sha,calls_used,
                                     status,migrated_legacy_rows
                    FROM p2_batch_budget WHERE p1_batch_sha=?""",
                    (batch["batch_sha"],)).fetchone()
            self.assertEqual(rows,(
                "legacy-1",2,"LEGACY_DUPLICATE_HOLD",2))
            self.assertEqual(db.execute("""
                SELECT COUNT(*) FROM p2_causal_batches
                WHERE p1_batch_sha=?""",(batch["batch_sha"],)).fetchone()[0],2)
        with closing(self.p2._db()) as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM p2_batch_budget").fetchone()[0],1)
        blocked=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,allow_retry=True,client_factory=self.factory())
        self.assertEqual(blocked["status"],"LEGACY_DUPLICATE_HOLD")
        self.assertEqual(blocked["calls_used"],2)
        self.assertFalse(self.clients)
        for research,_,_ in entries:
            self.assertIsNotNone(self.p2.read(research))

    def test_never_attempt_when_cancelled_before_reservation(self):
        self.ready()
        result=self.p2.analyze_after_review(
            match_id="match-2",model="m",access_token="mock",
            gate_file=self.gate,cancel=lambda:True,client_factory=self.factory())
        self.assertEqual(result,{"status":"CANCELLED","calls_used":0})
        self.assertFalse(self.clients)


if __name__=="__main__":
    unittest.main()
