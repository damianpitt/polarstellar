# PolarStellar

**Navigate the Stellar network.**

Open-source desktop network intelligence for Stellar and Soroban. PolarStellar is being
built to help inspect accounts, understand transaction operations, discover counterparties,
and trace relationships in a local desktop workspace.

**Status: repository skeleton / pre-alpha.** The current application is a Qt window with
navigation, a search field, and a Mainnet/Testnet selector. Search is disabled until data
providers are implemented. There is no live network access, graph analysis, or export yet.

## Product direction

- Account balances, trustlines, signers, and activity.
- Human-readable transaction decoding with operations as first-class objects.
- Counterparty discovery and interactive relationship graphs.
- Soroban contract inspection and events.
- Local SQLite caching and investigation state; CSV and JSON export.

These are planned capabilities. See the [full project scope and specification](PolarStellar_Project_Spec.md)
for architecture, release boundaries, acceptance criteria, and the roadmap.

## Run the skeleton

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

The UI will call a shared service layer rather than remote APIs directly. RPC, Horizon,
and eventually Hubble will serve different data needs behind provider interfaces.

PolarStellar is an open-source project, not affiliated with the Stellar Development Foundation.

## License

[MIT](LICENSE) © 2026 Damiano Pittau.
