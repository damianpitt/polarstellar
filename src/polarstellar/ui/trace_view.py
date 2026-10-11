"""Interactive, explicitly paginated expansion of an account's observed payment graph."""

import asyncio
import math

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
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
    QVBoxLayout,
)

from polarstellar.analysis.trace_filters import TraceFilters, instant
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
        self.operation_type.addItem("Payments and funding", ("payment", "create_account"))
        self.operation_type.addItem("Direct payments", ("payment",))
        self.operation_type.addItem("Account funding", ("create_account",))
        apply = QPushButton("Apply time / type")
        apply.clicked.connect(self.apply_filters)
        clear = QPushButton("Clear time / type")
        clear.clicked.connect(self.clear_filters)
        for widget in (self.from_time, self.until_time, self.operation_type, apply, clear):
            time_row.addWidget(widget)
        layout.addLayout(time_row)
        self.filter_status = QLabel(
            "Applied: all times, payments and funding. Edits apply only on Apply time / type."
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
        split.addWidget(self.table)
        layout.addWidget(split, 1)
        self.evidence = QListWidget()
        self.evidence.setMaximumHeight(90)
        self.evidence.hide()
        self.evidence.setToolTip("Double-click evidence to inspect the transaction")
        self.evidence.itemDoubleClicked.connect(
            lambda item: self.transaction_requested.emit(item.data(Qt.ItemDataRole.UserRole))
        )
        layout.addWidget(self.evidence)
        self.paths = QPlainTextEdit()
        self.paths.setReadOnly(True)
        self.paths.setMaximumHeight(110)
        layout.addWidget(self.paths)
        self.coverage = QPlainTextEdit()
        self.coverage.setReadOnly(True)
        self.coverage.setMaximumHeight(120)
        layout.addWidget(self.coverage)
        self.export = ExportControls(self.export_document)
        self.export.setEnabled(True)
        layout.addWidget(self.export)
        self.save = QPushButton("Save expanded graph to selected investigation")
        self.save.clicked.connect(lambda: self.save_requested.emit(self.export_document()))
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
            "Applied: all times, payments and funding. Edits apply only on Apply time / type."
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
        for combo, values, default in (
            (
                self.account,
                sorted(distances, key=lambda address: (distances[address], address)),
                None,
            ),
            (self.target, sorted(set(distances) - {self.trace.root}), "Choose path target"),
            (
                self.asset,
                sorted({edge.asset for edge in connections}),
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

    def render(self, *_):
        """Draw bounded nodes and all filtered table edges; list route evidence without flow claims."""
        connections, _, distances = self.trace.build(self.view_filters)
        asset = self.asset.currentData()
        self.connections = [edge for edge in connections if asset is None or edge.asset == asset]
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
        addresses = {self.trace.root}
        for edge in self.connections:
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
        self.canvas.fitInView(
            self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        self.summary.setText(
            f"{self.trace.network.value} • Root: {self.trace.root}\n"
            f"{len(self.trace.pages)}/10 fetched accounts • Up to 3 hops, 5 pages/account. "
            f"Canvas {len(visible)}/{len(addresses)} nodes; table includes all filtered edges.\n"
            "Local filters select evidence; expansion uses unfiltered discovered accounts. "
            "Observed connections only — not proof of the same funds, chronology, or ownership."
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
        self.update_controls()

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
