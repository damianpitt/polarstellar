"""Build, archive and validate a portable app on its own operating system and architecture."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "native"


def run(arguments, **kwargs):
    """Fail immediately on build/validation errors, with shell expansion deliberately disabled."""
    return subprocess.run(arguments, check=True, **kwargs)


def architecture():
    """Normalize host architecture names so downloadable filenames are unambiguous."""
    name = platform.machine().lower()
    return {"amd64": "x86_64", "aarch64": "arm64"}.get(name, name)


def notices(destination):
    """Include installed distribution licensing information without copying project/private files.

    This inventory deliberately includes build tools as well as runtime libraries;
    it describes the build environment and does not claim every package is bundled.
    Shared Qt libraries remain separate files in the portable distribution.
    """
    sections = [
        (
            "PolarStellar third-party build-environment inventory\n"
            "Some listed tools are used only to build this application.\n"
            "PySide6/Qt libraries are dynamically bundled. Qt source and licensing:\n"
            "https://www.qt.io/licensing/ and https://download.qt.io/official_releases/\n"
            "PyInstaller distribution exception: https://pyinstaller.org/en/stable/license.html\n"
        )
    ]
    for distribution in sorted(
        importlib.metadata.distributions(), key=lambda item: item.metadata["Name"].lower()
    ):
        sections.append(
            f"\n=== {distribution.metadata['Name']} {distribution.version} ===\n"
            f"License: {distribution.metadata.get('License-Expression') or distribution.metadata.get('License', 'See included license text')}\n"
        )
        for file in distribution.files or ():
            if "dist-info" not in str(file) or not any(
                word in file.name.lower() for word in ("license", "copying", "notice")
            ):
                continue
            path = distribution.locate_file(file)
            if path.is_file():
                sections.append(path.read_text(encoding="utf-8", errors="replace"))
    destination.write_text("\n".join(sections), encoding="utf-8")


def windows_version(path, version):
    """Create trusted PyInstaller version-resource source from the package's numeric version."""
    numbers = tuple(int(part) for part in version.split("."))
    numbers = (*numbers, *([0] * (4 - len(numbers))))
    path.write_text(
        "VSVersionInfo(ffi=FixedFileInfo(filevers="
        + repr(numbers)
        + ", prodvers="
        + repr(numbers)
        + ", mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)), "
        + "kids=[StringFileInfo([StringTable('040904B0', ["
        + "StringStruct('ProductName', 'PolarStellar'), "
        + f"StringStruct('ProductVersion', {version!r}), StringStruct('FileVersion', {version!r}), "
        + "StringStruct('FileDescription', 'Stellar network investigation'), "
        + "StringStruct('OriginalFilename', 'PolarStellar.exe')])]), "
        + "VarFileInfo([VarStruct('Translation', [1033, 1200])])])",
        encoding="utf-8",
    )


def executable(folder, system):
    """Locate the runnable program in an extracted distribution, including the macOS app bundle."""
    if system == "Darwin":
        return folder / "PolarStellar.app" / "Contents" / "MacOS" / "PolarStellar"
    return folder / ("PolarStellar.exe" if system == "Windows" else "PolarStellar")


def validate_archive(archive, system, version):
    """Launch the unpacked app from a fresh directory with host Python/Qt paths removed.

    Validation uses the native display selected by the caller (Xvfb on Linux CI).
    A frozen JSON report is required in addition to a zero process exit code, so
    windowed Windows executables cannot silently succeed without doing the check.
    """
    report = OUTPUT / "validation.json"
    screenshot = OUTPUT / "startup.png"
    with tempfile.TemporaryDirectory(prefix="polarstellar-unpacked-") as temporary:
        destination = Path(temporary)
        if system == "Darwin":
            run(["ditto", "-x", "-k", str(archive), str(destination)])
        else:
            shutil.unpack_archive(archive, destination)
        folder = destination / "PolarStellar"
        program = executable(folder, system)
        if system == "Darwin":
            run(["codesign", "--verify", "--deep", "--strict", str(folder / "PolarStellar.app")])
        environment = dict(os.environ)
        for key in (
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "QT_PLUGIN_PATH",
            "QT_QPA_PLATFORM_PLUGIN_PATH",
        ):
            environment.pop(key, None)
        # All report paths live outside the temporary extracted app directory.
        run(
            [
                str(program),
                "--smoke-test",
                "--smoke-report",
                str(report),
                "--smoke-screenshot",
                str(screenshot),
            ],
            cwd=destination,
            env=environment,
            timeout=120,
        )
        result = json.loads(report.read_text(encoding="utf-8"))
        if (
            result.get("status") != "passed"
            or result.get("version") != version
            or not result.get("frozen")
        ):
            raise RuntimeError(f"Frozen app validation failed: {result}")
        reported_arch = {"AMD64": "x86_64", "aarch64": "arm64"}.get(
            result.get("architecture"), result.get("architecture")
        )
        if result.get("platform") != system or reported_arch != architecture():
            raise RuntimeError("Frozen app validation ran on an unexpected platform/architecture.")
        expected_plugin = (
            environment.get("QT_QPA_PLATFORM")
            or {"Darwin": "cocoa", "Linux": "xcb", "Windows": "windows"}[system]
        )
        if result.get("qt_platform_plugin") != expected_plugin:
            raise RuntimeError("The requested native Qt display plugin was not exercised.")
        if not screenshot.is_file():
            raise RuntimeError("Frozen app did not render the requested validation screenshot.")
        print(
            f"Validated extracted {system} app: {len(result['checks'])} checks passed.", flush=True
        )


