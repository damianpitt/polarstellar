"""Desktop entry point; version inspection does not require a display."""

import argparse
import asyncio
import json
import sys
import tempfile
import traceback
from pathlib import Path

from polarstellar import __version__


def main() -> int:
    """Start the desktop event loop; construct the optional cache without opening it."""
    parser = argparse.ArgumentParser(description="PolarStellar desktop investigation toolkit")
    parser.add_argument("--version", action="version", version=f"PolarStellar {__version__}")
    parser.add_argument(
        "--smoke-test", action="store_true", help="Run an offline startup check with temporary data"
    )
    parser.add_argument("--smoke-report", type=Path, help="Write a JSON startup-validation report")
    parser.add_argument(
        "--smoke-screenshot", type=Path, help="Save the checked window for release review"
    )
    parser.add_argument(
        "--live-check",
        action="store_true",
        help="Run explicit read-only Mainnet/Testnet release checks without opening the desktop",
    )
    parser.add_argument(
        "--live-report", type=Path, help="Write the opt-in live qualification JSON report"
    )
    args = parser.parse_args()
    if args.live_check:
        if args.live_report is None:
            parser.error("--live-check requires --live-report")
        if args.smoke_test or args.smoke_report or args.smoke_screenshot:
            parser.error("Live checks and offline smoke checks are separate commands")
        # This opt-in path has no QApplication or local storage. Windowed native
        # apps must write a report because they may have no attached console.
        from polarstellar.stellar.live_checks import qualify

        report = asyncio.run(qualify())
        args.live_report.parent.mkdir(parents=True, exist_ok=True)
        args.live_report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return 0 if report["status"] == "passed" else 1
    if args.live_report:
        parser.error("--live-report requires --live-check")
    if args.smoke_test and args.smoke_report is None:
        parser.error("--smoke-test requires --smoke-report")
    if not args.smoke_test and (args.smoke_report or args.smoke_screenshot):
        parser.error("smoke output options require --smoke-test")

    from PySide6.QtCore import QStandardPaths
    from PySide6.QtWidgets import QApplication
    from qasync import QEventLoop

    from polarstellar.app.window import MainWindow
    from polarstellar.stellar.horizon import HorizonProvider
    from polarstellar.stellar.service import AccountService
    from polarstellar.storage.database import SnapshotCache
    from polarstellar.storage.investigations import InvestigationStore
    from polarstellar.storage.provider import CachedProvider
    from polarstellar.storage.watchlists import WatchlistStore

    # Validation never opens the user's existing cache or investigations. Normal
    # launches continue to use the platform's standard application directories.
    temporary = (
        tempfile.TemporaryDirectory(prefix="polarstellar-smoke-") if args.smoke_test else None
    )
    app = QApplication(sys.argv)
    app.setApplicationName("PolarStellar")
    app.setOrganizationName("PolarStellar")
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    # Qt chooses the user's platform-specific cache directory, never the source checkout.
    location = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))
    if temporary is not None:
        location = Path(temporary.name)
    provider = CachedProvider(HorizonProvider(), SnapshotCache(location / "snapshots.sqlite3"))
    # Durable evidence belongs in application data, separate from disposable cache files.
    data_location = Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    )
    if temporary is not None:
        data_location = Path(temporary.name)
    window = MainWindow(
        AccountService(provider),
        InvestigationStore(data_location / "investigations.sqlite3"),
        watchlist_store=WatchlistStore(data_location / "watchlists.sqlite3"),
    )
    window.show()
    with loop:
        status = 0
        if args.smoke_test:
            from polarstellar.app.smoke import validate

            try:
                report = loop.run_until_complete(
                    validate(window, data_location, args.smoke_screenshot)
                )
            except Exception:  # noqa: BLE001 -- Windowed executables need a machine-readable failure report.
                report = {
                    "status": "failed",
                    "version": __version__,
                    "error": traceback.format_exc(),
                }
                status = 1
            args.smoke_report.parent.mkdir(parents=True, exist_ok=True)
            args.smoke_report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        else:
            loop.run_forever()
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    if temporary is not None:
        temporary.cleanup()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
