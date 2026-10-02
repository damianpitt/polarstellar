"""Minimal executable entry point analyzed by PyInstaller on each target platform."""

from polarstellar.app.main import main

if __name__ == "__main__":
    raise SystemExit(main())
