"""Check offline organization, identity isolation, live refresh, and destructive/async safeguards."""

import asyncio
import sqlite3

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from test_accounts import ADDRESS, ISSUER, account_data
from test_assets import asset_row
from test_contracts import CONTRACT
from test_contracts import provider as rpc_fixture

from polarstellar.app.window import MainWindow
from polarstellar.stellar.assets import parse_asset
from polarstellar.stellar.horizon import parse_account
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService
from polarstellar.storage.watchlists import WatchlistStore, resource
from polarstellar.ui.watchlists_view import WatchlistsView


@pytest.fixture
def app():
    """Keep the Qt application alive; all providers in these tests are offline fixtures."""
    return QApplication.instance() or QApplication([])


def account(network=Network.TESTNET):
    """Create exact financial evidence with the requested network and fixture provenance."""
    return parse_account(account_data(), ADDRESS, network, "fixture")


def saved(store, network=Network.TESTNET):
    """Create one local account bookmark without any network activity."""
    list_id = store.create("Review")
    entry_id = store.add(list_id, resource("account", ADDRESS, network))
    return list_id, entry_id


def view_for(store, provider=None, contract_provider=None):
    """Create the offline library; missing provider objects fail if a test accidentally performs I/O."""
    return WatchlistsView(
        store, provider or object(), contract_provider or object(), lambda: Network.TESTNET
    )


def test_restart_deduplication_and_identity_separation(tmp_path):
    """Same addresses on different networks and same-code assets from different issuers stay distinct."""
    path = tmp_path / "watchlists.sqlite3"
    store = WatchlistStore(path)
    assert store.list() == [] and not path.exists()
    list_id, entry_id = saved(store)
    store.edit(list_id, entry_id, "Local label", "Private interpretation")
    store.snapshot(list_id, entry_id, {"exact": "0.0000001"})
    assert store.add(list_id, resource("account", ADDRESS, Network.TESTNET)) == entry_id
    for identity in (
        resource("account", ADDRESS, Network.MAINNET),
        resource("asset", "USD", Network.TESTNET, ADDRESS),
        resource("asset", "USD", Network.TESTNET, ISSUER),
        resource("asset", "XLM", Network.TESTNET),
        resource("asset", "XLM", Network.TESTNET, ISSUER),
        resource("contract", CONTRACT, Network.TESTNET),
    ):
        store.add(list_id, identity)
    restarted = WatchlistStore(path).list()[0]
    assert len(restarted["entries"]) == 7
    first = restarted["entries"][0]
    assert (first["label"], first["notes"], first["snapshot"]) == (
        "Local label",
        "Private interpretation",
        {"exact": "0.0000001"},
    )
    store.rename(list_id, "New name")
    assert store.list()[0]["name"] == "New name"
    store.remove(list_id, entry_id)
    assert len(store.list()[0]["entries"]) == 6
    store.delete(list_id)
    assert store.list() == []


@pytest.mark.parametrize(
    "kind,value,issuer,network",
    [
        ("account", "G" * 56, "", Network.TESTNET),
        ("contract", "C" * 56, "", Network.TESTNET),
        ("asset", "USD", "", Network.TESTNET),
        ("asset", "USD:issuer", ISSUER, Network.TESTNET),
        ("asset", "xlm", "", Network.TESTNET),
        ("transaction", "0" * 64, "", Network.TESTNET),
        ("account", ADDRESS, "", "Unknown network"),
    ],
)
def test_invalid_bookmarks_rejected(kind, value, issuer, network):
    """Only supported resource identities and explicit Stellar networks can enter storage."""
    with pytest.raises((ValueError, AccountError)):
        resource(kind, value, network, issuer)


def test_limits_rollback_and_future_schema(tmp_path):
    """Oversized annotations/snapshots cannot replace saved state; future database versions are refused."""
    store = WatchlistStore(tmp_path / "watchlists.sqlite3")
    list_id, entry_id = saved(store)
    previous = store.list()
    with pytest.raises(ValueError):
        store.edit(list_id, entry_id, "label", "x" * 10_001)
    with pytest.raises(ValueError, match="10 MB"):
        store.snapshot(list_id, entry_id, {"huge": "x" * 10_000_000})
    with pytest.raises(ValueError):
        store.snapshot(list_id, entry_id, {"bad": float("nan")})
    assert store.list() == previous
    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="version"):
        store.list()
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_offline_ui_and_annotation_protection(app, tmp_path, monkeypatch):
    """Local selection/reopening makes no requests and never silently discards unsaved notes."""
    store = WatchlistStore(tmp_path / "watchlists.sqlite3")
    list_id, entry_id = saved(store)
    second_id = store.add(list_id, resource("account", ADDRESS, Network.MAINNET))
    view = view_for(store)
    view.reload(list_id, entry_id)
    view.label.setText("My local label")
    view.notes.setPlainText("Unsaved note")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    view.entries.setCurrentRow(1)
    assert view.current["id"] == entry_id and view.entries.currentRow() == 0
    view.save_notes()
    restarted = view_for(WatchlistStore(store.path))
    restarted.reload(list_id, entry_id)
    assert restarted.notes.toPlainText() == "Unsaved note"
    assert "never refreshed" in restarted.status.text()
    restarted.remove()
    assert len(store.list()[0]["entries"]) == 2
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    restarted.remove()
    assert store.list()[0]["entries"][0]["id"] == second_id
    restarted.delete()
    assert store.list() == []


