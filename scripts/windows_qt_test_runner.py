"""Run offline/Qt unit-test groups and exit by their real unittest results.

On Windows CI, PySide6's offscreen platform occasionally returns exit status
1 *after* unittest reports success, during interpreter/plugin shutdown. This
wrapper preserves every assertion failure as nonzero while leaving the real
desktop exe smoke test responsible for application startup/shutdown checks.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Running 'python scripts/...' places scripts/, not the repository root,
# on sys.path. Restore the root so tests.test_vision_preferences imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    # Each invocation still runs a separately scoped test group. Preserve
    # the test result while bypassing a nondeterministic interpreter/Qt DLL
    # finalizer crash seen *after* unittest prints OK on Windows.
    # Missing modules become unittest _FailedTest errors, not silent skips.
    names=sys.argv[1:] or ["tests.test_vision_preferences"]
    suite=unittest.defaultTestLoader.loadTestsFromNames(names)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    success=result.wasSuccessful()
    # CI diagnostic: distinguish assertion failure from Windows native process
    # termination. TextTestRunner prints "OK" only for successful assertions.
    print(f"UNITTEST_RUNNER_RESULT success={success} tests={result.testsRun} "
          f"errors={len(result.errors)} failures={len(result.failures)}",flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if success else 1)


if __name__=="__main__":
    main()
