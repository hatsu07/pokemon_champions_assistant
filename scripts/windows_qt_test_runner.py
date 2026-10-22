"""Run GUI unit tests and exit by their result, not Qt DLL finalizer state.

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
    # New inline-settings tests reuse the same Qt singleton. Run them before
    # the legacy dialog suite, whose tearDownClass intentionally quits Qt.
    suite=unittest.defaultTestLoader.loadTestsFromNames(
        ("tests.test_chatgpt_settings_inline", "tests.test_vision_preferences")
    )
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if result.wasSuccessful() else 1)


if __name__=="__main__":
    main()
