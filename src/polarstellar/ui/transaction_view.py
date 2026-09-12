"""Non-modal transaction inspector with cancellable loading and plain-text decoding."""

import asyncio
from decimal import Decimal

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
)

from polarstellar.stellar.providers import AccountError


class TransactionDialog(QDialog):
    def __init__(self, service, hash_value, network, parent=None):
        """Build this view and connect user actions to its data-loading controls."""
        super().__init__(parent)
        self.service = service
        self.hash_value = hash_value
        self.network = network
        self.generation = 0
        self.tasks = set()
        self.task = None
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
        layout.addWidget(tabs)
        actions = QHBoxLayout()
        self.retry = QPushButton("Reload")
        self.retry.clicked.connect(self.start)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        actions.addWidget(self.retry)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.finished.connect(self.invalidate)

    def invalidate(self, *_):
        """Cancel work from the previous context so late results cannot overwrite the current view."""
        self.generation += 1
        for task in self.tasks:
            task.cancel()
        self.task = None

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
                f"{tx.cache_status} • Source: {tx.source} • Retrieved {tx.fetched_at:%Y-%m-%d %H:%M:%S} UTC",
            ]
            if tx.warning:
                lines.extend(["", "PARTIAL DATA: " + tx.warning])
            for index, operation in enumerate(tx.operations, 1):
                lines.extend(
                    [
                        "",
                        f"{index}. {operation.kind.upper().replace('_', ' ')}",
                        f"Operation {operation.identifier} • Source: {operation.source}",
                        operation.explanation,
                    ]
                )
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
