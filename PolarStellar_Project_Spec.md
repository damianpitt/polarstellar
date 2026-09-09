# PolarStellar

> **Open-source Stellar network intelligence.**\
> Navigate, inspect, trace, and understand the Stellar network from your desktop.

---

## Project Status

**Idea status:** Locked\
**Implementation status:** Pre-alpha 0.0.3 account explorer with paginated activity and transaction inspection; analysis, storage, and export are planned\
**Project name:** PolarStellar\
**Primary category:** Open-source desktop blockchain scanner / explorer / investigation toolkit\
**Primary network:** Stellar\
**Smart contracts:** Soroban\
**Primary implementation language:** Python\
**UI framework:** PySide6 / Qt\
**Initial platform focus:** Linux-first, cross-platform from day one

---

# 1. Name and Identity

## PolarStellar

The name **PolarStellar** is a wordplay combining:

- **Polestar** — the North Star, historically used for navigation and orientation.
- **Stellar** — the blockchain network the application is dedicated to.

The metaphor is deliberate:

> **PolarStellar helps users navigate the Stellar network.**

This gives the project a strong identity that is both technical and visual. It naturally supports themes of:

- navigation,
- orientation,
- discovery,
- tracing,
- mapping,
- network topology,
- stars,
- compass points,
- orbit,
- coordinates,
- graph exploration.

### Suggested positioning

**PolarStellar**\
*Open-source Stellar network intelligence.*

Alternative supporting line:

> Navigate, inspect and trace the Stellar network from your desktop.

---

# 2. Product Vision

PolarStellar should **not** be designed as a generic clone of an existing browser-based block explorer.

The stronger idea is a:

> **Desktop Stellar investigation and network intelligence tool.**

A user should be able to paste an:

- Stellar account address,
- transaction hash,
- asset,
- issuer,
- Soroban contract ID,

and progressively inspect what is happening around it.

The project should combine the convenience of a block explorer with the workflow of a network-analysis tool.

A useful mental model is:

> **Wireshark for Stellar**

rather than:

> another StellarExpert-style explorer.

The application should prioritize:

- investigation,
- progressive discovery,
- structured decoding,
- local analysis,
- counterparties,
- asset flows,
- relationship graphs,
- transaction interpretation,
- Soroban contract inspection,
- local export and annotation.

---

# 3. Core User Experience

A user should be able to:

1. Launch PolarStellar.
2. Paste a Stellar address, transaction hash, asset, or contract ID.
3. Get a clear overview immediately.
4. Inspect balances, trustlines, assets, operations, payments, and transactions.
5. Decode individual Stellar transactions into understandable operations.
6. Identify important counterparties.
7. Expand relationships into an interactive graph.
8. Trace flows through the network.
9. Inspect Soroban contracts and events.
10. Export useful data locally.
11. Optionally retain investigation data in a local SQLite database.

---

# 4. Why Stellar Is a Good Fit

Stellar exposes a relatively structured ledger model with first-class concepts such as:

- accounts,
- balances,
- assets,
- trustlines,
- payments,
- transactions,
- operations,
- offers,
- trades,
- ledgers,
- sponsorships,
- signers,
- thresholds,
- Soroban contracts,
- Soroban events.

Unlike Ethereum, where much application meaning is reconstructed from contract calls and logs, Stellar exposes many financial and account actions as explicit ledger operations.

That makes it particularly suitable for a polished desktop scanner.

---

# 5. Stellar Transaction Model

A key design decision is that **operations must be first-class UI objects**.

A Stellar transaction can contain multiple operations.

Example:

```text
Transaction
 ├─ Operation 1: payment
 ├─ Operation 2: change trust
 ├─ Operation 3: manage sell offer
 └─ Operation 4: set options
```

The UI should therefore never reduce a transaction to only:

- hash,
- sender,
- recipient,
- amount.

Instead, PolarStellar should explain the actual operation sequence.

Example transaction view:

```text
TX 2af9...13ce

SUCCESS
Ledger #59483720
Fee: 0.00004 XLM
Operations: 4

1  CHANGE TRUST
   USDC:G...

2  PAYMENT
   2,500 USDC
   GABC → GXYZ

3  MANAGE SELL OFFER
   500 USDC → XLM

4  SET OPTIONS
   Signer added
```

