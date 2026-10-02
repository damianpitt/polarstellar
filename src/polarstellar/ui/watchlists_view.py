"""Offline bookmark organization with explicit explorer opening and single-resource refresh."""

import asyncio
import copy
import json
import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService
from polarstellar.storage.export import account_document, document, plain
from polarstellar.storage.watchlists import resource


class WatchlistsView(QWidget):
    """Organize public identifiers locally; selection is offline and refresh is always manual."""

    open_requested = Signal(dict)

    def __init__(self, store, provider, contract_provider, network):
        """Inject live providers, keeping cache bypass and network identity explicit.

        A watchlist can contain entries from either network. Every row shows its saved
        network; new entries use the main window's selected network. No timers poll APIs.
        """
        super().__init__()
        self.store, self.network = store, network
        self.service = AccountService(getattr(provider, "provider", provider))
        self.contract_provider = contract_provider
        self.items, self.current_list, self.current = [], None, None
        self.task, self.tasks, self.generation = None, set(), 0
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Local watchlists • Selection and saved snapshots work offline. "
            "Open in explorer and Refresh contact the entry's saved network. "
            "New entries use the network selected above. Local files are unencrypted."
        )
        notice.setWordWrap(True)
        notice.setToolTip(str(store.path))
        layout.addWidget(notice)
        library_row = QHBoxLayout()
        self.library = QComboBox()
        self.library.setAccessibleName("Watchlist")
        self.library.currentIndexChanged.connect(self.select_list)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Watchlist name (up to 100 characters)")
        self.name.setMaxLength(100)
        self.name.setAccessibleName("Watchlist name")
        library_row.addWidget(self.library)
        library_row.addWidget(self.name)
        for title, callback in (
            ("Create", self.create),
            ("Rename", self.rename),
            ("Delete list", self.delete),
        ):
            button = QPushButton(title)
            button.clicked.connect(callback)
            library_row.addWidget(button)
        layout.addLayout(library_row)
        form = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.addItems(["Account", "Asset", "Contract"])
        self.kind.setAccessibleName("Watchlist resource type")
        self.value = QLineEdit()
        self.value.setPlaceholderText("G-address, asset code, or C-address")
        self.value.setAccessibleName("Watchlist resource identifier")
        self.issuer = QLineEdit()
        self.issuer.setPlaceholderText("Asset issuer (blank for native XLM)")
        self.issuer.setAccessibleName("Watchlist asset issuer")
        self.issuer.setEnabled(False)
        self.kind.currentTextChanged.connect(lambda kind: self.issuer.setEnabled(kind == "Asset"))
        self.add_button = QPushButton("Add to list (offline)")
        self.add_button.clicked.connect(self.add)
        for widget in (self.kind, self.value, self.issuer, self.add_button):
            form.addWidget(widget)
        layout.addLayout(form)
        self.entries = QListWidget()
        self.entries.setMaximumHeight(150)
        self.entries.currentRowChanged.connect(self.select_entry)
        self.entries.itemDoubleClicked.connect(lambda *_: self.open_selected())
        layout.addWidget(self.entries)
        self.label = QLineEdit()
        self.label.setMaxLength(200)
        self.label.setPlaceholderText("Selected entry's local label (up to 200 characters)")
        self.label.setAccessibleName("Watchlist entry label")
        layout.addWidget(self.label)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText(
            "Local notes — your interpretation, not verified network facts"
        )
        self.notes.setMaximumHeight(90)
        self.notes.setAccessibleName("Watchlist entry notes")
        layout.addWidget(self.notes)
        actions = QHBoxLayout()
        self.save_button = QPushButton("Save label / notes")
        self.save_button.clicked.connect(self.save_notes)
        self.remove_button = QPushButton("Remove entry")
        self.remove_button.clicked.connect(self.remove)
        self.open_button = QPushButton("Open in explorer (network lookup)")
        self.open_button.clicked.connect(self.open_selected)
        self.refresh_button = QPushButton("Refresh saved snapshot (live)")
        self.refresh_button.clicked.connect(self.start_refresh)
        self.cancel_button = QPushButton("Cancel refresh")
        self.cancel_button.clicked.connect(self.cancel_refresh)
        for widget in (
            self.save_button,
            self.remove_button,
            self.open_button,
            self.refresh_button,
            self.cancel_button,
        ):
            actions.addWidget(widget)
        layout.addLayout(actions)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        layout.addWidget(self.evidence, 1)
        self.reload()

    def attempt(self, callback, *arguments):
        """Contain local validation/storage failures and leave previously saved data readable."""
        try:
            return True, callback(*arguments)
        except (AccountError, OSError, ValueError, KeyError, sqlite3.Error) as exc:
            self.error(str(exc))
            return False, None

    def error(self, message):
        """Show safe errors without presenting local annotations as verified facts."""
        self.status.setText(message)
        QMessageBox.warning(self, "Watchlists", message)

    def allow_discard(self):
        """Protect unsaved annotations before selection, deletion, or application shutdown."""
        if self.current is None or (
            self.label.text() == self.current["label"]
            and self.notes.toPlainText() == self.current["notes"]
        ):
            return True
        return (
            QMessageBox.question(
                self,
                "Unsaved watchlist notes",
                "Discard unsaved label and notes edits?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def reload(self, list_id=None, entry_id=None, preserve=False):
        """Re-read committed lists offline; a refresh must not erase unfinished annotation edits."""
        draft = (self.label.text(), self.notes.toPlainText()) if preserve else None
        success, items = self.attempt(self.store.list)
        if not success:
            return
        self.items = items
        self.library.blockSignals(True)
        self.library.clear()
        for item in items:
            self.library.addItem(item["name"], item["id"])
        row = next((i for i, item in enumerate(items) if item["id"] == list_id), 0 if items else -1)
        self.library.setCurrentIndex(row)
        self.library.blockSignals(False)
        self.current_list = copy.deepcopy(items[row]) if row >= 0 else None
        self.name.setText(self.current_list["name"] if self.current_list else "")
        self.entries.blockSignals(True)
        self.entries.clear()
        for entry in self.current_list["entries"] if self.current_list else []:
            self.entries.addItem(
                f"{entry['network']} • {entry['kind']} • "
                + (f"{entry['label']} • " if entry["label"] else "")
                + entry["identifier"]
            )
        entries = self.current_list["entries"] if self.current_list else []
        selected = next((i for i, entry in enumerate(entries) if entry["id"] == entry_id), -1)
        self.entries.setCurrentRow(selected)
        self.entries.blockSignals(False)
        self.display(selected)
        if draft is not None and self.current is not None:
            self.label.setText(draft[0])
            self.notes.setPlainText(draft[1])

    def select_list(self, row):
        """Switch lists only after protecting notes, and cancel requests belonging to the old list."""
        if not self.allow_discard():
            self.library.blockSignals(True)
            self.library.setCurrentIndex(self.library.findData(self.current_list["id"]))
            self.library.blockSignals(False)
            return
        self.cancel_refresh()
        self.reload(self.library.itemData(row))

    def select_entry(self, row):
        """Keep selection offline; stable IDs restore a previous selection after a refused discard."""
        if not self.allow_discard():
            previous = next(
                i
                for i, entry in enumerate(self.current_list["entries"])
                if entry["id"] == self.current["id"]
            )
            self.entries.blockSignals(True)
            self.entries.setCurrentRow(previous)
            self.entries.blockSignals(False)
            return
        self.cancel_refresh()
        self.display(row)

    def display(self, row):
        """Render saved JSON and its original provenance, without fetching anything."""
        entries = self.current_list["entries"] if self.current_list else []
        self.current = copy.deepcopy(entries[row]) if 0 <= row < len(entries) else None
        self.label.setText(self.current["label"] if self.current else "")
        self.notes.setPlainText(self.current["notes"] if self.current else "")
        snapshot = self.current.get("snapshot") if self.current else None
        self.evidence.setPlainText(
            json.dumps(snapshot, ensure_ascii=False, indent=2)
            if snapshot
            else "No saved snapshot. Choose Refresh to fetch one explicitly."
        )
        self.status.setText(
            f"{self.current['network']} • {self.current['kind']} • {self.current['identifier']}\n"
            + (
                "Saved snapshot, not current data. Refresh replaces only this latest snapshot."
                if snapshot
                else "Bookmark saved locally; never refreshed."
            )
            if self.current
            else "Create/select a list, then add or select a resource."
        )
        self.controls()

    def controls(self):
        """Enable actions only for their valid local selection and current request state."""
        for button in (self.save_button, self.remove_button, self.open_button):
            button.setEnabled(self.current is not None)
        self.refresh_button.setEnabled(self.current is not None and self.task is None)
        self.cancel_button.setEnabled(self.task is not None)
        self.add_button.setEnabled(self.current_list is not None)
        self.label.setEnabled(self.current is not None)
        self.notes.setEnabled(self.current is not None)

    def create(self):
        """Create a named local list after protecting edits to the previously selected entry."""
        if not self.allow_discard():
            return
        success, identifier = self.attempt(self.store.create, self.name.text())
        if success:
            self.cancel_refresh()
            self.reload(identifier)

    def rename(self):
        """Rename the selected list while retaining unsaved entry annotations."""
        if self.current_list is None:
            return
        identifier = self.current_list["id"]
        success, _ = self.attempt(self.store.rename, identifier, self.name.text())
        if success:
            self.reload(identifier, self.current["id"] if self.current else None, preserve=True)

    def delete(self):
        """Confirm deletion of a complete list, its notes and latest snapshots."""
        if self.current_list is None or not self.allow_discard():
            return
        if (
            QMessageBox.question(
                self,
                "Delete watchlist?",
                "Delete this list and all its entries?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.cancel_refresh()
        success, _ = self.attempt(self.store.delete, self.current_list["id"])
        if success:
            self.reload()

    def add(self):
        """Validate the typed identifier before saving; adding a bookmark never contacts an API."""
        success, identity = self.attempt(
            resource,
            self.kind.currentText().lower(),
            self.value.text(),
            self.network(),
            self.issuer.text(),
        )
        if success:
            self.add_resource(identity)

    def add_resource(self, identity):
        """Bookmark a typed or displayed resource in the selected list, retaining duplicate notes."""
        if self.current_list is None:
            self.error("Create or select a watchlist first.")
            return
        if not self.allow_discard():
            return
        identifier = self.current_list["id"]
        success, entry_id = self.attempt(self.store.add, identifier, identity)
        if success:
            self.cancel_refresh()
            self.reload(identifier, entry_id)

    def save_notes(self):
        """Explicitly commit annotations; identities and source snapshots cannot be edited here."""
        if self.current is None:
            return
        success, _ = self.attempt(
            self.store.edit,
            self.current_list["id"],
            self.current["id"],
            self.label.text(),
            self.notes.toPlainText(),
        )
        if success:
            self.reload(self.current_list["id"], self.current["id"])

    def remove(self):
        """Confirm removing just the selected entry and cancel its pending refresh first."""
        if self.current is None or not self.allow_discard():
            return
        if (
            QMessageBox.question(
                self,
                "Remove entry?",
                "Remove this entry, notes, and snapshot?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.cancel_refresh()
        success, _ = self.attempt(self.store.remove, self.current_list["id"], self.current["id"])
        if success:
            self.reload(self.current_list["id"])

    def open_selected(self):
        """Request an explicit explorer lookup on the saved network; selection itself stays offline."""
        if self.current:
            self.open_requested.emit(copy.deepcopy(self.current))

    def cancel_refresh(self):
        """Invalidate the generation as well as cancel tasks, rejecting providers that finish late."""
        self.generation += 1
        for task in self.tasks:
            task.cancel()
        self.task = None
        self.controls()
        self.status.setText("Refresh cancelled. Saved snapshots are unchanged.")

    def start_refresh(self):
        """Capture list, entry, identity and network before starting one live lookup."""
        if self.current is None or self.task is not None:
            return
        self.task = asyncio.create_task(
            self.refresh(self.current_list["id"], copy.deepcopy(self.current), self.generation)
        )
        self.tasks.add(self.task)
        self.task.add_done_callback(self.tasks.discard)
        self.status.setText(
            "Refreshing from the entry's saved network; previous snapshot retained…"
        )
        self.controls()

    async def refresh(self, list_id, entry, generation):
        """Replace the latest snapshot only after success, preserving notes and rejecting stale data."""
        try:
            network = Network(entry["network"])
            if entry["kind"] == "account":
                evidence = account_document(await self.service.lookup(entry["identifier"], network))
            elif entry["kind"] == "asset":
                detail = await self.service.asset(entry["code"], entry["issuer"], network)
                evidence = document("asset", plain(detail), [])
            else:
                snapshot = await self.contract_provider.inspect(entry["identifier"], network)
                if (snapshot["contract"], snapshot["network"]) != (
                    entry["identifier"],
                    network.value,
                ):
                    raise AccountError("Contract response does not match this watchlist entry.")
                evidence = document(
                    "contract",
                    {
                        "instance": snapshot,
                        "events_queried": False,
                        "coverage": "Contract instance only; no events loaded by watchlist refresh.",
                    },
                    [],
                )
            # Cancellation alone cannot prevent a provider from completing late. Never
            # write after selection/network changes, and never recreate a removed item.
            if generation != self.generation:
                return
            self.store.snapshot(list_id, entry["id"], evidence)
            self.reload(list_id, entry["id"], preserve=True)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 -- Keep network/storage failures inside the view.
            if generation == self.generation:
                self.status.setText(
                    (str(exc) if isinstance(exc, AccountError) else "Refresh failed.")
                    + " Previous snapshot retained; retry when ready."
                )
        finally:
            if generation == self.generation:
                self.task = None
                self.controls()
