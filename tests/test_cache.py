"""Exercise disk persistence and cache races using isolated temporary directories."""

import asyncio
import json
import sqlite3
from dataclasses import replace

import httpx
import pytest
from test_accounts import ADDRESS, account_data
from test_activity import payload
from test_transactions import HASH, metadata, operation

from polarstellar.stellar.activity import ActivityKind
from polarstellar.stellar.horizon import HorizonProvider, parse_account
from polarstellar.stellar.models import Network
from polarstellar.storage import codec
from polarstellar.storage.database import SnapshotCache
from polarstellar.storage.provider import CachedProvider


def test_disabled_cache_never_creates_files(tmp_path):
    """Fresh installs must not retain investigation data before the user opts in."""

    async def scenario():
        provider = CachedProvider(
            HorizonProvider(
                httpx.MockTransport(lambda r: httpx.Response(200, json=account_data()))
            ),
            SnapshotCache(tmp_path / "cache.db"),
        )
        result = await provider.get_account(ADDRESS, Network.MAINNET)
        assert result.cache_status == "Live data"
        assert not provider.cache.path.exists()

    asyncio.run(scenario())


def test_restart_exact_amounts_and_expiry(tmp_path):
    """A fresh provider can reuse disk data, but expiry forces a live refresh."""
    now = [1000.0]
    calls = []

    def handler(request):
        """Record requests to distinguish live fetches from cache hits."""
        calls.append(request)
        return httpx.Response(200, json=account_data())

    def create():
        """Simulate reopening the application against the same disk database."""
        result = CachedProvider(
            HorizonProvider(httpx.MockTransport(handler)),
            SnapshotCache(tmp_path / "cache.db", lambda: now[0]),
        )
        result.set_enabled(True)
        return result

    async def scenario():
        first = await create().get_account(ADDRESS, Network.MAINNET)
        second = await create().get_account(ADDRESS, Network.MAINNET)
        assert len(calls) == 1
        assert second.balances == first.balances
        assert second.fetched_at == first.fetched_at
        assert "Local cache" in second.cache_status
        now[0] += 60
        assert (await create().get_account(ADDRESS, Network.MAINNET)).cache_status == "Live data"
        assert len(calls) == 2
        await create().get_account(ADDRESS, Network.TESTNET)
        assert len(calls) == 3

    asyncio.run(scenario())


def test_activity_keys_include_kind_cursor_and_network(tmp_path):
    """Pagination cannot reuse a page from another list, cursor, or network."""
    calls = []

    def handler(request):
        """Return a valid page below the requested cursor."""
        calls.append(request)
        kind = ActivityKind(request.url.path.rsplit("/", 1)[-1])
        token = int(request.url.params.get("cursor", "10")) - 1
        return httpx.Response(200, json=payload([token], kind))

    async def scenario():
        provider = CachedProvider(
            HorizonProvider(httpx.MockTransport(handler)), SnapshotCache(tmp_path / "cache.db")
        )
        provider.set_enabled(True)
        for kind in ActivityKind:
            page = await provider.get_activity(ADDRESS, Network.MAINNET, kind)
            cached = await provider.get_activity(ADDRESS, Network.MAINNET, kind)
            assert cached.records == page.records
            await provider.get_activity(ADDRESS, Network.MAINNET, kind, "5")
        assert len(calls) == 6

    asyncio.run(scenario())


def test_complete_transactions_cached_partial_transactions_not_cached(tmp_path):
    """Missing operations should be retried rather than retained for 24 hours."""
    calls = []
    partial = [False]

    def handler(request):
        """Return either complete operation evidence or a temporary operations error."""
        calls.append(request)
        if request.url.path.endswith("operations"):
            return (
                httpx.Response(503)
                if partial[0]
                else httpx.Response(
                    200, json={"_embedded": {"records": [operation(1), operation(2)]}}
                )
            )
        return httpx.Response(200, json=metadata())

    async def scenario():
        provider = CachedProvider(
            HorizonProvider(httpx.MockTransport(handler)), SnapshotCache(tmp_path / "cache.db")
        )
        provider.set_enabled(True)
        first = await provider.get_transaction(HASH, Network.MAINNET)
        cached = await provider.get_transaction(HASH, Network.MAINNET)
        assert cached.operations == first.operations and len(calls) == 2
        await provider.clear()
        partial[0] = True
        for _ in range(2):
            assert (await provider.get_transaction(HASH, Network.MAINNET)).warning
        assert len(calls) == 6

    asyncio.run(scenario())


