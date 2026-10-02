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
from polarstellar.stellar.contracts import ContractProvider
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService, validate_account
from polarstellar.storage.export import (
    account_document,
    activity_document,
    graph_document,
    transaction_document,
)
from polarstellar.ui.activity_view import ActivityView
from polarstellar.ui.asset_view import AssetView
from polarstellar.ui.cache_controls import CacheControls
from polarstellar.ui.contract_view import ContractView
from polarstellar.ui.export_controls import ExportControls
from polarstellar.ui.graph_view import GraphView
from polarstellar.ui.investigations_view import InvestigationsView
from polarstellar.ui.transaction_view import TransactionDialog
from polarstellar.ui.watchlists_view import WatchlistsView


class MainWindow(QMainWindow):
    """The main explorer window, coordinating search, network changes, and local cache controls."""

    def __init__(
        self,
        service: AccountService,
        investigation_store=None,
        contract_provider=None,
        watchlist_store=None,
    ) -> None:
        """Build explorer pages and optionally inject independent local evidence/bookmark stores."""
        super().__init__()
        self.service = service
        self.account = None
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
        # Test providers remain usable without persistence; the application injects caching explicitly.
        if hasattr(service.provider, "set_enabled"):
            self.cache_controls = CacheControls(service.provider, self.invalidate, self.tasks)
            layout.addWidget(self.cache_controls)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Paste a Stellar G-address, C-address, or transaction hash")
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
        self.navigation = navigation
        navigation.addItems(
            [
                "Overview",
                "Transactions",
                "Operations",
                "Payments",
                "Graph",
                "Assets",
                "Contracts",
                "Investigations",
                "Watchlists",
            ]
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
        overview = QWidget()
        overview_layout = QVBoxLayout(overview)
        overview_layout.addWidget(self.balances)
        self.open_balance_asset = QPushButton("Inspect selected asset")
        self.open_balance_asset.setEnabled(False)
        self.open_balance_asset.clicked.connect(self.inspect_balance_asset)
        self.balances.itemSelectionChanged.connect(self.update_balance_asset_button)
        self.balances.cellDoubleClicked.connect(lambda *_: self.inspect_balance_asset())
        overview_layout.addWidget(self.open_balance_asset)
        self.export = ExportControls(lambda: account_document(self.account))
        overview_layout.addWidget(self.export)
        self.pages.addWidget(overview)
        self.activity_views = [ActivityView(service, kind) for kind in ActivityKind]
        for view in self.activity_views:
            self.pages.addWidget(view)
            view.transaction_requested.connect(self.open_transaction)
        self.graph = GraphView(self.activity_views[2])
        self.graph.account_requested.connect(self.investigate_counterparty)
        self.graph.transaction_requested.connect(self.open_transaction)
        self.pages.addWidget(self.graph)
        self.assets = AssetView(service, lambda: Network(self.network.currentText()))
        self.assets.issuer_requested.connect(self.investigate_issuer)
        self.activity_views[2].asset_requested.connect(self.open_asset)
        self.pages.addWidget(self.assets)
        self.contracts = ContractView(
            contract_provider or ContractProvider(), lambda: Network(self.network.currentText())
        )
        self.contracts.transaction_requested.connect(self.open_transaction)
        self.pages.addWidget(self.contracts)
        self.investigations = None
        if investigation_store is not None:
            self.investigations = InvestigationsView(investigation_store, service.provider)
            self.pages.addWidget(self.investigations)
            self.graph.can_save_trace = True
            self.graph.trace_save_requested.connect(self.save_trace)
        # Keep fixed page indexes even when test/embedded clients omit persistence.
        # Watchlist opening relies on the same explorer destinations as normal search.
        while self.pages.count() < 8:
            self.pages.addWidget(QWidget())
        self.watchlists = None
        if watchlist_store is not None:
            self.watchlists = WatchlistsView(
                watchlist_store,
                service.provider,
                self.contracts.provider,
                lambda: Network(self.network.currentText()),
            )
            self.watchlists.open_requested.connect(self.open_watchlist_resource)
            self.pages.addWidget(self.watchlists)
        watch_resource = QPushButton("Add displayed resource to selected watchlist")
        self.watch_resource_button = watch_resource
        watch_resource.setEnabled(self.watchlists is not None)
        watch_resource.clicked.connect(self.watch_resource)
        pane.addWidget(watch_resource)
        save_resource = QPushButton("Save resource to selected investigation")
        self.save_resource_button = save_resource
        save_resource.clicked.connect(self.save_resource)
        save_resource.setEnabled(self.investigations is not None)
        pane.addWidget(save_resource)
        pane.addWidget(self.pages)
        navigation.currentRowChanged.connect(self.select_page)
        content.addLayout(pane, 1)
        layout.addLayout(content, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage("Mainnet selected • Ready")
        disabled = ([7] if self.investigations is None else []) + (
            [8] if self.watchlists is None else []
        )
        for index in disabled:
            item = navigation.item(index)
            item.setText(item.text() + " (planned)")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        self.network.currentTextChanged.connect(self.show_network)
        self.message.setText("Paste a Stellar G-address to inspect its balances and trustlines.")
        # Native Windows/macOS styles can paint light table headers or dark selected
        # text despite the surrounding dark theme. Explicit foreground/background
        # pairs keep these important labels readable on every packaged platform.
        self.setStyleSheet("""
            QWidget { background: #171b24; color: #e5e9f2; font-size: 14px; }
            QLineEdit, QComboBox, QListWidget {
                background: #202634; border: 1px solid #343e52;
                border-radius: 5px; padding: 10px;
            }
            QListWidget::item { padding: 12px 6px; }
            QListWidget::item:selected { background: #34446a; color: #e5e9f2; }
            QHeaderView::section, QTableCornerButton::section {
                background: #202634; color: #e5e9f2;
                border: 1px solid #343e52; padding: 5px;
            }
            QStatusBar { color: #a7b3cc; }
        """)

    def watch_resource(self):
        """Bookmark displayed identity on its actual network, without another provider request."""
        if self.watchlists is None:
            return
        index = self.pages.currentIndex()
        if index == 6 and self.contracts.snapshot is not None:
            snapshot = self.contracts.snapshot
            identity = {
                "kind": "contract",
                "identifier": snapshot["contract"],
                "network": snapshot["network"],
            }
        elif index == 5 and self.assets.snapshot is not None:
            asset = self.assets.snapshot
            identity = {
                "kind": "asset",
                "code": asset.code,
                "issuer": asset.issuer,
                "identifier": asset.code,
                "network": asset.network.value,
            }
        elif index < 5 and self.account is not None:
            identity = {
                "kind": "account",
                "identifier": self.account.address,
                "network": self.account.network.value,
            }
        else:
            self.watchlists.error("Load an account, asset, or contract before bookmarking it.")
            return
        self.watchlists.add_resource(identity)
        self.navigation.setCurrentRow(8)

    def open_watchlist_resource(self, entry):
        """Open the saved identity explicitly, switching networks before any explorer request.

        Explorer opening follows normal cache settings and never changes a watchlist's
        stored snapshot. The separate watchlist Refresh action always bypasses caching.
        """
        self.network.setCurrentText(entry["network"])
        self.invalidate()
        if entry["kind"] == "asset":
            self.message.setText("Asset inspection • " + entry["network"])
            self.open_asset(entry["code"], entry["issuer"])
        else:
            self.search.setText(entry["identifier"])
            self.navigation.setCurrentRow(0 if entry["kind"] == "account" else 6)
            self.start_search()

    def save_trace(self, snapshot):
        """Persist the entire expanded graph, including filters, evidence, routes and page coverage."""
        if self.investigations is not None:
            self.investigations.add_evidence(
                {
                    "kind": "expanded_graph",
                    "identifier": snapshot["metadata"]["root"],
                    "network": snapshot["metadata"]["network"],
                    "evidence": [snapshot],
                }
            )

    def save_transaction(self, dialog):
        """Capture the loaded transaction, including partial evidence and its network."""
        tx = dialog.transaction
        if tx is not None:
            self.investigations.add_evidence(
                {
                    "kind": "transaction",
                    "identifier": tx.hash,
                    "network": tx.network.value,
                    "evidence": [transaction_document(tx)],
                }
            )

    def save_resource(self):
        """Save the displayed asset or account with currently loaded activity and graph evidence."""
        if self.investigations is None:
            return
        if self.pages.currentIndex() == 6 and self.contracts.snapshot is not None:
            snapshot = self.contracts.snapshot
            entry = {
                "kind": "contract",
                "identifier": snapshot["contract"],
                "network": snapshot["network"],
                "evidence": [self.contracts.export_document()],
            }
        elif self.pages.currentIndex() == 5 and self.assets.snapshot is not None:
            asset = self.assets.snapshot
            entry = {
                "kind": "asset",
                "identifier": asset.code + ":" + asset.issuer,
                "code": asset.code,
                "issuer": asset.issuer,
                "network": asset.network.value,
                "evidence": [self.assets.export_document()],
            }
        elif self.pages.currentIndex() < 5 and self.account is not None:
            evidence = [account_document(self.account)]
            evidence.extend(activity_document(view) for view in self.activity_views if view.loaded)
            if self.activity_views[2].loaded:
                evidence.append(graph_document(self.graph))
            entry = {
                "kind": "account",
                "identifier": self.account.address,
                "network": self.account.network.value,
                "evidence": evidence,
            }
        else:
            self.investigations.error(
                "Load an account, asset, or contract first; save transactions from their inspector."
            )
            return
        self.investigations.add_evidence(entry)

    def select_page(self, index):
        """Show a navigation section and lazily load its first page when needed."""
        if index < self.pages.count():
            # Local libraries have their own notices and actions. Hiding explorer
            # controls gives lists/notes room without implying a displayed resource
            # is being saved or fetched simply by opening a local library page.
            for widget in (self.message, self.watch_resource_button, self.save_resource_button):
                widget.setVisible(index < 7)
            self.pages.setCurrentIndex(index)
            if index == 4:
                view = self.activity_views[2]
                if not view.loaded:
                    view.start()
                self.graph.fit()
            elif 0 < index < 4:
                view = self.activity_views[index - 1]
                if not view.loaded:
                    view.start()

    def update_balance_asset_button(self):
        """Enable issued/native asset inspection while excluding liquidity-pool shares."""
        row = self.balances.currentRow()
        valid = self.account is not None and 0 <= row < len(self.account.balances)
        self.open_balance_asset.setEnabled(
            valid and self.account.balances[row].asset != "Pool shares"
        )

    def inspect_balance_asset(self):
        """Open the selected balance using its full issuer identity, not only its code."""
        row = self.balances.currentRow()
        if self.account is None or not 0 <= row < len(self.account.balances):
            return
        balance = self.account.balances[row]
        if balance.asset != "Pool shares":
            self.open_asset(balance.asset, "" if balance.identity == "Native" else balance.identity)

    def open_asset(self, code, issuer):
        """Switch to Assets and inspect the selected code/issuer on the current network."""
        self.navigation.setCurrentRow(5)
        self.assets.open_asset(code, issuer)

    def investigate_issuer(self, address):
        """Load the issuer account; activity sections then show that account's full history."""
        self.navigation.setCurrentRow(0)
        self.investigate_counterparty(address)

    def investigate_counterparty(self, address):
        """Start a new investigation for the account selected in the graph."""
        self.search.setText(address)
        self.start_search()

    def open_transaction(self, hash_value):
        """Open a separate transaction inspector using the currently selected network."""
        dialog = TransactionDialog(
            self.service, hash_value, Network(self.network.currentText()), self
        )
        self.dialogs.add(dialog)
        dialog.finished.connect(lambda: self.dialogs.discard(dialog))
        dialog.finished.connect(dialog.deleteLater)
        if self.investigations is not None:
            save = QPushButton("Save to selected investigation")
            save.clicked.connect(lambda: self.save_transaction(dialog))
            dialog.layout().addWidget(save)
        dialog.show()
        dialog.start()

    def invalidate(self) -> None:
        """Cancel work from the previous context so late results cannot overwrite the current view."""
        if self.watchlists is not None and self.watchlists.task is not None:
            self.watchlists.cancel_refresh()
        self.assets.reset()
        self.contracts.reset()
        self.open_balance_asset.setEnabled(False)
        for dialog in tuple(self.dialogs):
            dialog.reject()
        for view in self.activity_views:
            view.reset()
        self.generation += 1
        if self.task is not None:
            self.task.cancel()
        self.task = None
        self.cancel.setEnabled(False)
        self.account = None
        self.export.setEnabled(False)
        self.balances.setRowCount(0)
        self.message.setText("Ready for a new investigation.")
        self.statusBar().showMessage(f"{self.network.currentText()} selected • Ready")

    def cancel_search(self) -> None:
        """Stop the active lookup and clear its displayed investigation state."""
        self.invalidate()
        self.message.setText("Search cancelled. You can start another lookup.")
        self.statusBar().showMessage(f"{self.network.currentText()} selected • Cancelled")

    def show_network(self, network: str) -> None:
        """Clear previous-network results and prepare the selected network for a new search."""
        self.invalidate()
        self.message.setText(
            f"{network} selected. Inspect the address to fetch this network's data."
        )
        self.statusBar().showMessage(f"{network} selected • Ready")

    def start_search(self) -> None:
        """Validate the entered identifier and start an account or transaction investigation."""
        value = self.search.text().strip()
        if value.startswith("C") and len(value) != 64:
            self.invalidate()
            self.navigation.setCurrentRow(6)
            self.message.setText("Contract inspection • " + self.network.currentText())
            self.contracts.address.setText(value)
            self.contracts.start()
            return
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
        """Display account balances only if this request still belongs to the active search."""
        try:
            account = await self.service.lookup(address, network)
            if generation != self.generation:
                return
            self.account = account
            self.export.setEnabled(True)
            self.message.setText(
                f"{account.address}\n{network.value} • Sequence {account.sequence}\n"
                f"Home domain: {account.home_domain or 'Not set'}\n"
                f"{account.cache_status} • Source: {account.source} • Retrieved {account.fetched_at:%Y-%m-%d %H:%M:%S} UTC"
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
        """Cancel outstanding requests before allowing the window to close."""
        if self.investigations is not None and not self.investigations.allow_close():
            event.ignore()
            return
        if self.watchlists is not None and not self.watchlists.allow_discard():
            event.ignore()
            return
        self.invalidate()
        if self.watchlists is not None:
            self.watchlists.cancel_refresh()
        if self.investigations is not None and self.investigations.task is not None:
            self.investigations.task.cancel()
        for task in self.tasks:
            task.cancel()
        super().closeEvent(event)