class LiveProvider:
    """Record source calls and return exact account/asset fixtures; no public API is contacted."""

    def __init__(self):
        """Start with an empty request log so tests can detect unexpected or cached calls."""
        self.calls = []

    async def get_account(self, address, network):
        """Return account evidence for the requested network."""
        self.calls.append(("account", address, network))
        return account(network)

    async def get_asset(self, code, issuer, network):
        """Return an exact issued asset with intentionally absent optional statistics."""
        self.calls.append(("asset", code, issuer, network))
        return parse_asset(asset_row(), code, issuer, network, "fixture")


class CachedWrapper:
    """Expose a live provider while failing if watchlist refresh tries to use cached access."""

    def __init__(self, provider):
        """Keep the underlying source provider available for deliberate cache bypass."""
        self.provider = provider

    async def get_account(self, *args):
        """Fail if manual refresh accidentally travels through this wrapper."""
        raise AssertionError("Manual refresh must bypass cached snapshots")


@pytest.mark.parametrize("kind", ["account", "asset", "contract"])
def test_manual_refresh_all_kinds_and_cache_bypass(app, tmp_path, kind):
    """Refresh updates one latest snapshot, uses the saved network, and does not load contract events."""

    async def scenario():
        """Drive an actual UI refresh using fixture Horizon/RPC responses."""
        store = WatchlistStore(tmp_path / "watchlists.sqlite3")
        list_id = store.create("Resources")
        identity = resource(
            kind,
            "USD" if kind == "asset" else CONTRACT if kind == "contract" else ADDRESS,
            Network.TESTNET,
            ISSUER if kind == "asset" else "",
        )
        entry_id = store.add(list_id, identity)
        raw = LiveProvider()
        rpc, calls = rpc_fixture()
        view = view_for(store, CachedWrapper(raw), rpc)
        view.reload(list_id, entry_id)
        view.start_refresh()
        await view.task
        snapshot = store.list()[0]["entries"][0]["snapshot"]
        assert snapshot is not None
        if kind == "account":
            assert raw.calls == [("account", ADDRESS, Network.TESTNET)]
            assert snapshot["records"][0]["amount"] == "123456789.1234567"
        elif kind == "asset":
            assert raw.calls == [("asset", "USD", ISSUER, Network.TESTNET)]
            assert snapshot["metadata"]["statistics"]["balances.authorized"] == "900000000.0000001"
        else:
            assert snapshot["metadata"]["instance"]["contract"] == CONTRACT
            assert snapshot["metadata"]["events_queried"] is False
            assert "getEvents" not in [body["method"] for _, body in calls]

    asyncio.run(scenario())


def test_refresh_preserves_intervening_saved_and_unsaved_notes(app, tmp_path):
    """Network results cannot overwrite annotations committed or drafted while awaiting I/O."""

    async def scenario():
        """Hold the provider until both a stored edit and an unsaved UI edit exist."""
        future = asyncio.get_running_loop().create_future()

        class Provider:
            """Delay account evidence until annotations have changed."""

            async def get_account(self, *args):
                """Wait for the controlled response without accessing a public endpoint."""
                return await future

        store = WatchlistStore(tmp_path / "watchlists.sqlite3")
        list_id, entry_id = saved(store)
        view = view_for(store, Provider())
        view.reload(list_id, entry_id)
        view.start_refresh()
        task = view.task
        await asyncio.sleep(0)
        store.edit(list_id, entry_id, "Stored label", "Committed during refresh")
        view.notes.setPlainText("Still typing")
        future.set_result(account())
        await task
        persisted = store.list()[0]["entries"][0]
        assert persisted["notes"] == "Committed during refresh" and persisted["snapshot"]
        assert view.notes.toPlainText() == "Still typing"

    asyncio.run(scenario())


