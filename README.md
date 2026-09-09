# PolarStellar

**Navigate the Stellar network.**

Open-source desktop network intelligence for Stellar and Soroban. PolarStellar is being
built to help inspect accounts, understand transaction operations, discover counterparties,
and trace relationships in a local desktop workspace.

**Version: 0.0.3 — pre-alpha.** See [CHANGELOG.md](CHANGELOG.md) for feature history. Paste a checksum-valid Stellar G-address to
fetch account details, XLM and asset balances, and trustlines from Horizon. Mainnet and
Testnet are separate. Lookups are asynchronous and cancellable; switching networks or
starting another search discards stale results. Issuers, trust limits, and authorization
status are shown alongside exact amounts.

Transactions, Operations, and Payments show recent activity after inspecting an account.
Open a section to fetch its first 20 records, then choose **Load more** for older records.
Each section keeps its own position. Failed page loads can be retried without losing rows.
Results are newest first and limited to Horizon's available history; failed records are labeled.
Payment amounts are preserved as exact strings; path payments show the destination amount.
Select a row and choose **Open selected transaction**, or double-click it, to see status,
ledger, charged fee, and ordered operations. You can also paste a 64-character transaction
hash directly into search. Common operations have readable explanations; unsupported types
retain raw data. Failed instructions and incomplete operation lists are labeled explicitly.
General XDR/Soroban decoding, graphs, contracts, local caching, and export remain planned.
No private keys are required, and the application does not submit transactions.
Lookups send the searched public address to the selected Horizon endpoint; no investigation
data is saved locally in this phase.

## Product direction

- Account balances, trustlines, signers, and activity.
- Human-readable transaction decoding with operations as first-class objects.
- Counterparty discovery and interactive relationship graphs.
- Soroban contract inspection and events.
- Local SQLite caching and investigation state; CSV and JSON export.

This list describes the broader product roadmap. See the [full project scope and specification](PolarStellar_Project_Spec.md)
for architecture, release boundaries, acceptance criteria, and the roadmap.

## Run PolarStellar

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
git clone https://github.com/damianpitt/polarstellar.git
cd polarstellar
uv sync
uv run polarstellar
```

Python 3.12 is the development baseline. Linux is the primary target; macOS and Windows
are architectural targets from day one. Native installers are not available yet.
On Ubuntu 24.04, Qt also requires system graphics libraries; install `libegl1`
if startup reports a missing `libEGL.so.1`.

```sh
uv run polarstellar --version
uv run ruff check .
uv run pytest
```

Setup references: [uv workflow documentation](https://docs.astral.sh/uv/guides/integration/github/)
and [Qt for Python getting started](https://doc.qt.io/qtforpython-6/gettingstarted.html).

## Structure

```text
src/polarstellar/
  app/        Application startup and window
  stellar/    Future providers, domain models, and decoding
  analysis/   Future counterparty, flow, and graph analysis
  storage/    Future SQLite persistence and cache
  ui/         Desktop widgets and presentation
  resources/  Styles and icons
```

The UI calls an account service, with checksum validation and a provider interface.
The Horizon adapter normalizes responses into account snapshots with network and retrieval
metadata. RPC and Hubble adapters remain planned.

The account adapter follows the [official Horizon account endpoint](https://developers.stellar.org/docs/data/apis/horizon/api-reference/retrieve-an-account).

PolarStellar is an open-source project, not affiliated with the Stellar Development Foundation.

## License

[MIT](LICENSE) © 2026 Damiano Pittau.
