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
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
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
        )

    def render(self, *_):
        """Draw bounded nodes and all filtered table edges; list route evidence without flow claims."""
        connections, excluded, distances = self.trace.build()
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
            "Observed connections only — not proof of the same funds, chronology, or ownership."
        )
        snapshot = self.export_document()
        lines = []
        for account in snapshot["metadata"]["accounts"]:
            lines.append(
                f"{account['address']} • {account['loaded_records']} records • "
                f"{account['oldest_record_time']} to {account['newest_record_time']} • "
                f"{account['expansion_status']}"
            )
            for page in account["pages"]:
                lines.append(
                    f"  {page['source']} • {page['cache_status']} • Retrieved {page['fetched_at']}"
                )
        lines.append(
            f"Excluded unique operations: {excluded}. Unfetched discovered accounts: "
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
            else "Choose one asset and a target to find directed routes. No route in loaded "
            "evidence does not prove no connection exists. Direction applies to routes, not the edge table."
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
                f"{flow.created} • {flow.amount:f} {flow.asset} • "
                f"Operation {flow.operation} • TX {flow.transaction}"
            )
            item = self.evidence.item(self.evidence.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, flow.transaction)
            item.setToolTip(item.text())
