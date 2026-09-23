"""Searchable asset inspection with exact identity, cancellable requests, and local export."""

import asyncio

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from polarstellar.stellar.providers import AccountError
from polarstellar.storage.export import document, plain
from polarstellar.ui.export_controls import ExportControls

FLAG_LABELS = {
    "auth_required": "Issuer approval required to hold the asset",
    "auth_revocable": "Issuer can revoke authorization",
    "auth_immutable": "Authorization flags are immutable",
    "auth_clawback_enabled": "New trustlines have clawback enabled",
}


class AssetView(QWidget):
    """Inspect issued assets without conflating their code with issuer identity."""

    issuer_requested = Signal(str)

    def __init__(self, service, network):
        """Build the form; obtain the active network only when a search starts."""
        super().__init__()
        self.service, self.network = service, network
        self.snapshot = None
        self.generation = 0
        self.tasks = set()
        self.task = None
        layout = QVBoxLayout(self)
        form = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("Asset code (e.g. USDC or XLM)")
        self.code.setAccessibleName("Asset code")
        self.issuer = QLineEdit()
        self.issuer.setPlaceholderText("Issuer G-address; empty only for native XLM")
        self.issuer.setAccessibleName("Asset issuer")
        self.inspect = QPushButton("Inspect asset")
        self.inspect.clicked.connect(self.start)
        self.code.returnPressed.connect(self.start)
        self.issuer.returnPressed.connect(self.start)
        for widget in (self.code, self.issuer, self.inspect):
            form.addWidget(widget)
        layout.addLayout(form)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        self.cancel = QPushButton("Cancel asset lookup")
        self.cancel.clicked.connect(self.cancel_lookup)
        layout.addWidget(self.cancel)
        self.open_issuer = QPushButton("Inspect issuer account and activity")
        self.open_issuer.clicked.connect(self.inspect_issuer)
        layout.addWidget(self.open_issuer)
        self.export = ExportControls(self.export_document)
        layout.addWidget(self.export)
        self.reset()

    def reset(self):
        """Clear data and invalidate late responses when network or investigation changes."""
        self.generation += 1
        for task in self.tasks:
            task.cancel()
        self.task = None
        self.snapshot = None
        self.code.clear()
        self.issuer.clear()
        self.export.setEnabled(False)
        self.open_issuer.setEnabled(False)
        self.cancel.setEnabled(False)
        self.details.setPlainText(
            "Enter an asset code and issuer, or open an asset from balances "
            "or Payments. Native XLM has no issuer. Pool shares and standalone "
            "contract tokens are not supported in this view."
        )

    def cancel_lookup(self):
        """Cancel the request and discard its snapshot so it cannot be exported accidentally."""
        self.reset()
        self.details.setPlainText("Asset lookup cancelled. Enter an asset to try again.")

    def open_asset(self, code, issuer):
        """Populate the exact asset selected elsewhere and begin inspection."""
        self.code.setText(code)
        self.issuer.setText(issuer)
        self.start()

    def start(self):
        """Capture identity and network before clearing previous data and starting a request."""
        code, issuer, network = self.code.text(), self.issuer.text(), self.network()
        self.reset()
        self.code.setText(code)
        self.issuer.setText(issuer)
        self.details.setPlainText(f"Loading asset on {network.value}…")
        self.cancel.setEnabled(True)
        self.task = asyncio.create_task(self.load(code, issuer, network, self.generation))
        self.tasks.add(self.task)
        self.task.add_done_callback(self.tasks.discard)

    async def load(self, code, issuer, network, generation):
        """Render only the active lookup; failures leave export and issuer navigation disabled."""
        try:
            detail = await self.service.asset(code, issuer, network)
            # Some providers may finish after cancellation, so cancellation alone is insufficient.
            if generation != self.generation:
                return
            self.snapshot = detail
            lines = [
                f"{detail.code} • {detail.network.value}",
                f"Issuer: {detail.issuer or 'None — native XLM'}",
                f"Type: {detail.asset_type}",
                detail.coverage,
                "",
                "Statistics",
            ]
            for key, value in detail.statistics.items():
                lines.append(
                    f"{key.replace('_', ' ').replace('.', ' / ')}: "
                    f"{value if value is not None else 'Unknown / not provided'}"
                )
            lines.extend(["", "Issuer authorization flags"])
            for key, value in detail.flags.items():
                label = "Unknown / not provided" if value is None else "Yes" if value else "No"
                lines.append(f"{FLAG_LABELS[key]}: {label}")
            lines.extend(
                [
                    "",
                    f"Source: {detail.source}",
                    detail.cache_status,
                    f"Retrieved: {detail.fetched_at.isoformat()}",
                    "Issuer activity is account-wide, not filtered to this asset.",
                ]
            )
            self.details.setPlainText("\n".join(lines))
            self.export.setEnabled(True)
            self.open_issuer.setEnabled(bool(detail.issuer))
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 -- Keep network errors inside this view.
            if generation == self.generation:
                self.details.setPlainText(
                    str(exc)
                    if isinstance(exc, AccountError)
                    else "Unable to inspect this asset. Please retry."
                )
        finally:
            if generation == self.generation:
                self.task = None
                self.cancel.setEnabled(False)

    def inspect_issuer(self):
        """Navigate using the displayed snapshot, never unsubmitted form edits."""
        if self.snapshot and self.snapshot.issuer:
            self.issuer_requested.emit(self.snapshot.issuer)

    def export_document(self):
        """Reuse the export schema with asset identity, flags, exact statistics, and provenance."""
        metadata = plain(self.snapshot)
        statistics = metadata.pop("statistics")
        return document(
            "asset",
            metadata,
            [{"statistic": key, "value": value} for key, value in statistics.items()],
        )
