# PolarStellar

### Navigate the Stellar network.

An open-source desktop investigation toolkit for Stellar, with read-only Soroban contract and event inspection.
Explore accounts, inspect transactions, and understand relationships between counterparties
from a local desktop workspace.

**Current version: 0.0.14 · Pre-alpha**

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

| Area | Available in 0.0.14 |
| --- | --- |
| Account lookup | G-address validation, balances, trustlines, sequence, and home domain |
| Recent activity | Transactions, operations, and payments with independent pagination |
| Transaction inspection | Status, ledger, fees, memo, ordered operations, and raw Horizon data |
| Counterparties | Incoming/outgoing totals by asset, with supporting operation evidence |
| Relationship graph | Direct counterparties plus bounded multi-hop expansion and same-asset routes |
| Local caching | Optional SQLite snapshots with expiry and clear-cache controls |
| Contracts | Read-only instance details, raw XDR, and paginated RPC contract events |
| Assets | Issued-asset discovery with exact filters/pagination, statistics, inspector shortcuts, and native XLM details |
| Investigations | Named offline evidence collections with local notes, labels, and explicit refresh |
| Watchlists | Local resource lists, combined offline filters, portable JSON import/export, annotations and manual refresh |
| Local export | CSV and JSON snapshots with source, coverage, and supporting evidence |
| Networks | Separate Mainnet and Testnet investigations |

> **Pre-alpha:** native portable builds are unsigned preview applications. System installers,
> signing/notarization, and advanced contract analysis remain planned.

## Getting started

### Native downloads

