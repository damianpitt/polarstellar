"""Cancellable contract-instance inspection and retained-window RPC event exploration."""

import asyncio
import json
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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

from polarstellar.stellar.contract_specs import interpret, readable
from polarstellar.stellar.contract_storage import entered_key, storage_key
from polarstellar.stellar.contract_storage import readable as readable_storage
from polarstellar.stellar.contracts import ENDPOINTS, MAX_EVENT_PAGES, validate_contract
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
        self.detail_tabs = detail_tabs
        self.instance_raw = QPlainTextEdit()
        self.instance_raw.setReadOnly(True)
        detail_tabs.addTab(self.details, "Contract details")
        detail_tabs.addTab(self.instance_raw, "Raw instance evidence")
        spec_tab = QWidget()
        spec_layout = QVBoxLayout(spec_tab)
        self.spec_load = QPushButton("Load specification (read only)")
        self.spec_load.clicked.connect(self.start_specification)
        self.spec_details = QPlainTextEdit()
        self.spec_details.setReadOnly(True)
        self.spec_details.setMinimumHeight(120)
        self.spec_load.setMinimumHeight(28)
        self.spec_export = ExportControls(self.specification_document)
        self.spec_export.setMinimumHeight(28)
        spec_layout.addWidget(self.spec_load)
        spec_layout.addWidget(self.spec_details)
        spec_layout.addWidget(self.spec_export)
        detail_tabs.addTab(spec_tab, "Specification")
        storage_tab = QWidget()
        storage_layout = QVBoxLayout(storage_tab)
        storage_help = QLabel(
            "Read one supplied key from the inspected contract. Symbol or base64 ScVal XDR; "
            "persistent and temporary are separate key spaces. Current state only; no enumeration."
        )
        storage_help.setWordWrap(True)
        storage_layout.addWidget(storage_help)
        storage_form = QHBoxLayout()
        self.storage_mode = QComboBox()
        self.storage_mode.addItems(["Symbol", "ScVal XDR"])
        self.storage_mode.setAccessibleName("Storage key format")
        self.storage_key = QLineEdit()
        self.storage_key.setMaxLength(5500)
        self.storage_key.setPlaceholderText("Exact symbol, e.g. COUNTER; or select ScVal XDR")
        self.storage_key.setAccessibleName("Explicit contract storage key")
        self.storage_durability = QComboBox()
        self.storage_durability.addItems(["Persistent", "Temporary"])
        self.storage_durability.setAccessibleName("Storage durability")
        self.storage_load = QPushButton("Read key / refresh")
        self.storage_load.clicked.connect(self.start_storage)
        self.storage_key.returnPressed.connect(self.start_storage)
        for widget in (
            self.storage_mode,
            self.storage_key,
            self.storage_durability,
            self.storage_load,
        ):
            storage_form.addWidget(widget)
        storage_form.setStretch(1, 1)
        storage_layout.addLayout(storage_form)
        self.storage_details = QPlainTextEdit()
        self.storage_details.setReadOnly(True)
        self.storage_details.setMinimumHeight(120)
        storage_layout.addWidget(self.storage_details)
        self.storage_export = ExportControls(self.storage_document)
        self.storage_export.setMinimumHeight(28)
        storage_layout.addWidget(self.storage_export)
        detail_tabs.addTab(storage_tab, "Storage key")
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
        detail_tabs.currentChanged.connect(self.show_detail_tab)
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
        self.storage_result = None
        self.storage_load.setEnabled(False)
        self.storage_export.setEnabled(False)
        self.storage_key.clear()
        self.storage_mode.setCurrentIndex(0)
        self.storage_durability.setCurrentIndex(0)
        self.storage_details.setPlainText(
            "Inspect a contract, supply a key, then choose Read key / refresh. "
            "The last accepted lookup is retained until another succeeds or the inspection resets."
        )
        self.specification = None
        self.spec_load.setEnabled(False)
        self.spec_export.setEnabled(False)
        self.spec_details.setPlainText(
            "Load the inspected executable's declared interface explicitly. No functions are executed."
        )
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
            self.spec_load.setEnabled(self.snapshot is not None)
            self.storage_load.setEnabled(self.snapshot is not None)
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
        self.spec_load.setEnabled(False)
        self.storage_load.setEnabled(False)

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
                self.spec_load.setEnabled(self.snapshot is not None)
                self.storage_load.setEnabled(self.snapshot is not None)

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
                self.spec_load.setEnabled(self.snapshot is not None)
                self.storage_load.setEnabled(self.snapshot is not None)

    def show_event(self):
        """Show decoded topics/values and original RPC evidence for the selected event."""
        row = self.table.currentRow()
        valid = 0 <= row < len(self.events)
        if valid:
            event = self.events[row]
            candidate = interpret(event, self.specification)
            lines = [f"Event interpretation: {candidate['status']} • {candidate.get('name', '')}"]
            for field in candidate.get("fields", []):
                lines.append(
                    f"{field['name']} ({field['location']}): "
                    + json.dumps(field["value"], ensure_ascii=False)
                )
            lines += [
                candidate["note"],
                candidate.get("call_status", ""),
                "",
                "ORIGINAL EVENT EVIDENCE",
                json.dumps(event, ensure_ascii=False, indent=2),
                "",
                "INTERPRETATION EVIDENCE",
                json.dumps(candidate, ensure_ascii=False, indent=2),
            ]
            self.raw.setPlainText("\n".join(lines))
        else:
            self.raw.clear()
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
                "specification": {
                    key: value for key, value in self.specification.items() if key != "entries"
                }
                if self.specification
                else None,
                "events_queried": bool(self.pages),
                "storage_lookup": {
                    key: value for key, value in self.storage_result.items() if key != "entry"
                }
                if self.storage_result
                else None,
                "query_complete": self.done,
                "page_limit": MAX_EVENT_PAGES,
                "coverage": "Loaded contract events only; provider retention and a fixed "
                "ledger window apply. No decimal or application semantics inferred.",
            },
            [
                {**event, "interpretation": interpret(event, self.specification)}
                for event in self.events
            ],
        )

    def show_detail_tab(self, index):
        """Give specification/storage details the full pane without discarding loaded events."""
        self.table.setVisible(index < 2)
        self.raw.setVisible(index < 2)

    def start_specification(self):
        """Capture the existing inspection before issuing an explicit optional code/specification read."""
        if self.task is not None or self.snapshot is None:
            return
        self.status.setText("Loading declared specification for the inspected executable…")
        self.launch(self.load_specification(self.snapshot, self.context_network, self.generation))

    async def load_specification(self, snapshot, network, generation):
        """Accept only the pinned contract/network/hash; failures and cancellation retain earlier evidence."""
        try:
            result = await self.provider.specification(snapshot, network)
            if generation != self.generation:
                return
            if (result["contract"], result["network"], result["wasm_hash"]) != (
                snapshot["contract"],
                network.value,
                snapshot.get("wasm_hash"),
            ):
                raise AccountError("Specification belongs to another inspection.")
            # Render/validate optional metadata before replacing accepted state;
            # malformed future/provider descriptions must preserve the prior spec.
            preview = readable(result)
            self.specification = result
            self.spec_details.setPlainText(preview)
            self.spec_export.setEnabled(True)
            self.show_event()
            self.status.setText(
                f"Specification: {result['status']}. Event interpretations use this executable only; historical compatibility is unverified."
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 -- Optional metadata failures must preserve useful instance/events.
            if generation == self.generation:
                self.status.setText(
                    (str(exc) if isinstance(exc, AccountError) else "Unable to load specification.")
                    + " Previous specification and events retained."
                )
        finally:
            if generation == self.generation:
                self.task = None
                self.cancel.setEnabled(False)
                self.spec_load.setEnabled(self.snapshot is not None)
                self.storage_load.setEnabled(self.snapshot is not None)
                self.more.setEnabled(
                    self.snapshot is not None
                    and not self.done
                    and len(self.pages) < MAX_EVENT_PAGES
                )

    def specification_document(self):
        """Export the interface once as a separate artifact, avoiding large repeated event CSV metadata."""
        return document(
            "contract_specification",
            {key: value for key, value in self.specification.items() if key != "entries"},
            self.specification["entries"],
        )

    def start_storage(self):
        """Freeze the supplied key/durability and inspected context before any network access."""
        if self.task is not None or self.snapshot is None:
            return
        try:
            key_xdr = entered_key(self.storage_key.text(), self.storage_mode.currentText())
            durability = self.storage_durability.currentText()
            # Validate the complete request before launching; edits during a read cannot
            # change its captured identity or relabel the last accepted export.
            storage_key(self.snapshot["contract"], key_xdr, durability)
        except AccountError as exc:
            self.status.setText(str(exc) + " Previous storage result retained.")
            return
        self.status.setText("Reading one supplied storage key from the inspected contract…")
        self.launch(
            self.load_storage(
                self.snapshot["contract"],
                self.context_network,
                key_xdr,
                durability,
                self.generation,
            )
        )

    async def load_storage(self, contract, network, key_xdr, durability, generation):
        """Accept one matching read atomically; failures, cancellation and late results retain evidence."""
        try:
            result = await self.provider.storage(contract, network, key_xdr, durability)
            if generation != self.generation:
                return
            expected = storage_key(contract, key_xdr, durability).to_xdr()
            if (
                result["contract"],
                result["network"],
                result["source"],
                result["requested_key_xdr"],
                result["durability"],
                result["ledger_key_xdr"],
            ) != (contract, network.value, ENDPOINTS[network], key_xdr, durability, expected):
                raise AccountError("Storage result belongs to another key, contract or network.")
            preview = readable_storage(result)
            self.storage_result = result
            self.storage_details.setPlainText(preview)
            self.storage_export.setEnabled(True)
            self.status.setText(
                f"Storage: {result['status']} • {network.value} • {durability} • "
                f"RPC ledger {result['latest_ledger']}. One supplied key; not historical/event state."
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 -- Optional reads must retain accepted instance/events/spec/storage.
            if generation == self.generation:
                self.status.setText(
                    (str(exc) if isinstance(exc, AccountError) else "Unable to read storage key.")
                    + " Previous storage result retained."
                )
        finally:
            if generation == self.generation:
                self.task = None
                self.cancel.setEnabled(False)
                self.storage_load.setEnabled(self.snapshot is not None)
                self.spec_load.setEnabled(self.snapshot is not None)
                self.more.setEnabled(
                    self.snapshot is not None
                    and not self.done
                    and len(self.pages) < MAX_EVENT_PAGES
                )

    def storage_document(self):
        """Freeze the last accepted lookup, including an explicit missing result with zero rows."""
        return document(
            "contract_storage",
            {key: value for key, value in self.storage_result.items() if key != "entry"},
            [self.storage_result["entry"]] if self.storage_result["entry"] is not None else [],
        )
