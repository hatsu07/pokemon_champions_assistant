"""Mock-only Vision API benchmark checks: no network or private image upload."""
from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import cv2
import numpy as np
from champions_assist.vision_api_benchmark import OpenAIVisionBenchmark, read_image, parse_extraction


class _Client:
    def __init__(self):
        self.responses = self
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(output_text=json.dumps({
            "visual_ui": "MOVE_MENU", "pokemon": ["ピカチュウ"],
            "moves": ["10まんボルト"], "hp": ["112/112"], "messages": []
        }, ensure_ascii=False))


class VisionBenchmarkTests(unittest.TestCase):
    def test_vision_single_image_structured_mock_request(self):
        client = _Client()
        settings = []
        def factory(**kwargs):
            settings.append(kwargs)
            return client
        result = OpenAIVisionBenchmark(client_factory=factory).run(
            np.zeros((50, 90, 3), dtype=np.uint8), api_key="test-key")
        self.assertEqual(result.status, "OK")
        report = result.report("MOVE_MENU")
        self.assertTrue(report["ui_match"])
        self.assertTrue(report["under_35_seconds"])
        self.assertEqual(report["image_size"], [90, 50])
        self.assertFalse(report["tactical_decision_executed"])
        self.assertFalse(client.requests[0]["store"])
        self.assertEqual(client.requests[0]["input"][0]["content"][1]["type"], "input_image")
        self.assertTrue(client.requests[0]["input"][0]["content"][1]["image_url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(settings[0]["max_retries"], 0)

    def test_bad_response_is_not_interpreted_as_game_fact(self):
        with self.assertRaises(ValueError):
            parse_extraction(json.dumps({"visual_ui": "IMPOSSIBLE", "pokemon": [], "moves": [], "hp": [], "messages": []}))

    def test_file_decode_and_bounds(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "preview.png"
            image = np.zeros((80, 120, 3), dtype=np.uint8)
            ok, enc = cv2.imencode(".png", image)
            self.assertTrue(ok)
            path.write_bytes(enc.tobytes())
            self.assertEqual(read_image(path).shape, (80, 120, 3))
