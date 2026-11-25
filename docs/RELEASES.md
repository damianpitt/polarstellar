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
The build-stage startup checks use offline fixtures. Separate fresh-runner qualification
jobs run the extracted app's eleven offline checks and then explicitly run live Mainnet/Testnet
provider checks. Neither stage tests every feature, proves compatibility with every OS
version, or replaces interactive clean-machine release testing. A successful workflow is
a pre-alpha packaging and automated runtime qualification result.

The bundle includes `BUILD_INFO.json` with source commit, version, platform, architecture
and build-tool versions, plus project license and third-party license information. Adjacent
`.sha256` files allow integrity verification. Checksums alone do not authenticate an unsigned
publisher; obtain both archive and checksum from the same trusted repository release.

Current source version: **0.0.32**; latest uploaded native candidate: **0.0.31**. The native workflow verifies each downloaded bundle
on a separate fresh runtime runner, without installing project/Qt/SDK packages. It verifies
archive integrity/provenance, eleven offline startup/desktop checks and opt-in live checks
from the frozen executable against both networks. See [acceptance gates](ACCEPTANCE.md)
and the [interactive acceptance record](MANUAL_ACCEPTANCE.md) for the distinction between
automated qualification and remaining manual desktop review. The completed 0.0.31 evidence
and downloads are recorded below; older version records remain historical.

Source acceptance fixes contain export-preparation
failures, prevent stale local investigation saves overwriting newer evidence, and disable
asset navigation for incomplete payment rows. See [acceptance and release gates](ACCEPTANCE.md)
for completed checks, observed provider timeouts and outstanding interactive qualification.
Source startup checks include account authority with
reported zero weights/thresholds and reserve sponsors, plus saved graph/timeline
comparison with independent filter/source coverage, saved investigation search
by identity and network, comparison of two observations with exact before/after amounts,
disposable watchlist persistence, retained snapshot pairs/exact comparison, portable JSON
round-trip, local trace filters, path/merge observation parsing,
chronological timeline controls, checked merge-result decoding, declared specification
parsing/event candidates (including nested union/Result success and error payloads),
full muxed-account identity with zero/maximum uint64 routing IDs,
explicit storage-key/entry identity and exact-value decoding,
local SAC identity derivation and live-instance confirmation against offline fixtures,
saved transaction/effects comparison through the offline dialog and JSON export,
and navigation. These checks use local fixtures without network requests or contract execution.
Native builds and workflow artifact uploads resumed successfully for 0.0.23 on Linux x86_64,
Windows x86_64 and macOS Apple Silicon only. The completed 0.0.11 checks below remain historical.
The latest validated native apps are 0.0.31. Packaging runs at selected development steps
on Linux x86_64, Windows x86_64 and Apple Silicon.

## Completed 0.0.31 qualification

