"""Qualify a downloaded native bundle on a separate runtime-only hosted runner.

Only Python's standard library is needed for this launcher. The app must supply its
own runtime dependencies. These automated checks do not claim interactive acceptance
of Gatekeeper, SmartScreen, system file dialogs, or every supported desktop version.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path


def read_json(path):
    """Read a UTF-8 qualification report; absent or malformed reports are validation failures."""
    return json.loads(path.read_text(encoding="utf-8"))


def architecture(value):
    """Normalize Windows/Unix architecture spelling without conflating Intel and Apple Silicon."""
    return {"amd64": "x86_64", "aarch64": "arm64"}.get(value.lower(), value.lower())


def checked_archive(folder, version, target):
    """Require one exact release archive and validate its adjacent filename-bound SHA-256."""
    suffix = ".tar.gz" if target.startswith("linux-") else ".zip"
    archive = folder / f"PolarStellar-{version}-{target}{suffix}"
    parts = archive.with_name(archive.name + ".sha256").read_text(encoding="utf-8").split()
    if len(parts) != 2 or parts[1] != archive.name or len(parts[0]) != 64:
        raise ValueError("Archive checksum must name the exact expected release file.")
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != parts[0]:
        raise ValueError("Downloaded native archive checksum does not match.")
    return archive, digest


def unpack(archive, destination, system):
    """Extract our checksum-checked artifact, preserving macOS bundle links and signatures."""
    if system == "Darwin":
        subprocess.run(["ditto", "-x", "-k", str(archive), str(destination)], check=True)
    elif system == "Linux":
        with tarfile.open(archive) as source:
            source.extractall(destination, filter="data")
    else:
        with zipfile.ZipFile(archive) as source:
            source.extractall(destination)


def accepted_report(payload, version, system, arch, *, live=False):
    """Require successful frozen-app evidence bound to the expected version and native platform."""
    if (
        payload.get("status") != "passed"
        or payload.get("version") != version
        or payload.get("platform") != system
        or not payload.get("frozen")
        or architecture(payload.get("architecture", "")) != arch
    ):
        raise ValueError("Report does not identify a successful expected frozen application.")
    if live:
        networks = payload.get("networks", [])
        if {row.get("network") for row in networks} != {"Mainnet", "Testnet"} or len(networks) != 2:
            raise ValueError("Live qualification must independently check both networks.")
        for row in networks:
            if (
                row.get("status") != "passed"
                or {step.get("name") for step in row.get("checks", [])}
                != {"horizon_workflows", "rpc_workflows"}
                or any(step.get("status") != "passed" for step in row["checks"])
            ):
                raise ValueError("Live report contains a failed or incomplete provider check.")
    else:
        expected = {"Darwin": "cocoa", "Linux": "xcb", "Windows": "windows"}[system]
        if payload.get("qt_platform_plugin") != expected:
            raise ValueError("Qualification did not use the native desktop Qt plugin.")
        if not {
            "account_lookup_exact_amounts",
            "csv_json_exports",
            "sqlite_saved_investigation_restart",
            "sqlite_watchlist_restart",
            "qt_navigation_and_async_event_loop",
            "stellar_sdk_xdr_exact_integer",
            "networkx_relationship_model",
            "httpx_tls_certificate_bundle",
            "desktop_compact_navigation",
            "desktop_notes_reopen",
            "desktop_invalid_search_recovery",
        }.issubset(payload.get("checks", [])):
            raise ValueError("Required desktop qualification workflows were not exercised.")


def qualify(bundle_dir, output, version, commit, target):
    """Verify provenance and run the extracted app from disposable storage without installed project packages."""
    system, arch = platform.system(), architecture(platform.machine())
    expected = {"Darwin": "macos-arm64", "Linux": "linux-x86_64", "Windows": "windows-x86_64"}
    if expected.get(system) != target or arch != target.rsplit("-", 1)[-1]:
        raise ValueError("Qualification host does not match the requested native target.")
    runtime_packages = ("polarstellar", "PySide6", "httpx", "networkx", "stellar_sdk")
    if any(importlib.util.find_spec(name) is not None for name in runtime_packages):
        raise ValueError(
            "Use an isolated standard-library launcher without project/runtime packages installed."
        )
    archive, digest = checked_archive(bundle_dir, version, target)
    output.mkdir(parents=True, exist_ok=True)
    summary = {
        "version": version,
        "commit": commit,
        "target": target,
        "sha256": digest,
        "checked_at": datetime.now(UTC).isoformat(),
        "checks": [],
        "launcher_runtime_packages_absent": list(runtime_packages),
        "environment": "Separate fresh hosted runner; no project/Qt/SDK packages installed for this job.",
        "limits": "Automated native desktop and live-provider checks; interactive OS approval prompts, "
        "file dialogs and clean consumer-machine acceptance remain pending.",
    }
    with tempfile.TemporaryDirectory(prefix="polarstellar-runtime-") as temporary:
        root = Path(temporary)
        unpack(archive, root, system)
        folder = root / "PolarStellar"
        # The requested local screenshot collection and private project guidance
        # must never be included in a distributed application archive.
        if any(
            "Software GUI" in path.parts or path.name in ("AGENTS.md", "capture_examples.py")
            for path in folder.rglob("*")
        ):
            raise ValueError("Native bundle contains an excluded local project file.")
        manifest = read_json(folder / "BUILD_INFO.json")
        if (
            manifest.get("version"),
            manifest.get("commit"),
            manifest.get("platform"),
            manifest.get("architecture"),
            manifest.get("working_tree_dirty"),
        ) != (version, commit, system, arch, False):
            raise ValueError("Bundle provenance differs from the expected clean source commit.")
        if system == "Darwin":
            subprocess.run(
                ["codesign", "--verify", "--deep", "--strict", str(folder / "PolarStellar.app")],
                check=True,
            )
            program = folder / "PolarStellar.app" / "Contents" / "MacOS" / "PolarStellar"
        else:
            program = folder / ("PolarStellar.exe" if system == "Windows" else "PolarStellar")
        environment = dict(os.environ)
        for key in (
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "QT_PLUGIN_PATH",
            "QT_QPA_PLATFORM_PLUGIN_PATH",
            "QT_QPA_PLATFORM",
        ):
            environment.pop(key, None)
        environment["PYTHONNOUSERSITE"] = "1"
        for live in (False, True):
            name = "live" if live else "desktop"
            report = output / f"{name}.json"
            screenshot = output / "desktop.png"
            # Do not reuse a stale successful report after a failed launch.
            report.unlink(missing_ok=True)
            if not live:
                screenshot.unlink(missing_ok=True)
            arguments = (
                ["--live-check", "--live-report", str(report)]
                if live
                else [
                    "--smoke-test",
                    "--smoke-report",
                    str(report),
                    "--smoke-screenshot",
                    str(screenshot),
                ]
            )
            step = {"name": name, "status": "failed"}
            summary["checks"].append(step)
            try:
                with (output / f"{name}.log").open("w", encoding="utf-8") as log:
                    result = subprocess.run(
                        [str(program), *arguments],
                        cwd=root,
                        env=environment,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=330 if live else 180,
                        check=False,
                    )
                payload = read_json(report)
                accepted_report(payload, version, system, arch, live=live)
                if result.returncode != 0 or (not live and not screenshot.is_file()):
                    raise ValueError(
                        "Extracted app failed to complete the requested qualification."
                    )
                step["status"] = "passed"
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                step["error"] = str(exc)
    summary["status"] = (
        "passed" if all(step["status"] == "passed" for step in summary["checks"]) else "failed"
    )
    (output / "qualification.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main():
    """Write reviewable runtime evidence and fail CI if either desktop or live qualification fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--version", help="Expected version; defaults to this checkout's pyproject.toml"
    )
    parser.add_argument("--commit", required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    version = (
        args.version
        or tomllib.loads(
            (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"]
    )
    try:
        result = qualify(
            args.bundle_dir.resolve(), args.output.resolve(), version, args.commit, args.target
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result = {
            "status": "failed",
            "version": version,
            "commit": args.commit,
            "target": args.target,
            "error": str(exc),
        }
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "qualification.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
