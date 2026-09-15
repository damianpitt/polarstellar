"""Shared local export buttons with explicit file selection and recoverable errors."""

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QMessageBox, QPushButton, QWidget

from polarstellar.storage.export import write_export


class ExportControls(QWidget):
    """Offer CSV and JSON for a caller-supplied immutable investigation snapshot."""

    def __init__(self, snapshot, parent=None):
        """Build disabled controls; the owning view enables them after data arrives."""
        super().__init__(parent)
        self.snapshot = snapshot
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for format_name in ("csv", "json"):
            button = QPushButton(f"Export {format_name.upper()}")
            button.clicked.connect(lambda _checked=False, kind=format_name: self.save(kind))
            layout.addWidget(button)
        self.setToolTip("Save loaded data locally, including source and coverage information.")
        self.setEnabled(False)

    def save(self, format_name):
        """Freeze current data, ask for a destination, then report success or failure."""
        snapshot = self.snapshot()
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export loaded investigation data",
            f"polarstellar-{snapshot['kind']}.{format_name}",
            f"{format_name.upper()} files (*.{format_name})",
        )
        if not filename:
            return
        path = Path(filename)
        if not path.suffix:
            path = path.with_suffix("." + format_name)
            # The native dialog could not confirm an existing suffixed destination.
            if (
                path.exists()
                and QMessageBox.question(self, "Replace file?", f"Replace {path.name}?")
                != QMessageBox.StandardButton.Yes
            ):
                return
        try:
            write_export(path, snapshot, format_name)
        except (OSError, ValueError, TypeError):
            QMessageBox.warning(
                self,
                "Export failed",
                "Unable to save the export. Check the "
                "destination permissions and available disk space, then try again.",
            )
            return
        QMessageBox.information(self, "Export saved", f"Saved to {path}")