This should become one of the defining UX strengths of the application.

---

# 6. Stellar Data Sources

PolarStellar should use a layered data architecture rather than depending on one API.

The three major sources are:

1. **Stellar RPC**
2. **Horizon**
3. **Hubble**

A local **SQLite index/cache** should sit above them.

---

# 7. Stellar RPC

## Role

Stellar RPC should be considered the **modern primary foundation** for new development.

Use it for:

- current ledger state,
- recent transactions,
- ledger entries,
- Soroban contracts,
- contract state,
- contract events,
- real-time or near-real-time network data.

It is especially important for modern Stellar because Soroban support belongs here rather than in the older Horizon-centric model.

## Important limitation

RPC should not be treated as a full historical analytics database.

Its typical use is recent/current network access rather than indefinite historical indexing.

Therefore PolarStellar should progressively build its own local model where useful.

---

# 8. Horizon

## Role

Horizon remains extremely useful because it provides convenient, parsed REST resources.

Typical account-oriented endpoints include concepts equivalent to:

```text
/accounts/{address}
/accounts/{address}/transactions
/accounts/{address}/operations
/accounts/{address}/payments
/accounts/{address}/effects
/accounts/{address}/offers
/accounts/{address}/trades
```

Horizon is particularly useful for quickly implementing:

- account lookup,
- balances,
- transaction history,
- payments,
- operations,
- offers,
- trades,
- account activity.

It can also be useful for streaming account activity.

Example live-monitor concept:

```text
Watching account G...
─────────────────────

12:03:14  +2,400 USDC
12:05:02  -800 XLM
12:05:02  path payment
12:08:41  trustline created
12:11:03  contract invocation
```

## Architecture rule

The UI should **not** depend directly on Horizon.

Horizon should sit behind a provider/service abstraction so it can be replaced or complemented without changing the GUI.

---

# 9. Hubble

## Role

Hubble should power **deep historical analytics**.

It provides access to Stellar historical data through BigQuery and is suitable for questions such as:

- historical counterparties,
- transaction volume by month,
- asset history,
- trustline history,
- DEX interaction history,
- account creation lineage,
- total incoming versus outgoing flows,
- long-term activity patterns,
- historical account behavior.

Possible UI section:

```text
Address: GCKF....

[ Overview ]
[ Activity ]
[ Assets ]
[ Network ]
[ Historical Analysis ]
```

Historical Analysis could contain:

```text
Top counterparties
Transaction volume by month
Assets historically held
Trustline history
DEX interaction history
Total incoming/outgoing XLM
Activity heatmap
```

## Usage philosophy

Do not use Hubble for every screen refresh.

It is best used selectively for expensive or historical analysis.

---

# 10. Local Data Layer

PolarStellar should include **SQLite** as its local persistence layer.

Possible uses:

- cache fetched accounts,
- cache transactions,
- cache operations,
- store graph edges,
- retain watchlists,
- retain investigation history,
- store local labels,
- store notes,
- avoid repeat API calls,
- progressively build a user-local index.

Conceptually:

```text
Remote RPC / Horizon / Hubble
              │
              ▼
        ingestion layer
              │
              ▼
           SQLite
              │
      ┌───────┴────────┐
      │                │
   accounts            tx
   assets              operations
   contracts           edges
   labels              notes
```

This local-first element should become increasingly important as the project matures.

---

# 11. Data Provider Abstraction

The backend should avoid coupling the rest of the app to a specific API.

Conceptual interface:

```python
class StellarDataProvider:
    async def get_account(...):
        ...

    async def get_transaction(...):
        ...

    async def get_ledger(...):
        ...

    async def get_contract(...):
        ...

    async def get_events(...):
        ...
```

Internally, requests can be routed to:

```text
RPC
    → current ledger
    → recent transactions
    → contract state
    → contract events
    → ledger entries

Horizon
    → parsed account history
    → payments
    → operations
    → offers
    → trades

Hubble
    → large historical analytics

SQLite
    → local cache
    → retained investigations
    → labels
    → derived relationships
```

---

# 12. Recommended Technology Stack

## Core

