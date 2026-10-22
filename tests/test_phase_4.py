from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from champions_assist.__main__ import main
from champions_assist.calibration import CalibrationLabels, OfflineReplayRunner
from champions_assist.capture import CaptureUnavailable, Frame, ImageFileFrameSource, SyntheticFrameSource, VideoFrameSource
from champions_assist.effect_catalog import EffectRule, FrozenEffectCatalog
from champions_assist.fsm import MatchController, ObservationEvent, WindowKind, WindowStatus
from champions_assist.observation_events import EventCertainty, EventPattern, ObservedEventKind, ScreenLexicon, TextObservationParser
from champions_assist.observer_pipeline import CandidateTextProfile, ExactTextCandidateExtractor, ObserverPipeline
from champions_assist.ocr import MappingOcrEngine, OcrRead
from champions_assist.roi import NormalizedROI, RoiConfiguration
from champions_assist.store import BattleStore
from champions_assist.vision import DetectedUI, TemplateUiClassifier
from champions_assist.windows_capture import WindowsNativeFrameSource


OPPONENTS = ("Opp1", "Opp2", "Opp3", "Opp4", "Opp5", "Opp6")
TEAM = ("Own1", "Own2", "Own3", "Own4", "Own5", "Own6")


def write_masters(root: Path) -> None:
    (root / "Pokemon_Champions_MC_live_decision_gate.txt").write_text("LIVE GATE\n", encoding="utf-8")
    (root / "Pokemon_Champions_MC_live_selection_matrix.txt").write_text("\n".join(f"{name}|matrix" for name in OPPONENTS) + "\n", encoding="utf-8")
    (root / "Pokemon_Champions_MC_60sec_lookup_master.txt").write_text("\n".join(f"{name}|lookup" for name in OPPONENTS) + "\n", encoding="utf-8")
    header = "record,name,item,ability,nature,ap_h_a_b_c_d_s,move1,move2,move3,move4,role,form_change,runtime_note\n"
    rows = "\n".join(f"MON,{name},item,ability,nature,evs,{name}-m1,{name}-m2,{name}-m3,{name}-m4,role,,note" for name in TEAM)
    (root / "Pokemon_Champions_MC_team_2026-10-03.csv").write_text(header + rows + "\n", encoding="utf-8")


def roi_config() -> RoiConfiguration:
    return RoiConfiguration(
        version="synthetic-v1",
        reference_resolution=(80, 60),
        source_label="SYNTHETIC_TEST_ONLY",
        rois={
            "ROI_STATUS": NormalizedROI("ROI_STATUS", 0, 0, 1, 1 / 6),
            "ROI_UI_CONTROLS": NormalizedROI("ROI_UI_CONTROLS", 0, 1 / 6, 1, 5 / 12),
            "ROI_PARTY_CARDS": NormalizedROI("ROI_PARTY_CARDS", 0, 5 / 12, 1, 2 / 3),
            "ROI_BATTLE_LOG": NormalizedROI("ROI_BATTLE_LOG", 0, 2 / 3, 1, 1),
        },
    )


_COLORS = {
    DetectedUI.COMMAND_MENU: (20, 110, 210),
    DetectedUI.MOVE_MENU: (40, 160, 60),
    DetectedUI.PARTY_MENU: (170, 60, 160),
    DetectedUI.DETAIL_OVERLAY: (200, 160, 40),
    DetectedUI.ACTION_RESOLUTION: (80, 80, 80),
    DetectedUI.RESULT_SCREEN: (210, 50, 50),
    DetectedUI.TEAM_SELECTION: (40, 180, 180),
}


def synthetic_image(kind: DetectedUI, *, log_code: int = 0, controls_code: int = 0, party_code: int = 0) -> np.ndarray:
    image = np.zeros((60, 80, 3), dtype=np.uint8)
    image[:10, :] = _COLORS[kind]
    image[:10, ::7] = (255, 255, 255)
    image[10:25, :] = controls_code
    image[25:40, :] = party_code
    image[40:, :] = log_code
    return image


def frame(frame_id: str, sequence: int, image: np.ndarray) -> Frame:
    return Frame(frame_id=frame_id, capture_seq=sequence, captured_at_ms=sequence * 100, image=image, source="SYNTHETIC_TEST")


