"""Validate asset identity, unavailable fields, network isolation, navigation and exports."""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from PySide6.QtWidgets import QApplication
from test_accounts import ADDRESS, ISSUER, account_data

from polarstellar.app.window import MainWindow
from polarstellar.stellar.assets import parse_asset, validate_asset
from polarstellar.stellar.horizon import HorizonProvider
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService
from polarstellar.storage.database import SnapshotCache
from polarstellar.storage.export import serialize
from polarstellar.storage.provider import CachedProvider
from polarstellar.ui.asset_view import AssetView


def asset_row(issuer=ISSUER):
    """Supply a modern asset record with exact balances and deliberately missing fields."""
    return {
        "asset_code": "USD",
        "asset_issuer": issuer,
        "asset_type": "credit_alphanum4",
        "accounts": {"authorized": 4},
        "balances": {"authorized": "900000000.0000001"},
        "contracts_amount": "0.0000002",
        "num_contracts": 1,
        "flags": {"auth_required": False, "auth_revocable": True},
    }


def test_identity_precision_and_unknown_fields():
    """Keep same-code assets distinct and avoid converting absent values into zero."""
    first = parse_asset(asset_row(), "USD", ISSUER, Network.MAINNET, "fixture")
    second = parse_asset(asset_row(ADDRESS), "USD", ADDRESS, Network.MAINNET, "fixture")
    assert first.issuer != second.issuer
    assert first.statistics["balances.authorized"] == Decimal("900000000.0000001")
    assert first.statistics["num_liquidity_pools"] is None
    assert first.flags["auth_clawback_enabled"] is None
    assert first.flags["auth_required"] is False
    with pytest.raises(ValueError):
        parse_asset(asset_row(), "USD", ADDRESS, Network.MAINNET, "fixture")


@pytest.mark.parametrize(
    "code,issuer",
    [("bad/code", ISSUER), ("A" * 13, ISSUER), ("USD", ""), ("USD", "Ginvalid"), ("é", ISSUER)],
)
def test_invalid_identity(code, issuer):
    """Reject malformed codes and issuers before constructing any request."""
    with pytest.raises(AccountError):
        validate_asset(code, issuer)


@pytest.mark.parametrize(
    "field,value",
    [
        ("balances", {"authorized": "NaN"}),
        ("accounts", {"authorized": True}),
        ("flags", {"auth_required": "false"}),
        ("contracts_amount", "-1"),
    ],
)
def test_malformed_statistics(field, value):
    """Do not display nonfinite amounts, negative holdings, or misleading flags/counts."""
    with pytest.raises((ValueError, TypeError)):
        parse_asset({**asset_row(), field: value}, "USD", ISSUER, Network.MAINNET, "fixture")


def test_provider_routing_native_and_empty(tmp_path):
    """Use exact query parameters and bypass asset caching; native XLM performs no HTTP call."""
    calls = []

    def handler(request):
        """Record network/query routing and simulate a not-found asset on Mainnet."""
        calls.append(request)
        records = [asset_row()] if "testnet" in request.url.host else []
        return httpx.Response(200, json={"_embedded": {"records": records}})

    async def scenario():
        """Exercise the production cache wrapper and service boundary together."""
        provider = CachedProvider(
            HorizonProvider(httpx.MockTransport(handler)), SnapshotCache(tmp_path / "cache.db")
        )
        provider.set_enabled(True)
        service = AccountService(provider)
        await service.asset("USD", ISSUER, Network.TESTNET)
        assert calls[0].url.params["asset_issuer"] == ISSUER
        assert calls[0].url.params["asset_code"] == "USD"
        native = await service.asset("XLM", "", Network.TESTNET)
        assert native.asset_type == "native" and not native.flags and len(calls) == 1
        assert not (tmp_path / "cache.db").exists()
        with pytest.raises(AccountError, match="not found"):
            await service.asset("USD", ISSUER, Network.MAINNET)
        with pytest.raises(AccountError):
            await service.asset("../", ISSUER, Network.TESTNET)
        assert len(calls) == 2

    asyncio.run(scenario())


@pytest.fixture
def app():
    """Keep Qt alive throughout each UI test."""
    return QApplication.instance() or QApplication([])


