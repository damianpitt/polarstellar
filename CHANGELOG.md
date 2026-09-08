# Changelog

User-visible features, fixes, and software versions are recorded here.
Versions remain pre-alpha until the V0.1 scope is complete. These entries describe
source updates; they do not imply a packaged installer release.

## [Unreleased]

Add upcoming changes here as they are implemented. For each version update, move the
completed entries into a dated section and keep `pyproject.toml`, the package version,
and the uv lockfile in sync. The application header and `--version` show the package version.

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
