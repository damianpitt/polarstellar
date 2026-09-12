"""Desktop entry point; version inspection does not require a display."""

import argparse
import asyncio
import sys
from pathlib import Path

from polarstellar import __version__


def main() -> int:
    """Start the desktop event loop; construct the optional cache without opening it."""
    parser = argparse.ArgumentParser(description="PolarStellar desktop investigation toolkit")
    parser.add_argument("--version", action="version", version=f"PolarStellar {__version__}")
    parser.parse_args()

    from PySide6.QtCore import QStandardPaths
    from PySide6.QtWidgets import QApplication
    from qasync import QEventLoop

    from polarstellar.app.window import MainWindow
    from polarstellar.stellar.horizon import HorizonProvider
    from polarstellar.stellar.service import AccountService
    from polarstellar.storage.database import SnapshotCache
    from polarstellar.storage.provider import CachedProvider

    app = QApplication(sys.argv)
    app.setApplicationName("PolarStellar")
    app.setOrganizationName("PolarStellar")
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    # Qt chooses the user's platform-specific cache directory, never the source checkout.
    location = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))
    provider = CachedProvider(HorizonProvider(), SnapshotCache(location / "snapshots.sqlite3"))
    window = MainWindow(AccountService(provider))
    window.show()
    with loop:
        loop.run_forever()
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