class Phase4TestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "masters"
        self.root.mkdir()
        write_masters(self.root)
        self.store = BattleStore(Path(self.temp.name) / "battle.sqlite3")
        self.controller = MatchController(self.root, store=self.store)
        self.config = roi_config()

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def battle_session(self, match_id: str = "phase4"):
        session = self.controller.start_match(match_id, now_ms=0)
        session.receive_opponent_preview(OPPONENTS, now_ms=1)
        session.record_actual_selection(TEAM[:3], TEAM[0], now_ms=2)
        session.observe_event("lead", ObservationEvent.OPPONENT_LEAD_VISIBLE, now_ms=3)
        return session

    def classifier(self) -> TemplateUiClassifier:
        templates = {kind: synthetic_image(kind)[:10] for kind in _COLORS}
        return TemplateUiClassifier(templates, threshold=0.99)

    def pipeline(self, session, readings, patterns=(), *, candidate_extractor=None, stable_frames=1, catalog=None) -> ObserverPipeline:
        return ObserverPipeline(
            session=session,
            store=self.store,
            roi_config=self.config,
            ui_classifier=self.classifier(),
            ocr_engine=MappingOcrEngine.for_images(readings),
            text_parser=TextObservationParser(patterns, lexicon=ScreenLexicon(pokemon_names=("Own1",), move_names=("demo",), statuses=("burn",))),
            candidate_extractor=candidate_extractor,
            effect_catalog=catalog,
            ocr_roi_names=("ROI_BATTLE_LOG", "ROI_UI_CONTROLS", "ROI_PARTY_CARDS"),
            classification_roi_name="ROI_STATUS",
            stable_frames=stable_frames,
        )

    def ocr_readings(self, items):
        records = []
        for image, values in items:
            for roi_name, text in values.items():
                crop = self.config.crop(roi_name, image)
                assert crop is not None
                records.append((roi_name, crop, OcrRead(text, 0.99, "SYNTHETIC_OCR")))
        return records

    def test_ui_categories_are_template_classified_and_party_reason_stays_in_fsm(self) -> None:
        for kind in _COLORS:
            with self.subTest(kind=kind):
                self.assertEqual(self.classifier().classify(synthetic_image(kind)[:10]).visual_ui, kind)
        session = self.battle_session()
        command = synthetic_image(DetectedUI.COMMAND_MENU, controls_code=10)
        party = synthetic_image(DetectedUI.PARTY_MENU, party_code=20)
        extractor = ExactTextCandidateExtractor(
            (
                CandidateTextProfile(DetectedUI.COMMAND_MENU, moves=("move-a", "move-b")),
                CandidateTextProfile(DetectedUI.PARTY_MENU, switch_targets=("Own2", "Own3")),
            )
        )
        pipe = self.pipeline(
            session,
            self.ocr_readings([(command, {"ROI_UI_CONTROLS": "move-a move-b"}), (party, {"ROI_PARTY_CARDS": "Own2 Own3"})]),
            candidate_extractor=extractor,
        )
        pipe.process_frame(frame("command", 1, command))
        self.assertEqual(session.current_window.kind, WindowKind.NORMAL_COMMAND)
        session.record_actual_action("manual-command", {"move": "move-a"})
        session.observe_event("faint", ObservationEvent.OUR_FAINTED, now_ms=110)
        result = pipe.process_frame(frame("party", 2, party))
        self.assertEqual(result.classification.visual_ui, DetectedUI.PARTY_MENU)
        self.assertEqual(session.current_window.kind, WindowKind.FAINT_REPLACEMENT)

    def test_duplicate_message_is_not_applied_twice_but_later_episode_is_new(self) -> None:
        session = self.battle_session()
        message = synthetic_image(DetectedUI.ACTION_RESOLUTION, log_code=90)
        blank = synthetic_image(DetectedUI.ACTION_RESOLUTION, log_code=0)
        pattern = EventPattern("move", r"^MOVE (?P<move>demo)$", ObservedEventKind.MOVE_USED, facts={"last_move": "$move"})
        pipe = self.pipeline(session, self.ocr_readings([(message, {"ROI_BATTLE_LOG": "MOVE demo"}), (blank, {"ROI_BATTLE_LOG": ""})]), (pattern,))
        first = pipe.process_frame(frame("message-1", 1, message))
        second = pipe.process_frame(frame("message-2", 2, message))
        pipe.process_frame(frame("blank", 3, blank))
        fourth = pipe.process_frame(frame("message-3", 4, message))
        self.assertEqual(len(first.events), 1)
        self.assertEqual(len(second.events), 0)
        self.assertEqual(len(fourth.events), 1)
        self.assertEqual(self.store.replay(session.match_id)["pending_effect_ids"], [
            f"{first.events[0].event_id}:activation", f"{fourth.events[0].event_id}:activation"
        ])
        raw_count = self.store._connection.execute("SELECT COUNT(*) FROM raw_observations WHERE match_id = ?", (session.match_id,)).fetchone()[0]
        self.assertEqual(raw_count, 4)

    def test_activation_is_pending_until_explicit_effect_confirmation(self) -> None:
        session = self.battle_session()
        move = synthetic_image(DetectedUI.ACTION_RESOLUTION, log_code=71)
        status = synthetic_image(DetectedUI.ACTION_RESOLUTION, log_code=172)
        patterns = (
            EventPattern("move", r"^MOVE demo$", ObservedEventKind.MOVE_USED, facts={"effect_source_type": "move", "effect_source_key": "demo"}, effect_instance_template="effect:demo"),
            EventPattern("status", r"^STATUS applied$", ObservedEventKind.STATUS_APPLIED, EventCertainty.CONFIRMED, effect_instance_template="effect:demo", confirm_effect=True, facts={"statuses": ["burn"]}),
        )
        catalog = FrozenEffectCatalog((EffectRule("MOVE::demo", "move", "demo", "VERIFIED", ("status",)),))
        pipe = self.pipeline(session, self.ocr_readings([(move, {"ROI_BATTLE_LOG": "MOVE demo"}), (status, {"ROI_BATTLE_LOG": "STATUS applied"})]), patterns, catalog=catalog)
        pipe.process_frame(frame("move", 1, move))
        pending = self.store.replay(session.match_id)
        self.assertEqual(pending["pending_effect_ids"], ["effect:demo"])
        pipe.process_frame(frame("status", 2, status))
        projection = self.store.replay(session.match_id)
        self.assertEqual(projection["pending_effect_ids"], [])
        self.assertEqual(projection["confirmed_effect_ids"], ["effect:demo"])
        note = next(item for item in self.store.events(session.match_id) if item["event_id"].endswith(":move"))
        self.assertIn("effect_rule", note["payload"])
        self.assertFalse(note["payload"]["effect_rule"]["activation_confirmed"])

    def test_observed_state_change_invalidates_a_waiting_model_response(self) -> None:
        session = self.battle_session()
        command = synthetic_image(DetectedUI.COMMAND_MENU, controls_code=10)
        hp = synthetic_image(DetectedUI.ACTION_RESOLUTION, log_code=33)
        extractor = ExactTextCandidateExtractor((CandidateTextProfile(DetectedUI.COMMAND_MENU, moves=("move-a",)),))
        patterns = (EventPattern("hp", r"^HP 80/100$", ObservedEventKind.HP_CHANGED, EventCertainty.CONFIRMED, facts={"hp_status": [{"side": "ours", "current": 80, "max": 100}]}),)
        pipe = self.pipeline(session, self.ocr_readings([(command, {"ROI_UI_CONTROLS": "move-a"}), (hp, {"ROI_BATTLE_LOG": "HP 80/100"})]), patterns, candidate_extractor=extractor)
        pipe.process_frame(frame("command", 1, command))
        session.current_window.status = WindowStatus.REQUESTED
        old_version = session.tactical_state_version
        pipe.process_frame(frame("hp", 2, hp))
        self.assertGreater(session.tactical_state_version, old_version)
        self.assertEqual(session.current_window.status, WindowStatus.STALE)
        self.assertEqual(session.state_facts["hp_status"][0]["current"], 80)

    def test_turn_end_is_deduplicated_across_repeated_ocr_and_replay(self) -> None:
        session = self.battle_session()
        self.store.append_event(session.match_id, "wall", "DURATION_STARTED", {"duration_id": "wall", "field": "screen", "remaining_candidates": [4, 7]})
        turn = synthetic_image(DetectedUI.ACTION_RESOLUTION, log_code=44)
        blank = synthetic_image(DetectedUI.ACTION_RESOLUTION, log_code=0)
        pattern = EventPattern("turn", r"^TURN END$", ObservedEventKind.TURN_END_CONFIRMED, EventCertainty.CONFIRMED, facts={"turn_id": "turn-1"})
        pipe = self.pipeline(session, self.ocr_readings([(turn, {"ROI_BATTLE_LOG": "TURN END"}), (blank, {"ROI_BATTLE_LOG": ""})]), (pattern,))
        pipe.process_frame(frame("turn-1", 1, turn))
        pipe.process_frame(frame("turn-dup", 2, turn))
        pipe.process_frame(frame("turn-blank", 3, blank))
        pipe.process_frame(frame("turn-later", 4, turn))
        projection = self.store.replay(session.match_id)
        self.assertEqual(projection["turn_end_ids"], ["turn-1"])
        self.assertEqual(projection["durations"]["wall"]["remaining_candidates"], [3, 6])

    def test_buffered_reanalysis_and_manual_correction_append_new_evidence(self) -> None:
        session = self.battle_session()
        weather = synthetic_image(DetectedUI.ACTION_RESOLUTION, log_code=120)
        pipe = self.pipeline(session, self.ocr_readings([(weather, {"ROI_BATTLE_LOG": "WEATHER sun"})]))
        pipe.process_frame(frame("weather", 1, weather))
        self.assertNotIn("weather", self.store.replay(session.match_id)["fields"])
        late_parser = TextObservationParser((
            EventPattern("weather", r"^WEATHER sun$", ObservedEventKind.WEATHER_SET, facts={"weather": "sun"}),
        ))
        late = pipe.reanalyze_buffered_frame("weather", parser=late_parser)
        self.assertEqual(len(late), 1)
        self.assertEqual(self.store.replay(session.match_id)["fields"]["weather"]["certainty"], "CANDIDATE")
        self.assertTrue(
            pipe.apply_manual_correction(
                "weather-correction",
                field="weather",
                value="rain",
                supersedes=(f"{late[0].event_id}:evidence:1",),
            )
        )
        projection = self.store.replay(session.match_id)
        self.assertEqual(projection["fields"]["weather"]["value"], "rain")
        self.assertFalse(projection["fields"]["weather"]["disagreement"])

    def test_roi_round_trip_and_offline_replay_metrics(self) -> None:
        session = self.battle_session()
        command = synthetic_image(DetectedUI.COMMAND_MENU)
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "roi.json"
            labels_path = Path(directory) / "labels.json"
            self.config.save(config_path)
            self.assertEqual(RoiConfiguration.load(config_path), self.config)
            labels_path.write_text(json.dumps({"source_label": "SYNTHETIC", "frames": [{"frame_id": "cmd", "visual_ui": "COMMAND_MENU"}]}), encoding="utf-8")
            labels = CalibrationLabels.load(labels_path)
            pipeline = self.pipeline(session, [])
            observations, report = OfflineReplayRunner(pipeline).run(SyntheticFrameSource((frame("cmd", 1, command),)), labels=labels)
        self.assertEqual(len(observations), 1)
        self.assertEqual(report.ui_correct, 1)
        self.assertEqual(report.event_missed, 0)
        self.assertFalse(report.as_dict()["real_game_validated"])

    def test_image_source_and_windows_adapter_boundary_are_explicit(self) -> None:
        image = synthetic_image(DetectedUI.COMMAND_MENU)
        with tempfile.TemporaryDirectory() as directory:
            import cv2

            path = Path(directory) / "frame.png"
            self.assertTrue(cv2.imwrite(str(path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR)))
            loaded = tuple(ImageFileFrameSource((path,)))
        self.assertEqual(loaded[0].source, "IMAGE_FILE")
        self.assertEqual(loaded[0].image.shape, image.shape)
        with self.assertRaises(CaptureUnavailable):
            next(iter(WindowsNativeFrameSource(max_frames=1)))

    def test_video_source_decodes_a_synthetic_recording(self) -> None:
        import cv2

        image = synthetic_image(DetectedUI.COMMAND_MENU)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.avi"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (80, 60))
            self.assertTrue(writer.isOpened())
            writer.write(cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
            writer.release()
            decoded = tuple(VideoFrameSource(path))
        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0].source, "VIDEO_FILE")
        self.assertEqual(decoded[0].image.shape, image.shape)

    def test_observer_replay_cli_runs_synthetic_fixture_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "frames.json"
            config = root / "roi.json"
            db = root / "observer.sqlite3"
            fixture.write_text(json.dumps({"width": 8, "height": 8, "frames": [{"frame_id": "one", "rgb": [1, 2, 3]}]}), encoding="utf-8")
            RoiConfiguration.uncalibrated().save(config)
            output = StringIO()
            with redirect_stdout(output):
                code = main(["observer-replay", "--synthetic-fixture", str(fixture), "--roi-config", str(config), "--db", str(db), "--master-dir", str(self.root)])
            result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result["mode"], "OFFLINE_OBSERVER_REPLAY")
        self.assertEqual(result["network"], "NOT_USED")
        self.assertFalse(result["real_game_validated"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
