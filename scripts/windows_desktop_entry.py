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
        from champions_assist.crash_diagnostics import record_exception
        record_exception("STARTUP_EXCEPTION", exc, stage="WINDOWS_ENTRY")
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


def probe_bundled_ocr_execution() -> dict[str, object]:
    """Exercise native OCR via the *bundled executable* and TSV parser.

    Runtime execution is a release gate. Synthetic glyph accuracy is a
    separate diagnostic, not proof of Japanese recognition on BlueStacks:
    different bundled Tesseract models can legitimately read the same
    synthetic ASCII crop differently even with a healthy OCR process.
    """
    import cv2
    import numpy as np
    from champions_assist.ocr import TesseractJapaneseOcr

    sample=np.full((150,800,3),255,dtype=np.uint8)
    cv2.putText(sample,"97%",(25,106),
                cv2.FONT_HERSHEY_SIMPLEX,2.1,(0,0,0),3,cv2.LINE_AA)
    reading=TesseractJapaneseOcr(timeout_seconds=5.0).read(
        sample,roi_name="opponent_hp_percent")
    if reading.engine!="tesseract-jpn+eng":
        raise RuntimeError("BUNDLED_OCR_ENGINE_MISMATCH")
    if reading.confidence is not None and not 0.0<=reading.confidence<=1.0:
        raise RuntimeError("BUNDLED_OCR_CONFIDENCE_INVALID")
    return {
        "bundled_native_ocr": "OCR_NATIVE_EXECUTION_OK",
        # Only a result of a generated, non-user image: never emit OCR text.
        "synthetic_numeric_recognized": "97" in reading.text,
    }


def probe_gpu_ocr_contract() -> dict[str, object]:
    """Validate bundled source bytes and actual recognition from frozen EXE."""
    from champions_assist.ocr_onnx import (
        MODEL_DET, MODEL_REC, ENGINE_NAME)
    from champions_assist.ocr_smoke import verify_real_cpu_ocr

    if (MODEL_DET, MODEL_REC) != (
            "PP-OCRv6_small_det", "PP-OCRv6_small_rec"):
        raise RuntimeError("ONNX_OCR_MODEL_MISMATCH")
    reading = verify_real_cpu_ocr()
    return {"gpu_ocr_backend": ENGINE_NAME,
            "gpu_ocr_runtime": "BUNDLED_ONNX_RUNTIME",
            "gpu_inference_tested": False,
            **reading}


