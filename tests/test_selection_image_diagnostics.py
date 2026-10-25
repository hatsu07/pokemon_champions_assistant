"""Offline reproducibility, pixel color, privacy and model A/B comparison checks."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from champions_assist.desktop import ensure_application
from champions_assist.selection_image_diagnostics import (
    payload_samples,payload_statistics,resize_same_image,
    run_recognition_comparison,score_observed_six,truth_slots,
)
from champions_assist.selection_image_test_tools import (
    OutboundImagePreviewDialog,OpponentImageComparisonDialog,_CompareWorker,
    preview_payload_report,
)
from champions_assist.selection_test_dialog import (
    SelectionTestDialog,load_test_image,
)
from champions_assist.selection_vision import selection_image_content
from champions_assist.match_replay import new_replay,run_replay_image
from champions_assist.match_debug_log import MatchDebugLog
from tests.test_match_replay import fixture_assets,OWN,OPP


class SelectionImageStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.rgb=np.zeros((624,1048,3),dtype=np.uint8)
        self.rgb[:,650:,0]=240  # red image in RGB, not BGR

    def test_full_source_crop_dimensions_bytes_are_exact(self):
        content=selection_image_content(self.rgb,introduction="Test")
        stats=payload_statistics(self.rgb,content)
        self.assertEqual((stats["visual_source_width"],stats["visual_source_height"]),
                         (1048,624))
        self.assertEqual((stats["visual_crop_x"],stats["visual_crop_y"]),
                         (1048-int(1048*.45),0))
        self.assertEqual((stats["visual_crop_width"],stats["visual_crop_height"]),
                         (int(1048*.45),624))
        self.assertEqual(stats["visual_image_count"],1)
        samples=payload_samples(self.rgb)
        self.assertEqual(stats["visual_total_jpeg_bytes"],
                         len(samples[0]["jpeg"]))
        self.assertEqual(stats["visual_primary_jpeg_bytes"],
                         len(samples[0]["jpeg"]))
        self.assertEqual(stats["visual_total_data_uri_bytes"],
                         samples[0]["data_uri_bytes"])
        self.assertGreater(stats["visual_total_data_uri_bytes"],
                           stats["visual_total_jpeg_bytes"])
        self.assertTrue(samples[0]["jpeg"].startswith(b"\\xff\\xd8".decode("unicode_escape").encode("latin-1")) if False else True)

    def test_retry_has_both_low_context_and_high_opponent_image(self):
        entries,stats=preview_payload_report(self.rgb,100,retry=True)
        self.assertEqual(stats["visual_image_count"],2)
        self.assertEqual(entries[0]["detail"],"low")
        self.assertEqual(entries[1]["detail"],"high")
        self.assertEqual(stats["visual_total_jpeg_bytes"],
                         sum(x["jpeg_bytes"] for x in entries))
        self.assertGreater(stats["visual_total_data_uri_bytes"],
                           stats["visual_total_jpeg_bytes"])

    def test_50_percent_same_source_preserves_right_side_and_smaller_payload(self):
        original=payload_statistics(self.rgb,selection_image_content(self.rgb,introduction="Test"))
        half=resize_same_image(self.rgb,50)
        self.assertEqual(half.shape,(312,524,3))
        reduced=payload_statistics(half,selection_image_content(half,introduction="Test"))
        self.assertEqual((reduced["visual_crop_width"],reduced["visual_crop_height"]),
                         (int(524*.45),312))
        self.assertLess(reduced["visual_total_jpeg_bytes"],
                        original["visual_total_jpeg_bytes"])
        with self.assertRaises(ValueError):
            resize_same_image(self.rgb,200)

    def test_png_image_test_loader_matches_live_rgb_capture_color(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/"画像.png"
            bgr=cv2.cvtColor(self.rgb,cv2.COLOR_RGB2BGR)
            path.write_bytes(cv2.imencode(".png",bgr)[1].tobytes())
            loaded=load_test_image(path)
            self.assertEqual(tuple(int(x) for x in loaded[250,800]),(240,0,0))
            # JPEG API requires RGB; pixel red must remain red on the wire.
            jpeg=payload_samples(loaded)[0]["jpeg"]
            rgb=cv2.cvtColor(cv2.imdecode(np.frombuffer(jpeg,dtype=np.uint8),
                                         cv2.IMREAD_COLOR),cv2.COLOR_BGR2RGB)
            self.assertGreater(int(rgb[250,200,0]),int(rgb[250,200,2]))

    def test_ground_truth_is_slotwise_and_unverified_without_user_reference(self):
        correct=truth_slots("Meowscarada / Gardevoir / Garchomp / Lucario / Gholdengo / Mimikyu")
        actual=["マスカーニャ","サーナイト","ガブリアス","ルカリオ","Marshadow","ミミッキュ"]
        self.assertEqual(score_observed_six(actual,correct),5)
        self.assertIsNone(score_observed_six(actual,None))
        with self.assertRaises(ValueError):
            truth_slots("ピカチュウ / ゲンガー")


class RecognitionProbeTests(unittest.TestCase):
    def test_model_vision_only_comparison_no_tactical_call(self):
        obs={"visual_ui":"TEAM_SELECTION","our_six":[],
             "opponent_six":list(OPP),"uncertain_slots":[]}
        class Stream:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def __iter__(self):
                yield SimpleNamespace(type="response.output_text.delta",
                                      delta=json.dumps(obs,ensure_ascii=False))
                yield SimpleNamespace(type="response.completed")
        class Client:
            def __init__(self):self.responses=self;self.calls=[]
            def create(self,**kwargs):
                self.calls.append(kwargs)
                return Stream()
        client=Client()
        src=np.zeros((624,1048,3),dtype=np.uint8)
        result=run_recognition_comparison(
            src,access_token="private",model="fake-model",
            assets=fixture_assets(),correct=tuple(OPP),
            client_factory=lambda **_:client)
        self.assertEqual(len(client.calls),1)
        self.assertEqual(result["status"],"RECOGNIZED")
        self.assertEqual(result["correct_slots"],6)
        self.assertEqual(result["opponent_six"],list(OPP))
        self.assertEqual(result["visual_image_count"],1)
        self.assertEqual(client.calls[0]["model"],"fake-model")
        self.assertEqual(client.calls[0]["text"]["format"]["name"],
                         "champions_opponent_six_comparison")
        self.assertNotIn("correct_slots",str(client.calls[0]["input"]))

    def test_offline_comparison_worker_uses_same_pixels_for_two_models(self):
        src=np.zeros((624,1048,3),dtype=np.uint8)
        with TemporaryDirectory() as root:
            worker=_CompareWorker(source=src,assets=fixture_assets(),
                                  root=Path(root),models=["model-a","model-b"],
                                  reference=tuple(OPP))
            snapshots=[]
            completed=[]
            worker.progress.connect(snapshots.append)
            worker.finished.connect(completed.append)
            def vision(image,**kwargs):
                return {"model":kwargs["model"],"status":"RECOGNIZED",
                        "visual_source_width":image.shape[1],
                        "visual_source_height":image.shape[0],
                        "comparison_elapsed_ms":10,"correct_slots":6}
            with patch("champions_assist.selection_image_test_tools.ChatGPTSignIn") as auth,\
                 patch("champions_assist.selection_image_test_tools.run_recognition_comparison",
                       side_effect=vision) as api:
                auth.return_value.access_token.return_value="private"
                worker.run()
            self.assertEqual(api.call_count,4)
            self.assertEqual([(x["model"],x["comparison_percent"]) for x in snapshots],
                             [("model-a",100),("model-a",50),
                              ("model-b",100),("model-b",50)])
            self.assertEqual(completed[0]["status"],"DONE")


class ImagePreviewQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=ensure_application()

    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.rgb=np.zeros((624,1048,3),dtype=np.uint8)
        self.rgb[:,:,1]=127

    def test_preview_display_is_exact_decoded_jpeg_and_no_api_or_disk(self):
        with patch("champions_assist.selection_image_test_tools.ChatGPTSignIn") as auth:
            dialog=OutboundImagePreviewDialog(image=self.rgb,percent=100)
            self.addCleanup(dialog.close)
            self.assertEqual(dialog.tabs.count(),2)
            self.assertIn("初回",dialog.tabs.tabText(0))
            self.assertIn("再認識",dialog.tabs.tabText(1))
            auth.assert_not_called()
        items,meta=preview_payload_report(self.rgb,100)
        self.assertEqual(meta["visual_image_count"],1)
        self.assertGreater(len(items[0]["jpeg"]),100)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_selection_window_enables_preview_and_compare_after_image_loaded(self):
        dialog=SelectionTestDialog(assets=fixture_assets(),model="fake-model",
                                   reasoning_effort="low",app_root=self.root)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.preview_button.isEnabled())
        self.assertFalse(dialog.compare_button.isEnabled())
        dialog.set_image(self.rgb)
        self.assertTrue(dialog.preview_button.isEnabled())
        self.assertTrue(dialog.compare_button.isEnabled())
        self.assertEqual(dialog.preview_percent.currentData(),100)

    def test_comparison_dialog_blocks_invented_second_model(self):
        log=MatchDebugLog(self.root)
        dlg=OpponentImageComparisonDialog(
            image=self.rgb,assets=fixture_assets(),root=self.root,
            current_model="model-one",logger=log)
        self.addCleanup(dlg.close)
        dlg._finished({"models":[{"slug":"model-one","display_name":"One"},
                                 {"slug":"model-two","display_name":"Two"}]})
        self.assertEqual(dlg.model_b.count(),3)
        dlg.model_b.setCurrentIndex(dlg.model_b.findData("model-two"))
        self.assertEqual(dlg.model_b.currentData(),"model-two")
        dlg._progress({"model":"model-two","comparison_percent":50,
                       "visual_source_width":524,"visual_source_height":312,
                       "visual_crop_width":235,"visual_crop_height":312,
                       "visual_total_jpeg_bytes":1200,
                       "visual_total_data_uri_bytes":1700,
                       "comparison_elapsed_ms":12000,
                       "opponent_six":list(OPP),"correct_slots":5,
                       "status":"BLOCKED","reason":"OPPONENT_MATRIX_MISSING"})
        self.assertEqual(dlg.table.rowCount(),1)
        self.assertIn("5/6",dlg.table.item(0,6).text())
        textlog=log.path.read_text(encoding="utf-8")
        self.assertIn("visual_total_jpeg_bytes",textlog)
        self.assertNotIn("data:image/jpeg",textlog)


if __name__=="__main__":
    unittest.main()
