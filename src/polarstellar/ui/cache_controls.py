"""Visible controls for optional disk persistence and clearing saved snapshots."""

import asyncio
import sqlite3

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QWidget


class CacheControls(QWidget):
    """Keep cache consent and deletion available above the investigation workspace."""

    def __init__(self, provider, invalidate, tasks):
        """Connect controls to the cache wrapper without starting disk or network work."""
        super().__init__()
        self.provider = provider
        self.invalidate = invalidate
        self.tasks = tasks
        layout = QHBoxLayout(self)
        self.enabled = QCheckBox("Use local cache")
        self.enabled.setToolTip(
            "Off at startup. Saves fetched public data locally, unencrypted. "
            "Uncheck and search again for live data. Disabling does not delete saved snapshots."
        )
        self.enabled.toggled.connect(self.toggle)
        self.clear_button = QPushButton("Clear cache")
        self.clear_button.clicked.connect(self.start_clear)
        self.status = QLabel("Disk cache off")
        self.status.setToolTip(str(provider.cache.path))
        layout.addWidget(self.enabled)
        layout.addWidget(self.clear_button)
        layout.addWidget(self.status, 1)

    def toggle(self, enabled):
        """Apply the user's choice and discard displayed results from the previous mode."""
        self.provider.set_enabled(enabled)
        self.invalidate()
        self.status.setText(
            "Disk cache on • accounts/activity: 60s • transactions: 24h"
            if enabled
            else "Disk cache off • saved snapshots remain until cleared"
        )

    def start_clear(self):
        """Prevent duplicate clears and immediately remove the old investigation from view."""
        self.invalidate()
        self.clear_button.setEnabled(False)
        self.enabled.setEnabled(False)
        self.status.setText("Clearing local snapshots…")
        task = asyncio.create_task(self.clear())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def clear(self):
        """Report deletion success or failure without claiming unavailable storage was cleared."""
        try:
            await self.provider.clear()
            self.status.setText("Local snapshots cleared")
        except (OSError, sqlite3.Error):
            self.status.setText(
                "Could not clear the local cache. Check its location and permissions."
            )
        finally:
            self.clear_button.setEnabled(True)
            self.enabled.setEnabled(True)
