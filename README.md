# PolarStellar

### Navigate the Stellar network.

An open-source desktop investigation toolkit for Stellar, with Soroban support on the roadmap.
Explore accounts, inspect transactions, and understand relationships between counterparties
from a local desktop workspace.

**Current version: 0.0.6 · Pre-alpha**

**Linux-first · Cross-platform Python / Qt application**

[Getting started](#getting-started) · [Features](#features) · [First investigation](#your-first-investigation) · [Roadmap](#roadmap) · [Changelog](CHANGELOG.md)

---

## About the project

PolarStellar brings account information, transaction explanations, and relationship analysis
into one desktop tool. Its focus is helping you understand what happened on the network
and which records support that interpretation.

The name combines **Polestar**, a reference point for navigation, with **Stellar**.
That navigation theme guides the product: start with an account or transaction, inspect
its activity, and follow the evidence to connected accounts.

The application is useful for exploring payment activity, checking asset and trustline
information, investigating transaction instructions, and examining direct counterparties.
It is read-only: no private keys are required, and it does not sign or submit transactions.

PolarStellar is an open-source project, **not affiliated with the Stellar Development Foundation**.

## At a glance

| Area | Available in 0.0.6 |
| --- | --- |
| Account lookup | G-address validation, balances, trustlines, sequence, and home domain |
| Recent activity | Transactions, operations, and payments with independent pagination |
| Transaction inspection | Status, ledger, fees, memo, ordered operations, and raw Horizon data |
| Counterparties | Incoming/outgoing totals by asset, with supporting operation evidence |
| Relationship graph | Interactive one-hop graph with asset and direction filters |
| Local caching | Optional SQLite snapshots with expiry and clear-cache controls |
| Local export | CSV and JSON snapshots with source, coverage, and supporting evidence |
| Networks | Separate Mainnet and Testnet investigations |

> **Pre-alpha:** native installers are not available yet. Contract exploration
> and several advanced investigation features remain planned.

## Getting started

### Requirements

- Git to clone the repository.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) to manage Python and dependencies.
- A desktop environment capable of running Qt applications.
- Internet access for live Stellar lookups.

Python **3.12** is the development baseline. The repository includes a Python version file
and a dependency lockfile for reproducible setup.

### Install and launch

```sh
git clone https://github.com/damianpitt/polarstellar.git
cd polarstellar
uv sync --locked
uv run polarstellar
```

Check the installed application version:

```sh
uv run polarstellar --version
```

### Platform notes

Linux is the primary target. Automated checks also run on macOS and Windows; these checks
cover code, tests, and Python package builds, rather than native installer validation.

On Ubuntu 24.04, if startup reports a missing `libEGL.so.1`, install the Qt graphics dependency:

```sh
sudo apt-get install libegl1
```

## Your first investigation

1. **Select Mainnet or Testnet.** Choose the network containing the account you want to inspect.
2. **Paste a Stellar G-address and select Inspect.** Review its balances and trustlines.
3. **Open Transactions, Operations, or Payments.** Each section starts with 20 records, newest first.
4. **Select Load more.** Fetch older records without losing the rows already displayed.
5. **Open a transaction.** Double-click a row or use **Open selected transaction** to inspect it.
6. **Open Graph.** Review direct counterparties, filter by asset or direction, and inspect the evidence.

You can also paste a **64-character transaction hash** directly into search to open its details.

## Features

### Account overview

Inspect an account's sequence number, home domain, XLM balance, and issued-asset balances.
Trustline information includes the asset issuer, trust limit, and authorization status.

Addresses are checksum-validated before requests are sent. Amounts retain their exact
values, and issued assets remain distinguished by both their code and issuer.

### Recent activity

Transactions, Operations, and Payments each maintain their own pagination position.
A failed page request can be retried without discarding existing rows.

Loading is asynchronous and cancellable. Starting another search or changing networks
invalidates old requests so late responses cannot overwrite the current investigation.

### Transaction inspection

The transaction inspector displays:

- Success or failure, ledger, and creation time.
- Source account, fee payer, and charged fee in XLM and stroops.
- Memo and operations in execution order.
- Each operation's source account and readable explanation where supported.
- Raw Horizon transaction and operation data for closer inspection.

Readable explanations cover common payments, path payments, account creation and merging,
trustline changes, offers, and recognized account-option changes.

Failed instructions are marked as **not applied**. Unsupported operation types retain raw
data, and incomplete operation lists are labeled explicitly.

### Counterparty analysis and graph

The Graph section derives relationships from the Payments records loaded for the current
account. It groups exact incoming and outgoing totals by counterparty and asset.

Select a counterparty row to see its supporting operations. Double-click an evidence item
to open the corresponding transaction.

| Action | Result |
| --- | --- |
| Filter by asset or direction | Focus the graph and counterparty table |
| Drag a node | Reposition it and its connected arrows |
| Scroll over the graph | Zoom in or out |
| Drag the background | Pan the view |
| Select Fit graph | Bring the displayed graph into view |
| Right-click a node | Copy its full address |
| Double-click a node or counterparty row | Start an investigation of that account |

The graph shows up to **30 filtered counterparties**, ranked by operation count. The table
retains all fetched relationships. Graph and Payments share the same **Load more** state.

### Optional local cache

Enable **Use local cache** to reuse recently fetched snapshots. Caching starts **off** each session.

