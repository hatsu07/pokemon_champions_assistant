"""Offline command-line entry point for the Phase 0/1 core."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from . import EFFECT_RULE_SCHEMA_VERSION, PROTOCOL_VERSION, __version__
from .history_inspect import inspect_workbook
from .fsm import MatchController
from .model_adapter import ModelAdapter, OpenAIResponsesTransport
from .observer_replay import add_observer_replay_parser, run_observer_replay
from .session_payloads import request_session_model_job
from .settings import ModelSettings
from .simulate import run_simulated_normal_trace
from .store import BattleStore
from .validation import add_phase6_parser, run_capture_comparison, run_capture_probe, run_roi_calibration


def _json_output(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def resolve_desktop_settings_paths(config_override: Path | None) -> tuple[Path, Path]:
    """Return an app-data root and the non-secret settings path.

    An explicit ``.../config/settings.json`` is useful for a portable
    validation build: all generated local data then remains beside that chosen
    validation root instead of silently mixing with the normal application
    profile.  Other explicit filenames use their containing directory as the
    root.  This helper performs no I/O.
    """
    if config_override is None:
        from .app_config import default_app_root

        app_root = default_app_root()
        return app_root, app_root / "config" / "settings.json"
    config_path = Path(config_override).expanduser()
    app_root = config_path.parent.parent if config_path.parent.name.casefold() == "config" else config_path.parent
    return app_root, config_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="champions-assist")
    parser.add_argument("--version", action="store_true", help="show package and contract versions")
    parser.add_argument("--dry-run", action="store_true", help="run offline only; no API or game-control capability exists")
    subparsers = parser.add_subparsers(dest="command")

    replay = subparsers.add_parser("replay", help="rebuild one match projection from its append-only events")
    replay.add_argument("--db", type=Path, required=True)
    replay.add_argument("--match-id", required=True)

    inspect = subparsers.add_parser("inspect-history", help="read XLSX sheet structure without importing or modifying it")
    inspect.add_argument("path", type=Path)

    simulate = subparsers.add_parser("simulate", help="run a labelled synthetic Phase 3 mock-model session trace")
    simulate.add_argument("--master-dir", type=Path, default=Path("masters"))

    desktop_demo = subparsers.add_parser("desktop-demo", help="render the labelled Phase 5 mock desktop demonstration")
    desktop_demo.add_argument("--master-dir", type=Path, default=Path("masters"))
    desktop_demo.add_argument("--hold-ms", type=int, default=0, help="keep the demonstration window open for this many milliseconds")
    desktop_demo.add_argument("--offscreen", action="store_true", help="use Qt offscreen rendering for Docker verification")

    desktop = subparsers.add_parser("desktop", help="start the local observer-only PySide6 desktop shell")
    desktop.add_argument("--master-dir", type=Path, default=Path("masters"))
    desktop.add_argument("--db", type=Path, help="override the configured local SQLite path")
    desktop.add_argument("--config", type=Path, help="override the non-secret settings.json path")
    desktop.add_argument("--offscreen", action="store_true", help="use Qt offscreen rendering for CI/Docker verification")
    desktop.add_argument("--real-api", action="store_true", help="select the Responses transport; environment safeguards still must permit it")
    desktop.add_argument(
        "--settings-validation-only",
        action="store_true",
        help="disable match start and API-send controls for a local settings/capture validation session",
    )

    api_smoke = subparsers.add_parser("api-smoke", help="explicitly opt in to one real SELECT Responses API smoke request")
    api_smoke.add_argument("--master-dir", type=Path, default=Path("masters"))
    api_smoke.add_argument("--real-api", action="store_true", help="permit a paid external API attempt when environment safeguards also pass")
    add_observer_replay_parser(subparsers)
    add_phase6_parser(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.version:
        _json_output({"app_version": __version__, "protocol_version": PROTOCOL_VERSION, "effect_rule_schema": EFFECT_RULE_SCHEMA_VERSION})
        return 0
    if args.command == "replay":
        store = BattleStore(args.db)
        try:
            _json_output(store.replay(args.match_id))
        finally:
            store.close()
        return 0
    if args.command == "inspect-history":
        _json_output(inspect_workbook(args.path))
        return 0
    if args.command == "simulate":
        _json_output(run_simulated_normal_trace(args.master_dir))
        return 0
    if args.command == "desktop-demo":
        if args.hold_ms < 0:
            raise ValueError("--hold-ms cannot be negative")
        if args.offscreen:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from .desktop_demo import run_desktop_demo

        _json_output(run_desktop_demo(args.master_dir, hold_ms=args.hold_ms))
        return 0
    if args.command == "desktop":
        if args.offscreen:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from .app_config import AppSettingsStore
        from .desktop import AdapterJobExecutor, DesktopController, DesktopMainWindow, DesktopModelDispatcher, ensure_application
        from .model_adapter import MockResponsesTransport
        from .settings_management import DesktopSettingsService

        if args.settings_validation_only and args.real_api:
            raise ValueError("--settings-validation-only cannot be combined with --real-api")
        app_root, config_path = resolve_desktop_settings_paths(args.config)
        settings_service = DesktopSettingsService(AppSettingsStore(config_path, defaults_root=app_root), master_directory=args.master_dir)
        database_path = args.db or Path(settings_service.settings.storage.data_root) / "battle.sqlite3"
        store = BattleStore(database_path)
        dispatcher: DesktopModelDispatcher | None = None
        try:
            application = ensure_application()
            desktop_controller = DesktopController(
                store=store,
                match_controller=MatchController(args.master_dir, store=store),
                settings_service=settings_service,
            )
            settings = ModelSettings.from_environment()
            if args.real_api:
                transport_factory = lambda: OpenAIResponsesTransport(settings)
            else:
                # A deliberately empty local transport keeps normal desktop
                # startup offline.  Fixture/demo runners provide mock replies.
                transport_factory = lambda: MockResponsesTransport(())
            dispatcher = DesktopModelDispatcher(
                AdapterJobExecutor(database_path, transport_factory=transport_factory, settings=settings)
            )
            desktop_controller.bind_dispatcher(dispatcher)
            window = DesktopMainWindow(
                desktop_controller,
                settings_service=settings_service,
                settings_validation_only=args.settings_validation_only,
            )
            window.show()
            return application.exec()
        finally:
            if dispatcher is not None:
                dispatcher.close()
            store.close()
    if args.command == "observer-replay":
        _json_output(run_observer_replay(args))
        return 0
    if args.command == "capture-probe":
        _json_output(run_capture_probe(args))
        return 0
    if args.command == "roi-calibrate":
        _json_output(run_roi_calibration(args))
        return 0
    if args.command == "capture-compare":
        _json_output(run_capture_comparison(args))
        return 0
    if args.command == "api-smoke":
        if not args.real_api:
            _json_output({"status": "REFUSED", "reason": "PASS_--real-api_AND_SET_RUNTIME_ENVIRONMENT"})
            return 2
        settings = ModelSettings.from_environment()
        blocked = settings.real_transport_block_reason()
        if blocked is not None:
            _json_output({"status": "REFUSED", "reason": blocked})
            return 2
        store = BattleStore(":memory:")
        try:
            controller = MatchController(args.master_dir, store=store)
            session = controller.start_match("explicit-api-smoke", now_ms=0)
            opponents = tuple(list(session.assets.matrix_index)[:6])
            session.receive_opponent_preview(opponents, now_ms=1)
            outcome = request_session_model_job(
                session,
                ModelAdapter(store=store, transport=OpenAIResponsesTransport(settings), settings=settings),
                phase="SELECT",
                request_id="explicit-api-smoke-select",
                now_ms=2,
            )
            _json_output({"status": outcome.status.value, "reason": outcome.reason, "network": "EXPLICIT_OPT_IN_ATTEMPTED"})
            return 0 if outcome.response is not None else 1
        finally:
            store.close()
    _json_output(
        {
            "app_version": __version__,
            "mode": "OFFLINE_DRY_RUN" if args.dry_run else "OFFLINE_STATUS",
            "protocol_version": PROTOCOL_VERSION,
            "live_capture": "NOT_CONFIGURED",
            "openai_api": "DISABLED_BY_DEFAULT; MOCK_TRANSPORT_AVAILABLE; REAL_RESPONSES_REQUIRES_EXPLICIT_ENV_OPT_IN",
            "game_control": "NOT_IMPLEMENTED",
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
