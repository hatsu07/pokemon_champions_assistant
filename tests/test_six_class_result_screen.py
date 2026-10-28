"""Six-class dataset migration and stable result evidence (no real screen uploads)."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from champions_assist.screen_classifier import (
    LABELS, LABEL_SCHEMA_VERSION, ClassifiedMenuGate, ScreenPrediction)
from scripts.prepare_six_class_screen_dataset import build_plan, main
from champions_assist.match_replay import (
    run_replay_screen_result, select_screen_result_evidence)


class SixClassMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source=self.root/"legacy_seven"
        self.dest=self.root/"new_six"
        self.source.mkdir()

    def write(self,folder,name):
        directory=self.source/folder
        directory.mkdir(exist_ok=True)
        target=directory/name
        Image.new("RGB",(16,16),(80,20,30)).save(target)
        return target

    def test_exact_six_contract(self):
        self.assertEqual(LABEL_SCHEMA_VERSION,3)
        self.assertEqual(LABELS,("TEAM_SELECTION","TURN_ACTION",
                                "SWITCH_SELECTION","BATTLE_EVENT",
                                "RESULT_SCREEN","OTHER"))
        self.assertNotIn("UNKNOWN",LABELS)

    def test_legacy_result_is_copied_not_labelled_other_or_overwritten(self):
        result=self.write("RESULT","session1__result.png")
        waiting=self.write("MATCHMAKING","session1__wait.png")
        needs_review=self.write("UNKNOWN","session1__unknown.png")
        plan,manual=build_plan(self.source,self.dest)
        self.assertEqual(len(plan),2)
        self.assertEqual(manual["UNKNOWN"],1)
        self.assertFalse(self.dest.exists())
        self.assertEqual(main(["--source",str(self.source),
                               "--destination",str(self.dest),"--apply"]),0)
        self.assertTrue((self.dest/"RESULT_SCREEN"/result.name).exists())
        self.assertTrue((self.dest/"OTHER"/waiting.name).exists())
        self.assertFalse((self.dest/"OTHER"/result.name).exists())
        self.assertTrue(result.exists() and waiting.exists() and needs_review.exists())
        self.assertFalse((self.dest/"UNKNOWN").exists())
        self.assertEqual(main(["--source",str(self.source),
                               "--destination",str(self.dest),"--apply"]),1)

    def test_uncertain_event_never_silently_labelled(self):
        old=self.write("BATTLE_EVENT","session1__event.png")
        plan,manual=build_plan(self.source,self.dest)
        self.assertEqual(plan,[])
        self.assertEqual(manual["BATTLE_EVENT"],1)
        self.assertTrue(old.exists())

    def test_two_stable_high_confidence_result_frames_only(self):
        gate=ClassifiedMenuGate(stable_frames=2)
        poor=ScreenPrediction("RESULT_SCREEN",.85,.40,1.)
        clear=ScreenPrediction("RESULT_SCREEN",.99,.96,1.)
        self.assertIsNone(gate.observe(poor,"BATTLE"))
        self.assertIsNone(gate.observe(clear,"BATTLE"))
        self.assertEqual(gate.observe(clear,"BATTLE"),"RESULT_SCREEN")
        self.assertIsNone(gate.observe(clear,"BATTLE"))
        self.assertIsNone(gate.observe(clear,"SELECTION"))


class ResultScreenAsyncGateTests(unittest.TestCase):
    def fixture(self, *, status="QUEUED", previous=None):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from champions_assist.match_replay_dialog import MatchReplayDialog
        candidate={
            "frame":"capture-stable","time":20.0,
            "ocr_status":status,"ocr_rows":previous or [],
            "detected_at":-99999.0,
        }
        panel=SimpleNamespace(
            _result_screen_candidate=candidate,
            _result_screen_sent=False,
            state={"phase":"BATTLE"},
            _auto_running=True,
            _shutdown_intent=None,
            _local_event_reader=SimpleNamespace(enabled=True),
            _drain_event_candidates=MagicMock(return_value=[]),
            _thread=None,
            _trace_event=MagicMock(),
            _set_ai_status=MagicMock(),
            store_root=Path("."),
            model="test",
            reasoning_effort="low",
            assets=None,
            _cancel_event=None,
            _pending_result_messages=None,
            _pending_batch=None,
            _pending_screen_label=None,
            _active_job=None,
            _active_request_started=None,
        )
        panel._attempt_result_screen_verification=lambda: (
            MatchReplayDialog._attempt_result_screen_verification(panel))
        return panel

    def test_no_elapsed_timeout_and_no_gpt_before_ocr_completion(self):
        from unittest.mock import patch
        panel=self.fixture()
        # An arbitrarily old result-screen candidate must NOT cause the
        # post-battle OCR to be skipped for having exceeded 1.5 or 3 seconds.
        with patch("champions_assist.match_replay_dialog.QTimer.singleShot") as timer, \
             patch("champions_assist.match_replay_dialog._ReplayWorker") as model:
            panel._attempt_result_screen_verification()
            self.assertFalse(panel._result_screen_sent)
            self.assertIsNone(panel._thread)
            timer.assert_called_once()
            self.assertEqual(timer.call_args.args[0],250)
            model.assert_not_called()
        row={"frame_id":"capture-stable","raw_text":"対戦に勝利しました",
             "field":"screen_text.battle_message",
             "observed_at":20.1,"confidence":.95}
        panel._result_screen_candidate["ocr_status"]="COMPLETE"
        panel._result_screen_candidate["ocr_rows"]=[row]
        with patch("champions_assist.match_replay_dialog.QThread") as thread, \
             patch("champions_assist.match_replay_dialog._ReplayWorker") as model:
            panel._attempt_result_screen_verification()
            self.assertTrue(panel._result_screen_sent)
            self.assertEqual(model.call_count,1)
            self.assertEqual(model.call_args.kwargs["job"],"result_screen")
            self.assertEqual(model.call_args.kwargs["local_field_candidates"],[row])
            self.assertEqual(model.call_args.kwargs["battle_seconds"],120.0)
            thread.return_value.start.assert_called_once()

    def test_ocr_completed_with_no_words_does_not_call_gpt(self):
        from unittest.mock import patch
        panel=self.fixture(status="COMPLETE")
        with patch("champions_assist.match_replay_dialog._ReplayWorker") as model:
            panel._attempt_result_screen_verification()
            model.assert_not_called()
        self.assertTrue(panel._result_screen_sent)
        panel._set_ai_status.assert_called_once()
        self.assertTrue(any(
            call.args[0]=="RESULT_SCREEN_OCR_EMPTY"
            for call in panel._trace_event.call_args_list))

    def test_worker_failure_allows_manual_confirmation_without_timer(self):
        from unittest.mock import patch
        panel=self.fixture(status="QUEUED")
        panel._local_event_reader.enabled=False
        with patch("champions_assist.match_replay_dialog.QTimer.singleShot") as timer, \
             patch("champions_assist.match_replay_dialog._ReplayWorker") as model:
            panel._attempt_result_screen_verification()
            self.assertEqual(panel._result_screen_candidate["ocr_status"],"FAILED")
            timer.assert_not_called()
            model.assert_not_called()
        panel._set_ai_status.assert_called_once()


class ScreenResultEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.state={"phase":"BATTLE","our_active":"ゲンガー",
                    "opponent_active":"マスカーニャ","history":[]}

    def row(self,frame,text,t):
        return {"frame_id":frame,"field":"screen_text.battle_message",
                "raw_text":text,"observed_at":t,"confidence":.95}

    def call(self,reply,rows):
        with patch("champions_assist.match_replay._stream_json",
                   return_value=reply) as model:
            changed,report=run_replay_screen_result(
                state=self.state,screen_frame="capture-202",
                screen_time=20.0,local_field_candidates=rows,
                access_token="mock",model="test",
                client_factory=lambda **kwargs:object())
        return changed,report,model

    def test_current_screen_plus_last_ten_events_with_real_frame_ids(self):
        rows=[self.row(f"capture-{i}",f"event {i}",i) for i in range(1,19)]
        rows.append(self.row("capture-202","対戦に勝利しました",20.1))
        chosen=select_screen_result_evidence(rows,screen_frame="capture-202",
                                             screen_time=20.)
        self.assertEqual(len(chosen),10)
        self.assertEqual(chosen[-1]["frame_id"],"capture-202")
        self.assertNotIn("capture-1",{r["frame_id"] for r in chosen})
        rows.append(self.row("capture-999","対戦に敗北しました",21.0))
        self.assertNotIn("capture-999",{
            r["frame_id"] for r in select_screen_result_evidence(
                rows,screen_frame="capture-202",screen_time=20.)})

    def test_grounded_win_ends_only_after_result_screen_proof(self):
        row=self.row("capture-201","対戦に勝利しました",19.)
        changed,report,model=self.call(
            {"result":"WIN","evidence":"勝利しました",
             "evidence_frame_id":"capture-201"},[row])
        self.assertEqual(changed["phase"],"ENDED")
        self.assertEqual(changed["result"],"WIN")
        self.assertEqual(report["screen"],"RESULT_SCREEN")
        self.assertEqual(report["images_sent"],0)
        self.assertEqual(model.call_count,1)

    def test_grounded_loss_from_previous_event(self):
        row=self.row("capture-200","対戦に敗北しました",18.0)
        changed,report,_=self.call(
            {"result":"LOSS","evidence":"敗北しました",
             "evidence_frame_id":"capture-200"},[row])
        self.assertEqual(changed["phase"],"ENDED")
        self.assertEqual(changed["result"],"LOSS")
        self.assertEqual(report["evidence_frame_id"],"capture-200")

    def test_future_message_is_not_acceptable_as_result_proof(self):
        changed,report,_=self.call(
            {"result":"WIN","evidence":"勝利しました",
             "evidence_frame_id":"capture-999"},
            [self.row("capture-999","対戦に勝利しました",22.0)])
        self.assertEqual(report["reason"],"NO_RESULT_OCR_EVIDENCE")
        self.assertEqual(changed["phase"],"BATTLE")

    def test_faint_or_fabricated_frame_is_not_match_end(self):
        rows=[self.row("capture-201","ゲンガーは倒れた",19.)]
        for reply in (
            {"result":"WIN","evidence":"ゲンガーは倒れた",
             "evidence_frame_id":"capture-201"},
            {"result":"LOSS","evidence":"敗北しました",
             "evidence_frame_id":"not-sent"},
        ):
            changed,report,_=self.call(reply,rows)
            self.assertEqual(changed["phase"],"BATTLE")
            self.assertEqual(report["status"],"RESULT_UNCONFIRMED")

    def test_explicit_opposite_outcome_quote_is_rejected(self):
        changed,report,_=self.call(
            {"result":"WIN","evidence":"敗北しました",
             "evidence_frame_id":"capture-201"},
            [self.row("capture-201","対戦に敗北しました",19.)])
        self.assertEqual(changed["phase"],"BATTLE")
        self.assertEqual(report["status"],"RESULT_UNCONFIRMED")
        changed,report,_=self.call(
            {"result":"LOSS","evidence":"勝利しました",
             "evidence_frame_id":"capture-201"},
            [self.row("capture-201","対戦に勝利しました",19.)])
        self.assertEqual(changed["phase"],"BATTLE")
        self.assertEqual(report["status"],"RESULT_UNCONFIRMED")

    def test_unknown_or_missing_evidence_preserves_match(self):
        rows=[self.row("capture-201","とてもこうかはばつぐん",19.)]
        changed,report,_=self.call(
            {"result":"UNKNOWN","evidence":"",
             "evidence_frame_id":""},rows)
        self.assertEqual(changed["phase"],"BATTLE")
        self.assertEqual(report["status"],"RESULT_UNCONFIRMED")
        changed,report,model=self.call(
            {"result":"WIN","evidence":"勝利",
             "evidence_frame_id":"capture-201"},[])
        self.assertEqual(report["reason"],"NO_RESULT_OCR_EVIDENCE")
        model.assert_not_called()

    def test_low_confidence_result_ocr_does_not_auto_end(self):
        row=self.row("capture-201","対戦に勝利しました",19.)
        row["confidence"]=0.40
        changed,report,_=self.call(
            {"result":"WIN","evidence":"勝利しました",
             "evidence_frame_id":"capture-201"},[row])
        self.assertEqual(changed["phase"],"BATTLE")
        self.assertEqual(report["status"],"RESULT_UNCONFIRMED")

    def test_result_screen_only_never_confirms_without_result_phrase(self):
        changed,report,_=self.call(
            {"result":"WIN","evidence":"ゲンガー",
             "evidence_frame_id":"capture-202"},
            [self.row("capture-202","ゲンガー",20.1)])
        self.assertEqual(changed["phase"],"BATTLE")
        self.assertEqual(report["status"],"RESULT_UNCONFIRMED")


if __name__=="__main__":
    unittest.main()
