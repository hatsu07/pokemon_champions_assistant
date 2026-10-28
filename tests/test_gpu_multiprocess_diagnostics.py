"""Phase-1 cross-process GPU experiment tests (fake inference, real processes)."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from champions_assist import gpu_multiprocess_diagnostics as multi


# Created within the temp directory; subprocesses import only the public
# worker function and inject synthetic sleep instead of loading ONNX/GPU.
WORKER_SCRIPT = """
import os
from pathlib import Path
import sys
from time import sleep
from champions_assist.gpu_multiprocess_diagnostics import process_child

mode, role, iterations, progress, sync, root = sys.argv[1:]
count = 0
def fake_factory(role, gpu_id, model, app_root):
    def run():
        global count
        count += 1
        if mode == 'fail' and role == 'rec' and count == 1:
            os._exit(7)
        if mode == 'hang' and role == 'screen':
            sleep(5)
        else:
            sleep(0.20)
    return run

raise SystemExit(process_child(
    role, int(iterations), 0, Path(progress), Path(sync), None, Path(root),
    action_factory=fake_factory))
"""


class CrossProcessDiagnosticsTests(unittest.TestCase):
    def _case(self, root, *, mode="ok", iterations=2, timeout=15):
        script = root / "worker.py"
        script.write_text(WORKER_SCRIPT, encoding="utf-8")
        def command(role, progress, sync):
            return [sys.executable, str(script), mode, role, str(iterations),
                    str(progress), str(sync), str(root)]
        return multi.run_case(
            "exe-unused", output=root, trial=1, iterations=iterations,
            gpu_id=0, app_root=root, command_factory=command,
            timeout=timeout)

    def test_real_processes_overlap_and_report_each_child(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root)
            self.assertEqual(case["status"], "SUCCESS", case)
            self.assertEqual(case["mode"], "multiprocess")
            self.assertEqual(case["measured_iterations"], 2)
            self.assertEqual(case["overlap_iterations"], 2)
            self.assertGreater(case["overlap_ms"], 0)
            self.assertEqual(set(case["workers"]), {"rec", "screen"})
            self.assertTrue(all(c["status"] == "SUCCESS"
                                for c in case["workers"].values()))
            for role in multi.ROLES:
                self.assertEqual(case["workers"][role]["last_checkpoint"]["event"],
                                 "CHILD_SUCCESS")
            self.assertFalse((root / ".multiprocess-01-sync").exists())

    def test_failure_in_one_process_terminates_other(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root, mode="fail", iterations=3)
            self.assertEqual(case["status"], "ERROR", case)
            self.assertEqual(case["workers"]["rec"]["exit_code_unsigned"], 7)
            self.assertNotEqual(case["workers"]["screen"]["status"], "SUCCESS")
            self.assertFalse((root / ".multiprocess-01-sync").exists())

    def test_timeout_terminates_children_and_does_not_claim_success(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            case = self._case(root, mode="hang", iterations=2, timeout=1.5)
            self.assertEqual(case["status"], "TIMEOUT", case)
            self.assertFalse((root / ".multiprocess-01-sync").exists())

    def test_absent_role_or_invalid_gpu_is_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                multi.run_case("exe",output=root,trial=0,iterations=2,
                               gpu_id=0,app_root=root)
            self.assertEqual(multi.process_child("invalid",2,0,root/"x.jsonl",
                                                 root,None,root),2)

    def test_overlap_calculation_rejects_serial_intervals(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)
            a=root/"rec.jsonl"
            b=root/"screen.jsonl"
            a.write_text(json.dumps({"event":"RUN_COMPLETE","iteration":1,
                                     "run_start_ns":100,"run_end_ns":200})+"\n",
                         encoding="utf-8")
            b.write_text(json.dumps({"event":"RUN_COMPLETE","iteration":1,
                                     "run_start_ns":210,"run_end_ns":250})+"\n",
                         encoding="utf-8")
            self.assertEqual(multi._overlap(a,b),(0,0.0,1))


if __name__ == "__main__":
    unittest.main()
