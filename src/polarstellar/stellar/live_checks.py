"""Explicit, bounded read-only release checks against the application's public providers.

These checks are opt-in and never run on normal startup. Public sample identifiers
come from one recent Horizon transaction page and one recent RPC event page. The
report records coverage rather than treating empty or unavailable history as proof
that a resource or event does not exist. No application databases are opened.
"""

import asyncio
import json
import platform
import sys
from datetime import UTC, datetime

import httpx
from stellar_sdk import StrKey

from polarstellar import __version__
from polarstellar.stellar.activity import ActivityKind
from polarstellar.stellar.contracts import ENDPOINTS as RPC_ENDPOINTS
from polarstellar.stellar.contracts import PASSPHRASES, ContractProvider, integer
from polarstellar.stellar.horizon import ENDPOINTS as HORIZON_ENDPOINTS
from polarstellar.stellar.horizon import HorizonProvider
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService

MAX_REQUESTS = 64  # Per network, including discovery and every production-provider read.
NETWORK_TIMEOUT = 150
READ_METHODS = {"getNetwork", "getHealth", "getLedgerEntries", "getEvents"}


def stamp():
    """Return UTC retrieval context without depending on the host's local timezone."""
    return datetime.now(UTC).isoformat()


class ReadOnlyTransport(httpx.AsyncBaseTransport):
    """Enforce fixed providers, a request budget and read-only verbs before opening a connection."""

    def __init__(self, network, transport=None):
        """Allow deterministic HTTP fixtures in tests; normal runs use certificate-checked HTTPS."""
        self.network = network
        self.inner = transport or httpx.AsyncHTTPTransport()
        self.requests = []

    async def handle_async_request(self, request):
        """Refuse writes/redirect destinations and record only public query context for the report."""
        horizon = httpx.URL(HORIZON_ENDPOINTS[self.network])
        rpc = httpx.URL(RPC_ENDPOINTS[self.network])
        origin = (request.url.scheme, request.url.host, request.url.port)
        method = request.method
        if origin == (horizon.scheme, horizon.host, horizon.port) and method == "GET":
            action = "GET " + request.url.path
        elif origin == (rpc.scheme, rpc.host, rpc.port) and request.url.path == rpc.path:
            body = json.loads(request.content)
            if method != "POST" or body.get("method") not in READ_METHODS:
                raise ValueError("Release checks refuse transaction-writing RPC methods.")
            action = body["method"]
        else:
            raise ValueError("Release checks use only the selected fixed HTTPS providers.")
        if len(self.requests) >= MAX_REQUESTS:
            raise ValueError("Read-only qualification request budget exhausted.")
        record = {"action": action, "source": str(request.url), "requested_at": stamp()}
        self.requests.append(record)
        response = await self.inner.handle_async_request(request)
        record["http_status"] = response.status_code
        return response

    async def aclose(self):
        """Keep the shared transport alive across short-lived production-provider clients.

        The qualification run owns it and closes it once in finish(), so a provider
        closing its own client cannot invalidate the next independent check.
        """

    async def finish(self):
        """Release sockets exactly once after all checks for this network have finished."""
        await self.inner.aclose()


async def attempt(report, name, callback):
    """Record a failed step and continue independent checks; never turn a failure into a pass."""
    item = {"name": name, "started_at": stamp()}
    report["checks"].append(item)
    try:
        result = await callback()
        item.update(status="passed", evidence=result)
        return result
    except Exception as exc:  # noqa: BLE001 -- Keep a failed gate/report even after an unexpected provider shape or parser bug.
        item.update(status="failed", error=str(exc)[:1000], error_type=type(exc).__name__)
        return None
    finally:
        # A network deadline cancels the current step. Keep the partial report
        # explicit so timeout handling never mistakes an unfinished check for a pass.
        if "status" not in item:
            item.update(status="failed", error="Interrupted before this check completed.")
        item["finished_at"] = stamp()


def public_json(response):
    """Apply the provider-size ceiling to discovery replies before accepting their JSON shapes."""
    response.raise_for_status()
    if len(response.content) > 5_000_000:
        raise ValueError("Qualification discovery reply exceeds the 5 MB size limit.")
    return response.json()