The later 0.0.32 source update adds desktop access improvements and records an
[interactive macOS review](INTERACTIVE_ACCEPTANCE.md). It does not replace these uploaded
archives or imply that Linux/Windows interactive checks have been completed.
Its [source run](https://github.com/damianpitt/polarstellar/actions/runs/45714463842) passed
670 tests per platform and package checks. A local clean-source 0.0.32 macOS arm64 archive
passed eleven frozen-app checks and native menu/text/export review; its exact identity and
limits are in the interactive record. It was not uploaded and does not replace the public
workflow downloads below.

The [source run](https://github.com/damianpitt/polarstellar/actions/runs/45315580984) passed
**661 tests per platform**, Ruff, locked dependencies, version checks and source archive/wheel
builds on Linux, macOS and Windows. The [native run](https://github.com/damianpitt/polarstellar/actions/runs/45315599985)
built all three archives from clean commit `6fd97686a644cc9eb252080e20462209d631e5ba`.
Each package job passed the same 661 source tests and eleven frozen-app offline checks.
Manifests and native startup screenshots were downloaded and reviewed; Windows tab contrast
was corrected before producing these final archives.

Separate fresh hosted runners downloaded and SHA-256-verified those exact archives, checked
source/version/platform/architecture, and launched the frozen apps without installing project,
Qt or SDK Python packages. All eleven offline checks and both networks' live Horizon/RPC
workflows passed. Native plugins were `xcb`, `cocoa` and `windows`; macOS ad-hoc signatures
were verified. Desktop fixtures and saved notes use disposable storage.

| Target | Archive/checksum artifact | Uploaded artifact size | Build reports | Successful fresh-runner evidence |
| --- | --- | --- | --- | --- |
| Linux x86_64 | [Download](https://github.com/damianpitt/polarstellar/actions/runs/45315599985/artifacts/14884709035) | 89,256,445 bytes (89.3 MB) | [Reports](https://github.com/damianpitt/polarstellar/actions/runs/45315599985/artifacts/14884589310) | [Qualification](https://github.com/damianpitt/polarstellar/actions/runs/45315599985/artifacts/14885621075), attempt 2 |
| macOS arm64 | [Download](https://github.com/damianpitt/polarstellar/actions/runs/45315599985/artifacts/14884798845) | 51,369,381 bytes (51.4 MB) | [Reports](https://github.com/damianpitt/polarstellar/actions/runs/45315599985/artifacts/14884963722) | [Qualification](https://github.com/damianpitt/polarstellar/actions/runs/45315599985/artifacts/14885289188), attempt 3 |
| Windows x86_64 | [Download](https://github.com/damianpitt/polarstellar/actions/runs/45315599985/artifacts/14885368046) | 64,627,653 bytes (64.6 MB) | [Reports](https://github.com/damianpitt/polarstellar/actions/runs/45315599985/artifacts/14884544647) | [Qualification](https://github.com/damianpitt/polarstellar/actions/runs/45315599985/artifacts/14884769181), attempt 1 |

These are workflow artifact sizes, including the application archive and matching checksum.
All downloads require GitHub access and expire after 30 days; binaries remain outside Git.
Small build reports are 34–81 KB and successful qualification reports are 38–85 KB. Later
documentation commits record results; bundled documents retain the exact build commit above.

The first qualification attempt timed out loading the same sampled Testnet account's payments
on Linux and macOS; the second macOS attempt timed out on another sampled account's payments.
Those failed reports remain available: [Linux attempt 1](https://github.com/damianpitt/polarstellar/actions/runs/45315599985/artifacts/14883824766),
[macOS attempt 1](https://github.com/damianpitt/polarstellar/actions/runs/45315599985/artifacts/14885685378)
and [macOS attempt 2](https://github.com/damianpitt/polarstellar/actions/runs/45315599985/artifacts/14885750018).
Only failed qualification jobs were rerun, reusing unchanged archives; no timeouts were
converted to passes and no provider checks were skipped. Later sampled accounts passed.
These observations establish bounded successful workflows, not reliable availability for
every account or continuous endpoint health. Timeout/retry behavior remains part of manual review.

All successful native live samples selected built-in SACs, so WASM specification checks were
not sampled and explicit storage-key reads were not requested. Testnet failed transactions
were sampled on each successful target; Mainnet failed transactions were not sampled in those
final native observations. Activity second pages were exercised where available; some macOS
sample histories ended on the first page. Effect pages can be empty. See
[acceptance evidence and coverage](ACCEPTANCE.md) for request counts and retained ledger ranges.

Interactive clean consumer-machine acceptance remains **Not run**, as recorded in the
[manual template](MANUAL_ACCEPTANCE.md). OS approval prompts, native file dialogs, graph
gestures, scaling/accessibility and realistic interrupted workflows remain release gates.
This is automated pre-alpha qualification, without a V0.1 tag or published stable release.

The [0.0.30 source run](https://github.com/damianpitt/polarstellar/actions/runs/45098254242)
passed all 639 tests per platform, Ruff, locked dependency/version checks and Python source
archive/wheel builds on Linux, macOS and Windows for source commit `fb75b0c`. Regressions
cover unavailable exports, incomplete payment selection, stale case saves, deleted cases,
concurrent appends, quota rollback and reads during uncommitted library edits. Local source
startup checks also pass. No new portable native archives were built or uploaded in this slice.

The [0.0.29 source run](https://github.com/damianpitt/polarstellar/actions/runs/45047855750)
passed all 632 tests per platform, Ruff, locked dependency/version checks and Python source
archive/wheel builds on Linux, macOS and Windows. Account authority, sponsorship, old-cache
compatibility and compact-window assertions are included. Windows action labels were shortened
after its first compact-layout check exposed an excessive minimum button width.

The [0.0.28 source run](https://github.com/damianpitt/polarstellar/actions/runs/44924310241)
passed all 610 tests per platform, Ruff, locked dependency/version checks and Python source
archive/wheel builds on Linux, macOS and Windows. This source update adds saved graph/timeline
comparison. Its startup path checks the new comparison with disposable offline evidence.

## Completed 0.0.27 validation

The [native run](https://github.com/damianpitt/polarstellar/actions/runs/44838820941) passed
on all three targets from clean commit `f307a1ed67c42e2f763f1d50b8768f4dd3f8900d`.
Each native job passed all 587 source tests and Ruff, then built, extracted and launched
its deliverable app. All eight offline startup checks passed using X11/xcb on Linux,
Cocoa on macOS and Windows Qt on Windows. The checks include saved transaction/effects
comparison through the dialog and JSON export using disposable fixture evidence.
The downloaded build manifests, validation reports and startup screenshots were reviewed.

| Target | Archive/checksum artifact | Uploaded artifact size | Small validation reports |
| --- | --- | --- | --- |
| Linux x86_64 | [Download](https://github.com/damianpitt/polarstellar/actions/runs/44838820941/artifacts/14678774257) | 88,908,592 bytes (88.9 MB) | [Reports](https://github.com/damianpitt/polarstellar/actions/runs/44838820941/artifacts/14680185497), 79,489 bytes |
| macOS arm64 | [Download](https://github.com/damianpitt/polarstellar/actions/runs/44838820941/artifacts/14679872321) | 51,032,825 bytes (51.0 MB) | [Reports](https://github.com/damianpitt/polarstellar/actions/runs/44838820941/artifacts/14679892278), 69,743 bytes |
| Windows x86_64 | [Download](https://github.com/damianpitt/polarstellar/actions/runs/44838820941/artifacts/14680335357) | 64,292,942 bytes (64.3 MB) | [Reports](https://github.com/damianpitt/polarstellar/actions/runs/44838820941/artifacts/14678639711), 33,471 bytes |

Uploads completed for all bundles and reports with 30-day artifact retention. Downloads
require GitHub access. These sizes describe workflow artifacts, with binaries stored outside
Git. Linux/Windows bundles are unsigned and macOS is ad-hoc signed. This validation covers
automated pre-alpha startup checks; signing, notarization, installers and interactive
clean-machine qualification remain planned.

The [0.0.27 source run](https://github.com/damianpitt/polarstellar/actions/runs/44838801828)
also passed all 587 tests per platform, Ruff, locked dependency/version checks and Python
source archive/wheel builds on Linux, macOS and Windows. The source archive was checked
locally to contain the 92 public project files plus package metadata. Later documentation
commits record these results; native bundles retain the build commit above.

## Earlier source validation

The [0.0.26 source run](https://github.com/damianpitt/polarstellar/actions/runs/42152240958)
passed all 562 tests per platform, lint, locked dependency and version checks, and Python
source archive and wheel builds on Linux, macOS and Windows.

The [0.0.25 source run](https://github.com/damianpitt/polarstellar/actions/runs/42150896260)
passed all 547 tests per platform, lint, locked dependency and version checks, and Python
source archive and wheel builds on Linux, macOS and Windows. The source archive uses
an explicit list of public project paths to exclude local working files and generated builds.

The [0.0.24 source run](https://github.com/damianpitt/polarstellar/actions/runs/40646716271)
passed all 534 tests per platform, lint, locked dependency/version checks and source archive/
wheel builds on Linux, macOS and Windows. This is source validation, not new frozen-app validation.

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
3. Review build and fresh-runner qualification reports, live coverage and rendered screenshots;
   record failed targets and unobserved workflows honestly.
4. Download the successful archives and matching checksums; verify their integrity and exact
   version/source commit against the reviewed candidate.
5. Create a draft pre-release with those artifacts and the validation limits in its notes.
6. Perform interactive startup/investigation/export checks on clean target machines before
   promoting a bundle to broader platform support or a stable release.

GitHub workflow artifacts expire after their configured retention period. A draft release
provides a reviewable staging area; only published releases are publicly downloadable.
