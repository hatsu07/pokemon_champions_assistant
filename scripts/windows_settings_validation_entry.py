"""Portable, settings-only Windows validation entry point.

This PyInstaller target is deliberately separate from a production MSI.  It
opens the existing desktop UI with match-start and API-send controls disabled,
so a Windows operator can exercise window selection, a read-only one-frame
preview, ROI selection, storage validation, and team CSV registration without
installing development Python.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Sequence


_CATALOGUE_FILES = (
    "Pokemon_Champions_MC_usable_pokemon.csv",
    "Pokemon_Champions_MC_move_master.csv",
    "Pokemon_Champions_MC_usable_held_items.csv",
    "Pokemon_Champions_MC_ability_master.csv",
    "Pokemon_Champions_MC_pokemon_learnsets_linked.csv",
)


def bundled_master_directory() -> Path:
    """Locate the non-private fixed catalogue packaged beside this executable."""
    source_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    directory = source_root / "masters"
    missing = [name for name in _CATALOGUE_FILES if not (directory / name).is_file()]
    if missing:
        raise RuntimeError("portable settings catalogue is incomplete: " + ", ".join(missing))
    return directory


def run_smoke() -> int:
    """Exercise Qt/settings imports without capture, a display, API, or user data."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from champions_assist.app_config import AppSettingsStore
    from champions_assist.desktop import ensure_application
    from champions_assist.settings_dialog import SettingsDialog
    from champions_assist.settings_management import DesktopSettingsService

    master_directory = bundled_master_directory()
    application = ensure_application()
    with tempfile.TemporaryDirectory(prefix="pokemon-champions-settings-smoke-") as temporary:
        root = Path(temporary)
        service = DesktopSettingsService(
            AppSettingsStore(root / "config" / "settings.json", defaults_root=root),
            master_directory=master_directory,
        )
        dialog = SettingsDialog(service, match_active=lambda: False)
        try:
            application.processEvents()
            payload = {
                "status": "GUI_SETTINGS_VALIDATION_SMOKE_OK",
                "catalogue_available": service.catalog is not None,
                "network": "NOT_USED",
                "openai_api": "DISABLED_BY_ENTRYPOINT",
                "game_control": "NOT_IMPLEMENTED",
            }
        finally:
            dialog.close()
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="PokemonChampionsSettingsValidation")
    parser.add_argument("--config", type=Path, help="non-secret settings JSON; use an isolated validation directory")
    parser.add_argument("--smoke", action="store_true", help="validate the frozen GUI package without capture, API, or game control")
    parser.add_argument("--version", action="store_true", help="show the portable validation package version")
    args = parser.parse_args(argv)

    from champions_assist import PROTOCOL_VERSION, __version__

    if args.version:
        print(json.dumps({"app_version": __version__, "mode": "SETTINGS_VALIDATION_ONLY", "protocol_version": PROTOCOL_VERSION}, ensure_ascii=False, sort_keys=True))
        return 0
    if args.smoke:
        return run_smoke()

    from champions_assist.__main__ import main as assistant_main

    desktop_args = [
        "desktop",
        "--master-dir",
        str(bundled_master_directory()),
        "--settings-validation-only",
    ]
    if args.config is not None:
        desktop_args.extend(("--config", str(args.config.expanduser())))
    return assistant_main(desktop_args)


if __name__ == "__main__":
    raise SystemExit(main())