async def horizon_checks(network, transport, report):
    """Inspect current classic-network samples using the same services as the desktop UI."""
    provider = HorizonProvider(transport)
    service = AccountService(provider)
    async with httpx.AsyncClient(transport=transport, timeout=20) as client:
        response = await client.get(HORIZON_ENDPOINTS[network])
        root = public_json(response)
        if root["network_passphrase"] != PASSPHRASES[network]:
            raise ValueError("Horizon serves a different network; no samples accepted.")
        report["horizon"] = {
            key: root.get(key)
            for key in (
                "horizon_version",
                "core_version",
                "history_latest_ledger",
                "history_elder_ledger",
                "network_passphrase",
            )
        }
        response = await client.get(
            HORIZON_ENDPOINTS[network] + "/transactions",
            params={
                "order": "desc",
                "limit": 20,
                "include_failed": "true",
            },
        )
        rows = public_json(response)["_embedded"]["records"]
    if not isinstance(rows, list) or len(rows) > 20:
        raise ValueError("Invalid recent transaction sample page.")
    # A recent source may have merged away. Try at most five public sources,
    # recording discovery errors instead of silently claiming a complete account check.
    samples = [
        row
        for row in rows
        if row.get("successful") is True
        and 0 < row.get("operation_count", 0) <= 20
        and StrKey.is_valid_ed25519_public_key(row.get("source_account", ""))
    ][:5]
    account = None
    report["unavailable_account_samples"] = []
    for sample in samples:
        address = sample["source_account"]
        try:
            account = await service.lookup(address, network)
            break
        except AccountError as exc:
            report["unavailable_account_samples"].append({"account": address, "error": str(exc)})
    if account is None:
        raise ValueError("No available account among five or fewer recent source samples.")
    report["sample_account"] = address
    for kind in ActivityKind:
        first = await service.activity(address, network, kind)
        details = {"records": len(first.records), "next_cursor": first.next_cursor}
        if first.next_cursor is not None:
            second = await service.activity(address, network, kind, first.next_cursor)
            if {row.identifier for row in first.records} & {
                row.identifier for row in second.records
            }:
                raise ValueError("Activity pagination repeated evidence identities.")
            details.update(second_page_records=len(second.records), next_cursor=second.next_cursor)
        report["activity"][kind.value] = details
    transaction = await service.transaction(sample["hash"], network)
    report["sample_transaction"] = {
        "hash": transaction.hash,
        "ledger": transaction.ledger,
        "successful": transaction.successful,
        "operations": len(transaction.operations),
        "warning": transaction.warning,
        "result_warning": transaction.result_warning,
    }
    if transaction.warning or transaction.result_warning:
        raise ValueError("Live transaction evidence is partial; inspect the retained warnings.")
    if transaction.operations:
        effects = await service.effects(transaction, transaction.operations[0].identifier)
        report["effects"] = {
            "operation": effects.operation,
            "records": len(effects.records),
            "next_cursor": effects.next_cursor,
        }
    failed = next(
        (
            row
            for row in rows
            if row.get("successful") is False and 0 < row.get("operation_count", 0) <= 20
        ),
        None,
    )
    if failed:
        observed = await service.transaction(failed["hash"], network)
        if observed.successful:
            raise ValueError("Failed transaction sample was rendered as successful.")
        report["failed_transaction"] = {"hash": observed.hash, "ledger": observed.ledger}
    else:
        report["failed_transaction"] = {"status": "not sampled", "scope": "one recent page only"}
    catalog = await service.discover_assets("", "", network)
    report["asset_catalog"] = {"records": len(catalog.items), "next_cursor": catalog.next_cursor}
    if catalog.items:
        asset = catalog.items[0]
        checked = await service.asset(asset.code, asset.issuer, network)
        report["sample_asset"] = {"code": checked.code, "issuer": checked.issuer}
    return {
        "account": account.address,
        "balances": len(account.balances),
        "source": account.source,
        "fetched_at": account.fetched_at.isoformat(),
    }


