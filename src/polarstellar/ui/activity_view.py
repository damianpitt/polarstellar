"""A paginated activity list scoped to a single account and network."""

import asyncio

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from polarstellar.stellar.activity import HEADERS, ActivityKind
from polarstellar.stellar.providers import AccountError


class ActivityView(QWidget):
    transaction_requested = Signal(str)

    def __init__(self, service, kind: ActivityKind):
        super().__init__()
        self.service = service
        self.kind = kind
        self.context = None
        self.generation = 0
        self.task = None
        self.tasks = set()
        self.cursor = None
        self.loaded = False
        self.done = False
        self.identifiers = set()
        layout = QVBoxLayout(self)
        self.summary = QLabel("Inspect an account to view activity.")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, len(HEADERS[kind]))
        self.table.setHorizontalHeaderLabels(HEADERS[kind])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)
        self.table.cellDoubleClicked.connect(self.open_row)
        self.open_transaction = QPushButton("Open selected transaction")
        self.open_transaction.setEnabled(False)
        self.open_transaction.clicked.connect(lambda: self.open_row(self.table.currentRow(), 0))
        self.table.itemSelectionChanged.connect(
            lambda: self.open_transaction.setEnabled(self.table.currentRow() >= 0)
        )
        layout.addWidget(self.open_transaction)
        self.more = QPushButton("Load activity")
        self.more.setEnabled(False)
        self.more.clicked.connect(self.start)
        layout.addWidget(self.more)

    def open_row(self, row, _column):
        if self.context is None or row < 0:
            return
        column = 1 if self.kind == ActivityKind.TRANSACTIONS else self.table.columnCount() - 1
        item = self.table.item(row, column)
        if item is not None:
            self.transaction_requested.emit(item.text())

    def reset(self, context=None):
        self.generation += 1
        for task in self.tasks:
            task.cancel()
        self.task = None
        self.context = context
        self.cursor = None
        self.loaded = False
        self.done = False
        self.identifiers.clear()
        self.table.setRowCount(0)
        self.summary.setText(
            "Newest first • Horizon coverage may be limited."
            if context
            else "Inspect an account to view activity."
        )
        self.more.setText("Load activity")
        self.more.setEnabled(context is not None)

    def start(self):
        if self.context is None or self.task is not None or self.done:
            return
        self.more.setEnabled(False)
        self.summary.setText(
            f"Loading {self.kind.value}… {self.table.rowCount()} records retained."
        )
        self.task = asyncio.create_task(self.load(self.context, self.generation))
        self.tasks.add(self.task)
        self.task.add_done_callback(self.tasks.discard)

    async def load(self, context, generation):
        try:
            page = await self.service.activity(*context, self.kind, self.cursor)
            if generation != self.generation:
                return
            for record in page.records:
                if record.identifier in self.identifiers:
                    continue
                self.identifiers.add(record.identifier)
                row = self.table.rowCount()
                self.table.insertRow(row)
                for column, value in enumerate(record.values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    self.table.setItem(row, column, item)
            self.cursor = page.next_cursor
            self.loaded = True
            self.done = page.next_cursor is None
            self.table.resizeColumnsToContents()
            for column in range(self.table.columnCount()):
                self.table.setColumnWidth(column, min(self.table.columnWidth(column), 280))
            boundary = "End of available results." if self.done else "Load more for older records."
            self.summary.setText(
                f"{self.table.rowCount()} {self.kind.value} loaded • {context[1].value} • Newest first. "
                f"{boundary}\nHorizon coverage may be limited; failed records are labeled. "
                f"Source: {page.source} • Retrieved {page.fetched_at:%Y-%m-%d %H:%M:%S} UTC"
            )
            self.more.setText("No more results" if self.done else "Load more")
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 -- Recover at the UI boundary without losing rows.
            if generation == self.generation:
                message = str(exc) if isinstance(exc, AccountError) else "Unable to load activity."
                self.summary.setText(f"{message} {self.table.rowCount()} records retained.")
                self.more.setText("Retry")
        finally:
            if generation == self.generation:
                self.task = None
                self.more.setEnabled(not self.done and self.context is not None)
