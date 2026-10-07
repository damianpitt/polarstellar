"""Explicit, cancellable catalog discovery with independent pages and portable local exports."""

import asyncio

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from polarstellar.stellar.asset_discovery import MAX_PAGES, filters
from polarstellar.stellar.providers import AccountError
from polarstellar.storage.export import document, plain
from polarstellar.ui.export_controls import ExportControls


class AssetDiscoveryView(QWidget):
    """Browse issued assets without conflating codes, valuing holdings, or crawling automatically."""

    asset_requested = Signal(str, str)

    def __init__(self, service, network):
        """Build optional exact filters; freeze network/query only when Search is chosen."""
        super().__init__()
        self.service, self.network = service, network
        self.generation, self.task, self.tasks = 0, None, set()
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Issued assets in Horizon statistics only • Exact, case-sensitive filters; "
            "leave both blank to browse. Not a ranking, valuation, or endorsement. "
            "XLM here means issued assets named XLM; inspect native XLM in Inspect asset."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        form = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("Asset code filter (optional)")
        self.code.setAccessibleName("Discovery asset code filter")
        self.issuer = QLineEdit()
        self.issuer.setPlaceholderText("Issuer G-address filter (optional)")
        self.issuer.setAccessibleName("Discovery issuer filter")
        self.search = QPushButton("Search / restart")
        self.search.clicked.connect(self.start)
        self.code.returnPressed.connect(self.start)
        self.issuer.returnPressed.connect(self.start)
        for widget in (self.code, self.issuer, self.search):
            form.addWidget(widget)
        layout.addLayout(form)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            [
                "Code",
                "Issuer",
                "Type",
                "Authorized accounts",
                "Authorized account balance",
                "Approval required",
            ]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self.controls)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_selected())
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.more = QPushButton("Load more (20)")
        self.more.clicked.connect(self.load_more)
        self.cancel = QPushButton("Cancel discovery")
        self.cancel.clicked.connect(self.cancel_request)
        self.open = QPushButton("Inspect selected asset")
        self.open.clicked.connect(self.open_selected)
        for widget in (self.more, self.cancel, self.open):
            actions.addWidget(widget)
        layout.addLayout(actions)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.export = ExportControls(self.export_document)
        layout.addWidget(self.export)
        self.reset()

    def controls(self):
        """Keep lookup, pagination and export actions consistent with the captured query state."""
        self.more.setEnabled(
            self.context is not None
            and self.task is None
            and not self.done
            and len(self.pages) < MAX_PAGES
        )
        self.cancel.setEnabled(self.task is not None)
        self.open.setEnabled(0 <= self.table.currentRow() < len(self.items))
        self.export.setEnabled(bool(self.pages))

    def cancel_request(self):
        """Retain loaded rows/cursor, but reject late results even if a provider ignores cancellation."""
        self.generation += 1
        for task in self.tasks:
            task.cancel()
        self.task = None
        self.controls()
        self.status.setText("Discovery cancelled; loaded rows retained. Load more to retry.")

    def reset(self):
        """Clear the entire captured query when the main network/investigation context changes."""
        self.generation += 1
        for task in self.tasks:
            task.cancel()
        self.task = None
        self.context, self.cursor, self.done = None, None, False
        self.items, self.pages, self.cursors = [], [], set()
        self.code.clear()
        self.issuer.clear()
        self.table.setRowCount(0)
        self.controls()
        self.status.setText("Choose filters and Search to fetch the first 20 issued assets.")

    def start(self):
        """Validate before requesting data and discard the previous query only on explicit restart."""
        code, issuer = self.code.text(), self.issuer.text()
        try:
            code, issuer = filters(code, issuer)
        except AccountError as exc:
            self.status.setText(str(exc) + " Previous results retain their original filters.")
            return
        self.reset()
        self.code.setText(code)
        self.issuer.setText(issuer)
        self.context = (code, issuer, self.network())
        self.load_more()

    def load_more(self):
        """Request exactly one page using frozen filters, not unsubmitted form edits."""
        if (
            self.context is None
            or self.task is not None
            or self.done
            or len(self.pages) >= MAX_PAGES
        ):
            return
        self.task = asyncio.create_task(self.load(self.context, self.cursor, self.generation))
        self.tasks.add(self.task)
        self.task.add_done_callback(self.tasks.discard)
        self.controls()
        self.status.setText("Loading a live catalog page; earlier rows retained…")

    async def load(self, context, cursor, generation):
        """Accept pages atomically; reject cursor cycles and conflicting overlapping statistics."""
        try:
            page = await self.service.discover_assets(*context, cursor)
            if generation != self.generation:
                return
            if page.next_cursor is not None and page.next_cursor in self.cursors:
                raise AccountError("Asset cursor repeated. Restart discovery; old rows retained.")
            merged = {(item.code, item.issuer): item for item in self.items}
            for item in page.items:
                identity = (item.code, item.issuer)
                if identity in merged:
                    old = merged[identity]
                    # Pages are live observations taken at different times. Never quietly
                    # combine two conflicting snapshots into an apparently stable catalog.
                    if (old.asset_type, old.statistics, old.flags) != (
                        item.asset_type,
                        item.statistics,
                        item.flags,
                    ):
                        raise AccountError(
                            "Asset statistics changed across overlapping pages. "
                            "Restart discovery; old rows retained."
                        )
                else:
                    merged[identity] = item
            # All checks finish before state changes, preserving the previous cursor on failure.
            self.items = list(merged.values())
            self.pages.append(
                {
                    "source": page.source,
                    "fetched_at": page.fetched_at,
                    "requested_cursor": page.cursor,
                    "next_cursor": page.next_cursor,
                    "returned_records": len(page.items),
                    "done": page.done,
                }
            )
            self.cursor, self.done = page.next_cursor, page.done
            if page.next_cursor is not None:
                self.cursors.add(page.next_cursor)
            self.table.setRowCount(len(self.items))
            for row, item in enumerate(self.items):
                required = item.flags.get("auth_required")
                values = [
                    item.code,
                    item.issuer,
                    item.asset_type,
                    item.statistics.get("accounts.authorized"),
                    item.statistics.get("balances.authorized"),
                    "Unknown" if required is None else "Yes" if required else "No",
                ]
                for column, value in enumerate(values):
                    cell = QTableWidgetItem("Unknown" if value is None else str(value))
                    cell.setToolTip(item.issuer if column == 1 else cell.text())
                    self.table.setItem(row, column, cell)
            self.table.resizeColumnsToContents()
            self.describe()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 -- Preserve evidence/cursor on provider or parsing errors.
            if generation == self.generation:
                self.status.setText(
                    (
                        str(exc)
                        if isinstance(exc, AccountError)
                        else "Unable to load asset discovery page."
                    )
                    + " Previous rows retained. Load more to retry or restart."
                )
        finally:
            if generation == self.generation:
                self.task = None
                self.controls()

    def describe(self):
        """Label frozen filters, page provenance and bounded coverage without claiming completeness."""
        code, issuer, network = self.context
        boundary = "Reached current query end." if self.done else "Load more for another page."
        if len(self.pages) >= MAX_PAGES and not self.done:
            boundary = f"Stopped at {MAX_PAGES} pages; coverage incomplete."
        self.status.setText(
            f"{network.value} • Code: {code or 'Any'} • Issuer: {issuer or 'Any'}\n"
            f"{len(self.items)} distinct assets • {len(self.pages)} pages • {boundary}\n"
            f"Source: {self.pages[-1]['source']} • Retrieved: {self.pages[-1]['fetched_at'].isoformat()}\n"
            "Live pages may span different times. Authorized account balance is not total supply "
            "or market value; missing fields are unknown. No lifetime catalog claim."
        )

    def open_selected(self):
        """Inspect the exact selected pair with a separate lookup; keep discovery pages available."""
        row = self.table.currentRow()
        if 0 <= row < len(self.items):
            item = self.items[row]
            self.asset_requested.emit(item.code, item.issuer)

    def export_document(self):
        """Export loaded rows only, retaining exact values, filters, per-page provenance and limits."""
        code, issuer, network = self.context
        return document(
            "asset_discovery",
            {
                "network": network.value,
                "asset_code_filter": code,
                "asset_issuer_filter": issuer,
                "order": "asc",
                "pages": plain(self.pages),
                "page_limit": MAX_PAGES,
                "query_end_reached": self.done,
                "cache_status": "Live data; discovery not cached",
                "coverage": "Loaded issued-asset statistics only, across page retrieval times. "
                "Not lifetime coverage, supply, valuation, or endorsement; native XLM excluded.",
            },
            self.items,
        )