```text
Python
PySide6 / Qt
SQLite
NetworkX
httpx
qasync
py-stellar-base
PyQtGraph
```

### Python

Primary application language.

Reasons:

- excellent fit for blockchain/data tooling,
- readable and inspectable source,
- strong Stellar SDK support,
- fast development,
- strong data-analysis ecosystem,
- simple local scripting and inspection.

### PySide6 / Qt

Primary desktop GUI framework.

Reasons:

- polished native desktop experience,
- mature widgets,
- strong table and model/view support,
- Linux/macOS/Windows support,
- significantly higher visual ceiling than minimal Python GUI toolkits,
- appropriate for data-heavy desktop applications.

### SQLite

Local application database.

### NetworkX

Graph construction and graph analysis.

Use it for:

- degrees,
- shortest paths,
- connected components,
- neighbor discovery,
- centrality,
- relationship analysis,
- topology computations.

Do **not** rely on NetworkX itself for polished rendering.

### QGraphicsScene / QGraphicsView

Recommended for rendering interactive network graphs inside Qt.

### PyQtGraph

Useful for conventional charts such as:

- account activity over time,
- asset balance history,
- transaction volume,
- inflow/outflow,
- activity frequency.

### httpx

Async HTTP requests.

### qasync

Asyncio integration with Qt.

### py-stellar-base

Main Stellar Python SDK layer.

---

# 13. Qt Widgets vs QML

## Initial decision: Qt Widgets

Start with **Qt Widgets**, not QML.

Recommended building blocks:

```text
QWidget
QMainWindow
QTableView
QTreeView
QGraphicsView
QGraphicsScene
QSplitter
QDockWidget
QTabWidget
QLineEdit
QStackedWidget
```

Reasons:

- everything stays primarily in Python,
- straightforward maintenance,
- excellent for data-heavy applications,
- mature model/view framework,
- less architectural complexity.

QML can be introduced later if a specific UI area benefits from:

- animation,
- custom visual transitions,
- highly bespoke presentation.

It should not be required for V0.1.

---

# 14. Development Platform

## Development machine

Primary development can happen on **macOS**.

Recommended environment:

```text
macOS
   ↓
VS Code / Cursor
   ↓
Python 3.x
   ↓
uv
   ↓
PySide6
   ↓
GitHub
```

The application itself should remain platform-neutral.

---

# 15. Platform Strategy

## Linux-first, cross-platform

PolarStellar should have a strong open-source/Linux identity while remaining available on:

- Linux,
- macOS,
- Windows.

Suggested initial Linux targets:

- Ubuntu 24.04,
- Debian 12.

Linux makes sense as the identity platform because:

- open-source users are a natural audience,
- blockchain/network tooling has a strong Linux user base,
- it differentiates PolarStellar from proprietary desktop crypto applications,
- it fits the project's technical identity.

However, cross-platform support should be part of the architecture from day one.

---

# 16. Dependency Management

Use **uv**.

Local development setup should be as simple as:

```bash
git clone https://github.com/damianpitt/polarstellar
cd polarstellar

uv sync
uv run polarstellar
```

Goals:

- fast installation,
- reproducible environments,
- clean dependency management,
- low friction for local setup and independent forks.

---

# 17. Packaging

Suggested outputs:

```text
PolarStellar

Linux
├── AppImage
├── Flatpak
└── .deb later

macOS
├── .app
└── DMG

Windows
└── MSI / installer
```

Possible packaging tools:

- Briefcase
- PyInstaller

Important consideration:

Cross-platform builds generally need to be produced on the corresponding operating system rather than relying on a single local machine to create every target.

This is a strong use case for **GitHub Actions**.

---

# 18. GitHub Strategy

The repository should be public from the beginning.

Possible repository naming:

```text
polarstellar
```

or under an existing organization:

```text
<organization>/polarstellar
```

The public repo should emphasize:

- clean architecture,
- source accessibility,
- screenshots,
- strong README presentation,
- clear roadmap,
- open-source identity,
- easy install instructions.

---

# 19. Suggested Repository Structure