def test_cancelled_provider_finishing_late_is_ignored(app, tmp_path):
    """Generation checks prevent updates after cancellation, even if the provider suppresses it."""

    async def scenario():
        """Select another network's row while an uncancellable request is pending."""
        future = asyncio.get_running_loop().create_future()

        class Provider:
            """Intentionally ignore one cancellation to exercise stale-result protection."""

            async def get_account(self, *args):
                """Return late rather than obey cancellation, as some external clients may do."""
                try:
                    return await asyncio.shield(future)
                except asyncio.CancelledError:
                    return await future

        store = WatchlistStore(tmp_path / "watchlists.sqlite3")
        list_id, entry_id = saved(store)
        store.add(list_id, resource("account", ADDRESS, Network.MAINNET))
        view = view_for(store, Provider())
        view.reload(list_id, entry_id)
        view.start_refresh()
        task = view.task
        await asyncio.sleep(0)
        view.entries.setCurrentRow(1)
        await asyncio.sleep(0)
        future.set_result(account())
        await task
        assert all(entry["snapshot"] is None for entry in store.list()[0]["entries"])
        assert view.current["network"] == "Mainnet"

    asyncio.run(scenario())


def test_deleted_entry_not_resurrected_by_refresh(app, tmp_path):
    """Deleting a bookmark elsewhere during refresh must not recreate it when results arrive."""

    async def scenario():
        """Simulate another local window removing the requested entry."""
        future = asyncio.get_running_loop().create_future()

        class Provider:
            """Wait while the bookmark is deleted."""

            async def get_account(self, *args):
                """Return the controlled account result after deletion."""
                return await future

        store = WatchlistStore(tmp_path / "watchlists.sqlite3")
        list_id, entry_id = saved(store)
        view = view_for(store, Provider())
        view.reload(list_id, entry_id)
        view.start_refresh()
        task = view.task
        await asyncio.sleep(0)
        store.remove(list_id, entry_id)
        future.set_result(account())
        await task
        assert store.list()[0]["entries"] == []
        assert "Previous snapshot retained" in view.status.text()

    asyncio.run(scenario())


def test_wrong_network_response_retains_snapshot(app, tmp_path):
    """Bad provider identity cannot replace a previously saved snapshot."""

    async def scenario():
        """Send Mainnet evidence in response to a Testnet request."""

        class Provider:
            """Return a deliberately mismatched network."""

            async def get_account(self, *args):
                """Supply evidence that must be rejected by AccountService."""
                return account(Network.MAINNET)

        store = WatchlistStore(tmp_path / "watchlists.sqlite3")
        list_id, entry_id = saved(store)
        store.snapshot(list_id, entry_id, {"old": "kept"})
        view = view_for(store, Provider())
        view.reload(list_id, entry_id)
        view.start_refresh()
        await view.task
        assert store.list()[0]["entries"][0]["snapshot"] == {"old": "kept"}
        assert "Previous snapshot retained" in view.status.text()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["account", "asset", "contract"])
def test_explorer_reopening_uses_saved_network(app, tmp_path, kind):
    """Quick opening switches to the saved network before sending any explorer request."""

    async def scenario():
        """Open each supported resource in its existing inspector, preserving its bookmark."""
        store = WatchlistStore(tmp_path / "watchlists.sqlite3")
        list_id = store.create("Explore")
        identity = resource(
            kind,
            "USD" if kind == "asset" else CONTRACT if kind == "contract" else ADDRESS,
            Network.TESTNET,
            ISSUER if kind == "asset" else "",
        )
        entry_id = store.add(list_id, identity)
        rpc, _ = rpc_fixture()
        raw = LiveProvider()
        window = MainWindow(AccountService(raw), contract_provider=rpc, watchlist_store=store)
        window.watchlists.reload(list_id, entry_id)
        window.navigation.setCurrentRow(8)
        window.watchlists.open_selected()
        task = (
            window.task
            if kind == "account"
            else window.assets.task
            if kind == "asset"
            else window.contracts.task
        )
        await task
        assert window.network.currentText() == "Testnet"
        assert window.navigation.currentRow() == {"account": 0, "asset": 5, "contract": 6}[kind]
        assert store.list()[0]["entries"][0]["snapshot"] is None
        window.watch_resource()
        assert len(store.list()[0]["entries"]) == 1
        window.close()

    asyncio.run(scenario())


