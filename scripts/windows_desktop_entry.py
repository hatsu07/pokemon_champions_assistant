"""Windows PyInstaller GUI entry point (no terminal, no game input automation).

The production-style desktop is deliberately separated from the read-only
validation CLI and the settings-only GUI. All model transport stays mock-only
unless the existing desktop code is explicitly invoked through its real-API
switch, which this entry point does not expose.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from typing import Sequence


REQUIRED_BUNDLED_MASTERS = (
    "Pokemon_Champions_MC_live_decision_gate.txt",
    "Pokemon_Champions_MC_live_selection_matrix.txt",
    "Pokemon_Champions_MC_60sec_lookup_master.txt",
    "Pokemon_Champions_MC_usable_pokemon.csv",
    "Pokemon_Champions_MC_move_master.csv",
    "Pokemon_Champions_MC_usable_held_items.csv",
    "Pokemon_Champions_MC_ability_master.csv",
    "Pokemon_Champions_MC_pokemon_learnsets_linked.csv",
    "Pokemon_Champions_MC_team_2026-10-03.csv",
)


def bundled_master_directory() -> Path:
    """Find read-only bundled masters without depending on the working directory."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    master_dir = root / "masters"
    missing = [name for name in REQUIRED_BUNDLED_MASTERS if not (master_dir / name).is_file()]
    if missing:
        raise RuntimeError("bundled fixed masters missing: " + ", ".join(missing))
    return master_dir


def _diagnostic_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / "AppData" / "Local") / "PokemonChampionsAssistant" / "diagnostics"


def _record_startup_failure(exc: Exception) -> Path | None:
    """Log the exception TYPE and stack locations only; never log secrets/values."""
    try:
        root = _diagnostic_root()
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = root / f"startup-{stamp}.log"
        lines = [
            "Pokemon Champions Assistant startup failure",
            f"timestamp_utc={stamp}",
            f"error_type={type(exc).__name__}",
        ]
        for frame in traceback.extract_tb(exc.__traceback__):
            lines.append(f"at {Path(frame.filename).name}:{frame.lineno} {frame.name}")
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target
    except OSError:
        return None


def _show_startup_failure(log_path: Path | None) -> None:
    """Console-less failures must still be visible on Windows."""
    if sys.platform != "win32" or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return
    try:
        import ctypes

        detail = str(log_path) if log_path is not None else "診断ログの作成にも失敗しました。"
        message = "Pokémon Champions Assistantを起動できませんでした。\n診断情報: " + detail
        ctypes.windll.user32.MessageBoxW(None, message, "Pokémon Champions Assistant", 0x10)
    except Exception:
        pass


def run_smoke(report_path: Path) -> int:
    """Start the REAL desktop offline with an isolated profile, then close it."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QTimer
    from champions_assist.desktop import ensure_application
    from champions_assist.__main__ import main as assistant_main

    master_dir = bundled_master_directory()
    app = ensure_application()
    with tempfile.TemporaryDirectory(prefix="champions-desktop-smoke-") as temporary:
        root = Path(temporary)
        QTimer.singleShot(250, app.quit)
        result = assistant_main(
            ["desktop", "--master-dir", str(master_dir), "--config", str(root / "config" / "settings.json")]
        )
    if result != 0:
        raise RuntimeError("desktop smoke event loop exited unsuccessfully")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "status": "GUI_DESKTOP_SMOKE_OK",
                "game_control": "NOT_IMPLEMENTED",
                "network": "NOT_USED",
                "openai_api": "DISABLED_BY_ENTRYPOINT",
                "bundled_master_files": len(REQUIRED_BUNDLED_MASTERS),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="PokemonChampionsAssistant")
    parser.add_argument("--config", type=Path, help="optional local non-secret settings.json path")
    parser.add_argument("--smoke", type=Path, metavar="REPORT_JSON", help="offline headless GUI smoke test")
    args = parser.parse_args(argv)

    try:
        if args.smoke is not None:
            return run_smoke(args.smoke)
        from champions_assist.__main__ import main as assistant_main

        command = ["desktop", "--master-dir", str(bundled_master_directory())]
        if args.config is not None:
            command.extend(["--config", str(args.config.expanduser())])
        return assistant_main(command)
    except Exception as exc:
        log_path = _record_startup_failure(exc)
        if args.smoke is None:
            _show_startup_failure(log_path)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
