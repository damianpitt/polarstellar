"""Verify qualification refusal boundaries and realistic provider flows without public requests."""

import asyncio
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from test_accounts import ADDRESS, account_data
from test_activity import record
from test_asset_contract_identity import rpc_fixture as sac_fixture
from test_asset_discovery import row as asset_row
from test_contract_specs import rpc_fixture as spec_fixture
from test_contracts import event
from test_enrichment import effect, evidence

from polarstellar.stellar.activity import ActivityKind
from polarstellar.stellar.asset_contracts import derive
from polarstellar.stellar.contracts import PASSPHRASES, instance_key
from polarstellar.stellar.horizon import HorizonProvider
from polarstellar.stellar.live_checks import MAX_REQUESTS, ReadOnlyTransport, qualify_network
from polarstellar.stellar.models import Network
from polarstellar.stellar.service import AccountService

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "qualify_native", ROOT / "tools" / "qualify_native.py"
)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


async def fixture_transport(*, wrong_network=False, duplicate=False, rpc_error=False):
    """Serve real SDK/XDR fixtures through all production parsers, including two-page activity/events."""
    native_asset = await AccountService(HorizonProvider()).asset("XLM", "", Network.TESTNET)
    sac, _ = sac_fixture(native_asset)
    declarations, _ = spec_fixture()
    sac_key = instance_key(derive(native_asset)["contract"]).to_xdr()
    transaction, operations = evidence()

    async def handler(request):
        """Return only expected read paths and assert if qualification requests anything else."""
        if request.method == "POST":
            body = json.loads(request.content)
            if rpc_error:
                return httpx.Response(429)
            method, params = body["method"], body["params"]
            if method == "getNetwork":
                result = {"passphrase": PASSPHRASES[Network.TESTNET]}
            elif method == "getHealth":
                result = {"status": "healthy", "oldestLedger": 100, "latestLedger": 1500}
            elif method == "getEvents":
                result = {
                    "events": [] if params["pagination"].get("cursor") else [event()],
                    "cursor": "end" if params["pagination"].get("cursor") else "next",
                    "oldestLedger": 100,
                    "latestLedger": 1500,
                }
            elif method == "getLedgerEntries":
                provider = sac if params["keys"] == [sac_key] else declarations
                return await provider.transport.handle_async_request(request)
            else:
                raise AssertionError("Unexpected RPC write or method")
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})
        path = request.url.path
        if path == "/":
            data = {
                "network_passphrase": PASSPHRASES[
                    Network.MAINNET if wrong_network else Network.TESTNET
                ],
                "history_latest_ledger": 1500,
                "history_elder_ledger": 100,
            }
        elif path == "/transactions":
            data = {"_embedded": {"records": [transaction]}}
        elif path == f"/transactions/{transaction['hash']}":
            data = transaction
        elif path == f"/transactions/{transaction['hash']}/operations":
            data = {"_embedded": {"records": operations}}
        elif path.endswith("/effects"):
            data = {"_embedded": {"records": [effect(1), effect(2, "account_removed")]}}
        elif path == "/assets":
            data = {"_embedded": {"records": [asset_row()]}}
        elif path == f"/accounts/{ADDRESS}":
            data = account_data()
        elif path.startswith(f"/accounts/{ADDRESS}/"):
            kind = ActivityKind(path.rsplit("/", 1)[-1])
            second = "cursor" in request.url.params
            tokens = range(200, 180, -1) if not second or duplicate else range(180, 178, -1)
            rows = [record(token, kind) for token in tokens]
            data = {"_embedded": {"records": rows}}
        else:
            raise AssertionError("Unexpected qualification path")
        return httpx.Response(200, json=data)

    return httpx.MockTransport(handler)


