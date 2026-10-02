"""Validate the diagnostic startup path as a real subprocess with disposable storage."""

import json
import os
import subprocess
import sys

from polarstellar import __version__


def test_offline_startup_report_and_render(tmp_path):
    """A source launch exercises the same smoke path later required in each frozen bundle."""
    report, screenshot = tmp_path / "report.json", tmp_path / "window.png"
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "polarstellar.app.main",
            "--smoke-test",
            "--smoke-report",
            str(report),
            "--smoke-screenshot",
            str(screenshot),
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == "passed" and payload["version"] == __version__
    assert not payload["frozen"]
    assert {
        "sqlite_saved_investigation_restart",
        "sqlite_watchlist_restart",
        "csv_json_exports",
        "httpx_tls_certificate_bundle",
        "qt_navigation_and_async_event_loop",
    }.issubset(payload["checks"])
    assert screenshot.read_bytes().startswith(b"\x89PNG")


def test_smoke_requires_explicit_report_destination():
    """Do not silently start a smoke run without a report for windowed executable validation."""
    result = subprocess.run(
        [sys.executable, "-m", "polarstellar.app.main", "--smoke-test"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0 and "requires --smoke-report" in result.stderr