The [Native builds workflow](https://github.com/damianpitt/polarstellar/actions/workflows/native.yml)
runs manually and produces Linux x86_64, Windows x86_64, and macOS Apple Silicon bundles. Download
artifacts from a successful run while preview releases are being validated. Draft releases
are visible only to repository maintainers; published downloads will appear on the
[Releases page](https://github.com/damianpitt/polarstellar/releases).

The current source version is 0.0.14; previously validated native artifacts are 0.0.11.
New native bundles and release uploads are deferred until explicitly requested.

Extract the complete archive, then launch the Linux executable, Windows `.exe`, or macOS
`.app` inside `PolarStellar`. Runtime Python/uv installation is unnecessary. Keep the bundled
libraries with the executable. Check the matching `.sha256` file and validation report.
These are portable apps rather than system installers; Windows builds are unsigned and
macOS builds are ad-hoc signed, without notarization.

See [native builds and release validation](docs/RELEASES.md) for dependencies, architecture
selection, platform approval prompts, local builds, and exact validation limits.

### Requirements for running from source

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

Linux is the primary target. Source checks run on Linux, macOS and Windows. The separate
native workflow builds on Ubuntu 22.04, macOS 15 (Apple Silicon), and Windows Server
2022, then launches each extracted bundle with its native Qt display plugin. This preview
validation does not establish compatibility with every OS version or replace clean-machine
interactive testing. See the release guide for the current platform scope.

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

### Graph expansion and observed routes

In **Graph**, load Payments and choose **Explore multiple hops**. The expanded window
starts from up to the first five loaded root pages. Select a discovered account and
choose **Expand / load next 20** to fetch one page. Repeated clicks load older records
for that account; other accounts keep their own cursors. There is no automatic crawl.
Double-click a graph node to select it for expansion, drag to reposition nodes, scroll
to zoom, or use **Fit graph**. Failed requests can be retried; Cancel retains loaded data.

Expansion is limited to **three hops**, **ten fetched accounts**, and **five pages
(100 records) per account**. Depth is the shortest undirected discovery distance from
the root; directed routes are a separate query. The canvas shows up to **40 nodes**,
while the edge table and exports keep all filtered evidence. The account selector also
includes discovered nodes outside the canvas limit.

Choose an asset (including its issuer), a target, and **From root** or **To root** to
list up to 100 simple directed routes, each at most three edges. Routes never revisit
an account or cross asset identities. Direction affects routes; the edge table shows
all connections for the selected asset. Select an edge for its supporting operations;
double-click evidence to open the transaction inspector.

These are **observed connections**, not proof that the same funds flowed through a
route, that transfers occurred in chronological order, or that accounts share an owner.
Amounts are never summed across hops. Only successful direct payments and account
funding contribute. Overlapping pages are deduplicated by operation ID; conflicting
observations are excluded. No route in loaded evidence does not prove no connection.

The coverage panel records each fetched account's loaded time range, source, retrieval
times, cache labels, and pagination/limit status. Unfetched accounts and excluded
operations are identified in exports. End-of-results is not proof of lifetime coverage.
Normal cache settings apply to expansion requests; more public addresses are sent to
the selected Horizon endpoint only when you choose to expand them.

Export CSV/JSON from the expanded window, or choose **Save expanded graph to selected
investigation**. Saved snapshots include filtered edges, exact evidence, route query,
limits, and per-account provenance. They reopen in the offline evidence viewer and
are not refreshed by the single-resource refresh button. Build a new trace to capture
newer graph evidence. Closing the trace discards its unsaved expansion; changing the
root investigation or network closes it and rejects outstanding results.

### Asset discovery and filtering

In **Assets → Discover assets**, enter an optional exact **asset code**, an optional
checksum-valid **issuer G-address**, or both. Leave both blank to browse Horizon's
issued-asset statistics. Choose **Search / restart** to load the first **20 records**,
then **Load more (20)** for another page. Code filters are case-sensitive and match whole
codes; they are not prefix, name, or free-text searches. Edited filters take effect only
on Search / restart; existing rows and exports retain their submitted query and network.

Each row retains its full code/issuer identity and shows available authorized-account
counts, exact authorized-account balances, asset type and approval flag. Unknown fields
stay unknown. Same-code assets from different issuers are different resources. These
statistics are not circulating supply, prices, market value, asset rankings or endorsements.
Native XLM, liquidity-pool shares and standalone contract tokens are outside this catalog.
An `XLM` code search finds **issued assets named XLM**. Inspect native XLM in **Inspect asset**
with an empty issuer.

Select a row and choose **Inspect selected asset**, or double-click it, to open its exact
code/issuer pair in the existing inspector. Inspection performs a separate lookup; newer
statistics may differ. Return to Discover assets to keep browsing the loaded catalog.
To bookmark a result or save investigation evidence, open it in the inspector first.
Unrelated explorer metadata/save controls are hidden and disabled on the discovery tab.

Discovery is live and is not stored in the optional response cache. Each page carries
its own source/retrieval time; pages are not an atomic ledger snapshot. Failed page loads
retain previous rows/cursor for retry. Cancel retains loaded results, and late responses
cannot overwrite a new query or network. Identical overlaps are deduplicated without
changing original retrieval times; conflicting overlapping statistics require a restart.
Cursor cycles and malformed/mismatched rows are rejected before committing a page.

The limit is **50 pages / at most 1,000 returned records**, with no automatic crawl.
Only an empty page marks the current query end; a short page may still have more results.
Reaching the limit is labeled incomplete. Neither boundary proves a complete lifetime
catalog. Network/search/cache-context changes clear discovery results and exports.

**Export CSV / JSON** on the discovery tab saves loaded rows only, including exact values,
all available parsed statistics/flags, frozen filters, network, ascending query order,
per-page provenance and explicit coverage limits. Export makes no requests. Issuer
metadata/TOML crawling, market data and contract-token discovery remain planned.

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

### Asset inspection

Open **Assets** to enter a case-sensitive asset code and issuer G-address. From
Overview, select a balance and choose **Inspect selected asset** (or double-click
it). From Payments, select a row and choose **Inspect selected payment asset**.
Path-payment rows open the destination asset. Pool shares are not supported here.

The view keeps **code, issuer, and network** together: matching codes from different
issuers are different assets. Statistics show balances and account counts by
trustline authorization state, plus amounts/counts for claimable balances,
liquidity pools, and contracts when Horizon supplies them. Missing fields are
**unknown**, not zero. These figures are not a circulating-supply estimate or valuation.

Issuer flags show required approval, revocation, immutability, and clawback settings
when available. They do not establish issuer trustworthiness. Choose **Inspect issuer
account and activity** to open the issuer account, then use Transactions, Operations,
or Payments for its account-wide activity; that activity is not filtered to one asset.

For native XLM, enter **XLM** with an empty issuer. Its view explains that it has no
issuer or issuer flags; native supply statistics are not included. XLM with an issuer
is treated as an issued asset, distinct from native XLM. Standalone contract tokens
remain outside this feature's scope.

Asset statistics load live even when local caching is enabled. Cancel, retry, and
network changes discard stale results. CSV and JSON exports retain the asset's exact
statistics, identity, issuer flags, source, retrieval time, and coverage notes. No issuer
website or metadata file is fetched. Field definitions follow the
[Horizon asset reference](https://developers.stellar.org/docs/data/apis/horizon/api-reference/resources/assets/object).

### Soroban contract inspection and events

Open **Contracts**, enter a checksum-valid **C-address**, and choose **Inspect / refresh**.
You can also paste a contract ID into the main search bar. The current Mainnet/Testnet
selection applies to the entire inspection, and the RPC endpoint's network passphrase
is verified before accepting each lookup or event page.

**Contract details** shows the known persistent contract instance: executable type,
WASM hash when present, modification ledger, live-until ledger when available, and
instance-storage key/value evidence. **Raw instance evidence** retains the original
XDR and provenance. A missing live entry may be absent, archived, or unavailable;
it does not prove the contract never existed. Events can still be queried.

Choose **Load events (oldest first)**, then **Load more events**. By default, the query
covers the most recent 1,000 ledgers within the provider's retained history. To start
elsewhere, enter a start ledger before inspecting; expired or future starts are rejected.
The end ledger is captured at inspection time. Each click loads up to 20 contract events,
with a maximum of 50 pages (1,000 events). The status and exports distinguish unqueried,
completed, and incomplete coverage. A retention window can expire during paging; start
a new inspection if that happens.

Select an event to see topics, values, original RPC fields and XDR. Common scalar values
are decoded; large integers stay exact strings, with **no inferred token decimals**.
Structured or unsupported values retain raw XDR. Events are not automatically interpreted
as transfers or added to graph totals. A valid event transaction hash can be opened in the
existing Horizon inspector, subject to Horizon's own history availability.

CSV/JSON exports include the instance, loaded events, fixed query range, and per-page
source/retrieval/retention metadata. **Save resource to selected investigation** keeps
that evidence offline. Saved contract snapshots are not refreshed by the library's
single-resource refresh button; inspect again in Contracts and save a new snapshot.

RPC requests use the public Gateway Mainnet endpoint and SDF Testnet endpoint listed in
[Stellar's provider directory](https://developers.stellar.org/docs/data/apis/rpc/providers).
Queries disclose the public contract ID and ledger range to that provider. Contract data
loads live and is not stored in the optional response cache. There are no contract calls,
transaction simulations/submissions, source-code verification, WASM downloads, arbitrary
storage enumeration, or automatic function discovery in this slice. See the official
[getLedgerEntries](https://developers.stellar.org/docs/data/apis/rpc/api-reference/methods/getLedgerEntries)
and [getEvents](https://developers.stellar.org/docs/data/apis/rpc/api-reference/methods/getEvents)
references for the underlying data coverage.

### Saved investigations, notes, and labels

Open **Investigations**, enter a name, and choose **Create new**. Select that
investigation, inspect an account, asset, or contract, and choose **Save resource to selected
investigation**. Transaction inspectors have their own **Save to selected investigation**
button. An account save captures its balances and any already-loaded activity and graph
evidence; it does not fetch additional pages. Each resource retains its network.

Reopen an investigation from the local list and select an evidence entry to read its
saved JSON, including exact values, coverage, and original retrieval times, offline.
This snapshot viewer does not reconstruct the interactive graph layout. Multiple saves
retain separate evidence entries. Add notes and comma-separated labels, then choose
**Save name / notes / labels**; these are local interpretations, separate from network facts.

**Refresh selected resource from network** bypasses the response cache and appends
new evidence while keeping the original. An account refresh retrieves its current
account snapshot only; return to the explorer to fetch and save newer activity or graph
evidence. A failed refresh keeps saved evidence intact. Switching networks in the explorer
does not change the network stored with a saved resource.

CSV/JSON exports omit investigation names, notes, and labels by default. Select
**Include saved name, notes, and labels in export** to include committed annotations.
Unsaved edits are not exported. **Delete investigation** requires confirmation.

Investigations use a separate, unencrypted SQLite file in the operating system's
application-data folder; hover over the library notice to see its location. Clearing the
response cache does not delete investigations, and deleting an investigation does not
remove previously exported files. Schema version 1 refuses unknown future versions.
Each investigation is limited to 20 MB. Encrypted storage remains planned.

### Watchlists

Open **Watchlists** in the sidebar. Enter a name and choose **Create** to make a local
list. Add an **Account** G-address, **Asset** code and issuer, or **Contract** C-address
using **Add to list (offline)**. New entries use the network selected at the top of the
window. Identifiers are checksum-validated locally; asset codes remain case-sensitive.
For native XLM, enter `XLM` and leave the issuer empty. An issued asset named XLM with
an issuer remains a separate resource.

From a loaded Overview/activity/Graph, Assets, or Contracts page, choose **Add displayed
resource to selected watchlist** to bookmark its identity without another request.
Select a list in Watchlists first. This saves a bookmark, not the currently displayed
snapshot. Adding the same kind/identity/network twice returns to the existing entry
without replacing its label, notes, or saved snapshot. Different networks and different
asset issuers always remain distinct. Entries may belong to more than one named list.

| Action | Result |
| --- | --- |
| Select a list or entry | Read local annotations and the last saved snapshot offline |
| Save label / notes | Commit your local interpretation separately from network facts |
| Rename / Delete list | Organize lists; deleting a list requires confirmation |
| Remove entry | Delete that bookmark, notes, and latest snapshot after confirmation |
| Open in explorer / double-click entry | Switch to the entry's saved network and perform an explicit explorer lookup |
| Refresh saved snapshot | Fetch a fresh resource on its saved network, bypassing the optional response cache |
| Cancel refresh | Keep the previous saved snapshot and reject late results |

Explorer opening follows normal cache settings and does not change the watchlist's saved
snapshot. Watchlist refresh replaces **only the latest snapshot**, retaining labels and
notes. To preserve a sequence of historical evidence, use **Investigations**. Failed
refreshes retain old data, including its original source and retrieval time. Unsaved
annotation edits are protected when changing selections or closing the window, and a
refresh does not erase notes being typed. Saved snapshots are explicitly labeled as
past data; there is no automatic monitoring, polling, change alert, or batch refresh.

Account refresh retrieves account details/balances only. Issued-asset refresh retrieves
Horizon asset statistics. Native XLM uses a local definition without an API request.
Contract refresh retrieves the available RPC instance only; contract events are not
loaded or saved by watchlist refresh. Reopen Contracts to explore events explicitly.
Existing provider availability, retention, and accuracy limits still apply.

Watchlists use separate unencrypted `watchlists.sqlite3` storage in the operating
system's application-data folder; hover over the library notice for its location.
Limits are **100 lists**, **500 entries per list**, and **10 MB per list**; list names
allow 100 characters, labels 200, and notes 10,000. Unknown future database schemas are
refused. Clear cache and investigation deletion do not remove watchlists. Labels and
notes stay local; explicit lookups send only public identifiers to Horizon/Stellar RPC.
Automatic alerts and encrypted storage remain planned.

### Watchlist filters and portable JSON

In **Watchlists**, combine the **text search**, **network**, and **type** filters to
narrow the selected list. Text search is a case-insensitive substring match against
committed identifiers, asset codes/issuers, labels, and notes. It does not alter the
case-sensitive stored asset identity. The network filter is independent of the explorer's
network selector. The visible count shows how many entries match; hidden entries stay
saved. Filters never contact Horizon/RPC. If a filter would hide unsaved notes, you can
keep those edits and restore the previous filters. New lists, imports and new bookmark
additions clear filters so their entries are visible.

Choose **Export list JSON** for a portable schema-1 bookmark file:

- By default, export the **whole selected list**, even if filters are active.
- Check **Export visible entries only** for a filtered subset. The file records that
  scope, but omits the search text itself.
- List names, labels, and notes are **omitted by default**. Check **Include saved list
  name, labels and notes** to include committed annotations; unsaved edits stay local.
- Snapshots, network evidence, database IDs, creation history and private file paths
  are **never included**. This is bookmark portability, not an investigation backup.
- Sharing preferences reset when changing lists. Files retain every resource's exact
  network, kind, identifier, and asset code/issuer, including native versus issued XLM.

Choose **Import list JSON** to create a **separate new list**, with fresh local entry IDs
and no snapshots. Existing lists/notes/evidence are never merged or replaced, even if
names or resources overlap. Without exported annotations, the new list is named
**Imported watchlist**; rename it locally if desired. Imported annotations remain user
interpretations, not verified facts. Imports do not fetch or refresh network data.

Files must be UTF-8 JSON (an optional UTF-8 BOM is accepted), at most **10 MB** and
**500 entries**, with the supported portable format/schema. Invalid checksums, networks,
asset identities, duplicate identities/JSON fields, unknown fields/schemas, snapshots,
foreign database IDs, invalid annotation types/lengths and nonstandard JSON constants
are rejected before storage writes. A valid import commits as one transaction; quota
or write failures leave existing lists intact. Existing 100-list/10-MB storage limits
still apply. Exports validate their own format/byte size and replace the chosen file
atomically. File-dialog cancellation makes no library changes. JSON is the supported
portable format; CSV import, merging, encrypted files and snapshot transfer remain planned.

### CSV and JSON export

Choose **Export CSV** or **Export JSON** in Overview, Transactions, Operations,
Payments, Graph, Assets, Contracts, or an open transaction inspector. Choose a destination in the save
dialog. Exports use the data already loaded and make no additional network requests.
Use **Load more** first when you need older records.

| View | Export contents |
| --- | --- |
| Assets | Inspector statistics/flags, or loaded discovery rows with frozen filters and page provenance |
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

**Horizon** provides accounts, activity, transactions, and issued-asset statistics.
**Stellar RPC** provides contract instances and retained contract events. Hubble for
deeper historical analysis remains optional future work.

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

Portable native packaging is implemented. Further planned areas include:

- Broader asset metadata and contract-token discovery.
- Watchlist batch refresh/change comparison and expanded investigation organization.
- Advanced tracing controls and broader operation coverage.
- Advanced contract specifications, storage-key discovery, and richer event interpretation.
- Optional historical analytics through Hubble.
- Signed/notarized distributions, installers, and clean-machine release qualification.

These are **planned capabilities**, not features available in 0.0.14. The detailed release
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
