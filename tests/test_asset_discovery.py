"""Verify catalog identity, safe cursors, exact data, export coverage and async UI safeguards."""

import asyncio
from dataclasses import replace

import httpx
import pytest
from PySide6.QtWidgets import QApplication
from test_accounts import ADDRESS, ISSUER
from test_assets import asset_row

from polarstellar.app.window import MainWindow
from polarstellar.stellar.asset_discovery import filters, parse_catalog, token
from polarstellar.stellar.horizon import ENDPOINTS, HorizonProvider
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService
from polarstellar.storage.database import SnapshotCache
from polarstellar.storage.export import serialize
from polarstellar.storage.provider import CachedProvider
from polarstellar.ui.asset_discovery_view import AssetDiscoveryView


def row(code="USD", issuer=ISSUER, **changes):
    """Build exact issued-asset statistics with a canonical Horizon asset paging token."""
    value = {
        **asset_row(issuer),
        "asset_code": code,
        "asset_type": "credit_alphanum4" if len(code) <= 4 else "credit_alphanum12",
    }
    value["paging_token"] = f"{code}_{issuer}_{value['asset_type']}"
    return {**value, **changes}


def page(rows, code="", issuer="", cursor=None, network=Network.TESTNET):
    """Parse a fixture with the same query-boundary rules as the real Horizon adapter."""
    return parse_catalog(
        {"_embedded": {"records": rows}}, code, issuer, network, ENDPOINTS[network], cursor
    )


@pytest.fixture
def app():
    """Keep a display-independent Qt application alive for the desktop integration checks."""
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("code,issuer", [("", ""), ("USD", ""), ("", ISSUER), ("USD", ISSUER)])
def test_optional_filters_request_and_network_routing(code, issuer):
    """Browse, code-only, issuer-only and exact-pair requests preserve parameters and host identity."""
    requests = []

    def respond(request):
        """Serve a short page first, then an empty page; no next-page URL is followed."""
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "_embedded": {"records": [] if "cursor" in request.url.params else [row()]},
                "_links": {"next": {"href": "https://untrusted.invalid/"}},
            },
        )

    async def scenario():
        """Exercise real service/HTTP parsing with fixed endpoints on both networks."""
        service = AccountService(HorizonProvider(httpx.MockTransport(respond)))
        for network in Network:
            first = await service.discover_assets(code, issuer, network)
            assert len(first.items) == 1 and not first.done
            assert first.items[0].statistics["balances.authorized"].as_tuple().exponent == -7
            ending = await service.discover_assets(code, issuer, network, first.next_cursor)
            assert ending.done and ending.next_cursor is None

    asyncio.run(scenario())
    for index, request in enumerate(requests):
        assert request.url.path == "/assets" and request.url.params["limit"] == "20"
        assert request.url.params["order"] == "asc"
        assert request.url.params.get("asset_code", "") == code
        assert request.url.params.get("asset_issuer", "") == issuer
        assert request.url.host == (
            "horizon.stellar.org" if index < 2 else "horizon-testnet.stellar.org"
        )


@pytest.mark.parametrize(
    "code,issuer,cursor",
    [
        ("usd*", "", None),
        ("TOO_LONG_ASSET", "", None),
        ("", "G" * 56, None),
        ("", "", "123"),
        ("", "", "https://bad.invalid"),
        ("", "", f"USD_{ISSUER}_credit_alphanum12"),
        ("", "", f" USD_{ISSUER}_credit_alphanum4"),
    ],
)
def test_validation_before_network(code, issuer, cursor):
    """Invalid filters/checksums/cursors must fail before any public API request can occur."""

    def forbidden(request):
        """Fail if an invalid query gets past local validation."""
        raise AssertionError("Unexpected request")

    with pytest.raises(AccountError):
        asyncio.run(
            AccountService(HorizonProvider(httpx.MockTransport(forbidden))).discover_assets(
                code, issuer, Network.TESTNET, cursor
            )
        )


@pytest.mark.parametrize(
    "rows",
    [
        [row(asset_issuer="G" * 56)],
        [row(paging_token="wrong")],
        [row(asset_type="native")],
        [row(), row()],
        [row(balances={"authorized": "NaN"})],
        [row(asset_code="lower")],
        [row(asset_issuer=ADDRESS)],
        [row()] * 21,
    ],
)
def test_malformed_or_mismatched_page_is_rejected(rows):
    """A malformed row invalidates the whole page; filters are exact and case-sensitive."""
    with pytest.raises((ValueError, AccountError, TypeError)):
        page(rows, "USD", ISSUER)


