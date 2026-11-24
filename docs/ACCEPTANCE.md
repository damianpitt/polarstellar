# V0.1 acceptance and release gates

Review date: **2026-11-24**. Current source: **0.0.31, pre-alpha**.
Latest validated portable native apps: **0.0.27**.

PolarStellar is an open-source project, not affiliated with the Stellar Development Foundation.

This record distinguishes working source features from release qualification. Fixture
checks can prove parsing, persistence and repeatable UI behavior. They do not establish
current public endpoint availability or clean-machine desktop usability.

## Scope reviewed in 0.0.30

| Workflow | Repeatable source coverage and review | Remaining release check |
| --- | --- | --- |
| Account lookup | Address checksums, exact balances, trustlines, public authority/sponsorship, unknown optional fields and compact Overview | Fresh Mainnet/Testnet read-only observations |
| Activity | Independent cursor pagination, retry without lost rows, cancellation/stale response protection and failed-record labels | Realistic provider history and transient errors |
| Transaction evidence | Ordered instructions, checked envelope/result identity, exact merge amounts and bounded operation effects | Live successful/failed examples and unavailable historical effects |
| Graph and timeline | Deduplication, expansion bounds, separate path/merge evidence, time/type filters, chronology ambiguity and source coverage | Interactive pan/zoom/selection and compact display review on each target OS |
| Assets and contracts | Issuer/network identity, catalog paging, SAC derivation, pinned specifications, event compatibility and supplied storage-key checks | Live Horizon/RPC compatibility, deployment/retention/TTL boundaries |
| Local investigations | Notes/labels, search, saved comparison, deleted cases, concurrent append protection and quota rollback | Larger realistic case review and local storage failure recovery |
| Watchlists | Offline filters, portable transfer, manual batch refresh, retained snapshot pairs and exact differences | Interactive mixed-network lists and interrupted refresh review |
| Export | Exact CSV/JSON, source/coverage, annotation controls, atomic replacement and unavailable snapshot preparation | Native destination dialogs and file permissions on clean machines |

Populated feature views were rendered with Qt's macOS Cocoa platform and synthetic fixtures.
Review covered balances, authority/signers, activity, verified results/effects, counterparties,
expanded tracing and timelines, asset/SAC/catalog views, contract events/specifications/storage,
saved investigations and comparisons, and watchlist filtering/refresh/comparison. The captures
use temporary databases and no live provider calls. They show client areas without desktop
chrome; they do not substitute for interactive clean-machine acceptance.

## Concrete fixes

- An export snapshot could fail before entering the export error handler. Preparation now
  reports an unavailable selection and stops before the destination dialog or file write.
- A stale investigation view could replace a case saved by another view. Targeted annotation
  edits and evidence appends now re-read and write in one SQLite transaction. A deleted case
  stays deleted, while a failed save leaves its draft available to copy. Last successful
  save wins if the same annotation field is edited in two views.
- An incomplete cached payment row could trigger an out-of-range asset-column lookup.
  Navigation stays disabled until a complete asset identity is available.
- Existing library reads repeated the schema-version write on every connection. Initialize
  unversioned storage once, then read committed cases/bookmarks without requesting a write
  lock or committing the same version number again.

Seven regression cases exercise these failures, including simultaneous evidence appends
through independent database connections and rollback after a bounded-size rejection.
The quota regression uses a reduced test limit; it is not a large-investigation benchmark.
Two library-read cases keep a separate edit transaction open and verify that committed
investigations and watchlists can still be read without waiting for its write lock.

## Validation record

- Focused investigation/watchlist/export regression checks: **78 passed** locally.
- Full local suite: **639 passed** on macOS with Qt offscreen. Ruff lint and formatting
  pass; package/lockfile/changelog versions agree and the offline lockfile check passes.
- All eight offline source startup checks pass with disposable storage and fixture
  transports. Source archive and wheel builds pass; archive inspection confirms they
  contain no local screenshots, capture helper or application databases.
- [Source run 45098254242](https://github.com/damianpitt/polarstellar/actions/runs/45098254242),
  source commit `fb75b0c`: **639 tests passed on each platform** (Linux `ubuntu-24.04`,
  macOS `macos-latest`, Windows `windows-latest`). Ruff, locked dependency setup, version
  checks and source archive/wheel builds passed on all three.
- Native builds/uploads: not run for this source slice. The validated 0.0.27 artifacts
  remain described in [release documentation](RELEASES.md).

## Qualification automation in 0.0.31

`polarstellar --live-check --live-report <path.json>` explicitly runs bounded Mainnet and
Testnet provider checks using production parsers. It selects public samples from one recent
transaction page and one recent event page, with fixed origins, read-only request guards,
64-request/150-second limits per network and independent failure reporting. Empty history
does not establish absence. WASM specifications are checked only when sampled; arbitrary
storage keys are not discovered or queried. No personal libraries are opened.

The native workflow now has **separate fresh-runner jobs**. Each downloads a built archive,
verifies its checksum and source/version/architecture manifest, extracts it and launches the
frozen executable without installing the project's Python/Qt/SDK dependencies. Linux uses
X11/Xvfb; macOS and Windows use their native Qt plugins. The disposable desktop check now
includes clicks through all navigation pages at compact size, notes/labels reopening and
invalid-search recovery. The same extracted executable runs live checks against both networks.
Reports retain evidence and limitations in `qualification-<target>` artifacts.

Hosted runners contain preinstalled OS tools. These jobs establish automated standalone
runtime behavior on fresh hosted systems; they do not establish interactive usability,
download/quarantine approval or universal OS/distribution compatibility. Use the
[manual acceptance record](MANUAL_ACCEPTANCE.md) for those remaining checks.

Final 0.0.31 source, live and native run results will be entered after completion. The earlier
0.0.30 record above remains historical. No V0.1 completion or public stable release is implied.

Review of the first Windows native screenshot found pale text on light native tab faces.
Explicit dark tab/pane styling fixes the contrast for normal, selected, hover and disabled
states. Rebuilt native screenshots provide the final rendering evidence. Interactive checks
remain the documented next step; no interactive clean Linux/Windows machines were supplied.

## Remaining planned slice: interactive release acceptance

1. Run bounded, read-only Horizon/RPC checks against the selected network. Record query
   identity, endpoint, retrieval time, available ledgers and history limits. Use only public
   identifiers; never request keys or sign/submit transactions.
2. Exercise the release candidate interactively on clean Linux x86_64, macOS Apple Silicon
   and Windows x86_64 environments. Check launch, navigation, compact views, graph actions,
   native export dialogs, notes persistence, offline reopening and clear error recovery.
3. Resolve reproducible findings, repeat the affected checks, then validate portable bundles
   and checksums at the selected release step. Mac Intel stays excluded.
4. Record exact source/native versions, tested platforms, known limitations and pending
   gates before calling V0.1 complete. Signing/notarization and system installers retain
   their own scope; portable build success alone does not complete those tasks.

Historical versions and results remain in the [changelog](../CHANGELOG.md). The
[project specification](../PolarStellar_Project_Spec.md) defines the wider roadmap.