```text
polarstellar/
│
├── pyproject.toml
├── README.md
├── LICENSE
│
├── src/
│   └── polarstellar/
│       │
│       ├── app/
│       │   ├── main.py
│       │   └── window.py
│       │
│       ├── stellar/
│       │   ├── rpc.py
│       │   ├── horizon.py
│       │   ├── hubble.py
│       │   ├── parser.py
│       │   ├── providers.py
│       │   └── models.py
│       │
│       ├── analysis/
│       │   ├── flows.py
│       │   ├── counterparties.py
│       │   ├── assets.py
│       │   ├── graph.py
│       │   └── heuristics.py
│       │
│       ├── storage/
│       │   ├── database.py
│       │   ├── repositories.py
│       │   └── models.py
│       │
│       ├── ui/
│       │   ├── account_view.py
│       │   ├── tx_view.py
│       │   ├── asset_view.py
│       │   ├── contract_view.py
│       │   ├── graph_view.py
│       │   └── components/
│       │
│       └── resources/
│           ├── icons/
│           └── styles/
│
└── tests/
    ├── test_rpc.py
    ├── test_horizon.py
    ├── test_parser.py
    ├── test_graph.py
    └── test_storage.py
```

This can evolve later, but the separation between:

- data acquisition,
- parsing,
- analysis,
- persistence,
- UI

should be maintained.

---

# 20. Main Application Navigation

Conceptual layout:

```text
┌────────────────────────────────────────────────────────────┐
│  ✦ POLARSTELLAR                    Mainnet ●    Settings   │
├───────────────┬────────────────────────────────────────────┤
│               │                                            │
│  SEARCH       │   ACCOUNT                                  │
│               │                                            │
│  Overview     │   GD7K...P1R                               │
│  Activity     │   ─────────────────────────────            │
│  Assets       │                                            │
│  Operations   │   4,281 XLM       12,522 USDC             │
│  Graph        │                                            │
│  Contracts    │   Account age       4.2 years              │
│               │   Transactions      8,492                  │
│               │   Counterparties     231                   │
│               │                                            │
│               │   RECENT ACTIVITY                          │
│               │                                            │
│               │   ↓  2,400 USDC        GABC...             │
│               │   ↑    580 XLM         GXYZ...             │
│               │                                            │
└───────────────┴────────────────────────────────────────────┘
```

Likely major sections:

- Search
- Overview
- Activity
- Assets
- Operations
- Payments
- Transactions
- Network / Graph
- Contracts
- Historical Analysis
- Watchlist
- Local Notes
- Export

---

# 21. Account View

An account page should include:

## Identity

- account address,
- sequence number,
- account age when derivable,
- home domain where available,
- flags,
- thresholds,
- signers,
- sponsorship information.

## Balances

Example:

```text
ASSETS

XLM                 2,483.21

USDC                12,419.00
Issuer              GA5...
Trust limit         100,000
Authorized          ✓

AQUA                 8,330
Issuer              GB...
```

## Activity

- recent transactions,
- recent payments,
- recent operations,
- recent trades,
- offers,
- contract interactions.

## Network summary

- unique counterparties,
- strongest counterparties,
- inflow/outflow,
- first activity,
- latest activity,
- total interaction count.

---

# 22. Asset and Trustline Analysis

Stellar assets should be treated as first-class objects.

The scanner should understand:

- XLM,
- issued assets,
- asset code,
- issuer,
- trustline,
- trust limit,
- authorization status,
- account exposure,
- transfers,
- trading activity.

Possible future asset screen:

```text
USDC

Issuer: G...
Type: issued Stellar asset

Account balance
Trustline status
Historical transfers
Top counterparties
DEX interactions
Related offers
```

---

# 23. Soroban Support

Modern PolarStellar should support Soroban from early in its roadmap.

The scanner should recognize that:

```text
G... = Stellar account
C... = Soroban contract
```

A contract view could contain:

```text
Contract
────────────────────────

ID
CABC....

Wasm hash
...

Functions
transfer(...)
balance(...)
mint(...)

Recent events
...

Storage
...

Interacting accounts
...
```

Potential data areas:

- contract metadata,
- WASM reference/hash,
- events,
- storage,
- recent invocations,
- interacting accounts,
- decoded function calls when possible.

---

# 24. The Killer Feature: Trace

The strongest differentiator should be **interactive network tracing**.

A user pastes an address and PolarStellar derives relationships around it.

