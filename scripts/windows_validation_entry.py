"""PyInstaller entry point for the Phase 6 validation harness only.

This is not an MSI entry point and does not package a production release.
"""

from champions_assist.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
