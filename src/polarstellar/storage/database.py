"""Small bounded SQLite cache, opened only when the user enables disk caching."""

import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path


class SnapshotCache:
    """Own schema creation, expiry, and deletion independently of UI and network code."""

    def __init__(self, path: Path, clock=time.time):
        """Remember the cache location without creating any files yet."""
        self.path = path
        self.clock = clock
        self.lock = threading.RLock()

    def connect(self):
        """Open a short-lived connection and initialize schema version 1 if necessary."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=1)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise sqlite3.DatabaseError("Unsupported cache schema version")
            # Bound retained data to snapshots rather than an unbounded investigation archive.
            connection.execute("PRAGMA secure_delete=ON")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS snapshots "
                "(key TEXT PRIMARY KEY, payload TEXT NOT NULL, stored REAL NOT NULL)"
            )
            connection.execute("PRAGMA user_version=1")
            connection.commit()
            return connection
        except Exception:
            connection.close()
            raise

    def get(self, key: str, ttl: float) -> str | None:
        """Read only fresh entries; an expired snapshot is never used as an offline fallback."""
        with self.lock, closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT payload, stored FROM snapshots WHERE key=?", (key,)
            ).fetchone()
            if row is None:
                return None
            age = self.clock() - row[1]
            if not 0 <= age < ttl:
                connection.execute("DELETE FROM snapshots WHERE key=?", (key,))
                connection.commit()
                return None
            return row[0]

    def put(self, key: str, payload: str):
        """Store a snapshot and prune old entries; SQL parameters keep identifiers as data."""
        # Do not allow a large transaction payload to grow the local cache without bounds.
        if len(payload.encode("utf-8")) > 2_000_000:
            return
        with self.lock, closing(self.connect()) as connection:
            now = self.clock()
            connection.execute("DELETE FROM snapshots WHERE stored < ?", (now - 86400,))
            connection.execute(
                "INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?)", (key, payload, now)
            )
            connection.execute(
                "DELETE FROM snapshots WHERE key IN "
                "(SELECT key FROM snapshots ORDER BY stored DESC LIMIT -1 OFFSET 250)"
            )
            connection.commit()

    def clear(self):
        """Remove all cached rows and compact the database; leave unrelated files untouched."""
        with self.lock:
            if not self.path.exists():
                return
            with closing(self.connect()) as connection:
                connection.execute("DELETE FROM snapshots")
                connection.commit()
                connection.execute("VACUUM")