Example:

```text
            Exchange A
                │
          14,000 USDC
                ▼
            GDX...
          /     │      \
         /      │       \
    GABC       GDEF     GXYZ
   36% flow    22%      13%
       │
       ▼
   Contract C...
```

Possible derived metrics:

- top counterparties,
- first-hop recipients,
- first-hop senders,
- second-hop relationships,
- incoming/outgoing ratio,
- unique counterparties,
- asset flows,
- DEX interactions,
- contract interactions,
- account age,
- transaction frequency.

This is the feature that turns PolarStellar from a block explorer into a network-intelligence tool.

---

# 25. Interactive Graph

Recommended graph behavior:

```text
double click node      → investigate account
right click            → copy address
shift click            → add to investigation
mouse wheel            → zoom
drag node               → reposition
hover                   → account summary
```

Additional possible interactions:

- collapse branch,
- expand one hop,
- expand all counterparties,
- filter by asset,
- filter by date,
- filter incoming/outgoing,
- highlight path,
- highlight contracts,
- highlight exchanges or locally labeled accounts,
- hide low-volume edges,
- save graph locally.

Use:

- **NetworkX** for graph logic,
- **QGraphicsScene/QGraphicsView** for visual rendering.

---

# 26. Visual Direction

PolarStellar should look polished and modern, but not like a generic neon crypto application.

## Suggested visual style

- dark charcoal background,
- white/light text,
- restrained Stellar-inspired blue/purple accents,
- strong typography,
- clean spacing,
- high-density information without clutter,
- subtle borders,
- minimal gradients,
- restrained animation.

The network graph can be the visually dramatic part.

Avoid:

- excessive neon,
- generic cyberpunk clichés,
- gratuitous glassmorphism,
- overly colorful token dashboards,
- visual noise.

The aesthetic should communicate:

> technical, investigative, precise, open-source, professional.

---

# 27. Icon Direction

PolarStellar should have a distinct technical/navigation visual identity.

Unlike animal-based application branding, this project should revolve around:

- North Star,
- compass,
- orbital path,
- network,
- navigation,
- coordinates,
- nodes,
- directional geometry.

Possible conceptual mark:

```text
        ◆
       ╱│╲
     ╱  │  ╲
   ─────●─────
     ╲  │  ╱
       ╲│╱
        ◆
```

The final icon should be much more simplified than the ASCII sketch.

Strong direction:

> a geometric North Star / compass mark whose center or negative space subtly references Stellar's orbital/network identity.

Requirements:

- no text,
- recognizable at small sizes,
- works in monochrome,
- works on Linux,
- works as a macOS app icon,
- works as a Windows icon,
- scalable to SVG,
- clean enough for GitHub and documentation,
- visually separate from existing Stellar branding while clearly belonging to the ecosystem.

---

# 28. README Direction

Example opening:

```text
                  ✦
             POLARSTELLAR

       Navigate the Stellar network.

Open-source desktop network scanner and
investigation toolkit for Stellar and Soroban.

Linux • macOS • Windows
```

Suggested README feature section:

```text
Features
• Account inspection
• Asset and trustline analysis
• Transaction decoding
• Stellar operations viewer
• Soroban contract exploration
• Counterparty discovery
• Interactive network graph
• Flow tracing
• Local investigation database
• CSV / JSON export
```

Screenshots should be prominent.

The README should make the project feel like a serious open-source tool, not a classroom application.

---

# 29. CLI Possibility

Even though PolarStellar is primarily a desktop GUI, the backend architecture should make a CLI possible later.

Example commands:

```bash
polarstellar scan G...
polarstellar tx <hash>
polarstellar trace G...
polarstellar watch G...
polarstellar asset USDC:<issuer>
polarstellar contract C...
```

This could be valuable for:

- power users,
- scripting,
- server-side use,
- testing,
- automation,
- OSS adoption.

The GUI and CLI should ideally share the same core service layer.

---

# 30. V0.1 Roadmap

The first release should remain intentionally small and usable.

## V0.1