def test_live_workflows_keep_network_provenance_and_bounded_pagination():
    """Run successful account/result/effects/asset/contract/specification checks with known protocol data."""

    async def scenario():
        """Confirm the production live-check path accepts complete fixture coverage and records its limits."""
        report = await qualify_network(Network.TESTNET, await fixture_transport())
        assert report["status"] == "passed", report["checks"]
        assert 0 < len(report["requests"]) <= MAX_REQUESTS
        assert all(item["second_page_records"] == 2 for item in report["activity"].values())
        assert report["contract_events"]["second_page_done"] is True
        assert report["contract_specification"]["entries"] == 4
        assert report["storage_key"]["status"] == "not queried"
        assert report["sample_account"] == ADDRESS and report["rpc_health"]["latestLedger"] == 1500
        assert all(request["http_status"] == 200 for request in report["requests"])

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["wrong_network", "duplicate", "rpc_error"])
def test_provider_failures_are_reported_without_hiding_independent_checks(failure):
    """Wrong network, nonadvancing activity and RPC rate limits cannot become successful release reports."""

    async def scenario():
        """Keep the other independent workflow's result even after one service fails."""
        report = await qualify_network(Network.TESTNET, await fixture_transport(**{failure: True}))
        assert report["status"] == "failed"
        statuses = {item["name"]: item["status"] for item in report["checks"]}
        assert statuses["horizon_workflows"] == ("passed" if failure == "rpc_error" else "failed")
        assert statuses["rpc_workflows"] == ("failed" if failure == "rpc_error" else "passed")
        if failure == "wrong_network":
            assert not any(
                item["action"].startswith("GET /accounts/") for item in report["requests"]
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "url,method,body",
    [
        ("https://untrusted.invalid", "GET", None),
        ("http://horizon-testnet.stellar.org", "GET", None),
        ("https://horizon-testnet.stellar.org/transactions", "POST", {}),
        ("https://soroban-testnet.stellar.org", "POST", {"method": "sendTransaction"}),
    ],
)
def test_qualification_refuses_writes_and_foreign_origins(url, method, body):
    """The guard rejects non-HTTPS, foreign destinations and transaction writes before transport access."""

    async def scenario():
        """Fail if a prohibited request reaches even the injected fake HTTP transport."""

        def unexpected(request):
            """Detect any transport access after a guard should have refused the action."""
            pytest.fail("Prohibited request reached network transport")

        guard = ReadOnlyTransport(Network.TESTNET, httpx.MockTransport(unexpected))
        async with httpx.AsyncClient(transport=guard) as client:
            with pytest.raises(ValueError):
                await client.request(method, url, json=body)
        assert not guard.requests
        await guard.finish()

    asyncio.run(scenario())


def test_request_budget_stops_before_an_additional_network_call():
    """A provider can never turn bounded qualification into an unbounded fetch loop."""

    async def scenario():
        """Use a tiny reply and verify the extra request is stopped before transport access."""
        calls = []

        def reply(request):
            """Count accepted requests without touching any external endpoint."""
            calls.append(request)
            return httpx.Response(200, json={})

        guard = ReadOnlyTransport(Network.TESTNET, httpx.MockTransport(reply))
        async with httpx.AsyncClient(transport=guard) as client:
            for _ in range(MAX_REQUESTS):
                await client.get("https://horizon-testnet.stellar.org")
            with pytest.raises(ValueError, match="budget"):
                await client.get("https://horizon-testnet.stellar.org")
        assert len(calls) == MAX_REQUESTS
        await guard.finish()

    asyncio.run(scenario())


def test_native_checksum_is_bound_to_exact_version_and_filename(tmp_path):
    """Refuse changed native archives and mismatched checksum filenames before extraction or launch."""
    archive = tmp_path / "PolarStellar-0.0.31-linux-x86_64.tar.gz"
    archive.write_bytes(b"trusted fixture archive")
    checksum = archive.with_name(archive.name + ".sha256")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text(digest + "  " + archive.name)
    assert native.checked_archive(tmp_path, "0.0.31", "linux-x86_64") == (archive, digest)
    archive.write_bytes(b"changed archive")
    with pytest.raises(ValueError, match="does not match"):
        native.checked_archive(tmp_path, "0.0.31", "linux-x86_64")
    checksum.write_text(digest + "  other.tar.gz")
    with pytest.raises(ValueError, match="exact expected"):
        native.checked_archive(tmp_path, "0.0.31", "linux-x86_64")


