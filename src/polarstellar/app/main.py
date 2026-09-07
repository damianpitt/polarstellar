"""Desktop entry point; version inspection does not require a display."""

import argparse
import sys

from polarstellar import __version__


def main() -> int:
    parser = argparse.ArgumentParser(description="PolarStellar desktop investigation toolkit")
    parser.add_argument("--version", action="version", version=f"PolarStellar {__version__}")
    parser.parse_args()

    from PySide6.QtWidgets import QApplication

    from polarstellar.app.window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("PolarStellar")
    app.setOrganizationName("PolarStellar")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