| Snapshot | Reuse period |
| --- | --- |
| Account details | 60 seconds |
| Individual activity pages | 60 seconds |
| Complete transaction details | 24 hours |

Cached results retain their original source and retrieval time and display a **Local cache**
label. Expired snapshots are fetched again; incomplete transaction details are not cached.

- **Force live data:** uncheck caching and investigate again.
- **Delete saved snapshots:** select **Clear cache**.
- **Find the database:** hover the cache status to see its location.

Disabling caching does not delete existing snapshots. The cache is stored outside the
repository in the platform's cache directory, is unencrypted, and does not save graph
layouts, annotations, or complete investigations.

### CSV and JSON export

Choose **Export CSV** or **Export JSON** in Overview, Transactions, Operations,
Payments, Graph, or an open transaction inspector. Choose a destination in the save
dialog. Exports use the data already loaded and make no additional network requests.
Use **Load more** first when you need older records.

| View | Export contents |
| --- | --- |
| Overview | Balances, trust limits, authorization, and account metadata |
| Activity lists | All loaded rows, stable identifiers, and individual page provenance |
| Graph | All filtered table relationships, exact totals, and supporting transfers |
| Transaction inspector | Metadata, ordered available operations, explanations, raw Horizon evidence, and partial-data warnings |

Graph exports respect asset/direction filters and include the full table beyond the
canvas's 30-counterparty limit. Exclusion counts cover all loaded Payments records.
Files retain network, source, original retrieval times, cache labels, export time,
software version, and coverage limitations. Activity exports include the loaded time
range and pagination boundary; the end of available results is not proof of complete
lifetime history.

JSON uses schema version 1 and represents monetary values as strings. CSV repeats
metadata columns on every row, with nested page provenance and evidence encoded as
JSON cells. Empty results retain a metadata row. Import amounts and identifiers as
**text** in spreadsheets to avoid automatic rounding or conversion. CSV prefixes
formula-like cells with an apostrophe; JSON preserves original text. Both use UTF-8.

Exports are unencrypted local files and may contain addresses, memos, and investigation
evidence. Saving is explicit and independent of caching. Clearing the cache does not
delete exports. Files are replaced only after a complete write; errors are reported.

## Understanding the results

### History and coverage

Results reflect **loaded records within Horizon's available history**. Reaching the end
of available results does not guarantee that you have the account's complete ledger history.
Counterparty totals are not lifetime totals or current balances.

### What contributes to counterparty totals

Only **successful direct payments and account-creation funding** contribute to totals.
Path payments, account merges, failed transactions, self-transfers, unsupported identifiers,
and unrelated records are excluded and accounted for in the coverage information.

Path payments can appear in the Payments list, where the displayed amount is the destination
amount. Their routing is not reconstructed into direct-flow totals.

### Interpretation and privacy

A relationship shows observed activity between addresses; it does not establish common
ownership or identify a real-world entity.

Live requests disclose the searched public identifier to the selected Horizon endpoint.
With caching enabled, fetched ledger snapshots are also retained on your computer.
System backups may retain prior copies independently of the application's clear-cache action.

## Architecture

PolarStellar separates acquisition, interpretation, analysis, persistence, and presentation
so the desktop interface does not call remote APIs directly.

| Component | Responsibility |
| --- | --- |
| Python | Application and analysis logic |
| PySide6 / Qt Widgets | Desktop interface, tables, and graph rendering |
| qasync | Asynchronous work integrated with the Qt event loop |
| httpx | HTTP access to Horizon |
| Stellar Python SDK | Stellar identifier validation |
| NetworkX | Directed counterparty relationship model |
| SQLite | Optional, expiring local snapshot cache |
| uv | Dependency management and reproducible environments |

**Horizon is the implemented data provider.** Stellar RPC for current ledger and Soroban
access, and Hubble for deeper historical analysis, remain planned integrations.

### Repository layout

```text
src/polarstellar/
  app/        Application startup and main window
  stellar/    Provider interfaces, Horizon access, models, and decoding
  analysis/   Counterparty aggregation and relationship modeling
  storage/    SQLite snapshots, serialization, and caching provider
  ui/         Activity tables, transaction inspector, graph, and cache controls
  resources/  Package reserved for visual resources

tests/        Automated tests for data handling and desktop behavior
```

`__init__.py` files are intentional Python package source. Generated bytecode, environments,
SQLite files, credentials, and local working files are excluded by `.gitignore`.

## Roadmap

The next areas of work include:

- Expanded asset inspection and filtering.
- Saved investigations, local notes, labels, and watchlists.
- Graph expansion and deeper flow tracing.
- Soroban contract inspection and event exploration through Stellar RPC.
- Optional historical analytics through Hubble.
- Native packaging and release validation for Linux, macOS, and Windows.

These are **planned capabilities**, not features available in 0.0.6. The detailed release
boundaries and acceptance criteria are maintained in the project specification.

## Documentation and checks

- [Full project specification](PolarStellar_Project_Spec.md) — vision, architecture, scope, and roadmap.
- [Changelog](CHANGELOG.md) — versioned features, fixes, limitations, and validation notes.
- [GitHub Actions](https://github.com/damianpitt/polarstellar/actions) — automated platform checks.

Run the local checks:

```sh
uv run ruff check .
uv run pytest
uv build
```

## License

[MIT License](LICENSE) © 2026 Damiano Pittau.
