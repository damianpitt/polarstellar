# Changelog

User-visible features, fixes, and software versions are recorded here.
Versions remain pre-alpha until the V0.1 scope is complete. These entries describe
source updates; they do not imply a packaged installer release.

## [Unreleased]

Add upcoming changes here as they are implemented. For each version update, move the
completed entries into a dated section and keep `pyproject.toml`, the package version,
and the uv lockfile in sync. The application header and `--version` show the package version.

## [0.0.14] - 2026-10-08

### Added — combined offline watchlist filters

- Added text, network and resource-type filters in **Watchlists**, with a visible/total
  entry count. Search is a case-insensitive substring match across committed identifiers,
  codes, issuers, labels and notes; saved identities remain case-sensitive and unchanged.
  Network/type filters combine with text and do not change the explorer network.
- Hidden entries stay saved. Visible positions map to stable entry IDs for edit, remove,
  refresh and opening actions. Filters that would hide unsaved notes require a choice;
  refusing restores the old filters/selection. Retained selections preserve typed drafts.
  The watchlist panel scrolls when needed so controls remain reachable on shorter windows.
  New lists/imports/bookmark additions clear filters so their entries are not hidden.
- Fixed a refresh/filter race: a concurrent saved annotation edit can stop matching the
  current search. Reload clears those filters while preserving the selected unsaved draft,
  instead of losing typed notes when the entry disappears from the filtered rows.

### Added — portable bookmark JSON with explicit sharing scope

- Added **Export list JSON** and **Import list JSON**. Portable schema 1 preserves every
  account/contract ID, saved network, and exact asset code/issuer identity. Native XLM
  remains distinct from issued assets named XLM; same-code issuers/networks stay separate.
- Export defaults to the whole selected list. **Export visible entries only** creates a
  filtered subset, recording that scope without storing the search text. The exporter
  re-reads committed state before the file dialog; unsaved edits are not exported.
- Local list names, labels and notes are omitted by default. **Include saved list name,
  labels and notes** opts into committed annotations. Sharing preferences reset on list
  changes. Snapshots/network evidence, database IDs, creation history and private paths
  are always excluded, even with annotation sharing enabled.
- Import creates a separate list with fresh IDs and no snapshots. It never merges or
  replaces existing lists, including lists with the same name or resources. Files without
  annotations receive the local name **Imported watchlist**. Imports/filtering/exporting
  make no network requests; imported annotations remain interpretations, not verified facts.

### Validation, storage and compatibility safeguards

- Bound UTF-8 JSON input reads to 10 MB; accept an optional UTF-8 BOM. Validate all entries
  before storage opens, requiring the supported format/schema, at most 500 entries, known
  resource kinds/networks, canonical checksum-valid IDs and consistent full asset identities.
- Reject duplicate JSON fields/identities, unknown fields/schema versions, foreign IDs,
  snapshots, invalid annotation types/lengths, malformed text/depth and NaN/Infinity.
  Portable schema 1 is independent of SQLite schema 1; no database migration is required.
- Commit valid imports as one transaction. Existing 100-list/10-MB-per-list limits apply;
  quota/write failures roll back the whole new list and preserve existing data. Export
  validates its own schema and formatted byte size, and atomically replaces the selected
  file. Cancellation leaves the library/destination untouched.
- Updated version metadata/header/lockfile, README and specification to 0.0.14. Dependencies
  are unchanged. CSV import, merging, snapshot transfer, encryption and batch refresh remain
  planned. Native builds/uploads stay paused; Mac Intel stays outside development targets.

### Validation results

- Local checks: all 210 tests and Ruff passed. Added 39 transfer/filter cases covering
  issuer/network/native-XLM round-trips, annotation privacy, saved-only export, filtered
  selection/removal, sharing resets, failed write rollback, quotas, duplicate fields/IDs,
  malformed UTF-8/depth/constants, future schemas, canonical identities and byte limits.
  The refresh/filter draft race and file-dialog cancellation paths are covered.
- Source/wheel builds and lock checks passed; startup validation now also performs a
  disposable bookmark JSON export/import. The UI was rendered at the normal 1180 × 760
  size; empty evidence panels are hidden to give bookmark rows room.
- Initial Linux/macOS CI passed. Windows exposed a pytest parameter-name issue: the
  10 MB boundary payload generated an environment variable above Windows' limit. Added
  short explicit test IDs while retaining the complete size/depth checks. Cross-platform
  source CI is rerun after this correction. No native 0.0.14 bundles
  have been built or uploaded; historical native results remain separate.

## [0.0.13] - 2026-10-07

### Added — issued-asset discovery and exact filters

- Added **Assets → Discover assets**, alongside the existing **Inspect asset** tab.
  Browse Horizon issued-asset statistics with optional exact code, issuer, or combined
  filters; leave both blank to browse. Codes are case-sensitive whole-code matches,
  and issuer checksums are validated before network requests.
