"""Account explorer presentation; networking lives behind the account service."""

import asyncio

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from polarstellar import __version__
from polarstellar.stellar.activity import ActivityKind
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService, validate_account
from polarstellar.ui.activity_view import ActivityView
from polarstellar.ui.graph_view import GraphView
from polarstellar.ui.transaction_view import TransactionDialog


class MainWindow(QMainWindow):
    def __init__(self, service: AccountService) -> None:
        super().__init__()
        self.service = service
        self.dialogs = set()
        self.task = None
        self.tasks = set()
        self.generation = 0
        self.setWindowTitle(f"PolarStellar {__version__} — Navigate the Stellar network")
        self.resize(1180, 760)
        self.setMinimumSize(800, 520)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        header = QHBoxLayout()
        brand = QLabel(f"✦  POLARSTELLAR  {__version__}")
        brand.setStyleSheet("font-size: 22px; font-weight: 600;")
        header.addWidget(brand)
        header.addStretch()
        header.addWidget(QLabel("Network"))
        self.network = QComboBox()
        self.network.addItems(["Mainnet", "Testnet"])
        self.network.setAccessibleName("Network")
        header.addWidget(self.network)
        layout.addLayout(header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Paste a Stellar G-address or transaction hash")
        self.search.setAccessibleName("Investigation search")
        search_row = QHBoxLayout()
        search_row.addWidget(self.search)
        self.submit = QPushButton("Inspect")
        self.cancel = QPushButton("Cancel")
        self.cancel.setEnabled(False)
        search_row.addWidget(self.submit)
        search_row.addWidget(self.cancel)
        layout.addLayout(search_row)
        self.submit.clicked.connect(self.start_search)
        self.search.returnPressed.connect(self.start_search)
        self.cancel.clicked.connect(self.cancel_search)
        content = QHBoxLayout()
        navigation = QListWidget()
        navigation.addItems(
            ["Overview", "Transactions", "Operations", "Payments", "Graph", "Assets", "Contracts"]
        )
        navigation.setFixedWidth(180)
        navigation.setCurrentRow(0)
        content.addWidget(navigation)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pane = QVBoxLayout()
        pane.addWidget(self.message)
        self.balances = QTableWidget(0, 5)
        self.balances.setHorizontalHeaderLabels(
            ["Asset", "Issuer / pool identity", "Balance", "Trust limit", "Authorized"]
        )
        self.balances.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.balances.horizontalHeader().setStretchLastSection(True)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.balances)
        self.activity_views = [ActivityView(service, kind) for kind in ActivityKind]
        for view in self.activity_views:
            self.pages.addWidget(view)
            view.transaction_requested.connect(self.open_transaction)
        self.graph = GraphView(self.activity_views[2])
        self.graph.account_requested.connect(self.investigate_counterparty)
        self.graph.transaction_requested.connect(self.open_transaction)
        self.pages.addWidget(self.graph)
        pane.addWidget(self.pages)
        navigation.currentRowChanged.connect(self.select_page)
        content.addLayout(pane, 1)
        layout.addLayout(content, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage("Mainnet selected • Ready")
        for index in range(5, navigation.count()):
            item = navigation.item(index)
            item.setText(item.text() + " (planned)")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        self.network.currentTextChanged.connect(self.show_network)
        self.message.setText("Paste a Stellar G-address to inspect its balances and trustlines.")
        self.setStyleSheet("""
            QWidget { background: #171b24; color: #e5e9f2; font-size: 14px; }
            QLineEdit, QComboBox, QListWidget {
                background: #202634; border: 1px solid #343e52;
                border-radius: 5px; padding: 10px;
            }
            QListWidget::item { padding: 12px 6px; }
            QListWidget::item:selected { background: #34446a; }
            QStatusBar { color: #a7b3cc; }
        """)

    def select_page(self, index):
        if index < self.pages.count():
            self.pages.setCurrentIndex(index)
            if index == 4:
                view = self.activity_views[2]
                if not view.loaded:
                    view.start()
                self.graph.fit()
            elif index > 0:
                view = self.activity_views[index - 1]
                if not view.loaded:
                    view.start()

    def investigate_counterparty(self, address):
        self.search.setText(address)
        self.start_search()

    def open_transaction(self, hash_value):
        dialog = TransactionDialog(
            self.service, hash_value, Network(self.network.currentText()), self
        )
        self.dialogs.add(dialog)
        dialog.finished.connect(lambda: self.dialogs.discard(dialog))
        dialog.finished.connect(dialog.deleteLater)
        dialog.show()
        dialog.start()

    def invalidate(self) -> None:
        for dialog in tuple(self.dialogs):
            dialog.reject()
        for view in self.activity_views:
            view.reset()
        self.generation += 1
        if self.task is not None:
            self.task.cancel()
        self.task = None
        self.cancel.setEnabled(False)
        self.balances.setRowCount(0)

    def cancel_search(self) -> None:
        self.invalidate()
        self.message.setText("Search cancelled. You can start another lookup.")
        self.statusBar().showMessage(f"{self.network.currentText()} selected • Cancelled")

    def show_network(self, network: str) -> None:
        self.invalidate()
        self.message.setText(
            f"{network} selected. Inspect the address to fetch this network's data."
        )
        self.statusBar().showMessage(f"{network} selected • Ready")

    def start_search(self) -> None:
        value = self.search.text().strip()
        if len(value) == 64:
            self.invalidate()
            self.message.setText("Transaction inspection • " + self.network.currentText())
            self.open_transaction(value)
            return
        self.invalidate()
        network = Network(self.network.currentText())
        try:
            address = validate_account(self.search.text())
        except AccountError as exc:
            self.message.setText(str(exc))
            self.statusBar().showMessage(f"{network.value} • Invalid address")
            return
        self.search.setText(address)
        self.message.setText(f"Loading account from {network.value}…")
        self.statusBar().showMessage(f"{network.value} • Loading")
        self.cancel.setEnabled(True)
        self.task = asyncio.create_task(self.load_account(address, network, self.generation))
        self.tasks.add(self.task)
        self.task.add_done_callback(self.tasks.discard)

    async def load_account(self, address: str, network: Network, generation: int) -> None:
        try:
            account = await self.service.lookup(address, network)
            if generation != self.generation:
                return
            self.message.setText(
                f"{account.address}\n{network.value} • Sequence {account.sequence}\n"
                f"Home domain: {account.home_domain or 'Not set'}\n"
                f"Source: {account.source} • Retrieved {account.fetched_at:%Y-%m-%d %H:%M:%S} UTC"
            )
            self.balances.setRowCount(len(account.balances))
            for row, balance in enumerate(account.balances):
                values = [
                    balance.asset,
                    balance.identity,
                    str(balance.amount),
                    str(balance.limit) if balance.limit is not None else "—",
                    "Unknown / N/A"
                    if balance.authorized is None
                    else "Yes"
                    if balance.authorized
                    else "No",
                ]
                for column, value in enumerate(values):
                    self.balances.setItem(row, column, QTableWidgetItem(value))
            self.balances.resizeColumnsToContents()
            if not account.balances:
                self.message.setText(self.message.text() + "\nNo balances returned.")
            for view in self.activity_views:
                view.reset((address, network))
            self.select_page(self.pages.currentIndex())
            self.statusBar().showMessage(f"{network.value} • Account loaded")
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 -- UI boundary must recover from provider failures.
            if generation == self.generation:
                self.message.setText(
                    str(exc)
                    if isinstance(exc, AccountError)
                    else "Unable to load this account. Please try again."
                )
                self.statusBar().showMessage(f"{network.value} • Lookup failed")
        finally:
            if generation == self.generation:
                self.cancel.setEnabled(False)
                self.task = None

    def closeEvent(self, event) -> None:
        self.invalidate()
        for task in self.tasks:
            task.cancel()
        super().closeEvent(event)
