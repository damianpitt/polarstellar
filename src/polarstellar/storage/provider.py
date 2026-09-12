"""Optional caching around provider calls, without changing investigation services."""

import asyncio
import json
import sqlite3
from dataclasses import replace
from decimal import InvalidOperation

from polarstellar.stellar.activity import ActivityPage
from polarstellar.stellar.models import Account
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.transaction import TransactionDetail
from polarstellar.storage import codec


class CachedProvider:
    """Reuse fresh snapshots, preserving network, query, cursor, and original provenance."""

    def __init__(self, provider, cache):
        """Start disabled; the UI must explicitly enable persistent caching each session."""
        self.provider = provider
        self.cache = cache
        self.enabled = False
        self.epoch = 0
        self.lock = asyncio.Lock()
        self.error = ""

    def set_enabled(self, enabled: bool):
        """Invalidate pending cache writes when the user changes the caching preference."""
        self.epoch += 1
        self.enabled = enabled
        self.error = ""

    async def disk(self, function, *args):
        """Keep the caller's lock until a disk operation finishes, even during cancellation.

        Cancelling asyncio.to_thread does not stop its underlying thread. Waiting for that
        thread prevents a delayed write from occurring after the user clears the cache.
        """
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def clear(self):
        """Serialize deletion after pending disk work and prevent old requests refilling it."""
        self.epoch += 1
        async with self.lock:
            await self.disk(self.cache.clear)
        self.error = ""

    def valid(self, snapshot, method, args):
        """Reject a wrong resource or network before it can be returned from or written to disk."""
        expected = {
            "get_account": Account,
            "get_activity": ActivityPage,
            "get_transaction": TransactionDetail,
        }[method]
        identity = "hash" if method == "get_transaction" else "address"
        return (
            isinstance(snapshot, expected)
            and getattr(snapshot, identity) == args[0]
            and snapshot.network == args[1]
            and (method != "get_activity" or snapshot.kind == args[2])
        )

    async def fetch(self, method: str, args: tuple, ttl: float):
        """Read a fresh snapshot or fetch remotely; cache problems never break a live lookup."""
        epoch = self.epoch
        warning = ""
        # Format revision and provider class isolate incompatible cached representations.
        key = json.dumps(
            [1, type(self.provider).__module__, type(self.provider).__name__, method, *args]
        )
        if self.enabled:
            try:
                async with self.lock:
                    payload = await self.disk(self.cache.get, key, ttl)
                if payload is not None and self.enabled and epoch == self.epoch:
                    snapshot = codec.loads(payload)
                    if not self.valid(snapshot, method, args):
                        raise ValueError("Invalid snapshot")
                    return replace(
                        snapshot, cache_status="Local cache • original retrieval time shown"
                    )
            except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, InvalidOperation):
                warning = "Cache unavailable or unreadable; using live data."
        # Errors and cancelled requests are never stored. The wrapped service still validates identity.
        snapshot = await getattr(self.provider, method)(*args)
        if not self.valid(snapshot, method, args):
            raise AccountError("Provider result does not match this search and network.")
        if self.enabled and epoch == self.epoch and not getattr(snapshot, "warning", ""):
            try:
                payload = codec.dumps(snapshot)
                async with self.lock:
                    # Recheck after waiting: Clear cache may have run while this request was waiting.
                    if self.enabled and epoch == self.epoch:
                        await self.disk(self.cache.put, key, payload)
            except (OSError, sqlite3.Error, ValueError, TypeError):
                warning = "Live data loaded, but the disk cache could not be updated."
        return replace(snapshot, cache_status=warning or "Live data")

    async def get_account(self, address, network):
        """Reuse an account snapshot for at most 60 seconds."""
        return await self.fetch("get_account", (address, network), 60)

    async def get_activity(self, address, network, kind, cursor=None):
        """Cache each activity page separately for 60 seconds, including its exact cursor."""
        return await self.fetch("get_activity", (address, network, kind, cursor), 60)

    async def get_transaction(self, hash_value, network):
        """Cache complete transaction details for one day; partial operations are retried live."""
        return await self.fetch("get_transaction", (hash_value, network), 86400)