@pytest.mark.parametrize(
    "change",
    [
        {"frozen": False},
        {"version": "old"},
        {"platform": "Windows"},
        {"architecture": "x86_64"},
        {"qt_platform_plugin": "offscreen"},
        {"checks": []},
    ],
)
def test_native_reports_require_expected_app_and_native_desktop(change):
    """A zero exit code cannot hide source-Python fallback, wrong binaries, or skipped native desktop checks."""
    payload = {
        "status": "passed",
        "version": "0.0.31",
        "platform": "Darwin",
        "frozen": True,
        "architecture": "arm64",
        "qt_platform_plugin": "cocoa",
        "checks": [
            "account_lookup_exact_amounts",
            "csv_json_exports",
            "sqlite_saved_investigation_restart",
            "sqlite_watchlist_restart",
            "qt_navigation_and_async_event_loop",
            "stellar_sdk_xdr_exact_integer",
            "networkx_relationship_model",
            "httpx_tls_certificate_bundle",
            "desktop_compact_navigation",
            "desktop_notes_reopen",
            "desktop_invalid_search_recovery",
        ],
    }
    native.accepted_report(payload, "0.0.31", "Darwin", "arm64")
    with pytest.raises(ValueError):
        native.accepted_report({**payload, **change}, "0.0.31", "Darwin", "arm64")


def test_runtime_qualification_refuses_an_installed_project_environment(tmp_path, monkeypatch):
    """Reject an installed runtime before archive launch so build environments cannot masquerade as fresh runtimes."""
    monkeypatch.setattr(native.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(native.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(native.importlib.util, "find_spec", lambda name: object())
    with pytest.raises(ValueError, match="isolated standard-library"):
        native.qualify(tmp_path, tmp_path / "reports", "0.0.31", "a" * 40, "macos-arm64")


def test_network_deadline_preserves_failed_partial_report(monkeypatch):
    """An interrupted step remains failed in the report instead of causing a missing-status error."""
    import polarstellar.stellar.live_checks as module

    monkeypatch.setattr(module, "NETWORK_TIMEOUT", 0.01)

    async def slow(request):
        """Wait longer than the deadline without contacting a public provider."""
        await asyncio.sleep(10)
        return httpx.Response(200, json={})

    report = asyncio.run(qualify_network(Network.TESTNET, httpx.MockTransport(slow)))
    assert report["status"] == "failed"
    assert all(step["status"] == "failed" for step in report["checks"])
    assert report["checks"][-1]["name"] == "network_deadline"


@pytest.mark.parametrize("failure", ["missing", "failed"])
def test_native_live_report_requires_both_networks_and_workflows(failure):
    """A successful process or top-level status cannot hide missing or failed live checks."""
    payload = {
        "status": "passed",
        "version": "0.0.31",
        "platform": "Darwin",
        "architecture": "arm64",
        "frozen": True,
        "networks": [
            {
                "network": name,
                "status": "passed",
                "checks": [
                    {"name": "horizon_workflows", "status": "passed"},
                    {"name": "rpc_workflows", "status": "passed"},
                ],
            }
            for name in ("Mainnet", "Testnet")
        ],
    }
    native.accepted_report(payload, "0.0.31", "Darwin", "arm64", live=True)
    if failure == "missing":
        payload["networks"].pop()
    else:
        payload["networks"][1]["checks"][1]["status"] = "failed"
    with pytest.raises(ValueError):
        native.accepted_report(payload, "0.0.31", "Darwin", "arm64", live=True)


@pytest.mark.parametrize("arguments", [["--live-check"], ["--live-report", "unused.json"]])
def test_live_check_cli_requires_explicit_report_pair(arguments):
    """Normal startup and mistyped options cannot silently initiate live qualification requests."""
    result = subprocess.run(
        [sys.executable, "-m", "polarstellar.app.main", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode != 0 and "requires" in result.stderr
