"""Versioned local investigations, independent of expiring network caches."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class InvestigationStore:
    """Persist named investigations atomically; evidence is JSON, never executable objects."""

    def __init__(self, path):
        """Remember the private application-data path without creating a file yet."""
        self.path = Path(path)

    def connect(self):
        """Open schema version 1 and refuse unknown future schemas rather than overwrite them."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            connection.close()
            raise ValueError("Unsupported investigation database version")
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS investigations (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        connection.execute("PRAGMA user_version=1")
        connection.commit()
        return connection

    def list(self):
        """Read saved investigations without creating storage on an unused installation."""
        if not self.path.exists():
            return []
        with closing(self.connect()) as connection:
            return [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT payload FROM investigations ORDER BY rowid DESC"
                )
            ]

    def save(self, investigation):
        """Commit a complete investigation in one transaction, preserving the old row on failure."""
        if not investigation["name"].strip():
            raise ValueError("Give the investigation a name.")
        payload = json.dumps(investigation, ensure_ascii=False, allow_nan=False)
        if len(payload.encode("utf-8")) > 20_000_000:
            raise ValueError(
                "Investigation exceeds the 20 MB limit. Create a separate investigation."
            )
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO investigations VALUES (?, ?)",
                (investigation["id"], payload),
            )

    def delete(self, identifier):
        """Delete only the selected investigation; cache files and exported files are untouched."""
        with closing(self.connect()) as connection, connection:
            connection.execute("DELETE FROM investigations WHERE id=?", (identifier,))


def new_investigation(name):
    """Create a named local workspace with annotations explicitly separate from evidence."""
    return {
        "id": str(uuid4()),
        "name": name.strip(),
        "created_at": datetime.now(UTC).isoformat(),
        "notes": "",
        "labels": "",
        "entries": [],
    }
