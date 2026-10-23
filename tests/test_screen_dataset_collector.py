"""Offline, deterministic staging and GUI tests: no real capture or uploads."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from champions_assist.app_config import CaptureConfiguration
from champions_assist.screen_dataset import (
    LabelledScreenDataset, UnlabelledScreenStaging, StagingLimitReached,
    ReviewBuffer, validate_session, MIB)
from champions_assist.screen_dataset_collector import (
    ScreenDatasetCollector, _ReadOnlyCollectorWorker, PAGE_SIZE)


class DatasetCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.dataset=LabelledScreenDataset(self.root/"screens")
        self.staging=UnlabelledScreenStaging(self.dataset.root)
        self.frame=np.full((70,110,3),170,dtype=np.uint8)

    def test_staged_image_is_not_labelled_until_human_confirmation(self):
        session="match20261023T111111_abc12345"
        staged=self.staging.save_unlabelled(self.frame,session)
        self.assertEqual(staged.suffix,".jpg")
        self.assertEqual(staged.parent.name,session)
        self.assertEqual(self.dataset.audit()["total"],0)
        self.assertEqual(self.staging.sessions(),[session])
        self.assertEqual(self.staging.session_files(session),[staged])
        labelled=self.staging.label(staged,"TEAM_SELECTION",self.dataset)
        self.assertFalse(staged.exists())
        self.assertEqual(labelled.suffix,".png")
        self.assertEqual(labelled.parent.name,"TEAM_SELECTION")
        self.assertEqual(self.dataset.audit()["counts"]["TEAM_SELECTION"],1)
        self.assertEqual(self.dataset.audit()["all_sessions"],1)

    def test_disk_quota_never_deletes_old_frame_and_no_silent_eviction(self):
        session="match20261023T111111_abc12345"
        staged=self.staging.save_unlabelled(self.frame,session)
        bytes_written=self.staging.used_bytes()
        tiny=UnlabelledScreenStaging(self.dataset.root,quota_bytes=bytes_written+100)
        self.assertEqual(tiny.used_bytes(),bytes_written)
        with self.assertRaisesRegex(StagingLimitReached,"STAGING_QUOTA_REACHED"):
            tiny.save_unlabelled(self.frame,session)
        self.assertTrue(staged.exists())
        self.assertEqual(len(tiny.session_files(session)),1)
        tiny.remove_staged(staged)
        self.assertFalse(staged.exists())
        self.assertEqual(tiny.used_bytes(),0)

    def test_staged_dataset_rejects_path_escape_and_invalid_labels(self):
        session="match20261023T111111_abc12345"
        staged=self.staging.save_unlabelled(self.frame,session)
        with self.assertRaises(ValueError):
            self.staging.label(staged,"WRONG_LABEL",self.dataset)
        with self.assertRaises(ValueError):
            self.staging.remove_staged(self.root/"not-my-file.jpg")
        with self.assertRaises(ValueError):
            validate_session("../other")
        self.assertTrue(staged.exists())
        self.staging.label(staged,"UNKNOWN",self.dataset)
        item=next((self.dataset.root/"UNKNOWN").glob("*.png"))
        moved=self.dataset.relabel(item,"MOVE_MENU")
        self.assertEqual(moved.parent.name,"MOVE_MENU")

    def test_recordings_are_separate_sessions_and_old_labels_unchanged(self):
        a="match20261023T111111_abc12345"
        b="match20261023T111112_abc12345"
        first=self.staging.save_unlabelled(self.frame,a)
        other=self.staging.save_unlabelled(self.frame,b)
        self.assertEqual(set(self.staging.sessions()),{a,b})
        out=self.staging.label(first,"RESULT",self.dataset)
        self.assertEqual(self.staging.session_files(a),[])
        self.assertEqual(self.staging.session_files(b),[other])
        self.staging.save_unlabelled(self.frame,a)
        self.assertTrue(out.exists())
        self.assertEqual(self.dataset.audit()["counts"]["RESULT"],1)

    def test_training_script_ignores_unlabelled_folder(self):
        from scripts.train_screen_classifier import collect
        session="match20261023T111111_abc12345"
        self.staging.save_unlabelled(self.frame,session)
        with self.assertRaisesRegex(ValueError,"Missing class directory"):
            collect(self.dataset.root)
        self.assertEqual(self.dataset.audit()["total"],0)


class DatasetCollectorGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        import gc
        # QApplication is shared by subsequent GUI test modules.
        cls.app.closeAllWindows()
        cls.app.processEvents()
        cls.app=None
        gc.collect()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.gui=ScreenDatasetCollector(
            config_path=self.root/"config"/"settings.json",
            dataset_root=self.root/"screens",
            capture=lambda cfg:np.full((85,155,3),145,dtype=np.uint8))
        self.image=np.full((85,155,3),145,dtype=np.uint8)

    def tearDown(self):
        import gc
        gui=self.gui
        gui.close()
        gui.deleteLater()
        self.gui=None
        self.app.processEvents()
        del gui
        gc.collect()

    def test_gui_has_5fps_default_and_requires_disk_consent(self):
        self.assertEqual(self.gui.fps_box.currentData(),5)
        self.assertEqual(self.gui.quota_box.currentData(),2)
        self.assertFalse(self.gui.consent.isChecked())
        self.assertFalse(self.gui.dataset.root.exists())
        self.gui._start(one_shot=True)
        self.assertIsNone(self.gui._thread)
        self.assertIn("同意",self.gui.status.text())
        self.assertFalse(self.gui.dataset.root.exists())

    def test_worker_one_shot_creates_single_unlabelled_file_offline(self):
        config=CaptureConfiguration(
            backend="WINDOWS_NATIVE",selected_window_identity={"hwnd":123},
            game_viewport_normalized=(0,0,1,1))
        before=self.gui.dataset.audit()["total"]
        worker=_ReadOnlyCollectorWorker(
            config,session_id=self.gui.session_id,staging=self.gui.staging,
            one_shot=True,fps=10,capture=lambda _:self.image)
        observed=[]
        worker.progress.connect(observed.append)
        worker.run()
        self.assertEqual(worker.saved,1)
        self.assertGreaterEqual(observed[-1]["bytes"],1)
        self.assertEqual(self.gui.dataset.audit()["total"],before)
        self.assertEqual(len(self.gui.staging.session_files(self.gui.session_id)),1)

    def test_staged_review_is_paginated_and_labelling_requires_confirmation(self):
        files=[self.gui.staging.save_unlabelled(self.image,self.gui.session_id)
               for _ in range(PAGE_SIZE+75)]
        self.gui._refresh_review_sessions(select=self.gui.session_id)
        self.assertEqual(self.gui.review_list.count(),PAGE_SIZE)
        self.assertTrue(self.gui.next_button.isEnabled())
        self.gui._next_page()
        self.assertEqual(self.gui.review_list.count(),75)
        self.gui.review_list.selectAll()
        self.gui._save_selected()
        self.assertEqual(self.gui.dataset.audit()["total"],0)
        self.gui.label_box.setCurrentIndex(
            self.gui.label_box.findData("TEAM_SELECTION"))
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.No):
            self.gui._save_selected()
        self.assertEqual(self.gui.dataset.audit()["total"],0)
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.Yes):
            self.gui._save_selected()
        self.assertEqual(self.gui.dataset.audit()["counts"]["TEAM_SELECTION"],75)
        self.assertEqual(len(self.gui.staging.session_files(self.gui.session_id)),PAGE_SIZE)
        self.assertEqual(self.gui.review_list.count(),PAGE_SIZE)
        self.assertTrue(all(f.exists() for f in files[:PAGE_SIZE]))

    def test_new_session_does_not_destroy_unlabelled_or_labelled_files(self):
        before=self.gui.session_id
        first=self.gui.staging.save_unlabelled(self.image,before)
        self.gui._new_session()
        self.assertNotEqual(self.gui.session_id,before)
        self.assertTrue(first.exists())
        self.gui._refresh_review_sessions(select=before)
        self.assertEqual(self.gui.review_session_box.currentData(),before)
        self.assertEqual(self.gui.review_list.count(),1)

    def test_group_label_choice_is_directly_below_thumbnails_and_separate_from_manual(self):
        parent_layout=self.gui.centralWidget().widget().layout()
        thumbnails_index=parent_layout.indexOf(self.gui.auto_frames_list)
        self.assertGreaterEqual(thumbnails_index,0)
        # Label selector is the immediate next row, not below the GPU,
        # pagination and ordinary manual-save controls.
        adjacent_row=parent_layout.itemAt(thumbnails_index+1).layout()
        self.assertIsNotNone(adjacent_row)
        self.assertGreaterEqual(adjacent_row.indexOf(self.gui.auto_label_box),0)
        self.assertGreater(parent_layout.indexOf(self.gui.label_box),thumbnails_index+1)
        self.assertIsNone(self.gui.auto_label_box.currentData())
        self.gui.label_box.setCurrentIndex(self.gui.label_box.findData("RESULT"))
        self.assertIsNone(self.gui.auto_label_box.currentData())
        self.gui.auto_label_box.setCurrentIndex(
            self.gui.auto_label_box.findData("PARTY_MENU"))
        self.assertEqual(self.gui.auto_label_box.currentData(),"PARTY_MENU")
        self.assertEqual(self.gui.label_box.currentData(),"RESULT")

    def test_manual_label_does_not_accidentally_correct_an_auto_frame(self):
        from champions_assist.screen_auto_label import AutoLabelGroup
        staged=self.gui.staging.save_unlabelled(self.image,self.gui.session_id)
        group=AutoLabelGroup((staged,),"MOVE_MENU",.99,.90,(staged,),"AI候補")
        self.gui._auto_on_proposals([group])
        self.gui.auto_list.setCurrentRow(0)
        self.gui.auto_frames_list.setCurrentRow(0)
        self.gui.label_box.setCurrentIndex(self.gui.label_box.findData("RESULT"))
        self.gui._correct_selected_auto_frames()
        self.assertNotIn(staged,self.gui._auto_frame_overrides)
        self.gui.auto_label_box.setCurrentIndex(
            self.gui.auto_label_box.findData("PARTY_MENU"))
        self.gui._correct_selected_auto_frames()
        self.assertEqual(self.gui._auto_frame_overrides[staged],"PARTY_MENU")
        # Ordinary manual label remains unchanged for its separate workflow.
        self.assertEqual(self.gui.label_box.currentData(),"RESULT")

    def test_png_save_keeps_other_groups_predictions_corrections_and_review(self):
        from champions_assist.screen_auto_label import AutoLabelGroup, AutoLabelFrame
        frames=[self.gui.staging.save_unlabelled(self.image,self.gui.session_id)
                for _ in range(6)]
        self.gui._refresh_review_sessions(select=self.gui.session_id)
        def group(paths,label):
            return AutoLabelGroup(
                tuple(paths),label,.97,.91,
                (paths[0],paths[len(paths)//2],paths[-1]),"AI候補",
                tuple(AutoLabelFrame(p,label,.97,.91,()) for p in paths))
        self.gui._auto_on_proposals([
            group(frames[:3],"MOVE_MENU"),
            group(frames[3:],"TEAM_SELECTION")])
        # Prepare the second group with a corrected image and review it first.
        self.gui.auto_list.setCurrentRow(1)
        self.gui.auto_frames_list.setCurrentRow(1)
        self.gui.auto_label_box.setCurrentIndex(self.gui.auto_label_box.findData("RESULT"))
        self.gui._correct_selected_auto_frames()
        self.gui.auto_review_confirm.setChecked(True)
        self.assertEqual(self.gui._auto_frame_overrides[frames[4]],"RESULT")
        self.assertIn(1,self.gui._auto_reviewed_groups)

        # Save only the first group. The other three images must NOT be
        # re-inferred, reset, or relabelled, even after a manual file refresh.
        self.gui.auto_list.setCurrentRow(0)
        self.gui.auto_review_confirm.setChecked(True)
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.Yes):
            self.gui._approve_auto_groups(use_suggestion=True)
        self.assertEqual(self.gui.dataset.audit()["counts"]["MOVE_MENU"],3)
        self.assertEqual(self.gui.auto_list.count(),1)
        self.assertEqual(self.gui.auto_frames_list.count(),3)
        self.assertEqual(self.gui._auto_groups[0].candidate,"TEAM_SELECTION")
        self.assertEqual(self.gui._auto_groups[0].frame_details[1].predicted_label,
                         "TEAM_SELECTION")
        self.assertEqual(self.gui._auto_frame_overrides[frames[4]],"RESULT")
        self.assertTrue(self.gui.auto_review_confirm.isChecked())
        self.assertTrue(all(p.exists() for p in frames[3:]))
        self.gui._refresh_review_sessions(select=self.gui.session_id)
        self.assertEqual(self.gui.auto_list.count(),1)
        self.assertEqual(self.gui._auto_frame_overrides[frames[4]],"RESULT")
        self.assertTrue(self.gui.auto_review_confirm.isChecked())
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.Yes):
            self.gui._approve_auto_groups(use_suggestion=True)
        counts=self.gui.dataset.audit()["counts"]
        self.assertEqual(counts["MOVE_MENU"],3)
        self.assertEqual(counts["TEAM_SELECTION"],2)
        self.assertEqual(counts["RESULT"],1)
        self.assertEqual(self.gui.auto_list.count(),0)

    def test_partial_png_save_keeps_unchanged_frames_and_clears_only_edited_group_review(self):
        from champions_assist.screen_auto_label import AutoLabelGroup
        frames=[self.gui.staging.save_unlabelled(self.image,self.gui.session_id)
                for _ in range(4)]
        self.gui._refresh_review_sessions(select=self.gui.session_id)
        self.gui._auto_on_proposals([
            AutoLabelGroup(tuple(frames),"MOVE_MENU",.98,.94,
                           (frames[0],frames[2],frames[3]),"AI候補")])
        self.gui.auto_list.setCurrentRow(0)
        self.gui.auto_review_confirm.setChecked(True)
        original_label=self.gui.staging.label
        successes=[]
        def fail_on_second(path,label,dataset):
            if successes:
                raise OSError("simulated disk error")
            result=original_label(path,label,dataset)
            successes.append(path)
            return result
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.Yes):
            with patch.object(self.gui.staging,"label",side_effect=fail_on_second):
                self.gui._approve_auto_groups(use_suggestion=True)
        self.assertEqual(self.gui.dataset.audit()["counts"]["MOVE_MENU"],1)
        self.assertEqual(self.gui.auto_list.count(),1)
        self.assertEqual(self.gui.auto_frames_list.count(),3)
        self.assertEqual(self.gui._auto_groups[0].files,tuple(frames[1:]))
        self.assertFalse(self.gui.auto_review_confirm.isChecked())
        self.assertTrue(all(p.exists() for p in frames[1:]))

    def test_manual_png_save_preserves_auto_label_cache_for_remaining_groups(self):
        from champions_assist.screen_auto_label import AutoLabelGroup
        frames=[self.gui.staging.save_unlabelled(self.image,self.gui.session_id)
                for _ in range(4)]
        self.gui._refresh_review_sessions(select=self.gui.session_id)
        self.gui._auto_on_proposals([
            AutoLabelGroup(tuple(frames[:2]),"MOVE_MENU",.96,.9,
                           (frames[0],frames[1]),"AI候補"),
            AutoLabelGroup(tuple(frames[2:]),"RESULT",.97,.92,
                           (frames[2],frames[3]),"AI候補")])
        self.gui._refresh_review_sessions(select=self.gui.session_id)
        # Save the first frame through the old manual path.
        self.gui.label_box.setCurrentIndex(self.gui.label_box.findData("UNKNOWN"))
        row=next(i for i in range(self.gui.review_list.count())
                 if self.gui.review_list.item(i).data(
                     Qt.ItemDataRole.UserRole)==str(frames[0]))
        self.gui.review_list.setCurrentRow(row)
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.Yes):
            self.gui._save_selected()
        self.assertEqual(self.gui.dataset.audit()["counts"]["UNKNOWN"],1)
        self.assertEqual([g.count for g in self.gui._auto_groups],[1,2])
        self.assertEqual(self.gui._auto_groups[1].candidate,"RESULT")
        self.assertTrue(all(p.exists() for p in frames[1:]))

    def test_auto_groups_require_explicit_human_confirmation(self):
        from champions_assist.screen_auto_label import analyze_staged_session
        staged=[self.gui.staging.save_unlabelled(self.image,self.gui.session_id)
                for _ in range(6)]
        groups=analyze_staged_session(self.gui.staging.session_files(self.gui.session_id))
        self.gui._auto_on_proposals(groups)
        self.assertEqual(self.gui.auto_list.count(),1)
        self.gui.auto_list.setCurrentRow(0)
        self.assertEqual(self.gui.dataset.audit()["total"],0)
        self.gui._approve_auto_groups(use_suggestion=True)
        self.assertEqual(self.gui.dataset.audit()["total"],0)
        self.assertEqual(self.gui.auto_frames_list.count(),6)
        self.assertFalse(self.gui.auto_review_confirm.isChecked())
        self.gui.auto_label_box.setCurrentIndex(self.gui.auto_label_box.findData("MOVE_MENU"))
        self.gui.auto_review_confirm.setChecked(True)
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.No):
            self.gui._approve_auto_groups(use_suggestion=False)
        self.assertEqual(self.gui.dataset.audit()["total"],0)
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.Yes):
            self.gui._approve_auto_groups(use_suggestion=False)
        self.assertEqual(self.gui.dataset.audit()["counts"]["MOVE_MENU"],6)
        self.assertTrue(all(not f.exists() for f in staged))
        self.assertEqual(self.gui.auto_list.count(),0)

    def test_ai_suggestion_approval_without_confirmation_keeps_staging(self):
        from champions_assist.screen_auto_label import AutoLabelGroup
        staged=self.gui.staging.save_unlabelled(self.image,self.gui.session_id)
        group=AutoLabelGroup((staged,),"TEAM_SELECTION",.99,.93,(staged,),
                             "AI候補・代表画像を確認")
        self.gui._auto_on_proposals([group])
        self.gui.auto_list.setCurrentRow(0)
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.No):
            self.gui._approve_auto_groups(use_suggestion=True)
        self.assertTrue(staged.exists())
        self.assertEqual(self.gui.dataset.audit()["total"],0)
        self.gui.auto_review_confirm.setChecked(True)
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.Yes):
            self.gui._approve_auto_groups(use_suggestion=True)
        self.assertEqual(self.gui.dataset.audit()["counts"]["TEAM_SELECTION"],1)

    def test_middle_frame_can_be_corrected_without_mislabeling_group(self):
        from champions_assist.screen_auto_label import AutoLabelGroup
        staged=[self.gui.staging.save_unlabelled(self.image,self.gui.session_id)
                for _ in range(6)]
        group=AutoLabelGroup(tuple(staged),"TEAM_SELECTION",.97,.85,
                             (staged[0],staged[3],staged[-1]),"AI候補・代表画像を確認")
        self.gui._auto_on_proposals([group])
        self.gui.auto_list.setCurrentRow(0)
        self.assertEqual(self.gui.auto_frames_list.count(),6)
        self.gui.auto_frames_list.setCurrentRow(2)
        self.gui.auto_label_box.setCurrentIndex(self.gui.auto_label_box.findData("PARTY_MENU"))
        self.gui._correct_selected_auto_frames()
        self.assertEqual(self.gui._auto_frame_overrides[staged[2]],"PARTY_MENU")
        self.assertFalse(self.gui.auto_review_confirm.isChecked())
        self.gui.auto_frames_list.setCurrentRow(4)
        self.gui._exclude_selected_auto_frames()
        self.assertIn(staged[4],self.gui._auto_frame_exclusions)
        self.gui.auto_review_confirm.setChecked(True)
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.Yes):
            self.gui._approve_auto_groups(use_suggestion=True)
        counts=self.gui.dataset.audit()["counts"]
        self.assertEqual(counts["TEAM_SELECTION"],4)
        self.assertEqual(counts["PARTY_MENU"],1)
        self.assertEqual(sum(counts.values()),5)
        self.assertTrue(staged[4].exists())
        self.assertTrue(all(not p.exists() for i,p in enumerate(staged) if i!=4))

    def test_changes_reset_review_and_block_accidental_save(self):
        from champions_assist.screen_auto_label import AutoLabelGroup
        files=[self.gui.staging.save_unlabelled(self.image,self.gui.session_id)
               for _ in range(3)]
        group=AutoLabelGroup(tuple(files),"MOVE_MENU",.99,.90,
                             (files[0],files[1],files[-1]),"AI候補")
        self.gui._auto_on_proposals([group])
        self.gui.auto_list.setCurrentRow(0)
        self.gui.auto_review_confirm.setChecked(True)
        self.gui.auto_frames_list.setCurrentRow(1)
        self.gui._exclude_selected_auto_frames()
        self.assertFalse(self.gui.auto_review_confirm.isChecked())
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.Yes) as confirm:
            self.gui._approve_auto_groups(use_suggestion=True)
        confirm.assert_not_called()
        self.assertEqual(self.gui.dataset.audit()["total"],0)
        self.assertTrue(all(p.exists() for p in files))
        self.gui.auto_frames_list.setCurrentRow(1)
        self.gui._reset_selected_auto_frames()
        self.assertFalse(self.gui._auto_frame_exclusions)
        self.assertEqual(self.gui.auto_frames_list.count(),3)

    @unittest.skipUnless(os.name=="nt","WSL process is Windows-native")
    def test_gpu_retrain_requires_explicit_user_confirmation(self):
        with patch("champions_assist.screen_dataset_collector.QInputDialog.getText",
                   return_value=("/home/test/pokemon_champions_assistant",True)):
            with patch.object(QMessageBox,"question",
                              return_value=QMessageBox.StandardButton.No):
                with patch("champions_assist.screen_dataset_collector.QProcess") as process:
                    self.gui._start_gpu_retrain()
                    process.assert_not_called()
                    self.assertIsNone(self.gui._gpu_process)

    def test_selected_file_discard_needs_confirmation(self):
        first=self.gui.staging.save_unlabelled(self.image,self.gui.session_id)
        self.gui._refresh_review_sessions(select=self.gui.session_id)
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.No):
            self.gui._discard_selected()
        self.assertTrue(first.exists())
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.Yes):
            self.gui._discard_selected()
        self.assertFalse(first.exists())
        self.assertEqual(self.gui.dataset.audit()["total"],0)


if __name__=="__main__":
    unittest.main()
