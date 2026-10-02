# Native builds and release validation

PolarStellar is an open-source project, not affiliated with the Stellar Development Foundation.

## Download and run

The native build workflow produces portable pre-alpha bundles for:

| Platform | Architecture | Bundle | Launch |
| --- | --- | --- | --- |
| Linux (built on Ubuntu 22.04) | x86_64 | `.tar.gz` | Extract, then run `PolarStellar/PolarStellar` |
| macOS (built on macOS 15) | Apple Silicon / arm64 | `.zip` | Extract, then open `PolarStellar/PolarStellar.app` |
| Windows (built on Windows Server 2022) | x86_64 | `.zip` | Extract all files, then open `PolarStellar/PolarStellar.exe` |

Keep the whole extracted folder together. Python and uv are bundled or unnecessary at
runtime; do not move the executable away from its libraries. Choose the bundle matching
your processor. Mac Intel, Windows ARM and Linux ARM builds are not provided. macOS development
packaging targets Apple Silicon only.

Linux requires a desktop session, glibc 2.35 or later, and Qt's native system dependencies.
On Ubuntu, install `libegl1 libgl1 libxcb-cursor0 libxkbcommon-x11-0 libxcb-icccm4
libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-xinerama0 libxcb-shape0`. The build is validated
on Ubuntu 22.04; other Linux distributions require separate compatibility checks.

Windows bundles are unsigned. macOS bundles are ad-hoc signed for bundle integrity and
are not Developer ID signed or notarized. The operating system may block a downloaded
app or show an unidentified-publisher message. Inspect the download's origin and checksum;
if you choose to run it, use the operating system's normal per-app approval controls.
No global security setting changes are required by PolarStellar. Authenticode signing,
Apple notarization, system installers, DMG, AppImage, Flatpak and automatic updates remain
future packaging work.

Caches and investigations use the operating system's normal user directories. They are
separate from the application folder and survive replacing the app. Native smoke checks
use disposable data and never open existing investigations.

## What a validated bundle means

Each target is built on its own operating system using Python 3.12 and locked dependencies.
The deliverable archive is unpacked into a fresh directory and its frozen executable is
launched without the development Python/Qt path settings. Linux uses Xvfb and the native
X11 Qt plugin; macOS and Windows use their native Qt plugins. Validation checks:

- Qt startup, navigation, rendering and asynchronous event-loop responsiveness.
- An offline account fixture, exact balances and checksum-aware SDK/XDR support.
- Saved investigation persistence/reopening and CSV/JSON writing in a temporary folder.
- NetworkX graph operations and HTTPX's bundled TLS certificate/SSL support.
- The actual frozen application's version and architecture, plus macOS bundle signatures.

`validation.json` and `startup.png` accompany each archive. These checks do not make live
network requests, test every feature, prove compatibility with every OS version, or replace
interactive clean-machine release testing. Existing source tests and live provider checks
have their own scope. A successful workflow is a pre-alpha packaging validation result.

The bundle includes `BUILD_INFO.json` with source commit, version, platform, architecture
and build-tool versions, plus project license and third-party license information. Adjacent
`.sha256` files allow integrity verification. Checksums alone do not authenticate an unsigned
publisher; obtain both archive and checksum from the same trusted repository release.

## Completed 0.0.11 validation

The [native workflow](https://github.com/damianpitt/polarstellar/actions/runs/37007888180)
passed on Linux x86_64, Windows x86_64, and both macOS architectures in the initial run.
Mac Intel was tested historically but is no longer a development/distribution target. Each extracted archive passed seven
offline checks using Cocoa on macOS, Windows Qt on Windows, and X11/xcb on Linux.
The [source workflow](https://github.com/damianpitt/polarstellar/actions/runs/37007888166)
also passed all 114 tests, lint and package builds on Linux, macOS and Windows.

The first Linux bundle check exposed a missing X11 shape library; `libxcb-shape0` is now
included in the documented native dependencies. Native startup diagnostics are retained
on failure, and repeat validation clears previous reports before launching the new app.
These completed automated results do not imply signing or interactive clean-machine approval.

## Build locally

Build only on the target operating system; PyInstaller does not cross-compile these apps.
From a checkout with Git, Python 3.12 and uv:

```sh
uv sync --locked --group packaging
uv run --group packaging python tools/build_native.py
```

Outputs are in `dist/native/`; intermediate files are in `build/native/`. Both are ignored
by Git. The build copies only explicitly named public documentation/license files along
with analyzed package code and dependencies. It does not copy local notes, credentials,
cache databases, investigation databases, test data or the entire checkout into the app.

For headless local Linux validation, run the build command under `xvfb-run -a`. Set
`QT_QPA_PLATFORM=offscreen` only for local checks that intentionally lack a native display;
the workflow's release check uses the native X11 plugin.

## Release procedure

Native packaging is manual-only. Routine pushes and pull requests run source checks;
they do not build or upload native app archives. Start **Native builds** from GitHub
Actions when ready to produce the three platform bundles. Release uploads are a separate
manual step and are currently paused.

1. Align package version, lockfile, README, specification and changelog.
2. Run source checks and the Native builds workflow for that exact commit.
3. Review each build report and rendered screenshot, and record failed targets honestly.
4. Download the successful archives and matching checksums; verify their integrity.
5. Create a draft pre-release with those artifacts and the validation limits in its notes.
6. Perform interactive startup/investigation/export checks on clean target machines before
   promoting a bundle to broader platform support or a stable release.

GitHub workflow artifacts expire after their configured retention period. A draft release
provides a reviewable staging area; only published releases are publicly downloadable.