- **Search / restart** loads the first 20 rows; **Load more (20)** fetches one additional
  page with submitted filters/network frozen independently of later form edits. There
  is no automatic crawl. Queries use ascending order and stop at 50 pages / at most
  1,000 returned records, explicitly labeled incomplete when capped.
- Rows display full code-and-issuer identities, asset type, available authorized-account
  counts, exact authorized-account balances and approval flags. Missing values remain
  unknown. Same-code issuers stay distinct. Native XLM, pool shares and standalone
  contract tokens are excluded; an XLM filter finds issued assets named XLM.
- Select **Inspect selected asset** or double-click a row for a separate exact-pair lookup.
  Returning to discovery preserves its loaded results. Resource save/bookmark controls
  require the inspector tab, avoiding accidental use of an older inspector snapshot.
  Unrelated explorer metadata/actions are hidden on discovery to give results room.
- Added discovery CSV/JSON exports for loaded rows, all parsed statistics/flags, frozen
  filters, network, query order, page timestamps/source/cursors, and explicit limits.
  Export is local and makes no requests; exact amounts remain decimal strings.

### Pagination, accuracy and privacy safeguards

- Validated Horizon's nonnumeric code_issuer_type asset tokens and reconstructed requests
  only against fixed /assets endpoints. Provider next-page URLs are ignored. Invalid
  rows, asset identities, type/cursor mismatches and query/network mismatches are refused
  before committing a page. Cursor cycles cannot lead to endless paging.
- Failed pages retain existing rows/cursors for retry. Cancel retains evidence and
  generation checks reject late responses after restart/network changes, including
  clients that suppress cancellation. Identical overlapping observations are deduplicated
  with original timestamps retained; conflicting statistics require an explicit restart.
- Discovery bypasses the response cache. Each page is a live observation at its own time;
  the catalog is not an atomic ledger snapshot. Only an empty page confirms the current
  query end. Short pages can continue, and end/limit boundaries do not imply lifetime
  coverage. Statistics are not circulating supply, prices, market value or endorsements.
- Only explicit discovery/inspection actions contact Horizon. No issuer website/TOML
  requests, contract calls, polling or automatic monitoring were added. Existing local
  watchlists/investigations and dependency versions are unchanged. Native builds remain
  manual-only, Mac Intel remains excluded, and native/release uploads stay paused.
- Updated README/specification, version header/package metadata and uv lockfile to 0.0.13.

### Validation

- Local validation: all 171 tests and Ruff passed, including 31 new discovery cases
  covering optional filters, network routing, local validation before requests, malformed
  pages, exact issuer/amount handling, failed-page retry, frozen queries, CSV/JSON
  provenance, overlap/cursor conflicts, late cancellation, page caps, uncached lookup
  and inspector navigation. Startup checks also navigate the discovery tab without
  fetching. The new view was rendered at the normal 1180 × 760 window size.
