"""Issue #136 review: explicit stop is honored at later workflow boundaries."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from champions_assist.settings_dialog import _MatchupAIWorker
from champions_assist.team_prep_readiness import progress_path

MASTER = Path(__file__).resolve().parents[1] / "masters"
BASE = MASTER / "Pokemon_Champions_MC_team_2026-10-03.csv"


class Issue136InterruptTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.team_id = "c" * 32
        self.team = self.root / "team.csv"
        self.team.write_bytes(BASE.read_bytes())
        store = self.root / "live-team-assets" / self.team_id / "fingerprint"
        store.mkdir(parents=True)
        self.manifest = store / "manifest.json"
        self.manifest.write_text(json.dumps({
            "fingerprint": "fingerprint", "quality_gate": {"live_ready": False}
        }), encoding="utf-8")
        (store / "matchup-evidence.json").write_text(json.dumps({
            "total_cells": 6,
            "cells": [{"evaluation": "UNKNOWN", "missing_source": []}
                      for _ in range(6)]
        }), encoding="utf-8")
        self.ready = SimpleNamespace(
            manifest_path=self.manifest,
            matrix_path=store / "matrix.txt", lookup_path=store / "lookup.txt",
            opponent_rows=1)
        record = SimpleNamespace(
            validation=SimpleNamespace(selectable_for_next_match=True),
            display_name="検証構築")
        self.service = SimpleNamespace(
            teams=SimpleNamespace(
                path_for_next_match=lambda _id: self.team,
                get=lambda _id: record),
            settings=SimpleNamespace(
                storage=SimpleNamespace(data_root=self.root)),
            master_directory=MASTER,
            environment=SimpleNamespace(snapshot=self.root / "missing.json"),
            refresh_public_battle_data=lambda **_: None,
            prepare_team_live_assets=lambda *_a, **_k: self.ready,
        )

    def run_case(self, point: str):
        worker = _MatchupAIWorker(self.service, self.team_id, "model", "auto")
        results, events = [], []
        worker.result_ready.connect(results.append)
        worker.progress.connect(events.append)
        def analysis(*_args, **_kwargs):
            return {"status": "COMPLETE", "count": 6, "batches": 1, "attempts": 1}
        def publish(**_kwargs):
            if point == "publish":
                worker.requestInterruption()
            return {"ai_proposal_cells": 0}
        def fake_preloader(*_a, **_k):
            frozen = SimpleNamespace(
                matchup_cache_index={str(i): {"opponent": {}} for i in range(6)})
            def preload():
                if point == "cache":
                    worker.requestInterruption()
                return frozen
            return SimpleNamespace(load_for_new_match=preload)
        def progress_hook(event):
            if (point == "before_publish" and event.get("stage") == "analysis"
                    and event.get("stage_status") == "完了"):
                worker.requestInterruption()
        worker.progress.connect(progress_hook)
        store = MagicMock()
        store.import_legacy_assets.return_value = {"imported_cells": 0}
        store.coverage.return_value = {}

        with (patch("champions_assist.matchup_learning_store.MatchupLearningStore",
                    return_value=store),
              patch("champions_assist.team_matchup_reuse.reuse_unchanged_matchups",
                    return_value={"reused_cells": 0}),
              patch("champions_assist.team_auto_analysis.run_automatic_analysis",
                    side_effect=analysis),
              patch("champions_assist.team_ai_matrix.publish_ai_matrix",
                    side_effect=publish),
              patch("champions_assist.team_ai_matrix.proposal_notes",
                    return_value=({}, 0)),
              patch("champions_assist.siwc_auth.ChatGPTSignIn",
                    return_value=SimpleNamespace(
                        access_token=lambda **_k: "mock-token")),
              patch("champions_assist.siwc_auth.ProtectedSessionStore",
                    return_value=object()),
              patch("champions_assist.app_config.default_app_root",
                    return_value=self.root),
              patch("champions_assist.preload.MasterPreloader",
                    side_effect=fake_preloader),
              patch("champions_assist.matrix_compatibility.matrix_matches_registered_team",
                    return_value=True)):
            worker.run()
        self.assertEqual(len(results), 1, results)
        self.assertTrue(results[0]["ok"], results)
        self.assertEqual(results[0]["status"], "INTERRUPTED")
        self.assertEqual(results[0]["workflow_status"], "停止")
        journal = json.loads(progress_path(self.root, self.team_id)
                             .read_text(encoding="utf-8"))
        self.assertEqual(journal["run_status"], "停止")
        self.assertNotEqual(journal["stages"]["cache"]["status"], "完了")
        return journal

    def test_stop_after_analysis_and_before_publication(self):
        data = self.run_case("before_publish")
        self.assertEqual(data["stages"]["publish"]["status"], "未実行")

    def test_stop_during_publication_after_prep_check(self):
        data = self.run_case("publish")
        self.assertEqual(data["stages"]["publish"]["status"], "停止")
        self.assertEqual(data["stages"]["prep"]["status"], "一部完了")

    def test_stop_during_cache_check_cannot_be_reported_complete(self):
        data = self.run_case("cache")
        self.assertEqual(data["stages"]["cache"]["status"], "停止")
        self.assertEqual(data["stages"]["prep"]["status"], "一部完了")


if __name__ == "__main__":
    unittest.main()
