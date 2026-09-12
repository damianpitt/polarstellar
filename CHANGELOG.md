# Changelog

User-visible features, fixes, and software versions are recorded here.
Versions remain pre-alpha until the V0.1 scope is complete. These entries describe
source updates; they do not imply a packaged installer release.

## [Unreleased]

Add upcoming changes here as they are implemented. For each version update, move the
completed entries into a dated section and keep `pyproject.toml`, the package version,
and the uv lockfile in sync. The application header and `--version` show the package version.

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
