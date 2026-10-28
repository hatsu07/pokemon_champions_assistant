"""Offline GPU stress diagnosis control-plane tests; no GPU or private game images."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from champions_assist import gpu_stress_diagnostics as gpu


class GPUStressTests(unittest.TestCase):
    def test_all_modes_are_isolated_and_native_crash_does_not_stop_later_cases(self):
        seen = []
        def runner(command, **kwargs):
            seen.append(command)
            self.assertTrue(kwargs["check"] is False)
            progress = Path(command[command.index("--gpu-diagnostic-progress") + 1])
            progress.write_text(json.dumps({
                "event": "RUN_BEGIN", "component": "rec", "iteration": 2
            }) + "\n", encoding="utf-8")
            return SimpleNamespace(returncode=(
                -1073741819 if command[2] == "rec" else 0))
        with TemporaryDirectory() as tmp:
            fake_model=Path(tmp)/"selected_screen.onnx"
            fake_model.write_bytes(b"mock file existence; runner never loads this")
            folder, summary = gpu.supervisor(
                "assistant.exe", modes=gpu.MODES, iterations=20, trials=2,
                gpu_id=0, app_root=Path(tmp), model=str(fake_model), runner=runner)
            data=json.loads((folder/"summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary,data)
            self.assertEqual(len(seen),8)
            self.assertEqual(data["crash_count"],2)
            self.assertEqual(data["success_count"],6)
            self.assertTrue(all(case["last_checkpoint"]["event"] == "RUN_BEGIN"
                                for case in data["cases"]))
            self.assertEqual(data["cases"][0]["exit_code_unsigned"],0xC0000005)
            self.assertEqual(data["cases"][0]["status"],"ACCESS_VIOLATION")
            self.assertEqual(data["cases"][-1]["mode"],"parallel")
            self.assertEqual(data["network"],"NOT_USED")

    def test_missing_real_classifier_is_skipped_not_faked(self):
        calls=[]
        def runner(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=0)
        with TemporaryDirectory() as tmp:
            _, summary=gpu.supervisor(
                "assistant.exe", modes=gpu.MODES, iterations=2, trials=1,
                gpu_id=0, app_root=Path(tmp),
                model=str(Path(tmp)/"model-not-installed.onnx"), runner=runner)
        self.assertEqual(len(calls),1)
        self.assertEqual(summary["skipped_count"],3)
        self.assertFalse(summary["screen_model_available"])
        self.assertEqual(summary["cases"][1]["status"],"SKIPPED_NO_SCREEN_MODEL")

    def test_timeout_and_launch_failure_are_not_misreported_as_success(self):
        count=[0]
        def runner(command, **kwargs):
            count[0]+=1
            if count[0]==1:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            raise OSError("private local path")
        with TemporaryDirectory() as tmp:
            _, summary=gpu.supervisor(
                "assistant.exe", modes=("rec",), iterations=2, trials=2,
                gpu_id=0, app_root=Path(tmp), runner=runner)
        self.assertEqual([x["status"] for x in summary["cases"]],
                         ["TIMEOUT", "ERROR"])
        self.assertEqual(summary["cases"][1]["launch_error_type"],"OSError")
        self.assertNotIn("private local path",str(summary))

    def test_isolated_child_runs_all_four_modes_with_injected_predictors(self):
        fake=lambda: None
        with TemporaryDirectory() as tmp, \
            patch.object(gpu, "_create_rec", return_value=fake), \
            patch.object(gpu, "_create_screen", return_value=fake):
            fake_model=Path(tmp)/"fake.onnx"
            fake_model.write_bytes(b"synthetic model stub, never executed")
            for mode in gpu.MODES:
                progress=Path(tmp)/(mode+".jsonl")
                code=gpu.child(mode, 3, 0, progress, str(fake_model), False, Path(tmp))
                self.assertEqual(code,0)
                events=[json.loads(s) for s in progress.read_text(
                    encoding="utf-8").splitlines()]
                self.assertEqual(events[-1]["event"],"CHILD_SUCCESS")
                self.assertEqual(sum(x["event"]=="ITERATION_COMPLETE"
                                     for x in events),3)
                if mode=="parallel":
                    self.assertEqual(sum(x["event"]=="RUN_COMPLETE"
                                         for x in events),6)

    def test_disallows_invalid_limits(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                gpu.supervisor("exe",modes=("parallel",),iterations=0,
                               trials=2,gpu_id=0,app_root=Path(tmp))
            with self.assertRaises(ValueError):
                gpu.supervisor("exe",modes=("fake",),iterations=20,
                               trials=1,gpu_id=0,app_root=Path(tmp))

    def test_screen_model_choice_preserves_explicit_trial_consent(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            model=root/"selected.onnx"
            model.write_bytes(b"not an actual model")
            (root/"screen_classifier_choice.json").write_text(
                json.dumps({"model_path":str(model),"trial_opt_in":True}),
                encoding="utf-8")
            chosen,opted_in=gpu._choose_screen_model(None,root)
            self.assertEqual(chosen,model)
            self.assertTrue(opted_in)
            selected,opted_in_override=gpu._choose_screen_model(str(model),root)
            self.assertEqual(selected,model)
            self.assertFalse(opted_in_override)

    def test_explicit_gpu_provider_is_required_without_cpu_fallback(self):
        class StubClassifier:
            provider="CPUExecutionProvider"
        with patch("champions_assist.screen_classifier.load_bundled_classifier",
                   return_value=(StubClassifier(),"MODEL_READY_CPU")):
            with self.assertRaisesRegex(gpu.DiagnosticFailure,
                                        "SCREEN_NOT_ON_DIRECTML"):
                gpu._create_screen(Path("fake.onnx"),False,0)

    def test_frozen_cli_smoke_is_gpu_free(self):
        with TemporaryDirectory() as tmp:
            target=Path(tmp)/"smoke.json"
            self.assertEqual(gpu.smoke(target),0)
            status=json.loads(target.read_text())
            self.assertEqual(status["status"],"GPU_DIAGNOSTIC_CLI_SMOKE_OK")
            self.assertEqual(status["modes"],list(gpu.COMPARISON_MODES))
            self.assertIs(status["gpu_inference_tested"],False)


if __name__ == "__main__":
    unittest.main()
