"""Phase 2④/⑤/⑥ offline contract tests — synthetic, NOT game validity."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from unittest.mock import patch

from champions_assist.local_strategy.prep18_audit import stage_prep18
from champions_assist.local_strategy.shadow_comparison_store import (
    BoundGPTAdvice, append_shadow_comparison, build_shadow_comparison,
    read_shadow_comparisons,
)
from champions_assist.local_strategy.shadow_quality_gate import (
    GoldenExpectation, audit_shadow_quality,
)
from champions_assist.local_strategy.offline_shadow_capture import (
    capture_offline_same_window,
)
from tests.test_issue_141_observed_full_grid_diagnostic import FullGridFakeWorker
from tests.test_issue_141_observed_synthetic_replay_bridge import cases
from tests.test_issue_141_replay_projection_audit import snapshot, sourced
from tests.test_issue_141_observed_id_bridge import with_state
from champions_assist.local_strategy.observed_full_grid_diagnostic import (
    ObservedFullGridDiagnostic,
)
from champions_assist.prep import PrepMatchup, PrepResult
from champions_assist.preload import FrozenMatchAssets
from champions_assist.local_strategy.models import fingerprint
from tests.test_issue_140_foundation import frozen, VERIFIED_ASSETS
from tests.test_issue_141_selection import make_assets, OWN, OPP


def assessment(*, switch_safe=False, ko_confirmed=False, missed=None):
    selected=OWN[:3]
    items=[]
    for own in selected:
        for op in OPP:
            if own == selected[0]:
                parts=(
                    "ACT=move:mv", "KO=confirmed" if ko_confirmed else "KO=not_guaranteed",
                    "DPS=mv", "PRI=none", "S=conditional",
                    "OUR=mv", "OPP=unknown_opponent_hit",
                    "SAFE=safe" if switch_safe else "SAFE=unsafe",
                    "PROC=unverified", "LOCK=unknown", "MEGA=recheck")
            else:
                parts=("S=conditional", "OUR=mv", "OPP=unknown_opponent_hit",
                       "PRI=unknown", "SAFE=unsafe", "PROC=unverified")
            if missed == (own,op):
                parts=parts[:-1]
            items.append(PrepMatchup(own,op,"|".join(parts)))
    return PrepResult("COMPLETE",tuple(items),
                      preserve=selected[0],stopper=selected[1],
                      closer=selected[2])


def local_diag(s):
    return ObservedFullGridDiagnostic(
        "SYNTHETIC_TURN_DIAGNOSTIC_ONLY",
        "UNVERIFIED_REAL_BATTLE_MAPPING_RNG_AND_OPPONENT_POLICY",
        match_id=s.window.match_id,
        decision_window_id=s.window.decision_window_id,
        state_revision=s.window.state_revision,
        observed_snapshot_sha256=s.snapshot_sha256,
        evaluated_branch_count=8)


def compared(s=None, *, local=True, elapsed=120, remaining=150,
             gpt_status="GPT_ADVICE"):
    snap=s or frozen()
    gpt=BoundGPTAdvice(
        snap.snapshot_sha256,gpt_status,
        snap.verified_action_ids[0] if gpt_status=="GPT_ADVICE" else "",
        "offline-synthetic-case",23)
    return build_shadow_comparison(
        snap,snap,gpt,local_diag(snap) if local else None,
        local_elapsed_ms=elapsed,remaining_ms_at_capture=remaining,
        recorded_at="2026-11-03T10:30:00+09:00")


class Prep18AuditTests(unittest.TestCase):
    def test_exact_six_matrix_fetch_and_eighteen_conditionals(self):
        assets=make_assets()
        with patch.object(FrozenMatchAssets,"matrix_rows_for",autospec=True,
                          side_effect=lambda instance,names:tuple(
                              instance.matrix_index[n] for n in names)) as rows:
            out=stage_prep18(assets,OWN[:3],OWN[0],OPP)
            rows.assert_called_once_with(assets,OPP)
        self.assertEqual(out.status,"MATRIX_18_CONDITIONAL_ONLY")
        self.assertEqual(len(out.lead_six),6)
        self.assertEqual(len(out.bench_twelve),12)
        self.assertEqual(
            [(c.own,c.opponent) for c in out.lead_six],
            [(OWN[0],x) for x in OPP])
        self.assertEqual(len({(c.own,c.opponent)
                              for c in out.lead_six+out.bench_twelve}),18)
        self.assertEqual(out.assessed_pairs,0)
        self.assertTrue(any(c.explicit_processor for c in out.lead_six))
        self.assertTrue(all(c.safe_hard_switch=="UNVERIFIED"
                            and c.immediate_ko=="UNKNOWN"
                            and c.opponent_max_hit=="UNKNOWN"
                            for c in out.lead_six+out.bench_twelve))
        self.assertFalse(out.tactics_verified)
        self.assertEqual(out.ranking_status,"NO_RANKING")

    def test_external_all_eighteen_actually_assessed_only_structural_pass(self):
        out=stage_prep18(make_assets(),OWN[:3],OWN[0],OPP,
                         reviewed_assessment=assessment())
        self.assertEqual(out.status,"ASSESSED_18_CONDITIONAL_ONLY",out.reason)
        self.assertEqual(out.assessed_pairs,18)
        self.assertEqual((out.preserve,out.stopper,out.closer),OWN[:3])
        self.assertEqual(sum(bool(x.reviewed_eval_code)
                             for x in out.lead_six+out.bench_twelve),18)
        self.assertEqual(out.safe_switches_certified,0)
        self.assertEqual(out.damage_claims_certified,0)
        self.assertFalse(out.tactics_verified)

    def test_missing_pair_invalid_action_wrong_roles_and_unsupported_claims(self):
        for assess in (
            replace(assessment(),matchups_18=assessment().matchups_18[:-1]),
            replace(assessment(),stopper="NOT_SELECTED"),
            assessment(switch_safe=True),
            assessment(ko_confirmed=True),
            assessment(missed=(OWN[1],OPP[0])),
        ):
            with self.subTest(assess=assess):
                out=stage_prep18(make_assets(),OWN[:3],OWN[0],OPP,
                                 reviewed_assessment=assess)
                self.assertEqual(out.status,"NO_PREP18")
                self.assertEqual(out.assessed_pairs,0)
        self.assertEqual(stage_prep18(
            make_assets(provisional=True),OWN[:3],OWN[0],OPP).status,
            "NO_PREP18")
        self.assertEqual(stage_prep18(
            make_assets(),OWN[:3],OWN[0],OPP[:-1]+("NOT_FOUND",)).status,
            "NO_PREP18")


class ShadowSameRevisionStoreTests(unittest.TestCase):
    def test_atomic_sqlite_capture_idempotence_and_no_overwrite(self):
        snap=frozen()
        record=compared(snap)
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/"shadow.sqlite"
            self.assertEqual(append_shadow_comparison(db,record),"RECORDED")
            self.assertEqual(append_shadow_comparison(db,record),"ALREADY_RECORDED")
            rows=read_shadow_comparisons(db)
            self.assertEqual(rows,(record,))
            self.assertEqual(rows[0].local_status,"MODEL_DIAGNOSTIC")
            self.assertEqual(rows[0].same_action_comparison,
                             "NO_VERIFIED_LOCAL_ACTION")
            self.assertEqual(rows[0].hypothetical_branch_count,8)
            self.assertFalse(any(
                getattr(rows[0],field,None) for field in ("raw_ocr","image","token")))
            with self.assertRaisesRegex(ValueError,"CONFLICTING_SAME_WINDOW_RECORD"):
                append_shadow_comparison(db,replace(record,local_elapsed_ms=999))
            self.assertEqual(read_shadow_comparisons(db),(record,))

    def test_real_offline_invoke_captures_elapsed_and_abstention_reason(self):
        snap=snapshot()
        advice=BoundGPTAdvice(snap.snapshot_sha256,"GPT_ABSTAIN",
                              reason="synthetic-only")
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/"pair.sqlite"
            worker=FullGridFakeWorker()
            result=capture_offline_same_window(
                db,snap,VERIFIED_ASSETS,gpt=advice,
                joint_candidates=sourced(),complete_branches=cases(),
                worker=worker,preserve="ガブリアス",stopper="ミミッキュ",
                current_snapshot=lambda:snap)
            self.assertEqual(result.store_status,"RECORDED")
            self.assertEqual(result.record.local_status,"MODEL_DIAGNOSTIC")
            self.assertEqual(len(worker.simulator.calls),4)
            self.assertEqual(read_shadow_comparisons(db),(result.record,))
            self.assertFalse(result.record.gpt_action_verified_in_window)
            self.assertEqual(result.record.same_action_comparison,
                             "NO_VERIFIED_LOCAL_ACTION")
            # A failing worker must be logged with its concrete reason,
            # not silently omitted or promoted to a local choice.
            failed=replace(snap,window=replace(
                snap.window,decision_window_id="new-offline-window"))
            bad_advice=BoundGPTAdvice(failed.snapshot_sha256,"GPT_ABSTAIN")
            worker2=FullGridFakeWorker(refuse_id="Joint-2")
            decline=capture_offline_same_window(
                db,failed,VERIFIED_ASSETS,gpt=bad_advice,
                joint_candidates=sourced(),complete_branches=cases(),
                worker=worker2,preserve="ガブリアス",stopper="ミミッキュ",
                current_snapshot=lambda:failed)
            self.assertEqual(decline.record.local_status,"NO_RANKING")
            self.assertEqual(decline.record.local_reason_code,"LOCAL_P2_REFUSED")
            self.assertEqual(decline.record.hypothetical_branch_count,0)
            self.assertEqual(len(read_shadow_comparisons(db)),2)
            stale=with_state(snap,field="later_observation",value=True)
            with self.assertRaisesRegex(ValueError,"STALE_WINDOW_BEFORE"):
                capture_offline_same_window(
                    db,snap,VERIFIED_ASSETS,gpt=advice,
                    joint_candidates=sourced(),complete_branches=cases(),
                    worker=FullGridFakeWorker(),preserve="ガブリアス",
                    stopper="ミミッキュ",current_snapshot=lambda:stale)

    def test_reject_forged_verified_local_record_at_sqlite_boundary(self):
        row=compared()
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/"state.sqlite"
            for fake in (replace(row,local_status="LOCAL_VERIFIED"),
                         replace(row,local_status="NO_RANKING",
                                 hypothetical_branch_count=1),
                         replace(row,snapshot_sha256="broken")):
                with self.assertRaisesRegex(ValueError,"INVALID_NONACTIONABLE"):
                    append_shadow_comparison(db,fake)
            self.assertFalse(db.is_file())

    def test_private_gpt_reason_unregistered_action_and_worker_text_never_persist(self):
        """User-supplied GPT/OCR prose and unknown action must not reach SQLite.

        These are invented sentinel values, NOT real credentials or OCR.
        Test both the exposed record and actual SQLite bytes after close.
        """
        snap=frozen()
        secret="TEST_ONLY_SENSITIVE_SENTINEL_9f0b8a"
        ocr="TEST_ONLY_OCR_TEXT_ダミー相手残りHP"
        reasoning="MODEL_LONG_PRIVATE_REASONING_"*100
        unknown_action="not-a-registered-action:"+secret+ocr+reasoning
        advice=BoundGPTAdvice(
            snap.snapshot_sha256,"GPT_ADVICE",unknown_action,
            reason=secret+ocr+reasoning,elapsed_ms=5)
        record=build_shadow_comparison(
            snap,snap,advice,None,local_elapsed_ms=11,
            remaining_ms_at_capture=50,
            local_refusal_reason="LOCAL_ABSTAIN:"+secret+ocr+reasoning)
        self.assertEqual(record.gpt_reason_code,"GPT_UNREGISTERED_ACTION")
        self.assertEqual(record.gpt_action_sha256,"")
        self.assertFalse(record.gpt_action_verified_in_window)
        self.assertEqual(record.local_reason_code,"LOCAL_OTHER_REFUSAL")
        self.assertNotIn(secret,repr(record))
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/"privacy.sqlite"
            self.assertEqual(append_shadow_comparison(db,record),"RECORDED")
            self.assertEqual(read_shadow_comparisons(db),(record,))
            data=db.read_bytes()
            for forbidden in (secret,ocr,reasoning,unknown_action):
                self.assertNotIn(forbidden.encode("utf-8"),data)
            self.assertNotIn(b"not-a-registered-action:",data)

    def test_verified_action_is_digest_only_not_raw_id_or_free_reason(self):
        snap=frozen()
        valid_action=snap.verified_action_ids[0]
        synthetic_secret="TEST_ONLY_PRIVATE_REASON_SENTINEL"
        gpt=BoundGPTAdvice(snap.snapshot_sha256,"GPT_ADVICE",
                           valid_action,reason=synthetic_secret)
        row=build_shadow_comparison(
            snap,snap,gpt,None,local_elapsed_ms=20,
            remaining_ms_at_capture=1)
        self.assertTrue(row.gpt_action_verified_in_window)
        self.assertRegex(row.gpt_action_sha256,r"^[a-f0-9]{64}$")
        self.assertEqual(row.gpt_reason_code,"GPT_REGISTERED_ACTION")
        self.assertNotEqual(row.gpt_action_sha256,valid_action)
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/"verified.sqlite"
            append_shadow_comparison(db,row)
            self.assertEqual(read_shadow_comparisons(db),(row,))
            self.assertNotIn(synthetic_secret.encode("utf-8"),db.read_bytes())
            self.assertNotIn(valid_action.encode("utf-8"),db.read_bytes())

    def test_sqlite_boundary_rejects_direct_raw_reason_action_and_local_text(self):
        row=compared()
        secret="TEST_ONLY_FAKE_TOKEN_TEXT_DO_NOT_STORE"
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/"strict.sqlite"
            for fake in (
                replace(row,gpt_reason_code=secret),
                replace(row,gpt_reason_code="GPT_UNREGISTERED_ACTION"),
                replace(row,gpt_action_sha256=secret),
                replace(row,gpt_action_sha256="",gpt_action_verified_in_window=True),
                replace(row,local_reason_code=secret),
                replace(row,local_reason_code="LOCAL_NOT_RUN"),
                replace(row,match_id=secret+" "+secret),
            ):
                with self.subTest(fake=fake):
                    with self.assertRaisesRegex(ValueError,
                                                "INVALID_NONACTIONABLE_SHADOW_RECORD"):
                        append_shadow_comparison(db,fake)
            self.assertFalse(db.exists(),"privacy rejection must precede DB open")

    def test_sqlite_handles_released_after_reads_early_return_and_exception(self):
        """Windows must be able to delete SQLite immediately after each path.

        sqlite3's transaction context commits/rolls back but does NOT close.
        This test catches WinError 32 rather than silently relying on CPython
        GC to release a connection after the test has already returned.
        """
        row=compared()
        with TemporaryDirectory() as tmp:
            db=Path(tmp)/"release.sqlite"
            self.assertEqual(append_shadow_comparison(db,row),"RECORDED")
            self.assertEqual(read_shadow_comparisons(db),(row,))
            self.assertEqual(append_shadow_comparison(db,row),"ALREADY_RECORDED")
            with self.assertRaisesRegex(ValueError,"CONFLICTING_SAME_WINDOW_RECORD"):
                append_shadow_comparison(db,replace(row,local_elapsed_ms=999))
            self.assertEqual(read_shadow_comparisons(db),(row,))
            db.unlink()  # Must succeed on Windows with no SQLite file lock.
            self.assertFalse(db.exists())
            self.assertEqual(append_shadow_comparison(db,row),"RECORDED")
            self.assertEqual(read_shadow_comparisons(db),(row,))
            db.unlink()

    def test_stale_gpt_and_local_window_rejected_before_db(self):
        snap=frozen()
        changed=frozen(decision_window_id="new-window")
        advice=BoundGPTAdvice(snap.snapshot_sha256,"GPT_ADVICE",
                              snap.verified_action_ids[0])
        with self.assertRaisesRegex(ValueError,"STALE_OR_UNBOUND"):
            build_shadow_comparison(snap,changed,advice,local_diag(snap),
                                   local_elapsed_ms=3,remaining_ms_at_capture=100)
        with self.assertRaisesRegex(ValueError,"GPT_OR_TIMING_UNBOUND"):
            build_shadow_comparison(snap,snap,replace(advice,snapshot_sha256="0"*64),
                                   local_diag(snap),
                                   local_elapsed_ms=3,remaining_ms_at_capture=100)
        with self.assertRaisesRegex(ValueError,"LOCAL_DIAGNOSTIC"):
            build_shadow_comparison(snap,snap,advice,
                                   replace(local_diag(snap),ready_for_engine=True),
                                   local_elapsed_ms=3,remaining_ms_at_capture=100)
        with self.assertRaisesRegex(ValueError,"LOCAL_DIAGNOSTIC"):
            build_shadow_comparison(snap,snap,advice,
                                   replace(local_diag(snap),observed_snapshot_sha256="0"*64),
                                   local_elapsed_ms=3,remaining_ms_at_capture=100)

    def test_never_certify_unverified_gpt_move_or_local_pseudo_advice(self):
        s=frozen()
        gpt=BoundGPTAdvice(s.snapshot_sha256,"GPT_ADVICE","unregistered",elapsed_ms=13)
        row=build_shadow_comparison(s,s,gpt,None,local_elapsed_ms=12,
                                    remaining_ms_at_capture=0,
                                    recorded_at="2026-11-03T10:30:00+09:00")
        self.assertFalse(row.gpt_action_verified_in_window)
        self.assertEqual(row.gpt_action_sha256,"")
        self.assertEqual(row.gpt_reason_code,"GPT_UNREGISTERED_ACTION")
        self.assertFalse(row.input_deadline_met)
        self.assertEqual(row.local_status,"NO_RANKING")
        with self.assertRaisesRegex(ValueError,"ABSTAINED_GPT_HAS_ACTION"):
            build_shadow_comparison(s,s,replace(gpt,status="GPT_ABSTAIN"),
                                    None,local_elapsed_ms=1,
                                    remaining_ms_at_capture=3)


class ShadowQualityGateTests(unittest.TestCase):
    kinds=("immediate_ko","choice_lock_pp","mega_form","priority_speed",
           "processor_vs_safe_switch","setup","endgame_1v1","stale_revision")

    def cases(self, *, diagnostic=True):
        rows=[]
        oracles=[]
        for i,kind in enumerate(self.kinds):
            s=frozen(state_revision=i+1,decision_window_id="golden-"+str(i))
            row=compared(s,local=diagnostic,elapsed=10+i*10,
                         remaining=5 if i%2==0 else 0)
            rows.append(row)
            oracles.append(GoldenExpectation(
                "synthetic-"+kind,kind,row.match_id,row.decision_window_id,
                row.state_revision,row.snapshot_sha256,row.local_status))
        return tuple(rows),tuple(oracles)

    def test_eight_synthetic_goldens_are_not_live_quality_acceptance(self):
        rows,oracles=self.cases()
        result=audit_shadow_quality(rows,oracles)
        self.assertEqual(result.status,"SYNTHETIC_DIAGNOSTICS_AUDITED")
        self.assertEqual(result.sample_count,8)
        self.assertEqual(len(result.golden_covered),8)
        self.assertEqual(result.model_diagnostic_count,8)
        self.assertEqual(result.actionable_count,0)
        self.assertEqual(result.actionable_applicability_rate,0.0)
        self.assertEqual(result.model_diagnostic_coverage_rate,1.0)
        self.assertEqual(result.deadline_met_rate,0.5)
        self.assertEqual((result.p50_ms,result.p95_ms,result.p99_ms),(40,80,80))
        self.assertEqual(result.game_safety_result,"UNVERIFIED")
        self.assertEqual(result.real_game_latency_result,"UNVERIFIED")
        self.assertFalse(result.pass_phase2_quality_gate)

    def test_all_abstention_is_never_successful_actionability(self):
        rows,oracles=self.cases(diagnostic=False)
        result=audit_shadow_quality(rows,oracles)
        self.assertEqual(result.status,"SYNTHETIC_DIAGNOSTICS_AUDITED")
        self.assertEqual(result.abstention_rate,1.0)
        self.assertEqual(result.actionable_applicability_rate,0.0)
        self.assertEqual(result.actionable_count,0)
        self.assertFalse(result.pass_phase2_quality_gate)

    def test_missing_wrong_shas_duplicate_revisions_and_mismatched_goldens(self):
        rows,oracles=self.cases()
        r=audit_shadow_quality(rows,oracles[:-1])
        self.assertEqual(r.status,"INCOMPLETE_GOLDEN_COVERAGE")
        self.assertIn("stale_revision",r.missing_goldens)
        wrong=replace(oracles[0],snapshot_sha256="0"*64)
        r=audit_shadow_quality(rows,(wrong,*oracles[1:]))
        self.assertEqual(r.status,"GOLDEN_MISMATCH")
        self.assertTrue(r.golden_mismatches)
        self.assertFalse(r.pass_phase2_quality_gate)
        r=audit_shadow_quality((*rows,rows[0]),oracles)
        self.assertEqual(r.status,"INVALID_EVIDENCE")
        self.assertEqual(audit_shadow_quality((),()).status,"INVALID_EVIDENCE")


if __name__=="__main__":
    unittest.main()