async def rpc_checks(network, transport, report):
    """Verify network/retention, then inspect one event-selected contract and a native SAC identity."""
    provider = ContractProvider(transport)
    async with httpx.AsyncClient(transport=transport, timeout=20) as client:
        await provider.verify(client, network)
        health = await provider.request(client, network, "getHealth")
        latest, oldest = integer(health["latestLedger"]), integer(health["oldestLedger"])
        if health.get("status") != "healthy" or not 0 < oldest <= latest:
            raise ValueError("RPC did not report a healthy retained ledger range.")
        report["rpc_health"] = health
        # Discovery is one explicitly bounded event page, never a storage crawl.
        start = max(oldest, latest - 49)
        found = await provider.request(
            client,
            network,
            "getEvents",
            {
                "startLedger": start,
                "endLedger": latest + 1,
                "filters": [{"type": "contract"}],
                "pagination": {"limit": 20},
            },
        )
    events = found["events"]
    if not isinstance(events, list) or len(events) > 20:
        raise ValueError("Invalid recent event discovery page.")
    native = await AccountService(HorizonProvider(transport)).asset("XLM", "", network)
    identity = await provider.asset_identity(native)
    report["native_sac"] = {"contract": identity["contract"], "status": identity["status"]}
    contract = next(
        (
            event["contractId"]
            for event in events
            if StrKey.is_valid_contract(event.get("contractId", ""))
        ),
        identity["contract"],
    )
    snapshot = await provider.inspect(contract, network)
    report["sample_contract"] = {
        key: snapshot.get(key)
        for key in (
            "contract",
            "instance_available",
            "instance_latest_ledger",
            "last_modified_ledger",
            "live_until_ledger",
            "wasm_hash",
            "warning",
        )
    }
    page = await provider.events(contract, network, start, latest)
    report["contract_events"] = {
        "start_ledger": start,
        "end_ledger_inclusive": latest,
        "records": len(page["events"]),
        "done": page["done"],
        "next_cursor": page["next_cursor"],
    }
    if not page["done"]:
        second = await provider.events(contract, network, start, latest, page["next_cursor"])
        report["contract_events"].update(
            second_page_records=len(second["events"]), second_page_done=second["done"]
        )
    if snapshot.get("wasm_hash"):
        specification = await provider.specification(snapshot, network)
        report["contract_specification"] = {
            "status": specification["status"],
            "entries": len(specification.get("entries", [])),
            "wasm_hash": snapshot["wasm_hash"],
        }
    else:
        report["contract_specification"] = {
            "status": "not sampled",
            "scope": "sample has no WASM executable",
        }
    report["storage_key"] = {"status": "not queried", "scope": "no explicit user-supplied key"}
    return {
        "source": RPC_ENDPOINTS[network],
        "oldest_ledger": oldest,
        "latest_ledger": latest,
        "sample_contract": contract,
        "fetched_at": stamp(),
    }


async def qualify_network(network, transport=None):
    """Keep one network's budget, failures and coverage independent from the other network."""
    guarded = ReadOnlyTransport(network, transport)
    report = {
        "network": network.value,
        "started_at": stamp(),
        "checks": [],
        "activity": {},
        "horizon_source": HORIZON_ENDPOINTS[network],
        "rpc_source": RPC_ENDPOINTS[network],
    }
    try:
        async with asyncio.timeout(NETWORK_TIMEOUT):
            await attempt(
                report, "horizon_workflows", lambda: horizon_checks(network, guarded, report)
            )
            await attempt(report, "rpc_workflows", lambda: rpc_checks(network, guarded, report))
    except TimeoutError:
        report["checks"].append(
            {
                "name": "network_deadline",
                "status": "failed",
                "error": "Network qualification deadline exceeded",
            }
        )
    finally:
        await guarded.finish()
    report.update(
        requests=guarded.requests,
        finished_at=stamp(),
        status="failed"
        if any(check["status"] == "failed" for check in report["checks"])
        else "passed",
    )
    return report


async def qualify(transport_factory=None):
    """Run both networks and return JSON-ready evidence without signing, submitting or saving private data."""
    reports = await asyncio.gather(
        *(
            qualify_network(network, transport_factory(network) if transport_factory else None)
            for network in Network
        )
    )
    return {
        "schema_version": 1,
        "version": __version__,
        "platform": platform.system(),
        "architecture": platform.machine(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "finished_at": stamp(),
        "status": "passed" if all(item["status"] == "passed" for item in reports) else "failed",
        "networks": reports,
        "coverage": "Bounded current provider observations, not lifetime history, "
        "independent consensus verification, interactive desktop acceptance or proof of ownership.",
        "storage": "No application cache, watchlist or investigation database is opened.",
    }
