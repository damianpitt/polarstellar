"""Offline investigation library with explicit saves, annotations, and live refresh."""

import asyncio
import copy
import json
import sqlite3

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
from polarstellar.stellar.service import AccountService
from polarstellar.storage.export import account_document, document, transaction_document
from polarstellar.storage.investigation_comparison import PRIMARY_KIND
from polarstellar.storage.investigations import matches_investigation, new_investigation
from polarstellar.ui.export_controls import ExportControls
from polarstellar.ui.investigation_comparison_dialog import InvestigationComparisonDialog

DEFAULT_FILTERS = ("", "", None)


class InvestigationsView(QWidget):
    """Keep saved evidence readable offline and distinguish local annotations from source facts."""

    def __init__(self, store, provider):
        """Build a local library; live refresh deliberately bypasses the optional response cache."""
        super().__init__()
        self.store = store
        self.service = AccountService(getattr(provider, "provider", provider))
        self.current = None
        self.items = []
        self.visible_items = []
        self.filters = DEFAULT_FILTERS
        self.task = None
        # A smaller window should scroll the library instead of clipping input
        # fields or shrinking the evidence viewer below a readable height.
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
            "Saved evidence • Offline until Refresh is chosen. Local files are unencrypted. "
            "Notes and labels are your annotations, not network facts."
        )
        notice.setWordWrap(True)
        notice.setToolTip(str(store.path))
        layout.addWidget(notice)
        filters = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Name, label, or resource")
        self.query.setAccessibleName("Search saved investigations")
        self.query.setToolTip(
            "Search saved case names, labels, addresses, hashes, codes, or issuers."
        )
        self.query.returnPressed.connect(self.apply_filters)
        filters.addWidget(self.query, 2)
        self.label_filter = QLineEdit()
        self.label_filter.setPlaceholderText("Exact label")
        self.label_filter.setAccessibleName("Filter investigations by exact label")
        self.label_filter.setToolTip("Match one complete saved label, ignoring case.")
        self.label_filter.setMinimumWidth(94)
        self.label_filter.returnPressed.connect(self.apply_filters)
        filters.addWidget(self.label_filter, 1)
        self.network_filter = QComboBox()
        self.network_filter.setAccessibleName("Filter investigations by saved network")
        self.network_filter.addItem("All networks", None)
        for network in Network:
            self.network_filter.addItem(network.value, network.value)
        filters.addWidget(self.network_filter)
        for title, callback in (
            ("Apply filters", self.apply_filters),
            ("Clear filters", self.clear_filters),
        ):
            button = QPushButton(title)
            button.clicked.connect(callback)
            filters.addWidget(button)
        layout.addLayout(filters)
        self.filter_count = QLabel()
        self.filter_count.setWordWrap(True)
        layout.addWidget(self.filter_count)
        self.library = QListWidget()
        self.library.setMinimumHeight(76)
        self.library.setMaximumHeight(94)
        self.library.currentRowChanged.connect(self.select_row)
        layout.addWidget(self.library)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Investigation name")
        self.labels = QLineEdit()
        self.labels.setPlaceholderText("Local labels (comma separated)")
        annotation_fields = QHBoxLayout()
        annotation_fields.addWidget(self.name)
        annotation_fields.addWidget(self.labels)
        layout.addLayout(annotation_fields)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Local notes — your interpretation, not verified facts")
        self.notes.setMinimumHeight(64)
        self.notes.setMaximumHeight(80)
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
        self.entries.setMinimumHeight(76)
        self.entries.setMaximumHeight(100)
        self.entries.currentRowChanged.connect(self.show_entry)
        layout.addWidget(self.entries)
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        self.evidence.setMinimumHeight(116)
        layout.addWidget(self.evidence, 1)
        self.refresh_button = QPushButton(
            "Refresh selected resource from network (keep old evidence)"
        )
        self.refresh_button.clicked.connect(self.start_refresh)
        layout.addWidget(self.refresh_button)
        self.compare_button = QPushButton("Compare selected snapshot (offline)")
        self.compare_button.clicked.connect(self.open_comparison)
        layout.addWidget(self.compare_button)
        self.annotations = QCheckBox("Include saved name, notes, and labels in export")
        layout.addWidget(self.annotations)
        self.export = ExportControls(self.export_document)
        layout.addWidget(self.export)
        self.reload()

    def allow_close(self):
        """Protect unsaved annotations when the application window is closed."""
        return self.allow_discard("Close and discard unsaved annotation edits?")

    def draft_annotations(self):
        """Read the editable fields without committing them to the saved investigation."""
        return {
            "name": self.name.text(),
            "labels": self.labels.text(),
            "notes": self.notes.toPlainText(),
        }

    def unsaved_annotations(self):
        """Return only edited fields so a reload can display newer values in other fields."""
        if self.current is None:
            return {}
        return {
            key: value
            for key, value in self.draft_annotations().items()
            if value != self.current[key]
        }

    def allow_discard(self, message):
        """Require explicit consent before a user action replaces an unsaved draft."""
        if not self.unsaved_annotations():
            return True
        return (
            QMessageBox.question(
                self,
                "Unsaved annotations",
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def error(self, message):
        """Report local storage or network failures without discarding displayed evidence."""
        QMessageBox.warning(self, "Investigation", message)

    def matching_items(self, filters):
        """Apply the combined library filters to the committed cases already loaded in memory."""
        return [item for item in self.items if matches_investigation(item, *filters)]

    def show_filter_controls(self, filters):
        """Display the active values after clearing filters or declining a change."""
        query, label, network = filters
        self.query.setText(query)
        self.label_filter.setText(label)
        self.network_filter.setCurrentIndex(
            0 if network is None else self.network_filter.findData(network)
        )

    def visible_row(self, identifier):
        """Find a case by its saved ID even when its visible row has moved."""
        return next(
            (row for row, item in enumerate(self.visible_items) if item["id"] == identifier), -1
        )

    def populate_library(self, visible, identifier, filters_reset=False):
        """Rebuild the list without triggering row handlers while its indexes are being replaced."""
        self.visible_items = visible
        self.library.blockSignals(True)
        self.library.clear()
        for item in visible:
            self.library.addItem(item["name"])
        row = self.visible_row(identifier)
        self.library.setCurrentRow(row)
        self.library.blockSignals(False)
        count = f"Showing {len(visible)} of {len(self.items)} saved investigations"
        if filters_reset:
            count += " • Filters cleared to keep the selected investigation visible."
        elif self.items and not visible:
            count += " • Clear filters to see all investigations."
        self.filter_count.setText(count)
        return row

    def apply_filters(self):
        """Apply the controls together so typing a search cannot repeatedly interrupt a draft."""
        self.change_filters(
            (self.query.text(), self.label_filter.text(), self.network_filter.currentData())
        )

    def clear_filters(self):
        """Restore the full saved library without performing a network lookup."""
        self.change_filters(DEFAULT_FILTERS)

    def change_filters(self, filters):
        """Keep the current editor intact unless the selected case would be hidden."""
        identifier = self.current["id"] if self.current else None
        visible = self.matching_items(filters)
        hides_selected = identifier is not None and not any(
            item["id"] == identifier for item in visible
        )
        if hides_selected:
            if not self.allow_discard(
                "These filters hide the selected investigation. Discard its unsaved edits?"
            ):
                self.show_filter_controls(self.filters)
                return
            # A live refresh can reload the library while the confirmation is open.
            visible = self.matching_items(filters)
        self.filters = filters
        self.show_filter_controls(filters)
        row = self.populate_library(visible, identifier)
        if identifier is not None and row < 0:
            self.open_row(-1)

    def reload(self, identifier=None, reveal=False):
        """Read committed state again, preserving filters and drafts when possible."""
        draft = {}
        evidence_row = -1
        if self.current is not None and self.current["id"] == identifier:
            evidence_row = self.entries.currentRow()
            draft = self.unsaved_annotations()
        try:
            self.items = self.store.list()
        except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
            self.error(str(exc))
            return
        visible = self.matching_items(self.filters)
        # Creating a case or saving its new name/labels can make it stop matching.
        # Reveal an explicitly saved case or an existing draft, never a different case.
        reset_filters = (
            (reveal or draft)
            and any(item["id"] == identifier for item in self.items)
            and not any(item["id"] == identifier for item in visible)
        )
        if reset_filters:
            self.filters = DEFAULT_FILTERS
            self.show_filter_controls(self.filters)
            visible = self.items
        row = self.populate_library(visible, identifier, reset_filters)
        self.open_row(row)
        if self.current is not None:
            self.entries.setCurrentRow(evidence_row)
            # Keep only actual drafts. A clean editor should display the latest
            # committed annotations after a background refresh reloads the case.
            if draft:
                annotations = {**self.current, **draft}
                self.name.setText(annotations["name"])
                self.labels.setText(annotations["labels"])
                self.notes.setPlainText(annotations["notes"])

    def select_row(self, row):
        """Switch by saved ID, even if a refresh reorders rows during the confirmation."""
        identifier = self.visible_items[row]["id"] if 0 <= row < len(self.visible_items) else None
        accepted = self.allow_discard("Discard unsaved edits to the selected investigation?")
        if not accepted:
            identifier = self.current["id"] if self.current else None
        selected = self.visible_row(identifier)
        self.library.blockSignals(True)
        self.library.setCurrentRow(selected)
        self.library.blockSignals(False)
        if accepted:
            self.open_row(selected)

    def open_row(self, row):
        """Display persisted annotations and evidence; selection never refreshes remote data."""
        self.current = (
            copy.deepcopy(self.visible_items[row]) if 0 <= row < len(self.visible_items) else None
        )
        item = self.current or {}
        self.name.setText(item.get("name", ""))
        self.notes.setPlainText(item.get("notes", ""))
        self.labels.setText(item.get("labels", ""))
        self.annotations.setChecked(False)
        self.entries.clear()
        for number, entry in enumerate(item.get("entries", []), 1):
            self.entries.addItem(
                f"#{number} • {entry['network']} • {entry['kind']} • {entry['identifier']}"
            )
        self.export.setEnabled(self.current is not None)
        self.show_entry(-1)

    def create(self):
        """Create a separate investigation using the entered name; evidence is added explicitly."""
        # The name is used for the new case. Other drafts would be lost when
        # the new case opens, so protect those edits before changing selection.
        if {"labels", "notes"} & self.unsaved_annotations().keys() and not self.allow_discard(
            "Create a new investigation and discard unsaved notes or labels in the current one?"
        ):
            return
        self.commit(new_investigation(self.name.text()))

    def commit(self, item):
        """Write a candidate copy and refresh the library only after storage succeeds."""
        try:
            self.store.save(item)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.error(str(exc))
            return False
        self.reload(item["id"], reveal=True)
        return True

    def save_notes(self):
        """Persist user-authored annotations without modifying any saved network evidence."""
        if self.current is None:
            self.error("Create or select an investigation first.")
            return
        item = copy.deepcopy(self.current)
        item.update(self.draft_annotations())
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
        comparable = valid and self.current["entries"][row]["kind"] in PRIMARY_KIND
        self.compare_button.setEnabled(comparable)
        self.compare_button.setToolTip(
            "Choose a second saved entry with the same resource and network."
            if comparable
            else "Choose saved account, asset, or contract evidence to compare."
        )
        refreshable = valid and self.current["entries"][row]["kind"] in (
            "account",
            "transaction",
            "asset",
        )
        self.refresh_button.setEnabled(refreshable and self.task is None)
        self.refresh_button.setToolTip(
            "Saved graph/timeline/contract evidence remains offline; open Graph or Contracts for a new snapshot."
            if valid and not refreshable
            else "Fetch a new resource snapshot while keeping old evidence."
        )
        self.evidence.setPlainText(
            json.dumps(self.current["entries"][row], ensure_ascii=False, indent=2)
            if valid
            else "Select saved evidence to inspect it offline."
        )

    def open_comparison(self):
        """Use the selected row as the baseline without refreshing data or saving draft notes."""
        row = self.entries.currentRow()
        if self.current is None or row < 0:
            return
        entries = self.current["entries"]
        if entries[row]["kind"] in PRIMARY_KIND:
            # A window-modal dialog prevents changes to the library selection
            # while leaving the application's asynchronous refresh loop free to run.
            InvestigationComparisonDialog(entries, row, self).open()

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