1. Search account address.
2. Search transaction hash.
3. Search contract ID where feasible.
4. Account overview.
5. XLM and asset balances.
6. Trustlines.
7. Transaction history.
8. Operations viewer.
9. Payments viewer.
10. Transaction detail decoder.
11. Basic counterparty extraction.
12. Simple graph visualization.
13. JSON export.
14. CSV export.
15. Mainnet/Testnet switch.
16. Basic local SQLite cache.
17. Polished Linux/macOS/Windows-compatible Qt UI.

A strong minimal flow is:

```text
paste G-address
      ↓
fetch account
      ↓
show balances
      ↓
fetch recent operations
      ↓
extract counterparties
      ↓
draw transaction graph
```

That alone is enough for a legitimate first public release.

---

# 31. V0.2 Roadmap

Potential V0.2 features:

```text
Soroban contract explorer
live address monitoring
expanded local SQLite indexing
asset pages
DEX trades
graph expansion
address labels
watchlists
local notes
better filtering
saved investigations
```

---

# 32. V0.3 Roadmap

Potential V0.3 features:

```text
Hubble historical analysis
account clustering
deeper flow tracing
historical relationship graphs
local annotations
risk heuristics
advanced watchlists
account behavior summaries
historical asset exposure
activity heatmaps
```

---

# 33. Potential Future Investigation Features

Longer-term ideas:

- address tagging,
- local/private labels,
- exchange label packs,
- entity clustering,
- path tracing,
- transaction flow reconstruction,
- asset-specific flow maps,
- temporal graph filters,
- saved investigations,
- investigation tabs,
- watchlists,
- live alerts,
- suspicious-behavior heuristics,
- account comparison,
- graph snapshots,
- CSV/JSON evidence export,
- local report generation.

Any risk or heuristic feature should be clearly labeled as heuristic rather than presented as authoritative attribution.

---

# 34. Open-Source Philosophy

PolarStellar should be:

- transparent,
- locally useful,
- easy to inspect and fork,
- privacy-respecting,
- dependency-light where practical,
- easy to install,
- easy to inspect,
- easy to fork.

Important project principle:

> The application should provide powerful blockchain visibility without requiring users to surrender their investigation history to a proprietary SaaS dashboard.

Local storage and local annotations support that identity strongly.

---

# 35. Project Personality

PolarStellar should feel like:

- an engineer's tool,
- an analyst's tool,
- a blockchain research workstation,
- a polished OSS desktop product.

It should **not** feel like:

- a retail trading app,
- a token price dashboard,
- a generic wallet,
- an Etherscan clone,
- a noisy crypto analytics website wrapped in a desktop shell.

---

# 36. Technical Principles

1. **API abstraction first**\
   Do not tightly couple UI code to RPC, Horizon, or Hubble.

2. **Operations are first-class**\
   Stellar operations should be easy to inspect and understand.

3. **Local-first where useful**\
   SQLite should progressively make the tool faster and more independent.

4. **Graph analysis is core**\
   Counterparties and network relationships are central to the product.

5. **Soroban is part of modern Stellar**\
   Contract support should be part of the architecture, not an afterthought.

6. **Cross-platform architecture**\
   Linux-first identity, but no platform-specific assumptions in core logic.

7. **Maintenance accessibility**\
   Python, Qt Widgets, uv, clear separation of layers, good documentation.

8. **Polished UI matters**\
   Open source does not mean visually unfinished.

9. **Readable decoding over raw JSON**\
   Raw protocol data should be transformed into useful explanations wherever possible.

10. **Power without unnecessary centralization**\
    Prefer local analysis and export where reasonable.

---

# 37. Initial Build Sequence

A practical implementation sequence:

## Stage 1 — Skeleton

- create public GitHub repository,
- initialize Python project with uv,
- add PySide6,
- create main window,
- add navigation,
- add search bar,
- add mainnet/testnet selector.

## Stage 2 — Stellar connectivity

- add py-stellar-base,
- implement Horizon client,
- implement RPC client,
- create provider abstraction,
- validate G-addresses,
- validate transaction hashes,
- validate C-addresses.

## Stage 3 — Account explorer

- account details,
- balances,
- trustlines,
- signers,
- thresholds,
- recent transactions,
- recent operations,
- recent payments.

## Stage 4 — Transaction decoder

- transaction overview,
- operation list,
- human-readable operation cards,
- success/failure state,
- fee,
- ledger,
- involved accounts,
- involved assets.

