"""Non-modal transaction inspector with cancellable loading and plain-text decoding."""

import asyncio
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from polarstellar.stellar.effects import MAX_PAGES, merge_comparison
from polarstellar.stellar.providers import AccountError
from polarstellar.storage.export import plain, transaction_document
from polarstellar.ui.export_controls import ExportControls


class TransactionDialog(QDialog):
    """Inspect and export a single transaction with its available operation evidence."""

    def __init__(self, service, hash_value, network, parent=None):
        """Build this view and connect user actions to its data-loading controls."""
        super().__init__(parent)
        self.service = service
        self.transaction = None
        self.hash_value = hash_value
        self.network = network
        self.generation = 0
        self.tasks = set()
        self.task = None
        self.effect_task = None
        self.effect_tasks = set()
        self.effects_generation = 0
        self.effect_pages = {}
        self.setWindowTitle(f"Transaction • {network.value}")
        self.resize(900, 700)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        self.details = QPlainTextEdit()
        self.raw = QPlainTextEdit()
        for editor in (self.details, self.raw):
            editor.setReadOnly(True)
        tabs.addTab(self.details, "Transaction && operations")
        tabs.addTab(self.raw, "Raw Horizon data")
        effect_tab = QWidget()
        effect_layout = QVBoxLayout(effect_tab)
        controls = QHBoxLayout()
        self.effect_operation = QComboBox()
        self.effect_operation.setAccessibleName("Operation effects selection")
        self.effect_operation.currentIndexChanged.connect(self.render_effects)
        self.effect_load = QPushButton("Load effects / next 20")
        self.effect_load.clicked.connect(self.start_effects)
        self.effect_cancel = QPushButton("Cancel effects request")
        self.effect_cancel.clicked.connect(self.cancel_effects)
        for widget in (self.effect_operation, self.effect_load, self.effect_cancel):
            controls.addWidget(widget)
        effect_layout.addLayout(controls)
        self.effect_status = QLabel(
            "Effects load only on an explicit click; up to five pages per operation."
        )
        self.effect_status.setWordWrap(True)
        self.effect_status.setTextFormat(Qt.TextFormat.PlainText)
        effect_layout.addWidget(self.effect_status)
        self.effects = QPlainTextEdit()
        self.effects.setReadOnly(True)
        effect_layout.addWidget(self.effects)
        tabs.addTab(effect_tab, "Operation effects")
        layout.addWidget(tabs)
        actions = QHBoxLayout()
        self.retry = QPushButton("Reload")
        self.retry.clicked.connect(self.start)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        actions.addWidget(self.retry)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.export = ExportControls(self.export_document)
        layout.addWidget(self.export)
        self.finished.connect(self.invalidate)
        self.render_effects()

    def invalidate(self, *_):
        """Cancel work from the previous context so late results cannot overwrite the current view."""
        self.transaction = None
        self.effects_generation += 1
        self.effect_pages = {}
        self.effect_task = None
        self.effect_operation.clear()
        self.export.setEnabled(False)
        self.generation += 1
        for task in self.tasks:
            task.cancel()
        self.task = None
        self.render_effects()

    def start(self):
        """Start one asynchronous request and prevent duplicate concurrent loads."""
        self.invalidate()
        self.retry.setEnabled(False)
        self.details.setPlainText(
            f"Loading transaction on {self.network.value}…\n{self.hash_value}"
        )
        self.raw.clear()
        self.task = asyncio.create_task(self.load(self.generation))
        self.tasks.add(self.task)
        self.task.add_done_callback(self.tasks.discard)

    async def load(self, generation):
        """Load the requested snapshot, reject stale responses, and display its provenance."""
        try:
            tx = await self.service.transaction(self.hash_value, self.network)
            if generation != self.generation:
                return
            self.transaction = tx
            self.export.setEnabled(True)
            status = "SUCCESS" if tx.successful else "FAILED — operation changes were not applied."
            lines = [
                tx.hash,
                f"{tx.network.value} • {status}",
                f"Ledger {tx.ledger} • {tx.created}",
                f"Fee charged: {Decimal(tx.fee_stroops) / Decimal(10000000):.7f} XLM ({tx.fee_stroops} stroops)",
                f"Transaction source: {tx.source_account}",
                f"Fee payer: {tx.fee_account}",
                f"Memo ({tx.memo})",
                f"Operations: {len(tx.operations)} of {tx.operation_count}",
                f"Horizon result: {tx.result_code}",
                "Result verification: envelope/network hash, fees, status, sources and operation alignment; not independent consensus verification.",
                f"{tx.cache_status} • Source: {tx.source} • Retrieved {tx.fetched_at:%Y-%m-%d %H:%M:%S} UTC",
            ]
            if tx.warning:
                lines.extend(["", "PARTIAL DATA: " + tx.warning])
            if tx.result_warning:
                lines.extend(["", "RESULT LIMITATION: " + tx.result_warning])
            for index, operation in enumerate(tx.operations, 1):
                lines.extend(
                    [
                        "",
                        f"{index}. {operation.kind.upper().replace('_', ' ')}",
                        f"Operation {operation.identifier} • Source: {operation.source}",
                        operation.explanation,
                    ]
                )
                if operation.result is not None:
                    outcome = operation.result
                    lines.append(
                        f"Result code: {outcome.code} • {'Applied' if outcome.applied else 'NOT APPLIED'}"
                    )
                    if outcome.merge_amount is not None:
                        lines.append(
                            f"Merge balance from verified Horizon result: {outcome.merge_amount:f} XLM ({outcome.merge_stroops} stroops)."
                        )
            self.effect_operation.blockSignals(True)
            self.effect_operation.clear()
            for operation in tx.operations:
                self.effect_operation.addItem(
                    f"{operation.identifier} • {operation.kind}", operation.identifier
                )
            self.effect_operation.blockSignals(False)
            self.render_effects()
            self.details.setPlainText("\n".join(lines))
            self.raw.setPlainText(
                tx.raw
                + "\n\nOPERATIONS\n"
                + "\n\n".join(operation.raw for operation in tx.operations)
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 -- Keep provider failures inside the dialog.
            if generation == self.generation:
                self.details.setPlainText(
                    str(exc)
                    if isinstance(exc, AccountError)
                    else "Unable to inspect this transaction. Please retry."
                )
        finally:
            if generation == self.generation:
                self.task = None
                self.retry.setEnabled(True)

    def export_document(self):
        """Freeze transaction results and all explicitly loaded effects with separate page provenance."""
        snapshot = transaction_document(self.transaction)
        snapshot["metadata"]["effects_scope"] = (
            "Explicit live reads, up to five pages per successful operation; partial/end-of-results "
            "is not proof of complete history. Graph/timeline evidence is not rewritten."
        )
        for row, operation in zip(snapshot["records"], self.transaction.operations, strict=True):
            pages = self.effect_pages.get(operation.identifier, [])
            row["effect_pages"] = plain(pages)
            row["effect_comparison"] = merge_comparison(operation, pages)
        return snapshot

    def render_effects(self, *_):
        """Show selected operation's accepted pages without fetching or discarding other operation evidence."""
        identifier = self.effect_operation.currentData()
        pages = self.effect_pages.get(identifier, [])
        operation = (
            next((op for op in self.transaction.operations if op.identifier == identifier), None)
            if self.transaction
            else None
        )
        available = (
            self.transaction is not None and self.transaction.successful and operation is not None
        )
        bounded = len(pages) >= MAX_PAGES or bool(pages and pages[-1].next_cursor is None)
        self.effect_load.setEnabled(available and not bounded and self.effect_task is None)
        self.effect_cancel.setEnabled(self.effect_task is not None)
        lines = [
            "Live effects are separate from the transaction cache. Loading sends the selected operation ID to this network's Horizon endpoint.",
            "Only loaded pages are shown; five-page/100-effect limit. End of available results is not proof of complete history.",
        ]
        if operation is not None:
            lines += ["", merge_comparison(operation, pages)]
        for index, page in enumerate(pages, 1):
            lines += [
                "",
                f"Page {index} • {page.network.value} • {page.source} • Retrieved {page.fetched_at.isoformat()} • Cursor {page.cursor} → {page.next_cursor}",
            ]
            for record in page.records:
                lines += [
                    "",
                    f"Effect {record.identifier} • {record.kind}",
                    record.description,
                    record.raw,
                ]
        if not available:
            lines.append(
                "Effects unavailable for an unloaded/failed transaction; no operation changes are treated as applied."
            )
        elif bounded:
            lines.append(
                "Stopped at the five-page limit."
                if len(pages) >= MAX_PAGES and pages[-1].next_cursor
                else "End of available Horizon effects."
            )
        self.effects.setPlainText("\n".join(lines))

    def start_effects(self):
        """Capture transaction/network/operation/cursor and request exactly one effects page."""
        if not self.effect_load.isEnabled() or self.effect_task is not None:
            return
        identifier, transaction = self.effect_operation.currentData(), self.transaction
        pages = self.effect_pages.get(identifier, [])
        cursor = pages[-1].next_cursor if pages else None
        self.effect_status.setText(
            f"Loading effects for {identifier} on {transaction.network.value}…"
        )
        self.effect_task = asyncio.create_task(
            self.load_effects(
                transaction, identifier, cursor, self.generation, self.effects_generation
            )
        )
        self.tasks.add(self.effect_task)
        self.effect_tasks.add(self.effect_task)
        self.effect_task.add_done_callback(self.tasks.discard)
        self.effect_task.add_done_callback(self.effect_tasks.discard)
        self.render_effects()

    async def load_effects(self, transaction, identifier, cursor, generation, effect_generation):
        """Accept one verified page; failures keep prior rows/cursor, cancellation invalidates late providers."""
        try:
            page = await self.service.effects(transaction, identifier, cursor)
            if (generation, effect_generation) != (self.generation, self.effects_generation):
                return
            self.effect_pages.setdefault(identifier, []).append(page)
            notice = (
                "End of available Horizon effects."
                if page.next_cursor is None
                else "Five-page limit reached; effects remain incomplete."
                if len(self.effect_pages[identifier]) >= MAX_PAGES
                else "More pages available."
            )
            self.effect_status.setText(
                f"Loaded {len(page.records)} effects for {identifier}. {notice}"
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 -- A provider failure must preserve the basic transaction and prior pages.
            if (generation, effect_generation) == (self.generation, self.effects_generation):
                self.effect_status.setText(
                    (
                        str(exc)
                        if isinstance(exc, AccountError)
                        else "Unable to load operation effects."
                    )
                    + " Prior pages retained; retry the same cursor."
                )
        finally:
            if (generation, effect_generation) == (self.generation, self.effects_generation):
                self.effect_task = None
                self.render_effects()

    def cancel_effects(self):
        """Cancel only pending effects, preserving loaded transaction/results and already accepted pages."""
        self.effects_generation += 1
        for task in self.effect_tasks:
            task.cancel()
        self.effect_task = None
        self.effect_status.setText(
            "Effects request cancelled; transaction and loaded effects retained."
        )
        self.render_effects()
