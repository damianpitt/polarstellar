"""Interactive, explicitly paginated expansion of an account's observed payment graph."""

import asyncio
import math

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QGraphicsScene,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from polarstellar.analysis.trace_filters import (
    DIRECT_TYPES,
    LINK_TYPES,
    OPERATION_TYPES,
    TraceFilters,
    instant,
)
from polarstellar.analysis.tracing import CANVAS_NODES, MAX_PATHS, Trace
from polarstellar.stellar.activity import ActivityKind
from polarstellar.stellar.providers import AccountError
from polarstellar.ui.export_controls import ExportControls
from polarstellar.ui.graph_view import Canvas, Edge, Node


class TraceDialog(QDialog):
    """Explore several hops without changing the root account or hiding fetched-history limits."""

    transaction_requested = Signal(str)
    save_requested = Signal(object)

    def __init__(self, payments, parent=None):
        """Seed with loaded root pages; fetch additional account pages only on explicit clicks."""
        super().__init__(parent)
        self.service = payments.service
        self.trace = Trace(*payments.context, payments.export_pages)
        self.generation = 0
        self.task = None
        self.tasks = set()
        self.edges = []
        self.connections = []
        self.links = []
        self.timeline_rows = []
        self.graph_paths_text = ""
        self.timeline_notice = ""
        self.view_filters = None
        self.setWindowTitle(f"Expanded graph • {self.trace.network.value}")
        self.resize(1120, 850)
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        controls = QHBoxLayout()
        self.account = QComboBox()
        self.account.setAccessibleName("Account to expand")
        self.account.setMinimumContentsLength(18)
        self.account.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.expand = QPushButton("Expand / load next 20")
        self.expand.clicked.connect(self.start)
        self.cancel = QPushButton("Cancel request")
        self.cancel.clicked.connect(self.cancel_request)
        for widget in (self.account, self.expand, self.cancel):
            controls.addWidget(widget)
        fit = QPushButton("Fit graph")
        self.fit_button = fit
        fit.clicked.connect(self.fit)
        controls.addWidget(fit)
        layout.addLayout(controls)
        self.status = QLabel("Select a discovered account and expand its payment history.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        filters = QHBoxLayout()
        self.asset = QComboBox()
        self.asset.setAccessibleName("Trace asset filter")
        self.direction = QComboBox()
        self.direction.addItems(["From root", "To root"])
        self.target = QComboBox()
        self.target.setAccessibleName("Path target")
        for widget in (self.asset, self.direction, self.target):
            widget.setMinimumContentsLength(12)
            widget.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            filters.addWidget(widget)
        layout.addLayout(filters)
        time_row = QHBoxLayout()
        self.from_time = QLineEdit()
        self.from_time.setPlaceholderText("From ISO time (blank: no lower bound)")
        self.from_time.setAccessibleName("Trace from time")
        self.until_time = QLineEdit()
        self.until_time.setPlaceholderText("Until ISO time (blank: no upper bound)")
        self.until_time.setAccessibleName("Trace until time")
        for field in (self.from_time, self.until_time):
            field.setMaxLength(64)
            field.setToolTip(
                "Explicit timezone required, e.g. 2026-10-01T00:00:00Z. Bounds are inclusive."
            )
        self.operation_type = QComboBox()
        self.operation_type.setAccessibleName("Trace operation type filter")
        self.operation_type.addItem("All supported operations", OPERATION_TYPES)
        self.operation_type.addItem("Direct payments", ("payment",))
        self.operation_type.addItem("Account funding", ("create_account",))
        self.operation_type.addItem("Payments and funding", DIRECT_TYPES)
        self.operation_type.addItem("Path payments (both types)", LINK_TYPES[:2])
        self.operation_type.addItem("Path payment: strict send", (LINK_TYPES[0],))
        self.operation_type.addItem("Path payment: strict receive", (LINK_TYPES[1],))
        self.operation_type.addItem("Account merges", ("account_merge",))
        apply = QPushButton("Apply time / type")
        apply.clicked.connect(self.apply_filters)
        clear = QPushButton("Clear time / type")
        clear.clicked.connect(self.clear_filters)
        for widget in (self.from_time, self.until_time, self.operation_type, apply, clear):
            time_row.addWidget(widget)
        layout.addLayout(time_row)
        self.filter_status = QLabel(
            "Applied: all times, all supported operations. Edits apply only on Apply time / type."
        )
        self.filter_status.setWordWrap(True)
        self.filter_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.filter_status)
        self.scene = QGraphicsScene(self)
        self.canvas = Canvas(self.scene)
        self.canvas.setMinimumHeight(150)
        self.canvas.setDragMode(Canvas.DragMode.ScrollHandDrag)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.canvas)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Sender", "Recipient", "Asset / issuer", "Exact total", "Operations"]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self.show_evidence)
        self.operation_table = QTableWidget(0, 5)
        self.operation_table.setHorizontalHeaderLabels(
            [
                "Sender",
                "Recipient",
                "Operation type",
                "Sent / received (not a total)",
                "Operation ID",
            ]
        )
        self.operation_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.operation_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.operation_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setMinimumHeight(110)
        self.operation_table.setMinimumHeight(110)
        self.operation_table.itemSelectionChanged.connect(self.show_link_evidence)
        self.tables = QTabWidget()
        self.tables.addTab(self.table, "Direct payment / funding totals")
        self.tables.addTab(self.operation_table, "Path payments / merges (individual observations)")
        self.timeline_page = QWidget()
        timeline_layout = QVBoxLayout(self.timeline_page)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_controls = QHBoxLayout()
        self.timeline_account = QComboBox()
        self.timeline_account.setAccessibleName("Timeline account focus")
        self.timeline_account.setMinimumContentsLength(18)
        self.timeline_account.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.timeline_order = QComboBox()
        self.timeline_order.setAccessibleName("Timeline display order")
        self.timeline_order.addItem("Oldest first", False)
        self.timeline_order.addItem("Newest first", True)
        timeline_controls.addWidget(QLabel("Timeline focus:"))
        timeline_controls.addWidget(self.timeline_account, 1)
        timeline_controls.addWidget(self.timeline_order)
        timeline_layout.addLayout(timeline_controls)
        self.timeline_table = QTableWidget(0, 8)
        self.timeline_table.setHorizontalHeaderLabels(
            [
                "Observed time (UTC)",
                "Operation type",
                "Timing / ambiguity",
                "Earlier selected gap (s)",
                "Amounts (individual observation)",
                "Sender",
                "Recipient",
                "Operation ID",
            ]
        )
        self.timeline_table.setMinimumHeight(110)
        self.timeline_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.timeline_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.timeline_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # Qt's optional table sorting would detach rows from the frozen evidence
        # list; the explicit order control sorts the data and display together.
        self.timeline_table.setSortingEnabled(False)
        self.timeline_table.itemSelectionChanged.connect(self.show_timeline_evidence)
        self.timeline_table.itemDoubleClicked.connect(self.open_timeline_transaction)
        timeline_layout.addWidget(self.timeline_table)
        self.tables.addTab(self.timeline_page, "Timeline")
        self.timeline_account.currentIndexChanged.connect(self.render_timeline)
        self.timeline_order.currentIndexChanged.connect(self.render_timeline)
        # A shared evidence panel must never show a stale selection from the other tab.
        self.tables.currentChanged.connect(self.refresh_evidence)
        split.addWidget(self.tables)
        layout.addWidget(split, 1)
        self.evidence = QListWidget()
        self.evidence.setMaximumHeight(110)
        self.evidence.hide()
        self.evidence.setToolTip("Double-click evidence to inspect the transaction")
        self.evidence.itemDoubleClicked.connect(
            lambda item: self.transaction_requested.emit(item.data(Qt.ItemDataRole.UserRole))
        )
        layout.addWidget(self.evidence)
        self.paths = QPlainTextEdit()
        self.paths.setReadOnly(True)
        self.paths.setMaximumHeight(80)
        layout.addWidget(self.paths)
        self.coverage = QPlainTextEdit()
        self.coverage.setReadOnly(True)
        self.coverage.setMaximumHeight(100)
        layout.addWidget(self.coverage)
        self.export = ExportControls(self.active_document)
        self.export.setEnabled(True)
        layout.addWidget(self.export)
        self.save = QPushButton("Save expanded graph to selected investigation")
        self.save.clicked.connect(lambda: self.save_requested.emit(self.active_document()))
        layout.addWidget(self.save)
        for widget in (self.asset, self.direction, self.target):
            widget.currentIndexChanged.connect(self.render)
        self.account.currentIndexChanged.connect(self.update_controls)
        self.finished.connect(self.cancel_request)
        self.rebuild()

    def apply_filters(self):
        """Validate all inputs before replacing applied state; bad edits keep the previous graph/export."""
        try:
            candidate = TraceFilters(
                instant(self.from_time.text().strip()) if self.from_time.text().strip() else None,
                instant(self.until_time.text().strip()) if self.until_time.text().strip() else None,
                self.operation_type.currentData(),
            )
        except ValueError as exc:
            self.filter_status.setText(str(exc) + " Previous applied filters/results retained.")
            return
        self.view_filters = candidate
        metadata = candidate.metadata()
        self.filter_status.setText(
            f"Applied inclusive UTC: {metadata['from_utc'] or 'Unbounded'} to "
            f"{metadata['until_utc'] or 'Unbounded'} • Types: {', '.join(candidate.operations)}. "
            "Loaded evidence only; no extra requests. Edits require Apply time / type."
        )
        self.render()

    def clear_filters(self):
        """Restore all supported types/times locally, retaining pages, cursors and expansion limits."""
        self.from_time.clear()
        self.until_time.clear()
        self.operation_type.setCurrentIndex(0)
        self.view_filters = None
        self.filter_status.setText(
            "Applied: all times, all supported operations. Edits apply only on Apply time / type."
        )
        self.render()

    def fit(self):
        """Fit after layout has established the visible canvas dimensions."""
        if self.scene.items():
            self.canvas.fitInView(
                self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40),
                Qt.AspectRatioMode.KeepAspectRatio,
            )

    def showEvent(self, event):
        """Defer initial fitting so all graph nodes are visible when the window first opens."""
        super().showEvent(event)
        QTimer.singleShot(0, self.fit)

    def choose_account(self, address):
        """Select a canvas node for expansion without opening a different root investigation."""
        self.account.setCurrentIndex(self.account.findData(address))

    def rebuild(self):
        """Refresh discovered nodes and assets, preserving user selections where possible."""
        connections, _, distances = self.trace.build()
        links = self.trace.links()
        for combo, values, default in (
            (
                self.account,
                sorted(distances, key=lambda address: (distances[address], address)),
                None,
            ),
            (self.target, sorted(set(distances) - {self.trace.root}), "Choose path target"),
            (self.timeline_account, sorted(distances), "All observed accounts"),
            (
                self.asset,
                sorted(
                    {edge.asset for edge in connections}
                    | {
                        asset
                        for link in links
                        for asset in (link.source_asset, link.destination_asset)
                    }
                ),
                "All assets (choose one for paths)",
            ),
        ):
            selected = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            if default:
                combo.addItem(default, None)
            for value in values:
                combo.addItem(value, value)
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.blockSignals(False)
        self.render()

    def update_controls(self):
        """Explain bounds and disable duplicate requests without hiding retained evidence."""
        address = self.account.currentData()
        reason = self.trace.reason(address) if address else "Select an account."
        self.expand.setEnabled(self.task is None and not reason)
        self.expand.setToolTip(
            reason or "Fetch one page for this account only; no automatic crawling."
        )
        self.cancel.setEnabled(self.task is not None)

    def start(self):
        """Capture the selected account and issue exactly one independent cursor request."""
        address = self.account.currentData()
        if self.task is not None or not address or self.trace.reason(address):
            return
        self.status.setText(f"Loading {address} on {self.trace.network.value}…")
        self.task = asyncio.create_task(self.load(address, self.generation))
        self.tasks.add(self.task)
        self.task.add_done_callback(self.tasks.discard)
        self.update_controls()

    async def load(self, address, generation):
        """Retain prior pages on failure and reject results after cancellation/context changes."""
        pages = self.trace.pages.get(address, [])
        cursor = pages[-1].next_cursor if pages else None
        try:
            page = await self.service.activity(
                address, self.trace.network, ActivityKind.PAYMENTS, cursor
            )
            if generation != self.generation:
                return
            self.trace.add_page(address, page)
            self.status.setText(
                f"Loaded {len(page.records)} records for {address}. "
                + (self.trace.reason(address) or "More pages available.")
            )
            self.rebuild()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 -- Retain usable evidence after any provider failure.
            if generation == self.generation:
                message = (
                    str(exc)
                    if isinstance(exc, (AccountError, ValueError))
                    else "Unable to load this account."
                )
                self.status.setText(
                    message + " Existing evidence retained; retry the same account."
                )
        finally:
            if generation == self.generation:
                self.task = None
                self.update_controls()

    def cancel_request(self, *_):
        """Invalidate even cancellation-resistant providers; keep already accepted evidence."""
        self.generation += 1
        for task in self.tasks:
            task.cancel()
        self.task = None
        self.status.setText("Request cancelled. Loaded evidence retained.")
        self.update_controls()

    def export_document(self):
        """Snapshot the selected asset, route query, evidence and every account's coverage."""
        return self.trace.export(
            self.asset.currentData(),
            self.target.currentData(),
            self.direction.currentText() == "To root",
            self.view_filters,
        )

    def timeline_document(self):
        """Freeze timeline rows/order/focus with applied graph filters and complete loaded coverage."""
        return self.trace.timeline_export(
            self.asset.currentData(),
            self.view_filters,
            self.timeline_account.currentData(),
            self.timeline_order.currentData(),
        )

    def active_document(self):
        """Export/save the visible timeline or graph; tab selection makes the chosen artifact explicit."""
        return (
            self.timeline_document() if self.tables.currentIndex() == 2 else self.export_document()
        )

    def render_timeline(self, *_):
        """Reorder/focus local observation rows without changing graph discovery or any network query."""
        snapshot = self.timeline_document()
        self.timeline_rows = snapshot["records"]
        timing = snapshot["metadata"]["timeline"]
        self.timeline_notice = (
            f"{len(self.timeline_rows)} selected operations • {timing['order']} • "
            f"{timing['selected_operations_with_shared_loaded_timestamp']} share a loaded timestamp.\n"
            "UTC timestamp order only; identifier ties do not establish execution order. "
            "Gaps are between selected observations, not proof of inactivity or missing history. "
            "Account focus is local; graph route target/direction do not select timeline rows."
        )
        if self.tables.currentIndex() == 2:
            self.paths.setPlainText(self.timeline_notice)
        self.timeline_table.setRowCount(0)
        if self.tables.currentIndex() == 2:
            self.evidence.clear()
            self.evidence.hide()
        for row, observation in enumerate(self.timeline_rows):
            self.timeline_table.insertRow(row)
            for column, value in enumerate(
                (
                    observation["created_utc"],
                    observation["operation_type"],
                    observation["timing"],
                    observation["seconds_since_earlier_selected_timestamp"] or "—",
                    self.timeline_amounts(observation),
                    observation["sender"],
                    observation["recipient"],
                    observation["operation"],
                )
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.timeline_table.setItem(row, column, item)
        for column, width in enumerate((210, 180, 230, 170, 340, 180, 180, 120)):
            self.timeline_table.setColumnWidth(column, width)
        self.timeline_table.setToolTip(
            "UTC observation order only. Shared timestamps do not establish execution order. "
            "Gaps are between selected timestamps, not evidence of inactivity or missing records. "
            "Select for original evidence; double-click a row to inspect its transaction."
        )

    @staticmethod
    def timeline_amounts(row):
        """Describe exact per-operation amounts, retaining separate path sides and unknown merge balances."""
        if row["evidence_kind"] == "direct_transfer":
            return f"{row['source_amount']} {row['source_asset']}"
        if row["operation_type"] == "account_merge":
            return "XLM balance amount unknown"
        return (
            f"Sent {row['source_amount']} {row['source_asset']} → "
            f"Received {row['destination_amount']} {row['destination_asset']}"
        )

    def show_timeline_evidence(self):
        """Show original time, exact amounts, timing ambiguity and all observation-page provenance."""
        self.evidence.clear()
        self.evidence.hide()
        row = self.timeline_table.currentRow()
        if not 0 <= row < len(self.timeline_rows):
            return
        observation = self.timeline_rows[row]
        origins = "; ".join(
            f"{item['account']} page {item['page_number']} • {item['source']} • "
            f"{item['cache_status']} • Retrieved {item['fetched_at']}"
            for item in observation["observed_in"]
        )
        detail = (
            f"Original: {observation['original_created']} • UTC: {observation['created_utc']} • "
            f"{observation['timing']} (display ties are not execution order)\n"
            f"{observation['operation_type']} • {self.timeline_amounts(observation)} • "
            f"Operation {observation['operation']}\nTX {observation['transaction']}\nObserved in: {origins}"
        )
        evidence = observation["evidence"]
        if evidence.get("limit_name"):
            detail += f"\nRequested {evidence['limit_name']}: {evidence['limit_amount']} • "
            detail += "Intermediary assets: " + (" → ".join(evidence["path"]) or "None")
        self.evidence.addItem(detail)
        item = self.evidence.item(0)
        item.setData(Qt.ItemDataRole.UserRole, observation["transaction"])
        item.setToolTip(detail)
        self.evidence.show()

    def open_timeline_transaction(self, item):
        """Open the transaction belonging to the clicked frozen row, independent of identifier tie order."""
        if 0 <= item.row() < len(self.timeline_rows):
            self.transaction_requested.emit(self.timeline_rows[item.row()]["transaction"])

    def render(self, *_):
        """Draw bounded nodes and all filtered table edges; list route evidence without flow claims."""
        connections, _, distances = self.trace.build(self.view_filters)
        asset = self.asset.currentData()
        self.connections = [edge for edge in connections if asset is None or edge.asset == asset]
        self.links = self.trace.links(self.view_filters, asset)
        self.operation_table.setRowCount(0)
        self.table.setRowCount(0)
        self.evidence.clear()
        self.evidence.hide()
        for row, edge in enumerate(self.connections):
            self.table.insertRow(row)
            for column, value in enumerate(
                (
                    edge.sender,
                    edge.recipient,
                    edge.asset,
                    format(edge.total, "f"),
                    str(len(edge.evidence)),
                )
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        for column in (0, 1, 2):
            self.table.setColumnWidth(column, 200)
        for row, link in enumerate(self.links):
            self.operation_table.insertRow(row)
            for column, value in enumerate(
                (
                    link.sender,
                    link.recipient,
                    link.operation_type,
                    link.description(),
                    link.operation,
                )
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.operation_table.setItem(row, column, item)
        self.operation_table.resizeColumnsToContents()
        for column in (0, 1, 2, 3):
            self.operation_table.setColumnWidth(column, 200 if column != 3 else 360)
        addresses = {self.trace.root}
        for edge in [*self.connections, *self.links]:
            addresses.update((edge.sender, edge.recipient))
        visible = sorted(addresses, key=lambda address: (distances.get(address, 999), address))[
            :CANVAS_NODES
        ]
        self.edges.clear()
        self.scene.clear()
        nodes = {}
        for index, address in enumerate(visible):
            node = Node(address, self.choose_account, address == self.trace.root)
            node.setToolTip(address + "\nDouble-click: select for expansion • Right-click: copy")
            self.scene.addItem(node)
            angle = 2 * math.pi * index / max(1, len(visible))
            node.setPos(300 * math.cos(angle), 300 * math.sin(angle))
            label = self.scene.addSimpleText(
                ("Root " if address == self.trace.root else "") + address[:4] + "…" + address[-4:]
            )
            label.setBrush(QColor("#c1cceb"))
            label.setParentItem(node)
            label.setPos(-35, 30)
            nodes[address] = node
        for edge in self.connections:
            if edge.sender in nodes and edge.recipient in nodes:
                self.edges.append(
                    Edge(
                        self.scene,
                        nodes[edge.sender],
                        nodes[edge.recipient],
                        f"{edge.total:f} {edge.asset} • {len(edge.evidence)} operations",
                    )
                )
        for link in self.links:
            if link.sender in nodes and link.recipient in nodes:
                arrow = Edge(
                    self.scene,
                    nodes[link.sender],
                    nodes[link.recipient],
                    f"{link.operation_type} • {link.description()} • Operation {link.operation}",
                )
                # Dashed lines distinguish observations from numeric direct-transfer
                # totals; labels and the separate tab carry the same distinction.
                pen = QPen(QColor("#e2b76d"), 2, Qt.PenStyle.DashLine)
                arrow.line.setPen(pen)
                arrow.arrow.setBrush(QColor("#e2b76d"))
                self.edges.append(arrow)
        self.canvas.fitInView(
            self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        self.summary.setText(
            f"{self.trace.network.value} • Root: {self.trace.root}\n"
            f"{len(self.trace.pages)}/10 fetched accounts • Up to 3 hops, 5 pages/account. "
            f"Canvas {len(visible)}/{len(addresses)} nodes; tables retain all matching evidence.\n"
            "Local filters select evidence; expansion uses unfiltered discovered accounts. "
            "Dashed: path/merge observations, excluded from direct totals/routes. "
            "No proof of the same funds, chronology, or ownership."
        )
        snapshot = self.export_document()
        lines = []
        for account in snapshot["metadata"]["accounts"]:
            lines.append(
                f"{account['address']} • {account['loaded_records']} records • "
                f"{account['oldest_record_time']} to {account['newest_record_time']} • "
                f"{account['expansion_status']} • Unknown times: {account['unknown_record_times']}"
            )
            for page in account["pages"]:
                lines.append(
                    f"  {page['source']} • {page['cache_status']} • Retrieved {page['fetched_at']}"
                )
        lines.append(
            f"Excluded unique operations: {snapshot['metadata']['excluded_unique_operations']}. "
            f"Hidden by time/type filters: {snapshot['metadata']['filtered_unique_operations']}. "
            f"Unfetched discovered accounts: "
            f"{len(snapshot['metadata']['unfetched_accounts'])}. Horizon coverage may be limited."
        )
        if self.trace.seed_truncated:
            lines.append("Root seed limited to the first five loaded pages.")
        self.coverage.setPlainText("\n".join(lines))
        routes = snapshot["metadata"]["paths"]
        self.paths.setPlainText(
            "Routes use direct payment/funding only; path payments and merges are excluded.\n"
            + (
                f"Observed same-asset routes (up to {MAX_PATHS}; select table edges for transaction evidence):\n"
                + "\n".join(" → ".join(route) for route in routes)
                if routes
                else (
                    "No observed route matches the applied filters in loaded evidence. "
                    "This does not prove no connection exists. Direction applies to routes, not the table."
                    if self.asset.currentData() and self.target.currentData()
                    else "Choose one asset and a target to find directed routes. No route in loaded "
                    "evidence does not prove no connection exists. Direction applies to routes, not the edge table."
                )
            )
        )
        self.graph_paths_text = self.paths.toPlainText()
        self.render_timeline()
        self.update_controls()

    def refresh_evidence(self, *_):
        """Display evidence for the currently visible table, never a hidden stale row."""
        is_timeline = self.tables.currentIndex() == 2
        # Give the chronological table the canvas space. Its graph remains loaded
        # and is shown again on graph tabs; tab changes never rebuild discovery.
        self.canvas.setVisible(not is_timeline)
        self.fit_button.setEnabled(not is_timeline)
        self.direction.setEnabled(not is_timeline)
        self.target.setEnabled(not is_timeline)
        if not is_timeline:
            QTimer.singleShot(0, self.fit)
        self.save.setText(
            "Save timeline to selected investigation"
            if is_timeline
            else "Save expanded graph to selected investigation"
        )
        self.export.setToolTip(
            "Save the visible timeline with timing/coverage evidence."
            if is_timeline
            else "Save the expanded graph with evidence and coverage."
        )
        self.paths.setPlainText(self.timeline_notice if is_timeline else self.graph_paths_text)
        if is_timeline:
            self.show_timeline_evidence()
        elif self.tables.currentIndex() == 1:
            self.show_link_evidence()
        else:
            self.show_evidence()

    def show_link_evidence(self):
        """Expose actual endpoint amounts, requested limits and asset path with a transaction link."""
        self.evidence.clear()
        self.evidence.hide()
        row = self.operation_table.currentRow()
        if not 0 <= row < len(self.links):
            return
        link = self.links[row]
        details = link.description()
        if link.limit_name:
            details += (
                f"\nRequested {link.limit_name}: {link.limit_amount:f} (not an actual amount)"
            )
            details += " • Intermediary assets: " + (" → ".join(link.path) or "None")
        self.evidence.addItem(
            f"{link.created} • {link.operation_type} • Operation {link.operation}\n"
            f"{details}\nTX {link.transaction}"
        )
        item = self.evidence.item(0)
        item.setData(Qt.ItemDataRole.UserRole, link.transaction)
        item.setToolTip(item.text())
        self.evidence.show()

    def show_evidence(self):
        """Expose each unique supporting operation with its transaction and exact amount."""
        self.evidence.clear()
        self.evidence.hide()
        row = self.table.currentRow()
        if not 0 <= row < len(self.connections):
            return
        self.evidence.show()
        for flow in self.connections[row].evidence:
            self.evidence.addItem(
                f"{flow.created} • {flow.operation_type} • {flow.amount:f} {flow.asset} • "
                f"Operation {flow.operation} • TX {flow.transaction}"
            )
            item = self.evidence.item(self.evidence.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, flow.transaction)
            item.setToolTip(item.text())
