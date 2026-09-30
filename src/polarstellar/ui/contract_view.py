"""Cancellable contract-instance inspection and retained-window RPC event exploration."""

import asyncio
import json
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from polarstellar.stellar.contracts import MAX_EVENT_PAGES, validate_contract
from polarstellar.stellar.providers import AccountError
from polarstellar.storage.export import document
from polarstellar.ui.export_controls import ExportControls


class ContractView(QWidget):
    """Show live instance evidence and paginated events without executing contract functions."""

    transaction_requested = Signal(str)

    def __init__(self, provider, network):
        """Inject RPC access and capture the selected network separately for each new inspection."""
        super().__init__()
        self.provider, self.network = provider, network
        self.generation = 0
        self.task = None
        self.tasks = set()
        layout = QVBoxLayout(self)
        form = QHBoxLayout()
        self.address = QLineEdit()
        self.address.setPlaceholderText("Contract C-address")
        self.address.setAccessibleName("Contract ID")
        self.start_ledger = QLineEdit()
        self.start_ledger.setPlaceholderText("Start ledger (blank: recent 1,000)")
        self.start_ledger.setAccessibleName("Event start ledger")
        self.inspect = QPushButton("Inspect / refresh")
        self.inspect.clicked.connect(self.start)
        self.address.returnPressed.connect(self.start)
        for widget in (self.address, self.start_ledger, self.inspect):
            form.addWidget(widget)
        layout.addLayout(form)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(100)
        split = QSplitter(Qt.Orientation.Vertical)
        detail_tabs = QTabWidget()
        self.instance_raw = QPlainTextEdit()
        self.instance_raw.setReadOnly(True)
        detail_tabs.addTab(self.details, "Contract details")
        detail_tabs.addTab(self.instance_raw, "Raw instance evidence")
        split.addWidget(detail_tabs)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Ledger", "Closed (UTC)", "Successful call", "Event ID"]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self.show_event)
        split.addWidget(self.table)
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        split.addWidget(self.raw)
        layout.addWidget(split, 1)
        actions = QHBoxLayout()
        self.more = QPushButton("Load events (oldest first)")
        self.more.clicked.connect(self.start_events)
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(self.cancel_request)
        self.open_transaction = QPushButton("Inspect selected transaction")
        self.open_transaction.clicked.connect(self.open_selected_transaction)
        for widget in (self.more, self.cancel, self.open_transaction):
            actions.addWidget(widget)
        layout.addLayout(actions)
        self.export = ExportControls(self.export_document)
        layout.addWidget(self.export)
        self.reset()

    def reset(self):
        """Invalidate old requests and clear every exportable snapshot on context changes."""
        self.cancel_request()
        self.snapshot = None
        self.events = []
        self.pages = []
        self.cursor = None
        self.cursors = set()
        self.done = False
        self.address.clear()
        self.start_ledger.clear()
        self.details.clear()
        self.instance_raw.clear()
        self.more.setText("Load events (oldest first)")
        self.raw.clear()
        self.table.setRowCount(0)
        self.more.setEnabled(False)
        self.open_transaction.setEnabled(False)
        self.export.setEnabled(False)
        self.status.setText(
            "Inspect a contract on the selected network. Read-only live RPC; "
            "no function calls, simulation, or transaction submission. Event history is limited."
        )

    def cancel_request(self):
        """Cancel pending work while retaining any already-loaded evidence for export and retry."""
        self.generation += 1
        for task in self.tasks:
            task.cancel()
        self.task = None
        self.cancel.setEnabled(False)
        self.status.setText("Request cancelled; previously loaded evidence retained.")
        if hasattr(self, "snapshot"):
            self.more.setEnabled(
                self.snapshot is not None and not self.done and len(self.pages) < MAX_EVENT_PAGES
            )

    def start(self):
        """Validate identifiers and ledger input before network access, then freeze the query context."""
        address, entered = self.address.text(), self.start_ledger.text().strip()
        self.reset()
        self.address.setText(address)
        self.start_ledger.setText(entered)
        try:
            contract = validate_contract(address)
            if entered and (not entered.isascii() or not entered.isdecimal() or len(entered) > 10):
                raise AccountError("Event start ledger must be a nonnegative integer.")
            start = int(entered) if entered else None
        except AccountError as exc:
            self.status.setText(str(exc))
            return
        self.status.setText("Loading contract instance and RPC retention window…")
        self.launch(self.load(contract, self.network(), start, self.generation))

    def launch(self, coroutine):
        """Track the active task so reset and app shutdown can cancel work consistently."""
        self.task = asyncio.create_task(coroutine)
        self.tasks.add(self.task)
        self.task.add_done_callback(self.tasks.discard)
        self.cancel.setEnabled(True)
        self.more.setEnabled(False)

    async def load(self, contract, network, start, generation):
        """Accept an instance only for the requested contract/network and retained ledger window."""
        try:
            snapshot = await self.provider.inspect(contract, network)
            if generation != self.generation:
                return
            if (snapshot["contract"], snapshot["network"]) != (contract, network.value):
                raise AccountError("Contract response does not match the selected network or ID.")
            oldest, latest = (
                snapshot["oldest_available_ledger"],
                snapshot["latest_available_ledger"],
            )
            begin = max(oldest, latest - 999) if start is None else start
            if not oldest <= begin <= latest:
                raise AccountError(
                    f"Choose a start ledger within RPC retention: {oldest}–{latest}."
                )
            self.snapshot = snapshot
            self.begin, self.end = begin, latest
            self.context_network = network
            lines = [
                snapshot["contract"],
                f"{network.value} • {snapshot['cache_status']}",
                f"Executable: {snapshot.get('executable', 'Unavailable')}",
                f"WASM hash: {snapshot.get('wasm_hash') or 'Not available / not WASM'}",
                f"Last modified ledger: {snapshot.get('last_modified_ledger', 'Unknown')}",
                f"Live until ledger: {snapshot.get('live_until_ledger', 'Unknown')}",
                f"Source: {snapshot['source']} • Retrieved: {snapshot['fetched_at']}",
                snapshot.get("warning", ""),
                snapshot["coverage"],
                "",
                "Instance storage (known instance only):",
                json.dumps(snapshot.get("instance_storage", []), ensure_ascii=False, indent=2),
            ]
            self.details.setPlainText("\n".join(lines))
            self.instance_raw.setPlainText(json.dumps(snapshot, ensure_ascii=False, indent=2))
            self.export.setEnabled(True)
            self.status.setText(
                f"{network.value} • Events query ledgers {begin}–{latest} inclusive, "
                f"oldest first; retained {oldest}–{latest}. Events not loaded yet. "
                "Instance data does not enumerate arbitrary contract storage."
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 -- Network/SDK failures must not escape the UI.
            if generation == self.generation:
                self.status.setText(
                    str(exc)
                    if isinstance(exc, AccountError)
                    else "Unable to inspect contract. Retry."
                )
        finally:
            if generation == self.generation:
                self.task = None
                self.cancel.setEnabled(False)
                self.more.setEnabled(self.snapshot is not None)

    def start_events(self):
        """Request one page using the frozen contract, network and initial ledger window."""
        if (
            self.task is not None
            or self.snapshot is None
            or self.done
            or len(self.pages) >= MAX_EVENT_PAGES
        ):
            return
        self.status.setText("Loading next event page; existing rows retained…")
        self.launch(self.load_events(self.generation))

    async def load_events(self, generation):
        """Deduplicate IDs, reject cursor cycles/conflicts, and retain the old page on failure."""
        try:
            page = await self.provider.events(
                self.snapshot["contract"], self.context_network, self.begin, self.end, self.cursor
            )
            if generation != self.generation:
                return
            if (page["contract"], page["network"], page["start_ledger"], page["end_ledger"]) != (
                self.snapshot["contract"],
                self.context_network.value,
                self.begin,
                self.end,
            ):
                raise AccountError("Event page does not match this inspection.")
            if not page["done"] and page["next_cursor"] in self.cursors:
                raise AccountError("Event cursor repeated. Inspect again to restart the query.")
            merged = {event["id"]: event for event in self.events}
            last_ledger = self.events[-1]["ledger"] if self.events else self.begin
            for event in page["events"]:
                if event["id"] in merged:
                    if merged[event["id"]] != event:
                        raise AccountError(
                            "Conflicting event evidence returned; previous events retained."
                        )
                    continue
                if event["ledger"] < last_ledger:
                    raise AccountError("Event order moved backwards; previous events retained.")
                merged[event["id"]] = event
                last_ledger = event["ledger"]
            self.events = list(merged.values())
            self.pages.append({key: value for key, value in page.items() if key != "events"})
            self.cursor = page["next_cursor"]
            self.cursors.add(self.cursor)
            self.done = page["done"]
            self.more.setText("End of queried events" if self.done else "Load more events")
            self.table.setRowCount(len(self.events))
            for row, event in enumerate(self.events):
                success = event["successful_call"]
                values = (
                    str(event["ledger"]),
                    event["closed_at"],
                    "Unknown" if success is None else "Yes" if success else "No",
                    event["id"],
                )
                for column, value in enumerate(values):
                    self.table.setItem(row, column, QTableWidgetItem(value))
            self.table.resizeColumnsToContents()
            boundary = (
                "Reached query boundary / no more events at retrieval time."
                if self.done
                else "Load more to continue."
            )
            if len(self.pages) >= MAX_EVENT_PAGES and not self.done:
                boundary = f"Stopped at {MAX_EVENT_PAGES} pages. Query coverage is incomplete."
            self.status.setText(
                f"{len(self.events)} events • {self.context_network.value} • "
                f"Query {self.begin}–{self.end}, oldest first. {boundary} "
                "Not complete lifetime history; raw integers have no inferred token decimals."
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 -- Pagination failures must retain earlier evidence.
            if generation == self.generation:
                self.status.setText(
                    (str(exc) if isinstance(exc, AccountError) else "Unable to load events.")
                    + " Existing rows retained; retry or inspect again."
                )
        finally:
            if generation == self.generation:
                self.task = None
                self.cancel.setEnabled(False)
                self.more.setEnabled(not self.done and len(self.pages) < MAX_EVENT_PAGES)

    def show_event(self):
        """Show decoded topics/values and original RPC evidence for the selected event."""
        row = self.table.currentRow()
        valid = 0 <= row < len(self.events)
        self.raw.setPlainText(
            json.dumps(self.events[row], ensure_ascii=False, indent=2) if valid else ""
        )
        value = self.events[row].get("transaction_hash") if valid else None
        self.open_transaction.setEnabled(
            isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None
        )

    def open_selected_transaction(self):
        """Open valid event transaction hashes through the existing Horizon inspector."""
        if self.open_transaction.isEnabled():
            self.transaction_requested.emit(
                self.events[self.table.currentRow()]["transaction_hash"]
            )

    def export_document(self):
        """Preserve instance evidence, event data, per-page provenance and explicit coverage bounds."""
        return document(
            "contract",
            {
                "instance": self.snapshot,
                "start_ledger": self.begin,
                "end_ledger": self.end,
                "pages": self.pages,
                "events_queried": bool(self.pages),
                "query_complete": self.done,
                "page_limit": MAX_EVENT_PAGES,
                "coverage": "Loaded contract events only; provider retention and a fixed "
                "ledger window apply. No decimal or application semantics inferred.",
            },
            self.events,
        )
