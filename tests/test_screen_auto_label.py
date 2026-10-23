"""No-network tests for experimental offline proposals and review safety."""
from __future__ import annotations

from hashlib import sha256
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from champions_assist.screen_auto_label import analyze_staged_session, load_suggestion_model
from champions_assist.screen_classifier import ScreenClassifier, ScreenClassifierError, ScreenPrediction, LABELS
from champions_assist.screen_dataset import LabelledScreenDataset, UnlabelledScreenStaging


SESSION="match20261023T174511_abcde123"


class GroupingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/"screens"
        self.staging=UnlabelledScreenStaging(self.root,reserve_bytes=0)
        self.dataset=LabelledScreenDataset(self.root)

    def _frames(self,n,*,value=140):
        frame=np.full((80,120,3),value,dtype=np.uint8)
        return [self.staging.save_unlabelled(frame,SESSION) for _ in range(n)]

    def test_without_model_groups_identical_images_and_does_not_save_labels(self):
        files=self._frames(7)
        reports=[]
        groups=analyze_staged_session(self.staging.session_files(SESSION),
                                       progress=lambda n,total:reports.append((n,total)))
        self.assertEqual(len(groups),1)
        self.assertEqual(groups[0].count,7)
        self.assertIsNone(groups[0].candidate)
        self.assertEqual(groups[0].representative[0],files[0])
        self.assertEqual(groups[0].representative[-1],files[-1])
        self.assertEqual(self.dataset.audit()["total"],0)
        self.assertEqual(reports[-1],(7,7))

    def test_groups_limit_size_and_never_cross_sessions(self):
        self._frames(35)
        groups=analyze_staged_session(self.staging.session_files(SESSION),max_group=30)
        self.assertEqual([g.count for g in groups],[30,5])
        second="match20261023T174512_abcdef42"
        self.staging.save_unlabelled(np.full((80,120,3),140,dtype=np.uint8),second)
        with self.assertRaisesRegex(ValueError,"INVALID_AUTOLABEL_SESSION_FILES"):
            analyze_staged_session(self.staging.session_files(SESSION)+
                                   self.staging.session_files(second))

    def test_ai_proposals_split_when_label_changes_and_abstain_when_weak(self):
        self._frames(4)
        seen=[]
        class Stub:
            def predict(self,image,*,raw_label=False):
                seen.append(raw_label)
                i=len(seen)
                if i<=2:
                    return ScreenPrediction("TEAM_SELECTION",.98,.93,0.1)
                return ScreenPrediction("MOVE_MENU",.60,.10,0.1)
        groups=analyze_staged_session(self.staging.session_files(SESSION),classifier=Stub())
        self.assertEqual([g.count for g in groups],[2,2])
        self.assertEqual(groups[0].candidate,"TEAM_SELECTION")
        self.assertIsNone(groups[1].candidate)
        self.assertTrue(all(seen))
        self.assertEqual(self.dataset.audit()["total"],0)

    def test_cannot_use_bad_staged_paths(self):
        external=Path(self.temp.name)/"external.jpg"
        external.write_bytes(b"bad")
        with self.assertRaises(ValueError):
            analyze_staged_session([external])

    def test_experimental_classifier_opt_in_does_not_relax_release_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            model=folder/"champions_screen_classifier.onnx"
            model.write_bytes(b"dummy bytes; synthetic session mock")
            manifest=folder/"champions_screen_classifier.json"
            manifest.write_text(json.dumps({
                "schema_version":1,
                "validated":False,
                "labels":list(LABELS),
                "input_size":[160,160],
                "sha256":sha256(model.read_bytes()).hexdigest(),
                "mean":[0,0,0],
                "std":[1,1,1],
            }),encoding="utf-8")

            class FakeSession:
                def __init__(self,*args,**kwargs):
                    pass
                def get_inputs(self):
                    return [SimpleNamespace(name="rgb")]
                def run(self,unused,feed):
                    return [np.array([[1.0,0,0,0,0,0,0]],dtype=np.float32)]

            with self.assertRaisesRegex(ScreenClassifierError,"SCREEN_MODEL_NOT_VALIDATED"):
                ScreenClassifier(model,manifest,session_factory=FakeSession)
            experimental=ScreenClassifier(
                model,manifest,experimental_for_labelling=True,session_factory=FakeSession)
            frame=np.full((64,64,3),120,dtype=np.uint8)
            self.assertEqual(experimental.predict(frame).label,"UNKNOWN")
            self.assertEqual(experimental.predict(frame,raw_label=True).label,"MATCHMAKING")
            with patch("champions_assist.screen_auto_label.ScreenClassifier",
                       return_value=experimental) as ctor:
                self.assertIs(load_suggestion_model(model),experimental)
                self.assertTrue(ctor.call_args.kwargs["experimental_for_labelling"])

if __name__ == "__main__":
    unittest.main()