def test_clear_prevents_late_network_response_refilling_cache(tmp_path):
    """Clear must invalidate writes from requests already waiting on the network."""

    async def scenario():
        future = asyncio.get_running_loop().create_future()

        class Delayed:
            """Hold an account response until deletion has completed."""

            async def get_account(self, address, network):
                """Return a snapshot after the test releases this request."""
                await future
                return parse_account(account_data(), address, network, "fixture")

        provider = CachedProvider(Delayed(), SnapshotCache(tmp_path / "cache.db"))
        provider.set_enabled(True)
        task = asyncio.create_task(provider.get_account(ADDRESS, Network.MAINNET))
        # Let the initial cache read complete before clearing the file.
        await asyncio.sleep(0.05)
        await provider.clear()
        future.set_result(None)
        await task
        with sqlite3.connect(provider.cache.path) as connection:
            assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0

    asyncio.run(scenario())


def test_corrupt_or_unwritable_cache_does_not_block_live_data(tmp_path):
    """Storage errors should leave the remote explorer usable and display the cache failure."""
    path = tmp_path / "cache.db"
    path.write_text("not a database", encoding="utf-8")

    async def scenario():
        provider = CachedProvider(
            HorizonProvider(
                httpx.MockTransport(lambda r: httpx.Response(200, json=account_data()))
            ),
            SnapshotCache(path),
        )
        provider.set_enabled(True)
        result = await provider.get_account(ADDRESS, Network.MAINNET)
        assert result.address == ADDRESS and "cache" in result.cache_status

    asyncio.run(scenario())


def test_schema_guard_and_pruning(tmp_path):
    """Bound retained rows and preserve an unknown future schema rather than overwriting it."""
    cache = SnapshotCache(tmp_path / "cache.db")
    for index in range(255):
        cache.put(str(index), "payload")
    with sqlite3.connect(cache.path) as connection:
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 250
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(sqlite3.DatabaseError):
        cache.get("254", 60)


def test_codec_rejects_unknown_types_and_preserves_decimal(tmp_path):
    """JSON must preserve domain types without allowing arbitrary class construction."""
    snapshot = parse_account(account_data(), ADDRESS, Network.TESTNET, "fixture")
    assert codec.loads(codec.dumps(snapshot)) == snapshot
    encoded = json.loads(codec.dumps(snapshot))
    encoded["fields"]["sequence"] = 23
    with pytest.raises(ValueError):
        codec.loads(json.dumps(encoded))
    with pytest.raises(KeyError):
        codec.loads('{"type":"ExecuteCode","fields":{}}')
    # Labels are metadata only and never change the original source or timestamp.
    assert replace(snapshot, cache_status="Local cache").source == "fixture"


def test_cancelled_disk_write_finishes_before_clear(tmp_path):
    """A cancelled worker thread must not repopulate the database after deletion returns."""
    import threading

    entered, release = threading.Event(), threading.Event()

    class SlowCache(SnapshotCache):
        """Pause a write to make the cancellation/deletion ordering deterministic."""

        def put(self, key, payload):
            """Wait for the test before storing the already-started snapshot."""
            entered.set()
            release.wait(3)
            super().put(key, payload)

    async def scenario():
        provider = CachedProvider(
            HorizonProvider(
                httpx.MockTransport(lambda r: httpx.Response(200, json=account_data()))
            ),
            SlowCache(tmp_path / "cache.db"),
        )
        provider.set_enabled(True)
        lookup = asyncio.create_task(provider.get_account(ADDRESS, Network.MAINNET))
        assert await asyncio.to_thread(entered.wait, 2)
        lookup.cancel()
        clearing = asyncio.create_task(provider.clear())
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await lookup
        await clearing
        with sqlite3.connect(provider.cache.path) as connection:
            assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0

    asyncio.run(scenario())


def test_cache_controls_toggle_and_clear(tmp_path):
    """The visible controls keep consent explicit and report completed deletion."""
    import os

    from PySide6.QtWidgets import QApplication

    from polarstellar.ui.cache_controls import CacheControls

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])

    async def scenario():
        provider = CachedProvider(
            HorizonProvider(
                httpx.MockTransport(lambda r: httpx.Response(200, json=account_data()))
            ),
            SnapshotCache(tmp_path / "cache.db"),
        )
        invalidations, tasks = [], set()
        controls = CacheControls(provider, lambda: invalidations.append(True), tasks)
        assert not controls.enabled.isChecked()
        controls.enabled.setChecked(True)
        await provider.get_account(ADDRESS, Network.MAINNET)
        controls.enabled.setChecked(False)
        assert provider.cache.path.exists() and not provider.enabled
        controls.start_clear()
        await asyncio.gather(*tasks)
        assert controls.status.text() == "Local snapshots cleared"
        assert len(invalidations) == 3
        controls.close()

    asyncio.run(scenario())
    app.processEvents()
