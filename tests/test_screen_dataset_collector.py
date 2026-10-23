"""Offline, deterministic screen dataset review tests (no real capture/AI)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication, QMessageBox

from champions_assist.app_config import CaptureConfiguration
from champions_assist.screen_dataset import (
    TrainingFrame, LabelledScreenDataset, ReviewBuffer, validate_session)
from champions_assist.screen_dataset_collector import (
    ScreenDatasetCollector, _ReadOnlyCollectorWorker)


class DatasetCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=LabelledScreenDataset(self.root/"screens")

    def test_no_disk_write_until_explicit_label(self):
        queue=ReviewBuffer(max_frames=2,max_edge=640)
        sample=queue.append(np.full((800,1300,3),100,dtype=np.uint8))
        self.assertFalse(self.store.root.exists())
        self.assertLessEqual(max(sample.image.shape[:2]),640)
        path=self.store.save(sample,"TEAM_SELECTION")
        self.assertTrue(path.is_file())
        self.assertIn(sample.session_id+"__",path.name)
        self.assertEqual(path.parent.name,"TEAM_SELECTION")
        data=self.store.audit()
        self.assertEqual(data["counts"]["TEAM_SELECTION"],1)
        self.assertEqual(data["all_sessions"],1)
        with self.assertRaises(FileExistsError):
            self.store.save(sample,"TEAM_SELECTION")
        queue.remove({sample.id})
        self.assertEqual(queue.frames,[])

    def test_correct_human_labels_and_reject_outside_dataset_paths(self):
        queue=ReviewBuffer()
        saved=self.store.save(queue.append(np.zeros((30,70,3),dtype=np.uint8)),"UNKNOWN")
        moved=self.store.relabel(saved,"MOVE_MENU")
        self.assertFalse(saved.exists())
        self.assertTrue(moved.exists())
        self.assertEqual(self.store.audit()["counts"]["MOVE_MENU"],1)
        with self.assertRaises(ValueError):
            self.store.relabel(self.root/"outside.png","TEAM_SELECTION")
        with self.assertRaises(ValueError):
            self.store.save(queue.append(np.zeros((10,10,3),dtype=np.uint8)),"BAD_LABEL")
        with self.assertRaises(ValueError):
            validate_session("../../other")

    def test_memory_bounded_and_session_ids_keep_matches_separate(self):
        queue=ReviewBuffer(max_frames=1)
        first=queue.append(np.zeros((40,70,3),dtype=np.uint8))
        with self.assertRaises(BufferError):
            queue.append(np.ones((40,70,3),dtype=np.uint8))
        self.assertEqual(len(queue.frames),1)
        self.assertEqual(queue.dropped,1)
        session=first.session_id
        queue.new_session()
        self.assertNotEqual(queue.session_id,session)
        self.assertEqual(len(queue.frames),0)
        second=queue.append(np.ones((40,70,3),dtype=np.uint8))
        self.store.save(first,"TEAM_SELECTION")
        self.store.save(second,"TEAM_SELECTION")
        self.assertEqual(self.store.audit()["session_counts"]["TEAM_SELECTION"],2)

    def test_dataset_supports_training_script_session_split(self):
        from scripts.train_screen_classifier import split_by_session
        rows=[]
        for sid in range(20):
            for label in range(7):
                rows.append((Path(f"sample{sid}_{label}.png"),label,f"match{sid}"))
        train,valid,held=split_by_session(rows)
        self.assertTrue(held)
        self.assertTrue({x[2] for x in train}.isdisjoint({x[2] for x in valid}))


class DatasetCollectorGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.gui=ScreenDatasetCollector(config_path=root/"config"/"settings.json",
                                        dataset_root=root/"screens",
                                        capture=lambda cfg: np.full((120,200,3),150,dtype=np.uint8))
        self.addCleanup(self.gui.close)

    def test_gui_does_not_capture_or_save_on_startup_or_without_consent(self):
        self.assertFalse(self.gui.dataset.root.exists())
        self.assertFalse(self.gui.consent.isChecked())
        self.gui._start(one_shot=True)
        self.assertIsNone(self.gui._thread)
        self.assertIn("明示的",self.gui.status.text())

    def test_preview_and_multiselect_save_requires_human_confirmed_label(self):
        self.gui._on_frame(np.full((75,130,3),190,dtype=np.uint8))
        self.gui._on_frame(np.full((75,130,3),70,dtype=np.uint8))
        self.assertEqual(self.gui.review_list.count(),2)
        self.assertFalse(self.gui.dataset.root.exists())
        self.assertFalse(self.gui.preview.pixmap().isNull())
        self.gui.review_list.selectAll()
        self.gui._save_selected()
        self.assertFalse(self.gui.dataset.root.exists())
        idx=self.gui.label_box.findData("TEAM_SELECTION")
        self.gui.label_box.setCurrentIndex(idx)
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.No):
            self.gui._save_selected()
        self.assertFalse(self.gui.dataset.root.exists())
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.Yes):
            self.gui._save_selected()
        self.assertEqual(self.gui.review_list.count(),0)
        self.assertEqual(self.gui.dataset.audit()["counts"]["TEAM_SELECTION"],2)
        self.assertEqual(self.gui.dataset.audit()["all_sessions"],1)
        self.assertIn("保存済み合計 2 枚",self.gui.audit_label.text())

    def test_capture_worker_produces_local_frame_no_network(self):
        config=CaptureConfiguration(
            backend="WINDOWS_NATIVE",selected_window_identity={"hwnd":123},
            game_viewport_normalized=(0,0,1,1))
        observed=[]
        worker=_ReadOnlyCollectorWorker(config,one_shot=True,
                   capture=lambda _:np.zeros((32,64,3),dtype=np.uint8))
        worker.frame.connect(observed.append)
        worker.run()
        self.assertEqual(len(observed),1)
        self.assertEqual(observed[0].shape,(32,64,3))

    def test_relabel_saved_file_and_new_session_does_not_discard_saved_data(self):
        self.gui._on_frame(np.zeros((55,88,3),dtype=np.uint8))
        first_session=self.gui.buffer.session_id
        self.gui.label_box.setCurrentIndex(self.gui.label_box.findData("UNKNOWN"))
        with patch.object(QMessageBox,"question",
                          return_value=QMessageBox.StandardButton.Yes):
            self.gui._save_selected()
        saved=next(self.gui.dataset.root.glob("UNKNOWN/*.png"))
        dest=self.gui.dataset.relabel(saved,"RESULT")
        self.assertTrue(dest.exists())
        self.gui._new_session()
        self.assertNotEqual(self.gui.buffer.session_id,first_session)
        self.assertEqual(self.gui.dataset.audit()["counts"]["RESULT"],1)


if __name__=="__main__":
    unittest.main()