def test_native_xlm_refresh_needs_no_api(app, tmp_path):
    """Native XLM's local definition must not be turned into an unnecessary Horizon request."""
    import httpx

    from polarstellar.stellar.horizon import HorizonProvider

    def unexpected(request):
        """Fail if native asset definition lookup attempts network access."""
        raise AssertionError("Native XLM requires no API request")

    async def scenario():
        """Refresh a native asset bookmark through the real provider's local-definition path."""
        store = WatchlistStore(tmp_path / "watchlists.sqlite3")
        list_id = store.create("Native assets")
        entry_id = store.add(list_id, resource("asset", "XLM", Network.TESTNET))
        view = view_for(store, HorizonProvider(httpx.MockTransport(unexpected)))
        view.reload(list_id, entry_id)
        view.start_refresh()
        await view.task
        metadata = store.list()[0]["entries"][0]["snapshot"]["metadata"]
        assert metadata["issuer"] == "" and metadata["asset_type"] == "native"
        assert metadata["cache_status"] == "Local definition; no network request"

    asyncio.run(scenario())


@pytest.mark.parametrize("bad_network,bad_contract", [("Mainnet", CONTRACT), ("Testnet", "other")])
def test_wrong_contract_response_retains_snapshot(app, tmp_path, bad_network, bad_contract):
    """RPC results from a different network or contract cannot enter saved bookmark evidence."""

    async def scenario():
        """Return a deliberately wrong RPC identity while preserving old offline evidence."""

        class Provider:
            """Supply a mismatched result without any public RPC access."""

            async def inspect(self, *args):
                """Return only identity fields; mismatch must be caught before saving."""
                return {"network": bad_network, "contract": bad_contract}

        store = WatchlistStore(tmp_path / "watchlists.sqlite3")
        list_id = store.create("Contracts")
        entry_id = store.add(list_id, resource("contract", CONTRACT, Network.TESTNET))
        store.snapshot(list_id, entry_id, {"old": "kept"})
        view = view_for(store, contract_provider=Provider())
        view.reload(list_id, entry_id)
        view.start_refresh()
        await view.task
        assert store.list()[0]["entries"][0]["snapshot"] == {"old": "kept"}
        assert "does not match" in view.status.text()

    asyncio.run(scenario())


def test_full_list_rejects_new_entry_but_preserves_duplicates(tmp_path):
    """At the size boundary, an existing bookmark still opens and a failed add loses no entries."""
    from stellar_sdk import StrKey

    store = WatchlistStore(tmp_path / "watchlists.sqlite3")
    list_id = store.create("Bounded list")
    first = None
    for index in range(500):
        identity = resource(
            "account", StrKey.encode_ed25519_public_key(index.to_bytes(32, "big")), Network.TESTNET
        )
        entry_id = store.add(list_id, identity)
        if index == 0:
            first = entry_id
    previous = store.list()
    assert store.add(list_id, resource("account", ADDRESS, Network.TESTNET)) == first
    with pytest.raises(ValueError, match="500"):
        store.add(list_id, resource("contract", CONTRACT, Network.TESTNET))
    assert store.list() == previous


def test_window_close_protects_unsaved_watchlist_notes(app, tmp_path, monkeypatch):
    """Application closure must offer a choice before losing uncommitted bookmark annotations."""
    store = WatchlistStore(tmp_path / "watchlists.sqlite3")
    list_id, entry_id = saved(store)
    window = MainWindow(AccountService(object()), watchlist_store=store)
    window.watchlists.reload(list_id, entry_id)
    window.watchlists.notes.setPlainText("Uncommitted interpretation")
    window.show()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    assert not window.close() and window.isVisible()
    assert window.watchlists.notes.toPlainText() == "Uncommitted interpretation"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    assert window.close() and not window.isVisible()
    assert store.list()[0]["entries"][0]["notes"] == ""


def test_global_network_change_rejects_late_watchlist_refresh(app, tmp_path):
    """The top-level network switch must invalidate pending watchlist work as well as explorer work."""

    async def scenario():
        """Deliver uncancellable Mainnet data after the application switches to Testnet."""
        future = asyncio.get_running_loop().create_future()

        class Provider:
            """Suppress cancellation to prove the main window also changes the watchlist generation."""

            async def get_account(self, *args):
                """Wait for the controlled late response without network access."""
                try:
                    return await asyncio.shield(future)
                except asyncio.CancelledError:
                    return await future

        store = WatchlistStore(tmp_path / "watchlists.sqlite3")
        list_id, entry_id = saved(store, Network.MAINNET)
        window = MainWindow(AccountService(Provider()), watchlist_store=store)
        window.watchlists.reload(list_id, entry_id)
        window.watchlists.start_refresh()
        task = window.watchlists.task
        await asyncio.sleep(0)
        window.network.setCurrentText("Testnet")
        await asyncio.sleep(0)
        future.set_result(account(Network.MAINNET))
        await task
        assert store.list()[0]["entries"][0]["snapshot"] is None
        assert window.watchlists.current["network"] == "Mainnet"
        window.close()

    asyncio.run(scenario())