def test_stale_asset_and_export(app):
    """Changing context rejects late results; exports preserve amounts, identity and flags."""
    futures = []

    class Provider:
        """Simulate a provider which returns even after its caller cancels."""

        async def get_asset(self, code, issuer, network):
            """Wait for explicitly controlled completion."""
            future = asyncio.get_running_loop().create_future()
            futures.append(future)
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                return await future

    async def scenario():
        """Complete the newer request first, then verify the old result cannot replace it."""
        view = AssetView(AccountService(Provider()), lambda: Network.TESTNET)
        view.open_asset("USD", ISSUER)
        await asyncio.sleep(0)
        old_task = view.task
        view.open_asset("USD", ADDRESS)
        await asyncio.sleep(0)
        current = parse_asset(asset_row(ADDRESS), "USD", ADDRESS, Network.TESTNET, "fixture")
        futures[1].set_result(current)
        await view.task
        futures[0].set_result(parse_asset(asset_row(), "USD", ISSUER, Network.TESTNET, "fixture"))
        await old_task
        snapshot = json.loads(serialize(view.export_document(), "json"))
        assert snapshot["metadata"]["issuer"] == ADDRESS
        assert snapshot["metadata"]["network"] == "Testnet"
        assert snapshot["records"][3]["value"] == "900000000.0000001"
        assert "metadata.issuer" in serialize(view.export_document(), "csv")
        addresses = []
        view.issuer_requested.connect(addresses.append)
        view.issuer.setText(ISSUER)
        view.inspect_issuer()
        assert addresses == [ADDRESS]
        view.reset()
        assert not view.export.isEnabled() and not view.open_issuer.isEnabled()
        view.close()

    asyncio.run(scenario())


def test_service_rejects_wrong_network():
    """Never show statistics returned for another network."""

    class Provider:
        """Deliberately return the wrong network to exercise service safeguards."""

        async def get_asset(self, *args):
            """Return a valid asset with an invalid network for this request."""
            return parse_asset(asset_row(), "USD", ISSUER, Network.MAINNET, "fixture")

    with pytest.raises(AccountError, match="network"):
        asyncio.run(AccountService(Provider()).asset("USD", ISSUER, Network.TESTNET))


def test_balance_asset_issuer_navigation(app):
    """Enable Assets, open a balance by full identity, and navigate to its issuer account."""

    def handler(request):
        """Serve account and asset endpoints with consistent fixture identities."""
        if request.url.path == "/assets":
            row = asset_row()
            row["asset_code"] = request.url.params["asset_code"]
            return httpx.Response(200, json={"_embedded": {"records": [row]}})
        data = account_data()
        data["account_id"] = request.url.path.split("/")[-1]
        return httpx.Response(200, json=data)

    async def scenario():
        """Use the real MainWindow signals and ensure a network change invalidates asset data."""
        window = MainWindow(AccountService(HorizonProvider(httpx.MockTransport(handler))))
        window.search.setText(ADDRESS)
        window.start_search()
        await window.task
        window.balances.selectRow(1)
        assert window.open_balance_asset.isEnabled()
        window.inspect_balance_asset()
        await window.assets.task
        assert window.pages.currentIndex() == 5
        assert window.assets.snapshot.issuer == ISSUER
        window.assets.inspect_issuer()
        await window.task
        assert window.account.address == ISSUER
        assert window.pages.currentIndex() == 0
        window.network.setCurrentText("Testnet")
        assert window.assets.snapshot is None and not window.assets.export.isEnabled()
        window.close()

    asyncio.run(scenario())


def test_payment_asset_navigation(app):
    """Payment navigation uses destination code and issuer, while native XLM has no issuer."""
    from test_counterparties import payment

    from polarstellar.stellar.activity import ActivityKind, parse_page
    from polarstellar.ui.activity_view import ActivityView

    class Service:
        """Serve a payment page containing issued and native assets."""

        async def activity(self, *args):
            """Return parsed records as the production service does."""
            return parse_page(
                {"_embedded": {"records": [payment(2), payment(1, asset_type="native")]}},
                ADDRESS,
                Network.MAINNET,
                ActivityKind.PAYMENTS,
                "fixture",
                None,
            )

    async def scenario():
        """Select both asset forms, then clear the account and verify navigation is disabled."""
        view = ActivityView(Service(), ActivityKind.PAYMENTS)
        selected = []
        view.asset_requested.connect(lambda code, issuer: selected.append((code, issuer)))
        view.reset((ADDRESS, Network.MAINNET))
        view.start()
        await view.task
        view.table.selectRow(0)
        view.open_asset()
        assert selected[0][1] == ISSUER
        view.table.selectRow(1)
        view.open_asset()
        assert selected[1] == ("XLM", "")
        view.reset()
        assert not view.open_asset_button.isEnabled()
        view.close()

    asyncio.run(scenario())


def test_asset_error_retry_and_native_ui(app):
    """Errors permit retry; native XLM can be exported but cannot open a nonexistent issuer."""

    async def scenario():
        """Run through invalid input followed by native XLM without network access."""
        view = AssetView(AccountService(HorizonProvider()), lambda: Network.MAINNET)
        view.open_asset("USD", "bad")
        await view.task
        assert "valid issuer" in view.details.toPlainText()
        assert not view.export.isEnabled()
        view.open_asset("XLM", "")
        await view.task
        assert view.export.isEnabled() and not view.open_issuer.isEnabled()
        assert view.export_document()["metadata"]["asset_type"] == "native"
        view.reset()
        view.close()

    asyncio.run(scenario())