def test_same_code_issuers_and_native_xlm_boundary():
    """Same-code issuers stay distinct; XLM code searches concern issued assets, not native XLM."""
    result = page([row(issuer=ISSUER), row(issuer=ADDRESS)])
    assert result.items[0].issuer != result.items[1].issuer
    issued = page([row("XLM")], "XLM")
    assert issued.items[0].issuer == ISSUER
    assert filters(" XLM ", "") == ("XLM", "")
    assert token(row()["paging_token"]) == row()["paging_token"]


class Provider:
    """Return controlled catalog pages and record the frozen request context."""

    def __init__(self, batches):
        """Keep fixture batches private so each request consumes only one page."""
        self.batches, self.calls = list(batches), []

    async def discover_assets(self, code, issuer, network, cursor=None):
        """Return a parsed page or raise a queued error without contacting any API."""
        self.calls.append((code, issuer, network, cursor))
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return page(batch, code, issuer, cursor, network)


def test_ui_failed_page_retry_query_freeze_and_exports(app):
    """Failed pagination keeps rows/cursor, form edits do not alter queries, and exports stay exact."""

    async def scenario():
        """Load same-code assets across pages while editing an unsubmitted filter."""
        provider = Provider([[row()], AccountError("Fixture outage"), [row(issuer=ADDRESS)], []])
        view = AssetDiscoveryView(AccountService(provider), lambda: Network.TESTNET)
        view.code.setText("USD")
        view.start()
        await view.task
        first_cursor = view.cursor
        view.code.setText("UNSUBMITTED")
        view.load_more()
        await view.task
        assert len(view.items) == 1 and view.cursor == first_cursor
        assert "Previous rows retained" in view.status.text()
        view.load_more()
        await view.task
        view.load_more()
        await view.task
        assert view.done and len(view.items) == 2 and len(view.pages) == 3
        assert all(call[0] == "USD" for call in provider.calls)
        payload = view.export_document()
        assert payload["metadata"]["asset_code_filter"] == "USD"
        assert payload["records"][0]["statistics"]["balances.authorized"] == "900000000.0000001"
        assert payload["metadata"]["query_end_reached"]
        assert "900000000.0000001" in serialize(payload, "csv")
        assert not view.more.isEnabled()

    asyncio.run(scenario())


def test_overlap_conflicts_keep_previous_rows(app):
    """Repeated identities with changed financial evidence cannot silently overwrite the catalog."""

    async def scenario():
        """Return an overlap with changed balances and a new final cursor."""
        provider = Provider(
            [[row()], [row(balances={"authorized": "1.0000001"}), row(issuer=ADDRESS)]]
        )
        view = AssetDiscoveryView(AccountService(provider), lambda: Network.TESTNET)
        view.start()
        await view.task
        view.load_more()
        await view.task
        assert len(view.items) == 1 and len(view.pages) == 1
        assert "changed across overlapping pages" in view.status.text()

    asyncio.run(scenario())


def test_identical_overlap_deduplicates_and_cursor_cycle_fails(app):
    """Matching overlaps keep original observations, while cursor cycles cannot loop forever."""

    async def scenario():
        """Cover stable overlap and a later return to an already-used cursor."""
        provider = Provider([[row()], [row(), row(issuer=ADDRESS)], [row()]])
        view = AssetDiscoveryView(AccountService(provider), lambda: Network.TESTNET)
        view.start()
        await view.task
        fetched = view.items[0].fetched_at
        view.load_more()
        await view.task
        assert len(view.items) == 2 and view.items[0].fetched_at == fetched
        view.load_more()
        await view.task
        assert len(view.pages) == 2 and "cursor repeated" in view.status.text()

    asyncio.run(scenario())


def test_late_results_after_reset_are_discarded(app):
    """Network/context resets must reject responses even when a client ignores cancellation."""

    async def scenario():
        """Release a fixture after reset has cleared all exportable state."""
        future = asyncio.get_running_loop().create_future()

        class Delayed:
            """Ignore one cancellation to test generation protection."""

            async def discover_assets(self, *args):
                """Wait for a controlled late result rather than a real API."""
                try:
                    return await asyncio.shield(future)
                except asyncio.CancelledError:
                    return await future

        view = AssetDiscoveryView(AccountService(Delayed()), lambda: Network.TESTNET)
        view.start()
        task = view.task
        await asyncio.sleep(0)
        view.reset()
        await asyncio.sleep(0)
        future.set_result(page([row()]))
        await task
        assert not view.items and not view.pages and not view.export.isEnabled()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["query", "network", "row_network", "cursor"])
