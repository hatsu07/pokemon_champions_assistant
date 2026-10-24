"""Offline tests for optional, bounded training storage alongside live AI."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
from time import monotonic, sleep
import unittest

import numpy as np

from champions_assist.background_training_capture import BackgroundTrainingCapture
from champions_assist.screen_dataset import (
    LabelledScreenDataset, StagingLimitReached, UnlabelledScreenStaging,
    new_session_id,
)


class FakeSink:
    def __init__(self, *, block=False, error=None):
        self.saved=[]
        self.lock=Lock()
        self.entered=Event()
        self.release=Event()
        if not block:
            self.release.set()
        self.error=error

    def save_unlabelled(self, image, session_id):
        self.entered.set()
        self.release.wait(timeout=3)
        if self.error:
            raise self.error
        with self.lock:
            self.saved.append((image,session_id))


def wait_for(predicate, timeout=3):
    limit=monotonic()+timeout
    while monotonic()<limit:
        if predicate():
            return True
        sleep(0.01)
    return bool(predicate())


class BackgroundTrainingCaptureTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.session=new_session_id()
        self.image=np.full((80,144,3),90,dtype=np.uint8)

    def test_no_disk_write_or_encoding_in_offer_and_only_one_worker(self):
        timer=[0.0]
        sink=FakeSink()
        writer=BackgroundTrainingCapture(
            self.root,self.session,clock=lambda:timer[0],sink=sink,
            interval_seconds=0.5)
        writer.start()
        self.assertTrue(writer.offer(self.image))
        self.assertTrue(wait_for(lambda:writer.stats().saved==1))
        self.assertIs(sink.saved[0][0],self.image)
        self.assertEqual(sink.saved[0][1],self.session)
        timer[0]=0.2
        self.assertFalse(writer.offer(self.image))
        timer[0]=1.0
        self.assertTrue(writer.offer(self.image))
        self.assertTrue(wait_for(lambda:writer.stats().duplicates==1))
        timer[0]=2.0
        changed=self.image.copy()
        changed[:30,:30,:]=180
        self.assertTrue(writer.offer(changed))
        self.assertTrue(wait_for(lambda:writer.stats().saved==2))
        writer.stop(wait=True)
        self.assertFalse(writer.offer(changed))
        self.assertFalse(writer.stats().running)

    def test_queue_full_drops_training_only_without_blocking_caller(self):
        timer=[0.0]
        sink=FakeSink(block=True)
        writer=BackgroundTrainingCapture(
            self.root,self.session,clock=lambda:timer[0],sink=sink,
            max_queue=1,interval_seconds=0.2)
        writer.start()
        self.assertTrue(writer.offer(self.image))
        self.assertTrue(sink.entered.wait(2))
        timer[0]=1.0
        self.assertTrue(writer.offer(self.image.copy()))
        timer[0]=2.0
        t0=monotonic()
        self.assertFalse(writer.offer(self.image.copy()))
        self.assertLess(monotonic()-t0,0.2)
        self.assertEqual(writer.stats().dropped,1)
        writer.stop()
        sink.release.set()
        writer.stop(wait=True)
        self.assertLessEqual(len(sink.saved),1)

    def test_disk_quota_error_halts_collection_without_raising_on_live_thread(self):
        sink=FakeSink(error=StagingLimitReached("STAGING_QUOTA_REACHED"))
        writer=BackgroundTrainingCapture(
            self.root,self.session,sink=sink)
        writer.start()
        self.assertTrue(writer.offer(self.image))
        self.assertTrue(wait_for(lambda:writer.stats().error is not None))
        self.assertEqual(writer.stats().error,"STAGING_QUOTA_REACHED")
        self.assertFalse(writer.offer(self.image))
        writer.stop(wait=True)

    def test_saved_images_are_unlabelled_and_share_review_app_layout(self):
        writer=BackgroundTrainingCapture(
            self.root,self.session,interval_seconds=0.2,reserve_bytes=0,
        )
        writer.start()
        self.assertTrue(writer.offer(self.image))
        self.assertTrue(wait_for(lambda:writer.stats().saved==1))
        writer.stop(wait=True)
        staging=UnlabelledScreenStaging(self.root,reserve_bytes=0)
        images=staging.session_files(self.session)
        self.assertEqual(len(images),1)
        self.assertEqual(images[0].suffix.lower(),".jpg")
        self.assertEqual(images[0].parent.name,self.session)
        self.assertEqual(LabelledScreenDataset(self.root).audit()["total"],0)
        self.assertTrue(images[0].is_file())

    def test_start_is_explicit_and_cannot_restart_a_stopped_writer(self):
        writer=BackgroundTrainingCapture(self.root,self.session)
        self.assertFalse(writer.offer(self.image))
        writer.start()
        with self.assertRaisesRegex(RuntimeError,"ALREADY"):
            writer.start()
        writer.stop(wait=True)
        with self.assertRaisesRegex(RuntimeError,"ALREADY"):
            writer.start()


if __name__=="__main__":
    unittest.main()
