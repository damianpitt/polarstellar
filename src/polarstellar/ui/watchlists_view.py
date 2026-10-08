"""Offline bookmark organization with explicit explorer opening and single-resource refresh."""

import asyncio
import copy
import json
import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService
from polarstellar.storage.export import account_document, document, plain, write_export
from polarstellar.storage.watchlist_transfer import matches, portable, read_file
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
        self.visible_entries = []
        self.last_filters = ("", "All networks", "All types")
        self.task, self.tasks, self.generation = None, set(), 0
        # Filters and transfer controls add useful height. A scrollable panel keeps
        # every action reachable on smaller displays instead of compressing widgets
        # below their minimum size or drawing one editor over another.
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        root.addWidget(scroll)
        layout = QVBoxLayout(content)
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
        filter_row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setMaxLength(200)
        self.query.setPlaceholderText("Search saved identifier, issuer, label or notes")
        self.query.setAccessibleName("Watchlist text filter")
        self.filter_network = QComboBox()
        self.filter_network.addItems(["All networks", "Mainnet", "Testnet"])
        self.filter_network.setAccessibleName("Watchlist network filter")
        self.filter_kind = QComboBox()
        self.filter_kind.addItems(["All types", "Account", "Asset", "Contract"])
        self.filter_kind.setAccessibleName("Watchlist type filter")
        for widget in (self.query, self.filter_network, self.filter_kind):
            filter_row.addWidget(widget)
        layout.addLayout(filter_row)
        self.filter_count = QLabel()
        layout.addWidget(self.filter_count)
        self.entries = QListWidget()
        self.entries.setMinimumHeight(100)
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
        transfer = QHBoxLayout()
        self.include_annotations = QCheckBox("Include saved list name, labels and notes")
        self.include_annotations.setToolTip(
            "Unchecked: export public identities only. Snapshots are never exported."
        )
        self.filtered_export = QCheckBox("Export visible entries only")
        self.filtered_export.setToolTip(
            "Unchecked: export the entire selected list, regardless of filters."
        )
        self.export_button = QPushButton("Export list JSON")
        self.export_button.clicked.connect(self.export_file)
        self.import_button = QPushButton("Import list JSON")
        self.import_button.clicked.connect(self.import_file)
        for widget in (
            self.include_annotations,
            self.filtered_export,
            self.export_button,
            self.import_button,
        ):
            transfer.addWidget(widget)
        layout.addLayout(transfer)
        self.query.textChanged.connect(self.apply_filters)
        self.filter_network.currentTextChanged.connect(self.apply_filters)
        self.filter_kind.currentTextChanged.connect(self.apply_filters)
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        layout.addWidget(self.evidence, 1)
        self.reload()

    def attempt(self, callback, *arguments):
        """Contain local validation/storage failures and leave previously saved data readable."""
        try:
            return True, callback(*arguments)
        except (AccountError, OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
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
        previous_list = self.current_list["id"] if self.current_list else None
        self.current_list = copy.deepcopy(items[row]) if row >= 0 else None
        if previous_list != (self.current_list["id"] if self.current_list else None):
            # Privacy choices apply to one selected list, never silently carry across lists.
            self.include_annotations.setChecked(False)
            self.filtered_export.setChecked(False)
        if preserve and entry_id and self.current_list:
            selected = next(
                (entry for entry in self.current_list["entries"] if entry["id"] == entry_id), None
            )
            if selected is not None and not matches(selected, *self.filter_values()):
                # A concurrent saved annotation edit may stop matching the filter. Keep
                # the selected draft visible instead of erasing it when refresh reloads.
                self.set_filters(("", "All networks", "All types"))
        self.name.setText(self.current_list["name"] if self.current_list else "")
        self.populate(entry_id)
        if draft is not None and self.current is not None:
            self.label.setText(draft[0])
            self.notes.setPlainText(draft[1])

    def filter_values(self):
        """Capture combined local filters; their network selector is independent of explorer network."""
        return self.query.text(), self.filter_network.currentText(), self.filter_kind.currentText()

    def set_filters(self, values):
        """Restore rejected filters without recursive signals, preserving an unsaved selected draft."""
        for widget in (self.query, self.filter_network, self.filter_kind):
            widget.blockSignals(True)
        self.query.setText(values[0])
        self.filter_network.setCurrentText(values[1])
        self.filter_kind.setCurrentText(values[2])
        for widget in (self.query, self.filter_network, self.filter_kind):
            widget.blockSignals(False)
        self.last_filters = values

    def populate(self, entry_id=None):
        """Map visible positions to stable entry IDs; hidden rows remain in committed storage."""
        entries = self.current_list["entries"] if self.current_list else []
        self.visible_entries = [entry for entry in entries if matches(entry, *self.filter_values())]
        self.entries.blockSignals(True)
        self.entries.clear()
        for entry in self.visible_entries:
            self.entries.addItem(
                f"{entry['network']} • {entry['kind']} • "
                + (f"{entry['label']} • " if entry["label"] else "")
                + entry["identifier"]
            )
        selected = next(
            (i for i, entry in enumerate(self.visible_entries) if entry["id"] == entry_id), -1
        )
        self.entries.setCurrentRow(selected)
        self.entries.blockSignals(False)
        self.filter_count.setText(
            f"Showing {len(self.visible_entries)} of {len(entries)} saved entries • Offline filters"
        )
        self.display(selected)

    def apply_filters(self, *_):
        """Filter offline while retaining selected drafts/refreshes unless the selected entry is hidden."""
        values = self.filter_values()
        selected = self.current["id"] if self.current else None
        entries = self.current_list["entries"] if self.current_list else []
        remains = any(entry["id"] == selected and matches(entry, *values) for entry in entries)
        if selected and not remains:
            if not self.allow_discard():
                self.set_filters(self.last_filters)
                return
            self.cancel_refresh()
        draft = (self.label.text(), self.notes.toPlainText()) if remains else None
        self.last_filters = values
        self.populate(selected)
        if draft is not None and self.current:
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
        self.include_annotations.setChecked(False)
        self.filtered_export.setChecked(False)
        self.reload(self.library.itemData(row))

    def select_entry(self, row):
        """Keep selection offline; stable IDs restore a previous selection after a refused discard."""
        if not self.allow_discard():
            previous = next(
                i
                for i, entry in enumerate(self.visible_entries)
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
        entries = self.visible_entries
        self.current = copy.deepcopy(entries[row]) if 0 <= row < len(entries) else None
        self.label.setText(self.current["label"] if self.current else "")
        self.notes.setPlainText(self.current["notes"] if self.current else "")
        snapshot = self.current.get("snapshot") if self.current else None
        # An empty evidence panel needlessly crowds bookmark rows and filter controls.
        # The status already explains that a never-refreshed bookmark has no snapshot.
        self.evidence.setVisible(bool(snapshot))
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
        self.export_button.setEnabled(self.current_list is not None)
        self.label.setEnabled(self.current is not None)
        self.notes.setEnabled(self.current is not None)

    def create(self):
        """Create a named local list after protecting edits to the previously selected entry."""
        if not self.allow_discard():
            return
        success, identifier = self.attempt(self.store.create, self.name.text())
        if success:
            self.cancel_refresh()
            self.set_filters(("", "All networks", "All types"))
            self.include_annotations.setChecked(False)
            self.filtered_export.setChecked(False)
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
            self.set_filters(("", "All networks", "All types"))
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

    def export_document(self):
        """Re-read saved state and freeze the chosen scope; drafts, snapshots and search text stay local."""
        if self.current_list is None:
            raise ValueError("Select a watchlist first.")
        item = next(
            (item for item in self.store.list() if item["id"] == self.current_list["id"]), None
        )
        if item is None:
            raise ValueError("This watchlist no longer exists.")
        entries = (
            [entry for entry in item["entries"] if matches(entry, *self.filter_values())]
            if self.filtered_export.isChecked()
            else None
        )
        return portable(
            item, entries, self.include_annotations.isChecked(), self.filtered_export.isChecked()
        )

    def export_file(self):
        """Write portable JSON atomically after a file choice; cancellation never changes the library."""
        success, payload = self.attempt(self.export_document)
        if not success:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export portable watchlist", "polarstellar-watchlist.json", "JSON (*.json)"
        )
        if not filename:
            return
        path = Path(filename)
        if not path.suffix:
            path = path.with_suffix(".json")
            if (
                path.exists()
                and QMessageBox.question(
                    self,
                    "Replace file?",
                    "Replace the existing JSON file?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
        success, _ = self.attempt(write_export, path, payload, "json")
        if success:
            self.status.setText(
                f"Exported {len(payload['watchlist']['entries'])} bookmarks; snapshots excluded. "
                + (
                    "Saved annotations included."
                    if payload["annotations_included"]
                    else "List name, labels and notes omitted."
                )
            )

    def import_file(self):
        """Validate a bounded portable file, then create a new list offline without merging old data."""
        if not self.allow_discard():
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "Import portable watchlist", "", "JSON (*.json);;All files (*)"
        )
        if not filename:
            return
        success, payload = self.attempt(read_file, filename)
        if not success:
            return
        success, identifier = self.attempt(self.store.import_portable, payload)
        if success:
            self.cancel_refresh()
            self.set_filters(("", "All networks", "All types"))
            self.include_annotations.setChecked(False)
            self.filtered_export.setChecked(False)
            self.reload(identifier)
            self.status.setText(
                f"Imported {len(payload['watchlist']['entries'])} bookmarks into a separate list. "
                "Imported annotations are local interpretations. No snapshots or network requests."
            )

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
