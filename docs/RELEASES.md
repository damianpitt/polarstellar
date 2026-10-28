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
- Saved investigation and watchlist persistence/reopening and CSV/JSON writing in a temporary folder.
- NetworkX graph operations and HTTPX's bundled TLS certificate/SSL support.
- The actual frozen application's version and architecture, plus macOS bundle signatures.

The matching `native-validation-<target>` workflow artifact contains `BUILD_INFO.json`,
`validation.json` and `startup.png`; the `PolarStellar-<target>` artifact contains the archive
and its checksum. Small reports can be reviewed separately from the application download.
These checks do not make live
network requests, test every feature, prove compatibility with every OS version, or replace
interactive clean-machine release testing. Existing source tests and live provider checks
have their own scope. A successful workflow is a pre-alpha packaging validation result.

The bundle includes `BUILD_INFO.json` with source commit, version, platform, architecture
and build-tool versions, plus project license and third-party license information. Adjacent
`.sha256` files allow integrity verification. Checksums alone do not authenticate an unsigned
publisher; obtain both archive and checksum from the same trusted repository release.

Current source version: **0.0.24**. Source startup checks include disposable watchlist
persistence, retained snapshot pairs/exact comparison, portable JSON round-trip, local trace filters, path/merge observation parsing,
chronological timeline controls, checked merge-result decoding, declared specification
parsing/event candidates (including nested union/Result success and error payloads),
full muxed-account identity with zero/maximum uint64 routing IDs,
explicit storage-key/entry identity and exact-value decoding,
local SAC identity derivation and live-instance confirmation against offline fixtures,
and navigation. These checks use local fixtures without network requests or contract execution.
Native builds and workflow artifact uploads resumed successfully for 0.0.23 on Linux x86_64,
Windows x86_64 and macOS Apple Silicon only. The completed 0.0.11 checks below remain historical.
No native builds/uploads are run for 0.0.24. The latest downloadable native apps stay at
0.0.23 and do not include 0.0.24's watchlist batch/comparison features. Packaging runs only
when explicitly requested at selected development steps; Mac Intel remains excluded.

## Completed 0.0.23 validation

The [native run](https://github.com/damianpitt/polarstellar/actions/runs/40120185412) passed on
all three targets, built from clean commit `779357b343c2b4929ae14a3762ed054eb0e74d3e`.
Each job passed 509 source tests and lint, then extracted its deliverable archive and launched
the frozen application. All eight offline startup checks passed on each platform, using
X11/xcb on Linux, Cocoa on macOS and Windows Qt on Windows. The uploaded manifests/reports
and rendered startup screenshots were downloaded and reviewed. Windows reports `AMD64`,
the same architecture normalized to `x86_64` in the build manifest.

| Target | Archive/checksum artifact | Uploaded artifact size | Small validation reports |
| --- | --- | --- | --- |
| Linux x86_64 | [Download](https://github.com/damianpitt/polarstellar/actions/runs/40120185412/artifacts/12571141218) | 88,864,570 bytes (88.9 MB) | [Reports](https://github.com/damianpitt/polarstellar/actions/runs/40120185412/artifacts/12571001412), 79,880 bytes |
| macOS arm64 | [Download](https://github.com/damianpitt/polarstellar/actions/runs/40120185412/artifacts/12570796170) | 50,985,485 bytes (51.0 MB) | [Reports](https://github.com/damianpitt/polarstellar/actions/runs/40120185412/artifacts/12571216008), 70,191 bytes |
| Windows x86_64 | [Download](https://github.com/damianpitt/polarstellar/actions/runs/40120185412/artifacts/12570756796) | 64,248,521 bytes (64.2 MB) | [Reports](https://github.com/damianpitt/polarstellar/actions/runs/40120185412/artifacts/12570791544), 33,592 bytes |

Sizes are GitHub workflow artifact sizes (archive plus checksum), not source repository growth.
Artifact retention is 30 days; downloads require GitHub access. Native binaries remain outside
Git. No artifact-upload quota error occurred in this run. These are unsigned/ad-hoc-signed
portable preview bundles, with the signing and clean-machine limitations described above;
they have not been published as a GitHub release.

The [source run](https://github.com/damianpitt/polarstellar/actions/runs/40120172443) separately
passed all 509 tests, lint, locked version/dependency checks and source archive/wheel builds
on Linux, macOS and Windows. Later documentation commits record results; the archives remain
bound to the exact build commit above.

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
manual step. Workflow artifact uploads are enabled for the explicitly requested native run;
they do not publish a GitHub release automatically.

1. Align package version, lockfile, README, specification and changelog.
2. Run source checks and the Native builds workflow for that exact commit.
3. Review each build report and rendered screenshot, and record failed targets honestly.
4. Download the successful archives and matching checksums; verify their integrity.
5. Create a draft pre-release with those artifacts and the validation limits in its notes.
6. Perform interactive startup/investigation/export checks on clean target machines before
   promoting a bundle to broader platform support or a stable release.

GitHub workflow artifacts expire after their configured retention period. A draft release
provides a reviewable staging area; only published releases are publicly downloadable.
