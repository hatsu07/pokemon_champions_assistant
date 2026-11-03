"""Cross-window ②〜⑥ harness tests. All synthetic, no battle authority."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import MappingProxyType
import unittest

from champions_assist.preload import REQUIRED_MASTER_FILES
from champions_assist.prep import PrepMatchup, PrepResult
from champions_assist.local_strategy.phase2_match_audit import (
    SELECTION_PREP_SHARED_SECONDS, SelectionPrepReceipt,
    _prep_receipt_status, audit_offline_phase2_match,
)
from champions_assist.local_strategy.shadow_comparison_store import (
    BoundGPTAdvice, read_shadow_comparisons,
)
from tests.test_issue_140_foundation import VERIFIED_ASSETS
from tests.test_issue_141_replay_projection_audit import snapshot, sourced
from tests.test_issue_141_observed_synthetic_replay_bridge import cases
from tests.test_issue_141_observed_full_grid_diagnostic import FullGridFakeWorker
from tests.test_issue_141_observed_id_bridge import with_state

OPPONENTS = ("カイリュー", "サーフゴー", "ドドゲザン",
             "ゲンガー", "ボーマンダ", "リザードン")


def with_matrix():
    """Attach 6 synthetic opponent rows but keep the true fixture team CSV."""
    names=VERIFIED_ASSETS.own_team_names
    header="# OWN={"+",".join(
        chr(ord("A")+i)+":"+name for i,name in enumerate(names))+"}"
    rows={}
    for enemy in OPPONENTS:
        rows[enemy]="|".join((
            enemy,"test/type","85",
            *["B,G,じしん,1,U,0"]*6,
            "ガブリアス/アシレーヌ/ミミッキュ",
            "warning test only","-","A?/A?/A?/A?/A?/A?","S",
        ))
    matrix=header+"\n"+"\n".join(rows.values())
    name=REQUIRED_MASTER_FILES[1]
    contents=dict(VERIFIED_ASSETS.contents)
    contents[name]=matrix
    sha=dict(VERIFIED_ASSETS.sha256)
    sha[name]=sha256(matrix.encode("utf-8")).hexdigest()
    assets=replace(
        VERIFIED_ASSETS,contents=MappingProxyType(contents),
        sha256=MappingProxyType(sha),matrix_index=MappingProxyType(rows))
    original=snapshot()
    win=replace(
        original.window,
        four_master_sha256=tuple((k,sha[k]) for k in REQUIRED_MASTER_FILES))
    state=replace(original,window=win)
    return assets,state


def sel(s, *, started=90.0, committed=100.0,
        finished=120.0, first_lead=195.0):
    return SelectionPrepReceipt(
        s.window.match_id,s.window.team_sha256,s.window.four_master_sha256,
        ("ガブリアス","アシレーヌ","ミミッキュ"),
        "ガブリアス",OPPONENTS,started,committed,finished,first_lead)


def gpt_for(s):
    return BoundGPTAdvice(s.snapshot_sha256,"GPT_ABSTAIN",
                          reason="synthetic-only-tag")


def full_prep():
    moves={"ガブリアス":"じしん","アシレーヌ":"ムーンフォース",
           "ミミッキュ":"じゃれつく"}
    rows=[]
    for own,move in moves.items():
        for opponent in OPPONENTS:
            if own == "ガブリアス":
                code="|".join((
                    "ACT=move:じしん","KO=not_guaranteed","DPS=じしん",
                    "PRI=none","S=conditional","OUR=じしん",
                    "OPP=unknown_hit","SAFE=unsafe","PROC=unverified",
                    "LOCK=unknown","MEGA=recheck",
                ))
            else:
                code="|".join((
                    "S=conditional",f"OUR={move}","OPP=unknown_hit",
                    "PRI=none","SAFE=unsafe","PROC=unverified",
                ))
            rows.append(PrepMatchup(own,opponent,code))
    return PrepResult("COMPLETE",tuple(rows),
                      "ガブリアス","ミミッキュ","アシレーヌ")


class CrossPhaseOfflineAuditTests(unittest.TestCase):
    def test_all_five_stages_can_be_inspected_without_false_acceptance(self):
        assets,turn=with_matrix()
        with TemporaryDirectory() as temp:
            worker=FullGridFakeWorker()
            # The inherited fake readout fixture otherwise hardcodes its
            # original four-master SHA. Bind it to the same truly frozen
            # matrix/team used by this cross-phase test, never bypass the
            # production SHA/roster verification.
            with patch("tests.test_issue_141_replay_projection_audit.VERIFIED_ASSETS",
                       assets):
                report=audit_offline_phase2_match(
                    sel(turn),turn,assets,gpt=gpt_for(turn),
                    current_snapshot=lambda:turn,
                    database_path=Path(temp)/"evidence.sqlite",
                    joint_candidates=sourced(),complete_branches=cases(),
                    worker=worker,preserve="ガブリアス",stopper="ミミッキュ",
                    prep_assessment=full_prep())
            self.assertEqual(report.status,"OFFLINE_EVIDENCE_ONLY",report.reason)
            self.assertEqual(report.observed_stage_status,
                             "PROVISIONAL_OBSERVED_BRANCH_CONSTRAINTS")
            self.assertEqual(report.observed_case_count,2)
            self.assertEqual(report.synthetic_turn_status,"MODEL_DIAGNOSTIC")
            self.assertEqual(report.prep_status,"ASSESSED_18_CONDITIONAL_ONLY")
            self.assertEqual(report.prep_assessed_pairs,18)
            self.assertEqual(report.prep_time_status,
                             "PREP_REPORTED_WITHIN_SHARED_90_SECONDS_UNVERIFIED")
            self.assertEqual(report.shadow_store_status,"RECORDED")
            self.assertEqual(len(worker.simulator.calls),4)
            self.assertEqual(read_shadow_comparisons(
                Path(temp)/"evidence.sqlite"),(report.shadow_record,))
            self.assertFalse(report.phase2_accepted)
            self.assertEqual(report.ranking_status,"NO_RANKING")
            self.assertIn("REAL_CHAMPIONS_FULL_BATTLE_AND_RNG_NOT_ATTESTED",
                          report.missing_acceptance_evidence)
            self.assertNotIn("PREP18_TACTICAL_FIELDS_NOT_ASSESSED",
                             report.missing_acceptance_evidence)
            self.assertEqual(report.shadow_record.gpt_reason_code,"GPT_ABSTAINED")

    def test_without_replay_only_records_abstention_without_worker(self):
        assets,turn=with_matrix()
        with TemporaryDirectory() as temp:
            record_path=Path(temp)/"abstain.sqlite"
            report=audit_offline_phase2_match(
                sel(turn),turn,assets,gpt=gpt_for(turn),
                database_path=record_path,
                joint_candidates=sourced(),
                current_snapshot=lambda:turn)
            self.assertEqual(report.status,"OFFLINE_EVIDENCE_ONLY")
            self.assertEqual(report.synthetic_turn_status,"NO_RANKING")
            self.assertEqual(report.prep_status,"MATRIX_18_CONDITIONAL_ONLY")
            self.assertEqual(report.prep_assessed_pairs,0)
            self.assertEqual(report.shadow_record.local_status,"NO_RANKING")
            self.assertEqual(report.shadow_record.local_reason_code,"LOCAL_NOT_RUN")
            self.assertFalse(report.shadow_record.input_deadline_met)
            self.assertIn("ONE_TURN_DIAGNOSTIC_NOT_AVAILABLE",
                          report.missing_acceptance_evidence)
            self.assertEqual(read_shadow_comparisons(record_path),
                             (report.shadow_record,))

    def test_stale_or_cross_match_never_starts_worker_or_database(self):
        assets,turn=with_matrix()
        old=with_state(turn,field="late_ocr",value=1)
        wrong=replace(sel(turn),match_id="other-match")
        for selection,current in ((sel(turn),old),(wrong,turn),
                                  (replace(sel(turn),team_sha256="f"*64),turn)):
            with self.subTest(selection=selection,current=current):
                with TemporaryDirectory() as temp:
                    db=Path(temp)/"not-written.sqlite"
                    worker=FullGridFakeWorker()
                    report=audit_offline_phase2_match(
                        selection,turn,assets,gpt=gpt_for(turn),
                        current_snapshot=lambda:current,
                        database_path=db, joint_candidates=sourced(),
                        complete_branches=cases(),worker=worker,
                        preserve="ガブリアス",stopper="ミミッキュ")
                    self.assertEqual(report.status,"BLOCKED")
                    self.assertEqual(worker.calls,[])
                    self.assertFalse(db.exists())
                    self.assertFalse(report.phase2_accepted)

    def test_stale_source_joint_batch_does_not_run_engine(self):
        assets,turn=with_matrix()
        bad=tuple(reversed(sourced()))
        with TemporaryDirectory() as temp:
            db=Path(temp)/"bad.sqlite"
            worker=FullGridFakeWorker()
            report=audit_offline_phase2_match(
                sel(turn),turn,assets,gpt=gpt_for(turn),
                current_snapshot=lambda:turn,
                database_path=db, joint_candidates=bad,
                complete_branches=cases(),worker=worker,
                preserve="ガブリアス",stopper="ミミッキュ")
            # The sourced candidate order itself may be valid; readout
            # ordering is NOT allowed to silently skip the conflicting case.
            self.assertNotEqual(report.synthetic_turn_status,"LOCAL_VERIFIED")
            self.assertFalse(report.phase2_accepted)
            self.assertEqual(report.ranking_status,"NO_RANKING")
            if report.status=="BLOCKED":
                self.assertFalse(db.exists())

    def test_roles_must_be_explicit_never_guessed_from_team_order(self):
        assets,turn=with_matrix()
        with TemporaryDirectory() as temp:
            db=Path(temp)/"missing-roles.sqlite"
            worker=FullGridFakeWorker()
            report=audit_offline_phase2_match(
                sel(turn),turn,assets,gpt=gpt_for(turn),
                current_snapshot=lambda:turn,database_path=db,
                joint_candidates=sourced(),complete_branches=cases(),worker=worker)
            self.assertEqual(report.status,"BLOCKED")
            self.assertEqual(report.reason,"EXPLICIT_SELECTED_WIN_ROLES_REQUIRED")
            self.assertFalse(db.exists())
            self.assertEqual(worker.calls,[])

    def test_shared_90_selection_and_prep_without_40_second_cap(self):
        assets,s=with_matrix()
        self.assertEqual(SELECTION_PREP_SHARED_SECONDS,90.0)
        scenarios=(
            # 10s selection + 80s PREP, 35s + 55s, 70s + 20s.
            (sel(s,started=100,committed=110,finished=190,first_lead=200),
             "PREP_REPORTED_WITHIN_SHARED_90_SECONDS_UNVERIFIED"),
            (sel(s,started=100,committed=135,finished=190,first_lead=200),
             "PREP_REPORTED_WITHIN_SHARED_90_SECONDS_UNVERIFIED"),
            (sel(s,started=100,committed=170,finished=190,first_lead=200),
             "PREP_REPORTED_WITHIN_SHARED_90_SECONDS_UNVERIFIED"),
            (sel(s,started=100,committed=110,finished=190.1,first_lead=200),
             "PREP_OVER_SHARED_90_SECONDS"),
            (sel(s,started=100,committed=135,finished=189,first_lead=188),
             "PREP_AFTER_OPPONENT_LEAD"),
            (sel(s,started=100,committed=110,finished=205,first_lead=190),
             "PREP_AFTER_OPPONENT_LEAD"),
            (sel(s,started=100,committed=135,finished=134),
             "SELECTION_OR_PREP_TIME_REVERSED"),
            (sel(s,started=100,committed=99,finished=120),
             "SELECTION_OR_PREP_TIME_REVERSED"),
            (replace(sel(s),assessment_finished_at=None),
             "PREP_END_NOT_OBSERVED"),
            (replace(sel(s),assessment_finished_at=float("nan")),
             "PREP_END_NOT_OBSERVED"),
            (replace(sel(s),selection_started_at=float("nan")),
             "UNATTESTED_SELECTION_TIMELINE"),
        )
        for receipt,expected in scenarios:
            with self.subTest(receipt=receipt):
                self.assertEqual(_prep_receipt_status(receipt,full_prep()),expected)
        # 60s PREP after 10s selection is permitted by the shared budget.
        self.assertEqual(_prep_receipt_status(
            sel(s,started=100,committed=110,finished=170),
            full_prep()),"PREP_REPORTED_WITHIN_SHARED_90_SECONDS_UNVERIFIED")
        with TemporaryDirectory() as temp:
            report=audit_offline_phase2_match(
                sel(s,started=100,committed=110,finished=191),s,assets,
                gpt=gpt_for(s),current_snapshot=lambda:s,
                database_path=Path(temp)/"late.sqlite",
                prep_assessment=full_prep())
            # A late result is NOT counted on-time, but 90s is only a
            # soft target. Preserve its complete 18-pair structural check.
            self.assertEqual(report.prep_status,"ASSESSED_18_CONDITIONAL_ONLY")
            self.assertEqual(report.prep_assessed_pairs,18)
            self.assertEqual(report.prep_time_status,"PREP_OVER_SHARED_90_SECONDS")
            self.assertIn("SELECTION_PREP_SHARED_90_TIME_NOT_VERIFIED",
                          report.missing_acceptance_evidence)
        with TemporaryDirectory() as temp:
            # The actual opponent lead supersedes unfinished PREP regardless
            # of remaining seconds; the 18 reviewed rows cannot be adopted.
            report=audit_offline_phase2_match(
                sel(s,started=100,committed=110,finished=130,first_lead=125),
                s,assets,gpt=gpt_for(s),current_snapshot=lambda:s,
                database_path=Path(temp)/"lead.sqlite",
                prep_assessment=full_prep())
            self.assertEqual(report.prep_time_status,"PREP_AFTER_OPPONENT_LEAD")
            self.assertEqual(report.prep_assessed_pairs,0)
        with TemporaryDirectory() as temp:
            report=audit_offline_phase2_match(
                sel(s,started=100,committed=110,finished=205,first_lead=190),
                s,assets,gpt=gpt_for(s),current_snapshot=lambda:s,
                database_path=Path(temp)/"late_lead.sqlite",
                prep_assessment=full_prep())
            self.assertEqual(report.prep_time_status,"PREP_AFTER_OPPONENT_LEAD")
            self.assertEqual(report.prep_assessed_pairs,0)


if __name__=="__main__":
    unittest.main()
