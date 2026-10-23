"""Portable Windows entrypoint for LOCAL training-data collection GUI.

No AI, networking, data upload, game inputs, screenshot persistence without
user approval, or model training runs at application startup.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from datetime import datetime, timezone


def _startup_error(exc):
    try:
        root=Path(os.environ.get("LOCALAPPDATA") or Path.home())/"PokemonChampionsAssistant"/"diagnostics"
        root.mkdir(parents=True,exist_ok=True)
        output=root/("dataset-collector-startup-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+".log")
        lines=["collector startup error", "error_type="+type(exc).__name__]
        lines += [f"{Path(f.filename).name}:{f.lineno} {f.name}"
                  for f in traceback.extract_tb(exc.__traceback__)]
        output.write_text("\n".join(lines),encoding="utf-8")
    except OSError:
        pass


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",type=Path)
    parser.add_argument("--dataset",type=Path)
    parser.add_argument("--smoke",type=Path,help="offline UI smoke output path")
    args=parser.parse_args(argv)
    try:
        if args.smoke is not None:
            os.environ["QT_QPA_PLATFORM"]="offscreen"
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QTimer
        from champions_assist.screen_dataset_collector import ScreenDatasetCollector
        from champions_assist.app_config import default_app_root
        app=QApplication.instance() or QApplication([])
        if args.smoke is None:
            window=ScreenDatasetCollector(config_path=args.config,dataset_root=args.dataset)
            window.show()
            return app.exec()
        with tempfile.TemporaryDirectory(prefix="champions-dataset-smoke-") as temporary:
            root=Path(temporary)
            window=ScreenDatasetCollector(
                config_path=root/"config"/"settings.json",
                dataset_root=root/"screens")
            window.show()
            QTimer.singleShot(200,app.quit)
            exit_code=app.exec()
            if exit_code:
                raise RuntimeError("dataset collector GUI smoke exited with an error")
            if list((root/"screens").rglob("*.png")):
                raise RuntimeError("dataset collector smoke saved images without approval")
            report={"status":"SCREEN_DATASET_COLLECTOR_SMOKE_OK",
                    "network":"NOT_USED",
                    "screenshot_files":"NOT_CREATED",
                    "game_control":"NOT_IMPLEMENTED"}
            args.smoke.parent.mkdir(parents=True,exist_ok=True)
            args.smoke.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
            return 0
    except Exception as exc:
        _startup_error(exc)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
