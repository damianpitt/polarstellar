"""Local, network-scoped resource bookmarks, separate from investigations and caches."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from polarstellar.stellar.assets import validate_asset
from polarstellar.stellar.contracts import validate_contract
from polarstellar.stellar.models import Network
from polarstellar.stellar.service import validate_account


def resource(kind, value, network, issuer=""):
    """Validate a bookmark without I/O; asset identity always includes its exact issuer.

    Native XLM has no issuer. An issued asset named XLM is a different identity.
    Labels, notes and snapshots are deliberately excluded from resource identity.
    """
    network = Network(network).value
    item = {"kind": kind, "network": network}
    if kind == "account":
        item["identifier"] = validate_account(value)
    elif kind == "contract":
        item["identifier"] = validate_contract(value)
    elif kind == "asset":
        code, issuer = validate_asset(value, issuer)
        item.update(code=code, issuer=issuer, identifier=code + (":" + issuer if issuer else ""))
    else:
        raise ValueError("Choose an account, asset, or contract.")
    return item


def text(value, limit, title, required=False):
    """Bound user annotations so one bookmark cannot create an unexpectedly large database."""
    value = value.strip()
    if (required and not value) or len(value) > limit:
        raise ValueError(f"{title} must contain {'1' if required else '0'}–{limit} characters.")
    return value


class WatchlistStore:
    """Persist complete lists atomically, keeping the latest snapshot rather than evidence history.

    Limits are 100 lists, 500 entries per list, and 10 MB of JSON per list. Existing
    investigations are never migrated or modified. Unknown future schemas are refused.
    """

    def __init__(self, path):
        """Remember the application-data path; unused watchlists do not create a database."""
        self.path = Path(path)

    def connect(self):
        """Open schema 1, or fail without changing an unsupported database."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            connection.close()
            raise ValueError("Unsupported watchlist database version")
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS watchlists (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        connection.execute("PRAGMA user_version=1")
        connection.commit()
        return connection

    def list(self):
        """Read committed lists offline, newest first, without creating an unused file."""
        if not self.path.exists():
            return []
        with closing(self.connect()) as connection:
            return [
                json.loads(row[0])
                for row in connection.execute("SELECT payload FROM watchlists ORDER BY rowid DESC")
            ]

    def write(self, connection, item):
        """Write bounded JSON in the caller's transaction; failures leave the previous row intact."""
        payload = json.dumps(item, ensure_ascii=False, allow_nan=False)
        if len(payload.encode("utf-8")) > 10_000_000:
            raise ValueError("Watchlist exceeds 10 MB. Remove snapshots or use another list.")
        connection.execute(
            "INSERT INTO watchlists VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (item["id"], payload),
        )

    def create(self, name):
        """Create a named empty list locally; no remote resource is looked up."""
        item = {
            "id": str(uuid4()),
            "name": text(name, 100, "List name", True),
            "created_at": datetime.now(UTC).isoformat(),
            "entries": [],
        }
        with closing(self.connect()) as connection, connection:
            # Acquire the write lock before reading quota state. SQLite's implicit
            # transaction otherwise begins only at INSERT, allowing competing creates.
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT COUNT(*) FROM watchlists").fetchone()[0] >= 100:
                raise ValueError("The watchlist limit is 100. Remove a list first.")
            self.write(connection, item)
        return item["id"]

    def update(self, identifier, change):
        """Re-read then modify one list atomically, preserving edits made during network I/O."""
        with closing(self.connect()) as connection, connection:
            # Lock before SELECT as well as UPDATE, so another local process cannot
            # commit notes between our read and write. Busy/error cases retain old data.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM watchlists WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                raise ValueError("This watchlist no longer exists.")
            item = json.loads(row[0])
            result = change(item)
            self.write(connection, item)
            return result

    def import_portable(self, payload):
        """Validate all bookmarks, then atomically create a separate list with fresh local IDs.

        No old lists are merged or replaced, and snapshots are never accepted from a
        portable file. Validation completes before storage opens. Quota/write failures
        roll back the entire new list instead of leaving a partially imported library.
        """
        from polarstellar.storage.watchlist_transfer import validate

        name, entries = validate(payload)
        created = datetime.now(UTC).isoformat()
        item = {
            "id": str(uuid4()),
            "name": name,
            "created_at": created,
            "entries": [
                {**entry, "id": str(uuid4()), "created_at": created, "snapshot": None}
                for entry in entries
            ],
        }
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT COUNT(*) FROM watchlists").fetchone()[0] >= 100:
                raise ValueError("The watchlist limit is 100. Remove a list before importing.")
            self.write(connection, item)
        return item["id"]

    def rename(self, identifier, name):
        """Rename a list without changing its resources, annotations or snapshots."""
        name = text(name, 100, "List name", True)
        self.update(identifier, lambda item: item.update(name=name))

    def delete(self, identifier):
        """Delete one entire list; exported files, investigations and the cache are unaffected."""
        with closing(self.connect()) as connection, connection:
            connection.execute("DELETE FROM watchlists WHERE id=?", (identifier,))

    def add(self, identifier, identity):
        """Add validated identity once per list/network; duplicates preserve all saved annotations."""
        identity = resource(
            identity["kind"],
            identity.get("code", identity["identifier"]),
            identity["network"],
            identity.get("issuer", ""),
        )

        def append(item):
            """Deduplicate using kind, full identity and network before assigning a new entry ID."""
            for entry in item["entries"]:
                if all(entry[key] == identity[key] for key in ("kind", "identifier", "network")):
                    return entry["id"]
            if len(item["entries"]) >= 500:
                raise ValueError("Each watchlist supports 500 entries. Use another list.")
            entry = {
                **identity,
                "id": str(uuid4()),
                "label": "",
                "notes": "",
                "created_at": datetime.now(UTC).isoformat(),
                "snapshot": None,
            }
            item["entries"].append(entry)
            return entry["id"]

        return self.update(identifier, append)

    def edit(self, identifier, entry_id, label, notes):
        """Save local annotations while keeping immutable identity and any refreshed snapshot."""
        label, notes = text(label, 200, "Label"), text(notes, 10_000, "Notes")

        def annotate(item):
            """Edit only the requested entry in freshly committed state."""
            self.entry(item, entry_id).update(label=label, notes=notes)

        self.update(identifier, annotate)

    def remove(self, identifier, entry_id):
        """Remove one bookmark and its latest snapshot without affecting other lists."""

        def remove_entry(item):
            """Require an existing entry so a stale UI cannot silently remove another resource."""
            item["entries"].remove(self.entry(item, entry_id))

        self.update(identifier, remove_entry)

    def snapshot(self, identifier, entry_id, evidence):
        """Replace only the latest snapshot; re-reading protects concurrent notes and deletions.

        This is a convenience bookmark snapshot, not immutable investigation history.
        Source/retrieval times belong to the evidence document and remain unchanged.
        """

        def replace(item):
            """Write into the original entry only; never recreate a deleted bookmark."""
            self.entry(item, entry_id)["snapshot"] = evidence

        self.update(identifier, replace)

    @staticmethod
    def entry(item, identifier):
        """Find a stable entry ID or report deletion; list positions are never used as identities."""
        entry = next((entry for entry in item["entries"] if entry["id"] == identifier), None)
        if entry is None:
            raise ValueError("This watchlist entry no longer exists.")
        return entry