def main():
    """Build only for the current native host, then archive and validate the actual deliverable."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-arch", help="Fail if a CI runner supplies an unexpected architecture"
    )
    args = parser.parse_args()
    system = platform.system()
    if system not in ("Darwin", "Linux", "Windows"):
        raise RuntimeError("Native packaging supports Linux, macOS and Windows.")
    arch = architecture()
    if args.expected_arch and arch != args.expected_arch:
        raise RuntimeError(f"Expected {args.expected_arch} runner, got {arch}")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    work = ROOT / "build" / "native"
    work.mkdir(parents=True, exist_ok=True)
    arguments = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "PolarStellar",
        "--noupx",
        "--distpath",
        str(work / "frozen"),
        "--workpath",
        str(work / "analysis"),
        "--specpath",
        str(work),
        "--paths",
        str(ROOT / "src"),
        # PyNaCl loads its CFFI backend dynamically; static import analysis misses it.
        "--hidden-import",
        "_cffi_backend",
        "--collect-submodules",
        "stellar_sdk",
        "--recursive-copy-metadata",
        "stellar-sdk",
    ]
    if system in ("Darwin", "Windows"):
        arguments.append("--windowed")
    if system == "Darwin":
        arguments += ["--osx-bundle-identifier", "org.polarstellar.desktop", "--target-arch", arch]
    if system == "Windows":
        arguments += ["--icon", "NONE"]
        resource = work / "version.txt"
        windows_version(resource, version)
        arguments += ["--version-file", str(resource)]
    arguments.append(str(ROOT / "tools" / "native_entry.py"))
    print(f"Building PolarStellar {version} for {system}/{arch}…", flush=True)
    with (OUTPUT / "build.log").open("w", encoding="utf-8") as log:
        run(arguments, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    staged = work / "staged" / "PolarStellar"
    if staged.exists():
        shutil.rmtree(staged)
    if system == "Darwin":
        staged.mkdir(parents=True)
        app = work / "frozen" / "PolarStellar.app"
        info = app / "Contents" / "Info.plist"
        metadata = plistlib.loads(info.read_bytes())
        metadata.update(CFBundleShortVersionString=version, CFBundleVersion=version)
        info.write_bytes(plistlib.dumps(metadata))
        # PyInstaller signs embedded binaries ad hoc; refresh the root signature after
        # setting the application version. No developer identity/notarization is implied.
        run(["codesign", "--force", "--sign", "-", str(app)])
        shutil.copytree(app, staged / app.name, symlinks=True)
    else:
        shutil.copytree(work / "frozen" / "PolarStellar", staged, symlinks=True)
    for path in (
        ROOT / "LICENSE",
        ROOT / "README.md",
        ROOT / "CHANGELOG.md",
        ROOT / "docs" / "RELEASES.md",
    ):
        shutil.copy2(path, staged / path.name)
    notices(staged / "THIRD_PARTY_NOTICES.txt")
    commit = run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    dirty = bool(
        run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip()
    )
    manifest = {
        "version": version,
        "commit": commit,
        "platform": system,
        "architecture": arch,
        "working_tree_dirty": dirty,
        "built_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "pyinstaller": importlib.metadata.version("pyinstaller"),
        "signing": "ad hoc, not notarized" if system == "Darwin" else "unsigned",
        "validation": "See adjacent validation.json for extracted-app checks",
    }
    (staged / "BUILD_INFO.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    label = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}[system]
    name = f"PolarStellar-{version}-{label}-{arch}"
    if system == "Darwin":
        archive = OUTPUT / (name + ".zip")
        run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(staged), str(archive)])
    elif system == "Windows":
        archive = Path(shutil.make_archive(str(OUTPUT / name), "zip", staged.parent, staged.name))
    else:
        archive = OUTPUT / (name + ".tar.gz")
        with tarfile.open(archive, "w:gz") as output:
            output.add(staged, arcname="PolarStellar")
    with archive.open("rb") as archive_file:
        digest = hashlib.file_digest(archive_file, "sha256").hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(
        digest + "  " + archive.name + "\n", encoding="utf-8"
    )
    shutil.copy2(staged / "BUILD_INFO.json", OUTPUT / "BUILD_INFO.json")
    validate_archive(archive, system, version)
    print(f"Ready: {archive.name}", flush=True)


if __name__ == "__main__":
    main()