## Stage 5 — Local database

- SQLite schema,
- cache accounts,
- cache transactions,
- cache operations,
- save local labels,
- retain recently investigated addresses.

## Stage 6 — Counterparty engine

- extract incoming relationships,
- extract outgoing relationships,
- aggregate volume,
- aggregate frequency,
- calculate top counterparties.

## Stage 7 — Graph view

- generate NetworkX graph,
- render through QGraphicsScene,
- clickable nodes,
- hover summaries,
- one-hop expansion,
- zoom,
- drag,
- filters.

## Stage 8 — Packaging and release

- GitHub Actions,
- Linux build,
- macOS build,
- Windows build,
- screenshots,
- README,
- release notes,
- V0.1.

---

# 38. Suggested First Milestone

The first milestone should be brutally simple:

> **Paste a Stellar G-address and receive a useful visual investigation page.**

Minimum success criteria:

```text
Input
  ↓
Account fetch
  ↓
Balances
  ↓
Trustlines
  ↓
Recent transactions
  ↓
Recent operations
  ↓
Counterparties
  ↓
Simple relationship graph
```

If this works well and looks polished, PolarStellar is already a meaningful open-source project.

---

# 39. Final Locked Concept

## PolarStellar

**PolarStellar is a Linux-first, cross-platform, open-source desktop network scanner and investigation toolkit for Stellar and Soroban.**

It will be built primarily with:

```text
Python
PySide6 / Qt Widgets
py-stellar-base
httpx
qasync
SQLite
NetworkX
QGraphicsScene / QGraphicsView
PyQtGraph
uv
GitHub Actions
```

It will combine:

- Stellar RPC for modern/current network and Soroban access,
- Horizon for convenient parsed account and operation data,
- Hubble for deep historical analytics,
- SQLite for local caching and investigation state.

Its differentiator is not merely showing blockchain records.

Its differentiator is:

> **helping users understand relationships, flows, counterparties, operations, assets, and contracts across the Stellar network from a polished local desktop interface.**

The central metaphor is navigation.

The central feature direction is tracing.

The visual identity is the North Star.

The project name is:

# **PolarStellar**

> **Navigate the Stellar network.**


---

# 40. Repository Baseline and Delivery Boundaries

The repository now contains a working asynchronous account explorer, packaging metadata,
a Python entry point, provider-independent account models, checksum validation, a Horizon
adapter, and automated checks. Search accepts G-addresses and displays sequence number,
home domain, exact balances, issuer or pool identity, trust limits, and authorization.
Mainnet and Testnet use separate endpoints; each snapshot includes its network and source.

Lookups have HTTP timeouts, cancellation, loading and error states. Switching network,
starting another search, or cancelling invalidates old results. The UI clears the previous
snapshot before a new lookup. Failed requests are not automatically retried; users can
retry explicitly. Transactions, operations, and payments are available through independent 20-record,
newest-first pages with Load more, retry, and stale-result rejection. Each page records
its network, source, and retrieval time; coverage is limited to available Horizon history.
Transaction details can be opened from any activity row or by hash search. Details include
status, ledger, fee payer, exact fees, memo, and operations in execution order. Common
Horizon operation fields have readable explanations, with raw JSON retained. Failed
instructions are marked not applied; missing operations remain visibly partial. Closing,
reloading, or changing investigation context invalidates outstanding detail requests.
General XDR decoding, local cache, graphs, contract exploration, and export remain planned.
Assets, Graph, and Contracts navigation remain disabled. See CHANGELOG.md for versioned
feature history. Update its Unreleased section with each implemented feature or fix and
keep package metadata and the lockfile aligned whenever the version changes.

Python 3.12 is the development baseline. Runtime dependencies are PySide6, qasync, httpx,
and the Stellar Python SDK (`stellar-sdk`). SQLite and NetworkX integration remain planned.
The uv lockfile is committed for reproducibility. `__init__.py` files are intentional Python
package source; `__pycache__` and `.pyc` files are generated and excluded by `.gitignore`.

# 41. Scope and Non-Goals

