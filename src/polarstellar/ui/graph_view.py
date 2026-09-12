"""Counterparty table and bounded interactive one-hop relationship graph."""

import math

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from polarstellar.analysis.counterparties import analyze


class Canvas(QGraphicsView):
    """A pannable relationship canvas with bounded mouse-wheel zoom."""

    def wheelEvent(self, event):
        """Zoom within a bounded scale so the graph cannot become unusably small or large."""
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        if 0.1 <= self.transform().m11() * factor <= 5:
            self.scale(factor, factor)
        event.accept()


class Node(QGraphicsEllipseItem):
    """A movable account node that can open an investigation or copy its address."""

    def __init__(self, address, activate, central=False):
        """Build this view and connect user actions to its data-loading controls."""
        super().__init__(-24, -24, 48, 48)
        self.address = address
        self.activate = activate
        self.edges = []
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setBrush(QColor("#698ddb" if central else "#8872b5"))
        self.setPen(QPen(QColor("#c1cceb"), 1))
        self.setToolTip(address + "\nDouble-click: investigate • Right-click: copy address")

    def itemChange(self, change, value):
        """Move connected arrows whenever a draggable account node changes position."""
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for edge in self.edges:
                edge.update()
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event):
        """Defer account navigation until the current graphics event has finished."""
        address, activate = self.address, self.activate
        QTimer.singleShot(0, lambda: activate(address))
        event.accept()

    def contextMenuEvent(self, event):
        """Copy this node’s full address to the clipboard."""
        QApplication.clipboard().setText(self.address)
        event.accept()


class Edge:
    """A directed relationship arrow that follows its source and target nodes."""

    def __init__(self, scene, source, target, tooltip):
        """Build this view and connect user actions to its data-loading controls."""
        self.source, self.target = source, target
        self.line = scene.addLine(0, 0, 0, 0, QPen(QColor("#6983ab"), 2))
        self.arrow = scene.addPolygon(QPolygonF(), QPen(QColor("#6983ab")), QColor("#6983ab"))
        for item in (self.line, self.arrow):
            item.setZValue(-1)
            item.setToolTip(tooltip)
        source.edges.append(self)
        target.edges.append(self)
        self.update()

    def update(self):
        """Recompute the line and arrowhead after either endpoint moves."""
        a, b = self.source.pos(), self.target.pos()
        dx, dy = b.x() - a.x(), b.y() - a.y()
        length = math.hypot(dx, dy) or 1
        unit = QPointF(dx / length, dy / length)
        normal = QPointF(-unit.y(), unit.x())
        # Offset reciprocal directions so both arrows remain visible.
        start = a + unit * 28 + normal * 5
        end = b - unit * 28 + normal * 5
        self.line.setLine(start.x(), start.y(), end.x(), end.y())
        self.arrow.setPolygon(
            QPolygonF([end, end - unit * 12 + normal * 5, end - unit * 12 - normal * 5])
        )