- Source/wheel builds and dependency-lock checks passed; dependencies are unchanged.
  [Source CI](https://github.com/damianpitt/polarstellar/actions/runs/37588570260) passed
  on Linux, macOS and Windows: all 171 tests on each platform, Ruff, version inspection
  and source/wheel builds.
- Live read-only Horizon checks validated 20 Mainnet catalog records, 20 Testnet records
  and a second 20-record Testnet page using its returned asset cursor. These small checks
  confirm current response/pagination compatibility, not exhaustive asset coverage.
- No native 0.0.13 bundles have been built or uploaded; historical native validation
  remains separate, and release uploads stay paused.

## [0.0.12] - 2026-10-03

### Added — local account, asset and contract watchlists

- Added **Watchlists** in the sidebar: create, rename and delete named lists; add
  checksum-valid account/contract IDs or exact, case-sensitive asset codes and issuers;
  select resources offline; and save local labels/notes. Native XLM has no issuer and
  remains distinct from issued assets named XLM. New entries use the selected network;
  each row retains and displays its own Mainnet/Testnet identity.
- Added **Add displayed resource to selected watchlist** on explorer pages. It bookmarks
  the loaded account, asset or contract without making another request or silently
  saving its current evidence. Repeated additions return to the existing bookmark
  without erasing annotations or a saved snapshot. Resources can appear in multiple lists.
- Added **Open in explorer (network lookup)** and entry double-click shortcuts. They
  switch to the entry's saved network before lookup, open the existing account/asset/
  contract inspector, and follow ordinary cache settings. Offline list selection and
  explorer opening do not modify a watchlist's saved snapshot.
- Added **Refresh saved snapshot (live)** and **Cancel refresh**. Manual refresh bypasses
  the optional cache, retrieves one resource, and replaces only the latest saved snapshot.
  Account refresh includes details/balances; issued assets include Horizon statistics;
  contracts include available Stellar RPC instance details without event requests.
  Native XLM is a local definition and needs no API access.

### Accuracy, privacy and failure safeguards

- Saved snapshots retain exact amounts, source, retrieval time and existing coverage
  limits. Past snapshots are clearly labeled; watchlists are convenience bookmarks,
  not historical evidence collections. Use Investigations to preserve snapshot history.
  No background polling, automatic monitoring, alerts or batch refresh is implemented.
- Failed or mismatched-provider refreshes keep old snapshots. Cancellation and generation
  checks reject late results after selection/network changes. Atomic re-reading preserves
  annotations saved during network I/O; refreshing never recreates a deleted bookmark.
  Draft notes survive a completed refresh. Selection changes and window closure protect
  unsaved edits; deletion of entries or whole lists requires confirmation.
- Added separate unencrypted schema-1 `watchlists.sqlite3` storage in application data.
  It is lazy-created, independent of response caches/investigations, and excluded from
  Git. Unsupported future schemas are refused. Limits: 100 lists, 500 entries and 10 MB
  per list, 100-character names, 200-character labels and 10,000-character notes.
- Labels and notes remain local interpretations and are not sent to providers. Only
  explicitly requested explorer/refresh actions send public identifiers to the entry's
  saved network. Watchlist export/import, filtering, encrypted storage and alerts remain
  planned. Runtime/build dependencies are unchanged.
- Local library pages hide unrelated explorer save/bookmark controls to give list,
  annotation and saved-evidence widgets room. Existing exploration and investigation
  features retain their behavior. Updated README/specification and aligned package/lock
  metadata; software version and startup header now show 0.0.12.

### Changed — development packaging scope

- Removed Mac Intel from future native builds and distributions. Current targets are
  Linux x86_64, Windows x86_64, and macOS Apple Silicon/arm64. Local macOS packaging
  also refuses Intel before producing an archive. Initial Intel validation remains
  recorded below as historical results.
- Made native packaging manual-only: routine development pushes and pull requests
  continue source CI without building/uploading large standalone app archives. Native
  builds can be requested explicitly from GitHub Actions when downloads are needed.
  Release uploads remain paused; no release-write staging workflow has been published.

### Validation

- Local validation: all 140 tests and Ruff passed; source distribution, wheel and
  locked dependency checks succeeded. The Watchlists layout was rendered and reviewed
  at the normal 1180 × 760 window size. Startup smoke validation now includes disposable
  watchlist persistence/reopening and the new sidebar page.
- Added 26 watchlist checks covering network/issuer isolation, invalid identifiers,
  offline restart, duplicate preservation, database/annotation bounds, unknown schema
  refusal, cache bypass, exact balances/statistics, native XLM without API access,
  contract refresh without events, provider mismatch, saved/unsaved note preservation,
  failed/cancelled refresh, deletion during I/O, explorer shortcuts and protected closure.
- [Source CI](https://github.com/damianpitt/polarstellar/actions/runs/37070491358) passed
  on Linux, macOS and Windows, including all 140 tests on each platform, Ruff, version
  inspection and source/wheel builds. No new native bundles have been built or uploaded
  for this slice; 0.0.11 packaging results remain historical and do not qualify a native
  0.0.12 release.

## [0.0.11] - 2026-10-02

### Added — portable native builds

- Added a build-only, locked PyInstaller 6.22.3 dependency group and a documented build
  script. Linux and Windows use complete portable onedir bundles; macOS uses `.app`
  bundles. Runtime users do not need to install Python or uv separately.
- Added a Native builds workflow for Ubuntu 22.04 x86_64, Windows Server 2022 x86_64,
  macOS 15 Apple Silicon, and macOS 15 Intel. Each target is built on its own operating
  system; architecture checks prevent accidentally labeling an incompatible runner.
- Added versioned archives, SHA-256 checksum files, source/version/platform build
  metadata, project licensing, and a third-party build-environment license inventory.
  Shared Qt libraries remain separate files. Build inputs copy only explicitly named
  public documents and analyzed source/dependencies, excluding private investigation data.
- Added Windows executable version resources and macOS bundle versions. macOS embedded
  binaries use PyInstaller's ad-hoc signing and extracted signatures are verified.
  Windows builds are unsigned; Apple notarization, publisher signing, system installers,
  DMG, AppImage, Flatpak and automatic updates remain future packaging work.

### Added — validation of the actual downloadable bundle

- The build unpacks its deliverable archive into a fresh directory and launches the
  frozen executable without development Python/Qt path overrides. JSON report/version/
  architecture checks and a rendered startup screenshot are required for success.
- Added offline smoke-test options that use disposable storage and fixture HTTP data.
  Checks cover Qt startup/navigation, async-loop responsiveness, exact account balances,
  SDK checksum/XDR support, NetworkX, saved investigation reopening, CSV/JSON output,
  and TLS certificate/SSL availability. Existing user data is never opened by this mode.
- Linux validation uses Xvfb with native X11 Qt; Windows and macOS validate their native
  Qt plugins. Reports/screenshots accompany successful workflow artifacts; failed builds
  preserve diagnostic logs. Public workflow permissions are read-only.
- Documented download/extraction, native dependencies, architecture choices, approval
  prompts, local builds, artifact retention and a draft-release review procedure. These
  are pre-alpha startup/integration checks, not full interactive clean-machine validation.
  Cache/investigation locations and normal live lookup behavior remain unchanged.

### Fixed — native display readability

- Reviewing the packaged startup screenshots exposed light Windows table headers with
  pale text and dark selected navigation text on native Windows/macOS styles. Explicit
  dark header backgrounds and light header/selection text preserve readable labels
  across native platforms. Table contents, precision and investigation data are unchanged.

### Validation

- Local source validation: all 114 tests and Ruff checks passed; source distribution,
  wheel, and dependency lockfile checks succeeded. The extracted Apple Silicon app
  passed all seven frozen integration checks, including ad-hoc signature verification.
- Fixed a frozen-startup failure by explicitly bundling the dynamically loaded CFFI
  backend used by the Stellar SDK/PyNaCl. This was found by testing the deliverable app.
- The first GitHub run passed all three source-platform jobs and both macOS/Windows
  bundles. Native Linux validation identified a missing `libxcb-shape0` system library;
  added it to the runner and documented Linux requirements. Startup loader logs are now
  retained on failure, and old reports/screenshots are removed before repeat validation.
  The corrected [native workflow](https://github.com/damianpitt/polarstellar/actions/runs/37007888180)
  passed all four targets: Linux x86_64, Windows x86_64, macOS arm64, and macOS x86_64.
  Each extracted bundle passed all seven checks with its native Qt display plugin.
- [Source CI](https://github.com/damianpitt/polarstellar/actions/runs/37007888166) passed
  on Linux, macOS and Windows, including all 114 tests on each platform, Ruff, version
  inspection, and source/wheel builds. Download checksums and build manifests are reviewed
  before staging the draft pre-release. Interactive clean-machine checks, developer
  signing and notarization remain pending; this version is still pre-alpha.

## [0.0.10] - 2026-09-30

### Added — read-only Soroban contract inspection

- Enabled Contracts navigation and C-address search from the main search bar. IDs
  are checksum-validated before requests; 64-character transaction hashes retain
  their existing routing. Contract inspection follows the selected network.
- Added a dedicated Stellar RPC provider using public Gateway Mainnet and SDF Testnet
  endpoints, with getNetwork passphrase verification for each lookup/event page. Its
  method allowlist contains only getNetwork, getHealth, getLedgerEntries and getEvents.
- Added readable instance details and a raw-evidence tab: executable type, WASM hash
  when available, last-modified/live-until ledgers, known instance storage, source and
  retrieval time. Instance identity is checked against the requested ledger key.
  An absent live instance remains explicitly uncertain, including possible archival.

### Added — retained contract-event exploration

- Added explicit event loading in ascending order, 20 records per page, capped at
  50 pages. The default query covers the latest 1,000 retained ledgers; users may enter
  another start ledger before inspection. Invalid/expired starts are rejected visibly.
- Captured a fixed end ledger and followed opaque RPC cursors without illegal ledger
  parameters on continuation requests. Newer events beyond the captured end are excluded.
  Deduplicated event IDs and rejected conflicting duplicates, cursor cycles, foreign
  contract events, and backwards ledger order. Failed loads retain rows for retry.
- Added selected-event evidence showing decoded scalar topics/values plus original RPC
  fields and XDR. Large integers remain exact strings; no token decimal scale, transfer
  semantics, or graph flow is inferred. Complex/unsupported values retain raw evidence.
- Added valid transaction-hash navigation to the existing Horizon inspector; its own
  historical availability still applies. Missing success flags are labeled Unknown.

### Exports, persistence, privacy, and scope

- Added CSV/JSON contract exports and saved investigations with instance, events,
  fixed query range, page provenance, retention bounds, and completion/page-limit state.
  Unqueried event history is distinguished from an empty completed query.
- Saved contract evidence is offline and excluded from single-resource library refresh;
  inspect and save again to capture newer evidence. Contract lookups bypass the response
  cache. Network/search changes and cancellation invalidate late requests.
- Public RPC providers receive contract IDs and query parameters. No private keys,
  credentials, contract execution, simulation, or transaction submission are used.
  No WASM/source downloads, function discovery, or arbitrary storage enumeration were added.
- Updated README and specification with endpoint choices, retention/precision caveats,
  supported features, and the remaining advanced-contract scope. No new dependencies.

### Validation

- Added deterministic RPC tests for valid/invalid IDs, network verification, WASM/native
  executable decoding, exact instance storage, absent/wrong entries, cursor parameters,
  ledger boundaries, event identity/status/precision, duplicate/cursor failure, stale
  responses, rate limits, RPC errors, retention validation, exports and saved snapshots.
- Local validation: all 112 tests and Ruff checks passed; source distribution and wheel
  builds, lockfile validation, and CLI version checks succeeded. A headless Qt preview
  was inspected, and readable details were separated from raw instance evidence.
- Read-only live checks on 2026-09-30 verified Testnet network/health and Mainnet's
  public documentation example contract instance plus two 20-event pages with an
  advancing cursor. This does not guarantee continuing provider availability or full history.
- GitHub's Linux/macOS/Windows checks remain pending at the time of this source update.

## [0.0.9] - 2026-09-29

### Added — bounded graph expansion and observed routes

- Added Explore multiple hops to Graph after root payments load. A separate window
  preserves the root investigation while discovered accounts can be selected for
  expansion, including by double-clicking a canvas node. Each click fetches one page
  of up to 20 records; accounts keep independent cursors and no automatic crawl occurs.
- Added explicit limits of three discovery hops, ten fetched accounts, and five pages
  per account. Root seeding uses at most five loaded pages and reports truncation.
  The canvas shows at most forty nodes; selectors, the edge table, and exports retain
  the broader discovered/filtered data. Added node dragging, zoom, pan, and Fit graph.
- Added directed per-asset edge tables with exact totals and operation-level evidence.
  Select an edge to see its transfers; double-click evidence to inspect its transaction.
  Repeated operations fetched from different accounts are counted once. Conflicting
  observations and unsupported, failed, self, or mismatched transfers are excluded.
- Added asset-specific directed routes from the root or to the root through a selected
  target, bounded to three edges and one hundred simple routes without repeated nodes.
  Asset identity includes issuer. Direction filters route queries, not the edge table.
  Paths represent observed connections, not same-fund continuity, chronological flow,
  ownership, or complete history. No amounts are summed across hops.

### Evidence, coverage, and persistence

- Added per-account source, retrieval/cache status, loaded time range, pagination and
  limit status, plus unique-operation exclusions and unfetched-account export metadata.
  End-of-available-results does not establish complete lifetime coverage. Existing cache
  settings apply; expanding an account sends that public address to the selected Horizon.
- Added expanded-graph CSV/JSON export using the existing export envelope with a new
  expanded_graph kind. Snapshots include filtered connections and evidence, route query,
  discovered unfetched accounts, page provenance, and explicit bounds/truncation flags.
- Added Save expanded graph to selected investigation. Saved graph evidence reopens
  offline and cannot be refreshed through the single-resource refresh action; build and
  save a new trace for newer evidence. Interactive graph layout restoration is not added.
- Closing the expansion window discards unsaved exploration. It is seeded independently
  of subsequent root pagination. Network/account reset closes it and rejects stale results.

### Reliability and validation

- Expansion failures retain prior evidence and cursors for retry. Cancellation uses a
  generation guard in addition to cancelling network work; retired windows remain alive
  until cancellation-resistant tasks settle. No new runtime dependencies.
- Added tests for deduplication/precision, cycles and asset isolation, depth/page/account
  bounds, invalid page identity/cursors, conflicts/exclusions, provenance exports, retry,
  per-account pagination, transaction evidence navigation, stale results after network
  changes, asset filters, seed truncation, and saved offline expanded graphs.
- Local validation: all 97 tests and Ruff checks passed; source distribution and wheel
  builds, lockfile validation, and CLI version checks succeeded. A rendered headless Qt
  preview was inspected and initial graph fitting corrected. Remote platform checks
  remain pending at the time of this source update.

## [0.0.8] - 2026-09-27

### Added — saved investigations with local notes and labels

- Added Investigations navigation with named local workspaces, reopening, renaming,
  explicitly saved notes and comma-separated labels, and confirmed deletion.
- Added Save resource to selected investigation for accounts and assets, plus a save
  action inside transaction inspectors. Account snapshots include already-loaded
  activity and graph evidence without fetching additional records. Every saved resource
  retains its network, identity, exact values, source, retrieval time, and coverage.
- Added an offline, read-only JSON evidence viewer. Multiple saves retain separate
  snapshots; this version does not restore interactive graph layouts or explorer state.
- Added explicit live refresh bypassing the optional cache. New evidence is appended,
  preserving original snapshots. Account refresh fetches the current account only;
  newer activity/graph evidence must be loaded and saved from the explorer separately.
- Added CSV/JSON investigation exports. Names, notes, and labels are excluded by default
  and included only when the annotation checkbox is selected. Only saved annotations
  are exported, clearly separated from network evidence.

### Storage, privacy, and reliability

- Added independent SQLite investigation storage under the system application-data
  directory, with schema version 1, atomic writes, a 20 MB per-investigation limit,
  and refusal to overwrite unknown future schema versions. Storage is unencrypted.
- Clearing cache does not remove investigations; deleting investigations does not remove
  exported files. The library notice tooltip exposes the local database location.
- Refresh keeps its original investigation identity even if users select another case.
  It preserves intervening saved annotations, never recreates deleted investigations,
  and retains existing evidence when network or storage operations fail.
- Unsaved annotations are protected when switching investigations or closing the app;
  adding evidence or refreshing does not erase unfinished annotation edits.
- No new runtime dependencies. Updated README and scope documentation with workflow,
  offline-viewer limitations, refresh coverage, and privacy/export behavior.

### Validation

- Added storage restart, future-schema refusal, deletion, offline reopening, annotation
  export, explorer capture, refresh failure, deletion-race, and cross-investigation
  refresh tests, plus asset/transaction capture and refresh coverage.
- Local validation: all 87 tests and Ruff checks passed; source distribution and wheel
  builds, lockfile validation, and CLI version checks succeeded. GitHub platform checks
  remain pending at the time of this source update.

## [0.0.7] - 2026-09-24

### Added — asset and issuer inspection

- Enabled Assets navigation with direct code/issuer search. Overview offers Inspect
  selected asset and balance double-click navigation. Payments offers Inspect selected
  payment asset while preserving transaction double-click behavior; path payments use
  the destination asset recorded in the payment row.
- Issued assets retain case-sensitive code, full checksum-valid issuer, and network.
  Assets sharing a code remain distinct. Invalid identifiers are rejected before I/O;
  mismatched identities, ambiguous responses, and malformed statistics are rejected.
- Displayed Horizon account counts and exact balances by authorization state, plus
  claimable-balance, liquidity-pool, and contract counts/amounts where available.
  Missing fields remain unknown; no circulating-supply estimate or valuation is inferred.
- Displayed issuer approval, revocation, immutable authorization, and clawback flags
  with plain-English labels. Flags are not an endorsement or proof of issuer trust.
- Added Inspect issuer account and activity, opening the existing account explorer.
  Transactions, Operations, and Payments then show the issuer's account-wide activity,
  not a feed filtered to the selected asset.
- Added native XLM inspection with no issuer and no network request. Native supply
  statistics are not included; an issued asset named XLM remains distinct. Liquidity
  pool shares and standalone contract tokens are outside this view's scope.
- Added CSV/JSON asset export through schema version 1, preserving exact statistics,
  unknown fields, flags, identity, retrieval time, source, and coverage. Native exports
  retain their definition/provenance metadata even without statistic rows.

### Reliability, privacy, and compatibility

- Added cancellable asset loading, retry through Inspect asset, stale-response rejection,
  and reset on network/account/cache-context changes. Exports and issuer navigation
  remain disabled until a successful response. Navigation uses the displayed snapshot,
  not unsubmitted edits to the issuer field.
- Issued asset lookups use the selected network's Horizon /assets endpoint and send the
  code and public issuer. Asset statistics load live even with caching enabled; existing
  cache formats and account/activity behavior are unchanged. Issuer websites and TOML
  files are not fetched. No new runtime dependencies.
- Updated README and specification with usage, field limitations, export behavior,
  and implemented status. Existing platform support and pre-alpha limitations apply.

### Validation

- Added tests for same-code/different-issuer identity, precise amounts, unknown values,
  malformed data, validation before requests, network routing, cache bypass, native XLM,
  stale responses, errors/retry, issuer/balance/payment navigation, and asset exports.
- Local validation: all 81 tests and Ruff checks passed; source distribution and wheel
  builds succeeded; dependency lockfile and CLI version checks passed. GitHub platform
  checks remain pending at the time of this source update.

## [0.0.6] - 2026-09-15

### Added — local CSV and JSON exports

- Added Export CSV and Export JSON controls to Overview, Transactions, Operations,
  Payments, Graph, and transaction inspectors. Controls become available after a
  successful load, including empty responses, and clear when the investigation resets.
- Overview exports balances and trustline details with account metadata. Activity
  exports include all loaded rows and stable identifiers. Exporting makes no new
  network requests; use Load more first to extend the saved coverage.
- Graph exports honor the current asset and direction filters and include every
  matching table relationship, exact totals, and supporting transfer evidence. The
  canvas's 30-counterparty limit does not restrict export. Exclusion counts describe
  all loaded payment records, including those outside the selected filters.
- Transaction exports retain metadata, exact fees, ordered available operations,
  readable explanations, raw Horizon evidence, and warnings about incomplete data.
- Introduced export schema version 1 with software version, export timestamp, network,
  data source, original retrieval times, cache labels, and coverage descriptions.
  Activity exports preserve individual page provenance when cached and live results
  are mixed, plus the loaded time range, next cursor, and end-of-available-results flag.
  These are snapshots of loaded Horizon data, not claims of complete lifetime history.

### Accuracy, privacy, and file handling

- JSON stores monetary amounts as exact strings. CSV repeats metadata columns and
  stores nested provenance/evidence as JSON cells; empty results keep a metadata row.
  Both formats are UTF-8. Import amounts and identifiers as text in spreadsheets to
  avoid automatic rounding or conversion.
- CSV prefixes formula-like cells with an apostrophe. JSON retains original text.
  CSV quoting preserves commas, quotes, line breaks, and non-ASCII content.
- Save dialogs support cancellation and destination selection. Writes use temporary
  files beside the destination and atomic replacement, preserving existing contents
  when replacement fails and cleaning temporary files. Save failures are reported.
- Exports are unencrypted local files, created only on request, independently of
  optional caching. Clearing the cache does not remove exported investigations.
- No new runtime dependencies. Updated README and specification to distinguish
  implemented exports from the remaining roadmap.

### Validation

- Added automated export checks for precision, Unicode/CSV quoting, spreadsheet
  formula protection, empty results, mixed page provenance, graph filters/evidence,
  network reset, partial transactions, cancellation, successful saves, and failed writes.
- Local validation: all 65 tests and Ruff checks passed; source distribution and wheel
  builds succeeded; the lockfile check and CLI version check passed. Cross-platform
  GitHub checks remain pending at the time of this source update.

### Documentation — 2026-09-15

- Reorganized the README into clearly separated sections with shorter paragraphs,
  descriptive headings, navigation links, and comparison tables to improve GitHub readability.
- Added project background, a current-feature overview, requirements, installation steps,
  a first-investigation walkthrough, graph controls, and cache usage instructions.
- Separated implemented behavior from the future roadmap and explained history coverage,
  counterparty exclusions, privacy, architecture, and platform-check limitations.
- Corrected outdated repository descriptions that still labeled implemented analysis,
  provider, and storage modules as future work.
- Documentation-only update; application version remains 0.0.5.

## [0.0.5] - 2026-09-13

### Added — optional local SQLite cache

- Added **Use local cache** above the investigation workspace. It starts off each time
  the application opens, so normal searches do not create a database until enabled.
- Enabled caching stores fetched account snapshots, individually keyed activity pages,
  and complete transaction details in a platform-specific Qt cache directory outside
  the repository. Hover the cache status to see the database path.
- Accounts and activity pages expire after 60 seconds. Complete transaction details
  expire after 24 hours. Expired records are fetched remotely; they are not silently
  returned as an offline fallback. Partial transaction details and failed requests are
  not cached, allowing subsequent requests to retry missing operations.
- Cache keys include provider identity, representation revision, resource type, network,
  identifier, activity kind, and pagination cursor. Identical addresses on Mainnet and
  Testnet cannot reuse each other's snapshots.
- Cached results display **Local cache** and retain their original source and retrieval
  timestamp. Live results and storage failures are labeled separately. Money is serialized
  as exact decimal text rather than floating-point numbers; structured transfer evidence
  and transaction-operation ordering are preserved across a restart.

### User controls and behavior

- Uncheck **Use local cache** and investigate again to force live requests. Changing this
  control clears the displayed investigation and invalidates requests from the previous mode.
  Disabling caching leaves existing disk snapshots intact until they expire or are cleared.
- **Clear cache** deletes stored rows, compacts the SQLite database, and clears the current
  investigation. It remains available while caching is off. Failures are reported rather
  than claiming data was removed. Clearing an unused cache does not create a database.
- Disk operations run outside the Qt event loop. Cancellation and deletion are coordinated
  so a pending request or an already-started write cannot refill a just-cleared cache.
- Corrupt, unavailable, or incompatible cache storage falls back to live fetching. Unknown
  schema versions are preserved rather than overwritten. Resource and network identities
  are checked before cached snapshots are used or newly fetched snapshots are stored.

### Storage scope and privacy

- Schema version 1 stores JSON snapshots only; it does not deserialize executable Python
  objects. Cache decoding accepts only known domain containers and validates field types.
- Retention is bounded to 250 snapshots and at most 2 MB per payload. Old entries are pruned
  during writes; expired entries are removed when read. This is a disposable response cache,
  not a complete historical index, saved-investigation system, or automatic watchlist.
- Local snapshots contain searched public ledger data and are not encrypted. System backups
  or filesystem snapshots may retain prior copies independently of the application.
- The existing ignore rules exclude SQLite files and sidecars. No database or local project
  instruction file is included in the public repository.

### Documentation and validation

- Added plain-English docstrings and comments explaining persistence, expiry, exact-value
  serialization, cancellation, clearing, and the affected UI lifecycle code.
- Added isolated tests for disabled-cache behavior, restart reuse, expiry, network/cursor
  separation, transaction completeness, corrupt storage, schema protection, bounded retention,
  typed serialization, late writes during deletion, and the visible cache controls.
- Updated the README, project specification, package metadata, and lockfile to 0.0.5.

## [0.0.4] - 2026-09-11

### Added

- Graph section with one-hop counterparty analysis from the Payments list's fetched records.
- Exact incoming/outgoing totals grouped by counterparty and full asset identity, with
  operation counts and transaction evidence. Different issuers are never combined.
- Successful direct payments and account-creation funding included; failed transactions,
  self-transfers, path payments, merges, unsupported identifiers, and unrelated records
  excluded explicitly. Repeated operation IDs do not double-count.
- NetworkX directed relationship model and native Qt graph rendering, with arrows,
  draggable nodes, zoom, pan, fit, hover summaries, and right-click address copying.
- Asset/direction filters and a counterparty table ranked by operation count. The graph
  shows up to 30 filtered counterparties; the table retains all fetched relationships.
- Double-click a node or counterparty row to investigate that account. Select a table row
  to see supporting operations; double-click evidence to open its transaction.
- Graph and Payments share pagination, loading/retry states, source and retrieval metadata.
  Switching accounts/networks clears analysis and rejects stale requests.

### Scope

- Totals cover loaded records only and are not lifetime balances. End of available results
  means the end of Horizon's available history, not a guarantee of complete ledger history.
- This is a one-hop graph. Branch expansion, path-payment routing reconstruction, persistent
  investigations, labels, and saved graphs remain planned.

## [0.0.3] - 2026-09-09

### Added

- Transaction inspection from selected rows in Transactions, Operations, or Payments,
  with a button or double-click. Activity lists retain their loaded pages.
- Direct transaction-hash search with hexadecimal validation and Mainnet/Testnet separation.
- Non-modal transaction inspector showing status, ledger, time, memo, source account,
  fee payer, exact charged fees in XLM and stroops, and ordered operations.
- Readable explanations for payments, path payments, account creation/merge, trustline
  changes, sell/buy/passive offers, and recognized account-option changes.
- Operation-specific source accounts, raw Horizon transaction/operation data, and explicit
  unsupported/malformed-operation explanations.
- Failed-transaction instructions marked as not applied; incomplete operations explicitly
  labeled while keeping available transaction metadata visible.
- Reload, cancellation when closing the inspector, and stale-response protection when
  changing the account/network or searching again.
- Tests for ordered multi-page operations, failed transactions, partial data, wrong
  transaction identities, decimal precision, decoder fallback, and dialog lifecycle.

### Validation and scope

- Confirmed the previous Windows failure was the already-fixed UTF-8 changelog test;
  the subsequent 0.0.2 checks passed on all three operating systems.
- Decoding uses Horizon's parsed fields. General XDR decoding, Soroban execution decoding,
  complete historical coverage, and tracing remain planned.

## [0.0.2] - 2026-09-09

### Added

- Transactions, Operations, and Payments navigation for the inspected account.
- Asynchronous Horizon activity fetching, newest first, with 20 records per page.
- Independent “Load more” pagination for each list, with end-of-results and empty states.
- Transaction hashes, status, source accounts, operation counts, and fees in stroops.
- Operation types, transaction references, and payment parties, exact amount strings,
  and asset codes with issuers. Path-payment amounts represent destination amounts;
  account-merge amounts unavailable from this endpoint are shown as unavailable.
- Source, retrieval time, network, and available-history coverage notices.
- Retry for failed pages without discarding existing rows or advancing the cursor.
- Stale-page rejection after account/network changes, duplicate-row suppression, and
  protection against repeated loading while a page is in progress.
- Application version in the window title and header; version consistency test.
- This changelog, included in the repository allowlist.

### Fixed

- Explicit UTF-8 reading in version/changelog verification for Windows compatibility.

### Scope

- Activity lists use Horizon's available history and request failed records too.
- Complete historical coverage, detailed transaction decoding, live streaming, graphs,
  local persistence, and exports remain planned.

## [0.0.1] - 2026-09-08

### Added

- Python/Qt desktop skeleton with uv packaging and dependency lockfile.
- Checksum-valid Stellar G-address lookup through a provider/service abstraction.
- Mainnet and Testnet Horizon account details, balances, and trustlines.
- Exact decimal balances, asset issuer identities, trust limits, and authorization states.
- Asynchronous loading, cancellation, errors, and stale-result protection.
- Automated checks on Linux, macOS, and Windows; account explorer tests.
- Project specification, README, MIT license, and independent-project notice.
- Repository ignore rules for local configuration, credentials, generated artifacts,
  private investigation data, and caches.

The initial repository was created on 2026-09-07; account lookup followed under the same
0.0.1 version. This entry consolidates that initial development history.
