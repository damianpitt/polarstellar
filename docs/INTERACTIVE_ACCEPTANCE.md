# Interactive candidate review — 2026-11-25

PolarStellar is an open-source project, not affiliated with the Stellar Development Foundation.

This is a partial acceptance record, not a completed V0.1 sign-off. Keep observed native UI
results separate from synthetic widget tests and unavailable clean-machine environments.
Use [the manual template](MANUAL_ACCEPTANCE.md) to complete the remaining target reviews.

## Candidate and environment

- Downloaded native candidate: **0.0.31**, macOS arm64, from
  [native run 45315599985](https://github.com/damianpitt/polarstellar/actions/runs/45315599985).
- Source commit: `6fd97686a644cc9eb252080e20462209d631e5ba`; manifest records a clean build.
- Archive SHA-256: `72eec0f08a44aa559761056bd6a51f3bb921ddae9f3c21c353bcfa2bd45d650c`.
  Matching checksum and deep/strict ad-hoc signature verification passed before launch.
- Review host: macOS **27.2.1**, build **26B131**, Apple Silicon/arm64; existing development
  machine. Python/Qt are installed on this host. It is not a fresh consumer machine.
- The download/extraction has no `com.apple.quarantine` attribute. Normal native launch
  succeeds, but this path does **not** exercise downloaded-app Gatekeeper approval.
- Source corrections in this review: **0.0.32**, clean source
  `4d1357d39be84ddc876172b7bedbf46bd60a2ccf`. The local macOS archive passes eleven extracted-app
  checks with the native Cocoa plugin. Uploaded three-platform archives remain 0.0.31.
- Local rebuilt archive: `PolarStellar-0.0.32-macos-arm64.zip`, 51,008,117 bytes, SHA-256
  `ad2697647725289cb9b526e28665e67b7ae9bcd26e19d773139d6802bf0a9760`.
  Checksum, clean manifest and deep/strict ad-hoc signature verification passed. This archive
  was extracted for the native recheck and remains local, without a binary upload.

## Observations and pending checks

| Check | macOS available host | Linux x86_64 | Windows x86_64 |
| --- | --- | --- | --- |
| Verified archive/signature and normal launch | Passed for downloaded 0.0.31 | Automated 0.0.31 only | Automated 0.0.31 only |
| OS approval prompts / clean consumer-machine launch | Not run; no quarantine, development host | Not run | Not run |
| Public account lookup and native control feedback | Passed, Mainnet public sample | Not run interactively | Not run interactively |
| Native export dialog open/cancel | Passed | Not run | Not run |
| JSON and CSV save / inspect exact metadata | Passed | Not run interactively | Not run interactively |
| Decline native overwrite confirmation | Passed; existing export preserved | Not run | Not run |
| Save-error feedback and return to explorer | Observed; permission-denied destination not isolated | Not run | Not run |
| Pointer drag / pan / wheel gestures | Synthetic Qt checks passed; physical gestures pending | Synthetic source checks passed | Synthetic source checks passed |
| Native menu navigation / enlarged text | Passed for rebuilt 0.0.32; Graph/Overview, 150%/200%/100%, scrolling | Source checks passed; native interactive review pending | Source checks passed; native interactive review pending |
| Display scaling / assistive screen readers | Not run | Not run | Not run |
| Full manual workflow and clean-machine acceptance | Pending | Pending | Pending |

The account lookup used a public Mainnet sample and the real Horizon endpoint on
2026-11-25. CSV/JSON exports were saved through the Cocoa save panel and parsed locally.
They identify the selected account, Mainnet, `https://horizon.stellar.org`, original UTC
retrieval time, authority data and an exact native XLM amount. This is a single observation,
not continuous provider availability or lifetime balance history. No signing or transaction
submission was involved. Local review exports/screenshots are ignored and not published.

The overwrite panel explicitly offered Cancel and Replace for the existing review export.
Cancel returned to the save panel; cancelling that panel returned to the explorer. Compare
the file hash before/after to verify preservation; this review confirmed identical SHA-256
before/after for the existing JSON export. Successful replacement and filesystem
permission errors still need isolated destination checks on the target desktops.

The rebuilt 0.0.32 app exposed Navigate and View in the native menu, and the named Explorer
pages list in the accessibility tree. Graph and Overview navigation retained the account,
network and loaded evidence. Text enlarged to 150% and 200%; scrolling reached lower graph/
table content, with horizontal scrolling also needed at 200%. Restoring 100% retained the
same account and graph coverage. The sampled page contained 20 unsupported direct-graph
records, correctly reported as excluded with zero direct counterparties. This is not a
manual review of populated relationship gestures. A second native JSON save in 0.0.32
completed and parsed with the expected software version, network and exact XLM amount.
Graph node labels and native system dialogs retain their own sizing. Physical gestures,
hardware keyboard workflow and assistive screen-reader acceptance remain pending.

## Corrections in 0.0.32

1. The graph wheel handler treated every nonpositive vertical delta as zoom-out. Horizontal
   scrolling and zero-delta boundaries therefore changed scale unexpectedly. Handle vertical
   angle/pixel deltas explicitly; delegate horizontal/boundary events to ordinary scrolling.
2. Graph zoom/fit had pointer-only controls. Add focused-canvas plus/minus/zero keys and keep
   ordinary arrow-key panning. Direct and expanded graphs share the implementation. Synthetic
   checks cover bounds, fit, modified keys and dragging with retained transaction tooltips.
3. Add native Navigate menu actions and standard page/search shortcuts as alternatives to
   the sidebar. Name important controls and provide visible focus borders. These expose
   navigation without requiring pointer access to graph nodes.
4. The theme used fixed 14-pixel text without an in-app enlargement option. View → Text size
   supplies 100%, 125%, 150% and 200% choices for the current session, preserving loaded context.
   Scrolling remains available at compact sizes. Native OS density/accessibility testing is
   separate and remains pending; graph node labels and native system dialogs keep their own sizing.

## Repeatable validation

Nine new offline cases and the full **670-test** suite pass locally with Qt offscreen, plus
Ruff lint/format. These are synthetic events, not physical pointer or screen-reader results.
No new runtime dependencies, provider behavior, database schema or export format changes.

[Source run 45714463842](https://github.com/damianpitt/polarstellar/actions/runs/45714463842)
passed **670 tests on each platform** for source `4d1357d`, plus Ruff, locked dependency/version
checks and source archive/wheel builds. Linux used `ubuntu-24.04`, macOS `macos-latest` and
Windows `windows-latest`; Qt widget checks used offscreen rendering. This does not validate
native file dialogs, OS approval, system display scaling or assistive readers on those runners.

Local 0.0.32 extracted-app validation: **eleven checks passed**, frozen arm64, Qt 6.11.2,
Cocoa, using temporary fixture storage. Native menu/text/export observations above were then
performed on that extracted app. Portable uploads: not performed for this source correction;
repeat three-platform native qualification at the selected release-candidate step. Local
archives, review JSON/CSV and screenshots remain excluded from Git and source packages.

## Remaining release gate and next planned slice

Complete interactive clean-machine acceptance on Linux x86_64 and Windows x86_64, and the
remaining macOS quarantine, physical gesture, scaling and assistive-access checks. Repeat
native dialogs with save, cancel, declined/accepted overwrite and permission-denied destinations;
review graph pan/zoom/fit, keyboard focus, 100–200% text and realistic interrupted workflows.
Also finish targeted live WASM/specification and explicitly supplied storage-key review.
Fix verified findings and requalify exact portable candidates before a V0.1 release candidate
or release publication. Keep unavailable checks pending; Mac Intel remains excluded.