def run_smoke(report_path: Path) -> int:
    """Start the REAL desktop offline with an isolated profile, then close it."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QTimer
    from champions_assist.desktop import ensure_application
    from champions_assist.__main__ import main as assistant_main

    master_dir = bundled_master_directory()
    # CI has no GPU. Verify configured backend without misreporting actual
    # CUDA inference as having been tested on a GPU-free runner.
    ocr_probe=probe_gpu_ocr_contract()
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
                **ocr_probe,
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
    parser.add_argument("--gpu-diagnostics", action="store_true",
                        help="isolate five offline DirectML stress cases, including cross-process")
    parser.add_argument("--gpu-diagnostic-child",
                        choices=("rec", "classifier", "serial", "parallel"))
    parser.add_argument("--gpu-diagnostic-modes", type=str,
                        default="rec,classifier,serial,parallel,multiprocess",
                        help="comma-separated comparison modes")
    parser.add_argument("--gpu-diagnostic-process-child", choices=("rec", "screen"),
                        help="isolated subprocess worker for condition E")
    parser.add_argument("--gpu-diagnostic-sync-dir", type=Path)
    parser.add_argument("--gpu-diagnostic-progress", type=Path)
    parser.add_argument("--gpu-diagnostic-smoke", type=Path)
    parser.add_argument("--gpu-id", type=int, default=0)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--screen-model", type=str)
    args = parser.parse_args(argv)

    from champions_assist.crash_diagnostics import (
        install_crash_diagnostics, record_event)
    install_crash_diagnostics(_diagnostic_root())
    record_event("WINDOWS_ENTRY_STARTED",
                 component="DESKTOP_SMOKE" if args.smoke is not None else "DESKTOP_GUI")
    # A prerelease can be compared by its tested build revision without
    # persisting any user settings or private filesystem paths.
    try:
        revision_file = Path(sys.executable).parent / "BUILD_REVISION.txt"
        if revision_file.is_file():
            revision = dict(
                line.split("=", 1) for line in
                revision_file.read_text(encoding="utf-8-sig").splitlines()
                if "=" in line
            )
            record_event("BUILD_REVISION",
                         build_revision=revision.get("commit_sha"),
                         build_version=revision.get("version"))
    except (OSError, ValueError, TypeError):
        record_event("BUILD_REVISION_UNAVAILABLE")
    rc = 1
    try:
        if args.gpu_diagnostic_smoke is not None:
            from champions_assist.gpu_stress_diagnostics import smoke
            rc = smoke(args.gpu_diagnostic_smoke)
            return rc
        if args.gpu_diagnostic_process_child is not None:
            if args.gpu_diagnostic_progress is None or args.gpu_diagnostic_sync_dir is None:
                raise ValueError("GPU_MULTIPROCESS_PROGRESS_AND_SYNC_REQUIRED")
            from champions_assist.gpu_multiprocess_diagnostics import process_child
            from champions_assist.app_config import default_app_root
            rc = process_child(args.gpu_diagnostic_process_child,
                               args.iterations, args.gpu_id,
                               args.gpu_diagnostic_progress,
                               args.gpu_diagnostic_sync_dir,
                               args.screen_model, default_app_root())
            return rc
        if args.gpu_diagnostic_child is not None:
            if args.gpu_diagnostic_progress is None:
                raise ValueError("GPU_DIAGNOSTIC_PROGRESS_REQUIRED")
            from champions_assist.gpu_stress_diagnostics import child
            from champions_assist.app_config import default_app_root
            rc = child(args.gpu_diagnostic_child, args.iterations, args.gpu_id,
                       args.gpu_diagnostic_progress, args.screen_model,
                       False, default_app_root())
            return rc
        if args.gpu_diagnostics:
            from champions_assist.gpu_stress_diagnostics import COMPARISON_MODES, supervisor
            from champions_assist.app_config import default_app_root
            output, report = supervisor(
                sys.executable, modes=tuple(args.gpu_diagnostic_modes.split(",")),
                iterations=args.iterations,
                trials=args.trials, gpu_id=args.gpu_id,
                app_root=default_app_root(), model=args.screen_model)
            if os.name == "nt":
                import ctypes
                message = (
                    "GPU診断が終了しました。\n"
                    f"成功: {report['success_count']} / {len(report['cases'])}\n"
                    f"アクセス違反: {report['crash_count']}\n"
                    "保存先: " + str(output) + "\n"
                    "summary.jsonと各JSONLを確認してください。"
                )
                ctypes.windll.user32.MessageBoxW(
                    None, message, "Pokemon Champions GPU診断", 0x40)
            rc = 0 if report["success_count"] == len(report["cases"]) else 3
            return rc
        if args.smoke is not None:
            rc = run_smoke(args.smoke)
            return rc
        from champions_assist.__main__ import main as assistant_main

        command = ["desktop", "--master-dir", str(bundled_master_directory())]
        if args.config is not None:
            command.extend(["--config", str(args.config.expanduser())])
        rc = assistant_main(command)
        return rc
    except Exception as exc:
        log_path = _record_startup_failure(exc)
        if args.smoke is None:
            _show_startup_failure(log_path)
        return 1
    finally:
        record_event("WINDOWS_ENTRY_EXIT", exit_code=rc)


if __name__ == "__main__":
    raise SystemExit(main())
