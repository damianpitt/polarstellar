"""Offline investigation library with explicit saves, annotations, and live refresh."""

import asyncio
import copy
import json
import sqlite3

from PySide6.QtWidgets import (
    QCheckBox,
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
from polarstellar.stellar.service import AccountService
from polarstellar.storage.export import account_document, document, transaction_document
from polarstellar.storage.investigations import new_investigation
from polarstellar.ui.export_controls import ExportControls


class InvestigationsView(QWidget):
    """Keep saved evidence readable offline and distinguish local annotations from source facts."""

    def __init__(self, store, provider):
        """Build a local library; live refresh deliberately bypasses the optional response cache."""
        super().__init__()
        self.store = store
        self.service = AccountService(getattr(provider, "provider", provider))
        self.current = None
        self.items = []
        self.task = None
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Saved evidence • Offline until Refresh is chosen. Local files are unencrypted. "
            "Notes and labels are your annotations, not network facts."
        )
        notice.setWordWrap(True)
        notice.setToolTip(str(store.path))
        layout.addWidget(notice)
        self.library = QListWidget()
        self.library.setMaximumHeight(110)
        self.library.currentRowChanged.connect(self.select_row)
        layout.addWidget(self.library)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Investigation name")
        layout.addWidget(self.name)
        self.labels = QLineEdit()
        self.labels.setPlaceholderText("Local labels (comma separated)")
        layout.addWidget(self.labels)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Local notes — your interpretation, not verified facts")
        self.notes.setMaximumHeight(100)
        layout.addWidget(self.notes)
        controls = QHBoxLayout()
        for title, callback in (
            ("Create new", self.create),
            ("Save name / notes / labels", self.save_notes),
            ("Delete investigation", self.delete),
        ):
            button = QPushButton(title)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)
        self.entries = QListWidget()
        self.entries.setMaximumHeight(100)
        self.entries.currentRowChanged.connect(self.show_entry)
        layout.addWidget(self.entries)
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        layout.addWidget(self.evidence)
        self.refresh_button = QPushButton(
            "Refresh selected resource from network (keep old evidence)"
        )
        self.refresh_button.clicked.connect(self.start_refresh)
        layout.addWidget(self.refresh_button)
        self.annotations = QCheckBox("Include saved name, notes, and labels in export")
        layout.addWidget(self.annotations)
        self.export = ExportControls(self.export_document)
        layout.addWidget(self.export)
        self.reload()

    def allow_close(self):
        """Protect unsaved annotations when the application window is closed."""
        if self.current is None or (
            self.name.text() == self.current["name"]
            and self.labels.text() == self.current["labels"]
            and self.notes.toPlainText() == self.current["notes"]
        ):
            return True
        return (
            QMessageBox.question(
                self,
                "Unsaved annotations",
                "Close and discard unsaved annotation edits?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def error(self, message):
        """Report local storage or network failures without discarding displayed evidence."""
        QMessageBox.warning(self, "Investigation", message)

    def reload(self, identifier=None):
        """Reload committed state and reopen the chosen investigation without making requests."""
        draft = None
        if self.current is not None and self.current["id"] == identifier:
            draft = (self.name.text(), self.labels.text(), self.notes.toPlainText())
        try:
            self.items = self.store.list()
        except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
            self.error(str(exc))
            return
        self.library.blockSignals(True)
        self.library.clear()
        for item in self.items:
            self.library.addItem(item["name"])
        row = next((i for i, item in enumerate(self.items) if item["id"] == identifier), -1)
        self.library.setCurrentRow(row)
        self.library.blockSignals(False)
        self.open_row(row)
        # A background refresh or evidence save must not erase an unfinished note.
        if draft is not None and self.current is not None:
            self.name.setText(draft[0])
            self.labels.setText(draft[1])
            self.notes.setPlainText(draft[2])

    def select_row(self, row):
        """Require a choice before switching away from unsaved local annotations."""
        if self.current is not None and (
            self.name.text() != self.current["name"]
            or self.labels.text() != self.current["labels"]
            or self.notes.toPlainText() != self.current["notes"]
        ):
            answer = QMessageBox.question(
                self,
                "Unsaved annotations",
                "Discard unsaved edits?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self.library.blockSignals(True)
                previous = next(
                    i for i, item in enumerate(self.items) if item["id"] == self.current["id"]
                )
                self.library.setCurrentRow(previous)
                self.library.blockSignals(False)
                return
        self.open_row(row)

    def open_row(self, row):
        """Display persisted annotations and evidence; selection never refreshes remote data."""
        self.current = copy.deepcopy(self.items[row]) if 0 <= row < len(self.items) else None
        item = self.current or {}
        self.name.setText(item.get("name", ""))
        self.notes.setPlainText(item.get("notes", ""))
        self.labels.setText(item.get("labels", ""))
        self.annotations.setChecked(False)
        self.entries.clear()
        for entry in item.get("entries", []):
            self.entries.addItem(f"{entry['network']} • {entry['kind']} • {entry['identifier']}")
        self.export.setEnabled(self.current is not None)
        self.show_entry(-1)

    def create(self):
        """Create a separate investigation using the entered name; evidence is added explicitly."""
        self.commit(new_investigation(self.name.text()))

    def commit(self, item):
        """Write a candidate copy and refresh the library only after storage succeeds."""
        try:
            self.store.save(item)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.error(str(exc))
            return False
        self.reload(item["id"])
        return True

    def save_notes(self):
        """Persist user-authored annotations without modifying any saved network evidence."""
        if self.current is None:
            self.error("Create or select an investigation first.")
            return
        item = copy.deepcopy(self.current)
        item.update(
            name=self.name.text(), notes=self.notes.toPlainText(), labels=self.labels.text()
        )
        self.commit(item)

    def add_evidence(self, entry):
        """Append a frozen resource snapshot to the currently selected saved investigation."""
        if self.current is None:
            self.error(
                "Create or select an investigation, then return to the resource and save it."
            )
            return
        item = copy.deepcopy(self.current)
        item["entries"].append(copy.deepcopy(entry))
        if self.commit(item):
            self.entries.setCurrentRow(len(item["entries"]) - 1)

    def show_entry(self, row):
        """Render the full saved JSON evidence and original timestamps without executing content."""
        valid = self.current is not None and 0 <= row < len(self.current["entries"])
        refreshable = valid and self.current["entries"][row]["kind"] in (
            "account",
            "transaction",
            "asset",
        )
        self.refresh_button.setEnabled(refreshable and self.task is None)
        self.refresh_button.setToolTip(
            "Saved graph/contract evidence remains offline; open Graph or Contracts for a new snapshot."
            if valid and not refreshable
            else "Fetch a new resource snapshot while keeping old evidence."
        )
        self.evidence.setPlainText(
            json.dumps(self.current["entries"][row], ensure_ascii=False, indent=2)
            if valid
            else "Select saved evidence to inspect it offline."
        )

    def delete(self):
        """Ask before deleting a complete investigation, including its local annotations."""
        if self.current is None:
            return
        if (
            QMessageBox.question(
                self,
                "Delete investigation?",
                "Delete this investigation and its saved evidence, notes, and labels?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.store.delete(self.current["id"])
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.error(str(exc))
            return
        self.reload()

    def export_document(self):
        """Export evidence by default; include only explicitly selected, persisted annotations."""
        metadata = {"coverage": "Saved snapshots with original retrieval times; not current data."}
        if self.annotations.isChecked():
            metadata["local_annotations"] = {
                key: self.current[key] for key in ("name", "notes", "labels")
            }
        return document("investigation", metadata, copy.deepcopy(self.current["entries"]))

    def start_refresh(self):
        """Capture the selected resource and owning investigation before asynchronous I/O."""
        row = self.entries.currentRow()
        if self.task is not None or self.current is None or row < 0:
            return
        entry = copy.deepcopy(self.current["entries"][row])
        if entry["kind"] not in ("account", "transaction", "asset"):
            return
        self.task = asyncio.create_task(self.refresh(self.current["id"], entry))
        self.refresh_button.setEnabled(False)

    async def refresh(self, identifier, entry):
        """Append new evidence without replacing history or writing into another selected case."""
        try:
            network = Network(entry["network"])
            if entry["kind"] == "account":
                evidence = account_document(await self.service.lookup(entry["identifier"], network))
            elif entry["kind"] == "transaction":
                evidence = transaction_document(
                    await self.service.transaction(entry["identifier"], network)
                )
            else:
                from polarstellar.storage.export import plain

                asset = await self.service.asset(entry["code"], entry["issuer"], network)
                evidence = document("asset", plain(asset), [])
            # Re-read persisted state after I/O so intervening notes or additions survive.
            item = next((item for item in self.store.list() if item["id"] == identifier), None)
            if item is not None:
                entry["evidence"] = [evidence]
                item["entries"].append(entry)
                self.store.save(item)
                selected = self.current["id"] if self.current else None
                self.reload(selected)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 -- Preserve saved evidence on provider/storage failure.
            self.error("Refresh failed. Saved evidence is unchanged; please retry.")
        finally:
            self.task = None
            self.show_entry(self.entries.currentRow())