The application is a read-only investigation workstation. V0.1 will not create wallets,
request seed phrases, hold private keys, sign or submit transactions, place trades, or
provide investment recommendations. It will not promise complete historical coverage,
identify real-world owners from addresses alone, or equate a graph connection with
ownership or misconduct.

V0.1 focuses on account investigation, transaction interpretation, recent counterparties,
a basic graph, local caching, and export. Contract recognition and a limited inspection
path are desirable where feasible; the fuller Soroban explorer is a V0.2 deliverable.
Hubble, long-term indexing, clustering, and risk heuristics remain later work.

# 42. Data Integrity and Service Contracts

Before implementing each provider, verify its current official API documentation,
endpoint availability, authentication, retention limits, and rate limits. The preceding
provider descriptions express intended responsibilities, not guaranteed service capabilities.
Hubble access may require the user's own cloud configuration and incur query charges;
it must be optional, explicit, and separate from routine application refreshes.

- Include network identity in every cache key, relationship, and export.
- Preserve asset identity by asset code and issuer; keep native XLM distinct.
- Use exact decimal or integer representations for monetary values, never binary floats.
- Preserve transaction and operation identifiers, ordering, and operation source overrides.
- Record source, retrieval time, ledger or cursor where available, and coverage boundaries.
- Show pagination limits, partial results, stale cache entries, and unavailable history clearly.
- Separate payment flows from other interactions; do not infer transfers from every operation.
- Do not combine volumes across assets without an explicit conversion methodology.
- Preserve raw source data alongside readable decoding where useful; expose unsupported
  operation types honestly and exclude failed operations from successful flow totals.

Provider adapters normalize remote responses into shared domain objects. Services coordinate
fetching, decoding, persistence, and analysis. UI code displays service results. Remote work
must be cancellable and must not block the Qt event loop. Switching networks or searches
must invalidate stale in-flight results. HTTP timeouts, bounded retries, pagination, and
clear errors belong in the acquisition layer.

Contract storage and function discovery depend on available ledger keys, metadata, and
contract specifications; do not promise arbitrary complete storage enumeration. Validate
account and contract identifiers with the SDK's checksum-aware decoding, not prefixes alone.

# 43. Local Data and Privacy

Place application data in platform-appropriate user directories using Qt standard paths.
Keep credentials out of source control and exports. Public API providers can observe
requested addresses even though investigations and annotations are stored locally.
SQLite storage is local, not automatically encrypted. Define schema migrations, cache
expiry, and explicit deletion controls before retaining investigations persistently.

Export JSON and CSV with network, source, coverage, time range, and stable identifiers.
Handle spreadsheet formula injection in CSV text fields. Local labels and notes must be
visibly distinguished from externally sourced facts. Exporting annotations must be explicit.

# 44. First Milestone Acceptance Criteria

The first functional milestone is complete when:

1. A valid G-address can be investigated on the selected network; malformed inputs produce
   a clear validation message before a request is sent.
2. Account balances and trustlines preserve issuer identity and exact amounts.
3. Recent transactions and ordered operations show their actual fetched coverage.
4. Transaction details explain supported operations and display unsupported ones honestly.
5. Counterparties are derived from supported operations with documented semantics.
6. A one-hop relationship graph links edges to the operations that justify them.
7. Network failures, nonexistent accounts, empty activity, and partial responses have
   usable states without freezing the UI or fabricating zero values.
8. Mainnet and Testnet results never mix, including after switching during a request.
9. Relevant parsing and service tests pass using deterministic fixtures without public API
   dependence; a separate manual live check confirms the chosen endpoints.

The remaining V0.1 work adds the promised local cache, CSV/JSON exports, broader views,
and release packaging. Passing this milestone alone does not imply the whole V0.1 is shipped.

# 45. Open Source and Release Practice

PolarStellar is an open-source project, not affiliated with the Stellar Development Foundation.

Documentation must distinguish implemented, experimental, and planned capabilities.
Each release should describe the data coverage and known limitations of supported features.
CI checks the skeleton on Linux, macOS, and Windows; successful CI is not a substitute for
interactive usability checks or native packaging validation. Build native release artifacts
on their corresponding systems and test them before describing a platform as supported.

The public specification is the canonical scope document. Update it when scope changes;
keep personal investigation data and private working notes outside version control.