def test_wrong_provider_context_rejected(change):
    """Provider metadata and row identity must both belong to the requested query/network."""

    async def scenario():
        """Inject mismatches independently so no weak metadata-only check can pass."""
        result = page([row()])
        if change == "query":
            result = replace(result, code="OTHER")
        elif change == "network":
            result = replace(result, network=Network.MAINNET)
        elif change == "row_network":
            result = replace(result, items=(replace(result.items[0], network=Network.MAINNET),))
        else:
            result = replace(result, next_cursor=row(issuer=ADDRESS)["paging_token"])

        class Wrong:
            """Supply the deliberately mismatched model."""

            async def discover_assets(self, *args):
                """Return fixture data without network activity."""
                return result

        with pytest.raises(AccountError):
            await AccountService(Wrong()).discover_assets("", "", Network.TESTNET)

    asyncio.run(scenario())


def test_inspector_shortcut_retains_discovery_and_network_reset_clears(app, tmp_path):
    """Opening a result uses its issuer; returning to discovery keeps pages, network changes clear them."""

    async def scenario():
        """Drive the production Assets tabs with the real HTTP adapter and caching wrapper."""
        calls = []

        def respond(request):
            """Distinguish catalog requests from one-pair inspector requests."""
            calls.append(request)
            rows = (
                [row(issuer=ADDRESS)]
                if request.url.params.get("limit") == "2"
                else [row(), row(issuer=ADDRESS)]
            )
            return httpx.Response(200, json={"_embedded": {"records": rows}})

        cached = CachedProvider(
            HorizonProvider(httpx.MockTransport(respond)), SnapshotCache(tmp_path / "cache.sqlite3")
        )
        cached.set_enabled(True)
        window = MainWindow(AccountService(cached))
        window.network.setCurrentText("Testnet")
        window.navigation.setCurrentRow(5)
        discovery = window.assets.discovery
        window.assets.tabs.setCurrentIndex(1)
        discovery.start()
        await discovery.task
        discovery.table.selectRow(1)
        discovery.open_selected()
        await window.assets.task
        assert window.assets.snapshot.issuer == ADDRESS
        assert window.assets.tabs.currentIndex() == 0 and len(discovery.items) == 2
        assert calls[-1].url.params["asset_issuer"] == ADDRESS
        assert not cached.cache.path.exists()
        window.assets.tabs.setCurrentIndex(1)
        window.network.setCurrentText("Mainnet")
        assert not discovery.items and not discovery.export.isEnabled()
        window.close()

    asyncio.run(scenario())


def test_page_cap_stops_requests_and_marks_incomplete_coverage(app):
    """Reaching the bounded page count must stop fetching without claiming an exhausted catalog."""

    async def scenario():
        """Consume fifty small fixture pages and prove a fifty-first click sends no request."""
        provider = Provider([[row("A" + str(index))] for index in range(50)])
        view = AssetDiscoveryView(AccountService(provider), lambda: Network.TESTNET)
        view.start()
        await view.task
        for _ in range(49):
            view.load_more()
            await view.task
        view.load_more()
        assert len(provider.calls) == 50 and view.task is None
        assert len(view.items) == 50 and not view.more.isEnabled()
        assert "coverage incomplete" in view.status.text()
        assert view.export_document()["metadata"]["query_end_reached"] is False

    asyncio.run(scenario())


def test_cancel_retains_pages_and_can_retry(app):
    """Cancelling an in-flight page retains the previous cursor and allows the same query to resume."""

    async def scenario():
        """Cancel a delayed second page, then retry from the first page's original cursor."""
        future = asyncio.get_running_loop().create_future()

        class Delayed(Provider):
            """Return a controlled future for just the second request."""

            async def discover_assets(self, code, issuer, network, cursor=None):
                """Record all requests, ignoring cancellation on the delayed response once."""
                if len(self.calls) == 1:
                    self.calls.append((code, issuer, network, cursor))
                    try:
                        return await asyncio.shield(future)
                    except asyncio.CancelledError:
                        return await future
                return await super().discover_assets(code, issuer, network, cursor)

        provider = Delayed([[row()], [row(issuer=ADDRESS)]])
        view = AssetDiscoveryView(AccountService(provider), lambda: Network.TESTNET)
        view.start()
        await view.task
        cursor = view.cursor
        view.load_more()
        pending = view.task
        await asyncio.sleep(0)
        view.cancel_request()
        await asyncio.sleep(0)
        future.set_result(page([row(issuer=ADDRESS)], cursor=cursor))
        await pending
        assert len(view.items) == 1 and view.cursor == cursor
        view.load_more()
        await view.task
        assert len(view.items) == 2 and provider.calls[-1][3] == cursor

    asyncio.run(scenario())