class GraphView(QWidget):
    """Counterparty evidence and a one-hop graph derived from fetched payment records."""

    account_requested = Signal(str)
    transaction_requested = Signal(str)

    def __init__(self, payments):
        """Build this view and connect user actions to its data-loading controls."""
        super().__init__()
        self.payments = payments
        self.relationships = []
        self.edges = []
        self.context = None
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        controls = QHBoxLayout()
        self.asset = QComboBox()
        self.asset.addItem("All assets", None)
        self.asset.setAccessibleName("Graph asset filter")
        self.asset.setMinimumContentsLength(18)
        self.asset.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.direction = QComboBox()
        self.direction.addItems(["Both directions", "Incoming", "Outgoing"])
        self.load = QPushButton("Load payments")
        self.load.clicked.connect(payments.start)
        fit = QPushButton("Fit graph")
        fit.clicked.connect(self.fit)
        for widget in (self.asset, self.direction, self.load, fit):
            controls.addWidget(widget)
        layout.addLayout(controls)
        split = QSplitter(Qt.Orientation.Vertical)
        self.scene = QGraphicsScene(self)
        self.canvas = Canvas(self.scene)
        self.canvas.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.canvas.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.canvas.setMinimumHeight(160)
        split.addWidget(self.canvas)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Counterparty", "Direction", "Asset / issuer", "Total", "Operations"]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self.show_evidence)
        self.table.cellDoubleClicked.connect(self.open_counterparty)
        self.table.setMinimumHeight(130)
        split.addWidget(self.table)
        split.setSizes([240, 160])
        split.setCollapsible(1, False)
        layout.addWidget(split, 1)
        self.evidence = QListWidget()
        self.evidence.setMaximumHeight(75)
        self.evidence.hide()
        self.evidence.setToolTip("Double-click evidence to open its transaction")
        self.evidence.itemDoubleClicked.connect(
            lambda item: self.transaction_requested.emit(item.data(Qt.ItemDataRole.UserRole))
        )
        layout.addWidget(self.evidence)
        self.asset.currentIndexChanged.connect(self.render)
        self.direction.currentIndexChanged.connect(self.render)
        payments.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        """Rebuild analysis from the Payments view while preserving the selected asset when possible."""
        self.context = self.payments.context
        self.load.setEnabled(
            self.context is not None and not self.payments.done and self.payments.task is None
        )
        self.load.setText("Loading…" if self.payments.task else self.payments.more.text())
        self.analysis = analyze(self.payments.records, *self.context) if self.context else None
        selected = self.asset.currentData()
        self.asset.blockSignals(True)
        self.asset.clear()
        self.asset.addItem("All assets", None)
        assets = sorted({r.asset for r in self.analysis.relationships}) if self.analysis else []
        for asset in assets:
            self.asset.addItem(asset, asset)
        self.asset.setCurrentIndex(max(0, self.asset.findData(selected)))
        self.asset.blockSignals(False)
        self.render()

    def fit(self):
        """Fit all currently drawn graph items within the visible canvas."""
        if self.scene.items():
            self.canvas.fitInView(
                self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40),
                Qt.AspectRatioMode.KeepAspectRatio,
            )

    def render(self, *_):
        """Apply filters and render a bounded graph alongside the complete counterparty table."""
        self.edges.clear()
        self.scene.clear()
        self.evidence.clear()
        self.evidence.hide()
        self.relationships = []
        self.table.setRowCount(0)
        if self.analysis is None:
            self.summary.setText("Inspect an account to discover its direct counterparties.")
            return
        chosen, direction = self.asset.currentData(), self.direction.currentText()
        self.relationships = [
            r
            for r in self.analysis.relationships
            if (chosen is None or r.asset == chosen)
            and (direction == "Both directions" or r.direction == direction)
        ]
        self.table.setRowCount(len(self.relationships))
        for row, relation in enumerate(self.relationships):
            for column, value in enumerate(
                [
                    relation.counterparty,
                    relation.direction,
                    relation.asset,
                    format(relation.total, "f"),
                    str(len(relation.evidence)),
                ]
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        for column in (0, 2):
            self.table.setColumnWidth(column, 230)
        peers = list(dict.fromkeys(r.counterparty for r in self.relationships))
        visible = peers[:30]
        network = self.context[1].value
        excluded = (
            "; ".join(f"{reason}: {count}" for reason, count in self.analysis.excluded.items())
            or "none"
        )
        page = self.payments.last_page
        provenance = (
            f"{page.cache_status} • Source: {page.source} • Retrieved {page.fetched_at:%Y-%m-%d %H:%M:%S} UTC"
            if page
            else "No payments fetched yet."
        )
        self.summary.setText(
            f"{network} • {len(self.payments.records)} loaded records • {len(peers)} counterparties "
            f"(graph: {len(visible)}). Ranked by operation count.\n"
            f"Direct payments/funding only • {sum(self.analysis.excluded.values())} excluded "
            f"• Loaded Horizon history, not lifetime totals.\n"
            f"Payments: {self.payments.summary.text().splitlines()[0]}"
        )
        self.summary.setToolTip(f"{provenance}\nExcluded: {excluded}")
        center_address = self.context[0]
        nodes = {}
        for index, address in enumerate([center_address, *visible]):
            node = Node(address, self.account_requested.emit, index == 0)
            self.scene.addItem(node)
            if index:
                angle = 2 * math.pi * (index - 1) / len(visible)
                radius = max(230, len(visible) * 18)
                node.setPos(radius * math.cos(angle), radius * math.sin(angle))
            label = self.scene.addSimpleText(
                "Account" if index == 0 else address[:3] + "…" + address[-3:]
            )
            label.setBrush(QColor("#e5e9f2"))
            label.setFont(QFont("", 9))
            label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            label.setParentItem(node)
            label.setPos(-80, 40)
            nodes[address] = node
        pairs = {}
        for source, target, relation in self.analysis.graph.edges(data="relationship"):
            if relation in self.relationships and source in nodes and target in nodes:
                pairs.setdefault((source, target), []).append(relation)
        for (source, target), relations in pairs.items():
            tooltip = "\n".join(
                f"{r.total:f} {r.asset} • {len(r.evidence)} operations" for r in relations
            )
            self.edges.append(Edge(self.scene, nodes[source], nodes[target], tooltip))
        self.fit()

    def open_counterparty(self, row, _column):
        """Defer investigation of the selected counterparty until the table event completes."""
        if 0 <= row < len(self.relationships):
            address = self.relationships[row].counterparty
            QTimer.singleShot(0, lambda: self.account_requested.emit(address))

    def show_evidence(self):
        """List the operations supporting the selected relationship for transaction inspection."""
        self.evidence.clear()
        self.evidence.hide()
        row = self.table.currentRow()
        if not 0 <= row < len(self.relationships):
            return
        self.evidence.show()
        for flow in self.relationships[row].evidence:
            self.evidence.addItem(
                f"{flow.created} • {flow.amount:f} {flow.asset} • Operation {flow.operation} • TX {flow.transaction}"
            )
            item = self.evidence.item(self.evidence.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, flow.transaction)
            item.setToolTip(item.text())
