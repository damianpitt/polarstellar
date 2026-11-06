"""Offline comparison of two observations in one saved investigation."""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
)

from polarstellar.storage.investigation_comparison import (
    COVERAGE,
    PRIMARY_KIND,
    compare_entries,
    comparison_document,
    resource_key,
)
from polarstellar.ui.export_controls import ExportControls


def observation_label(index, entry):
    """Identify a row by its library number and its primary evidence's original save time."""
    kind = PRIMARY_KIND[entry["kind"]]
    evidence = entry.get("evidence")
    for item in evidence if isinstance(evidence, list) else ():
        if isinstance(item, dict) and item.get("kind") == kind:
            saved = item.get("exported_at")
            if isinstance(saved, str):
                return f"#{index + 1}  •  saved {saved}"
    return f"#{index + 1}  •  save time unavailable"


def display_value(present, value):
    """Show missing and null values distinctly from each other and from an observed zero."""
    if not present:
        return "Unreported"
    if value is None:
        return "Unknown (null)"
    return json.dumps(value, ensure_ascii=False)


class InvestigationComparisonDialog(QDialog):
    """Compare saved state offline and export the exact report shown in the dialog."""

    def __init__(self, entries, baseline_index, parent=None):
        """Pin the selected observations before the library can reload during a live refresh."""
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Compare saved snapshots")
        self.resize(850, 540)
        # The library replaces its loaded entry list on reload and edits copies
        # when saving. Holding the existing entries pins the evidence without
        # duplicating large activity pages or adding anything to the database.
        self.entries = tuple(entries)
        self.baseline_index = baseline_index
        self.result = None
        baseline = self.entries[baseline_index]
        layout = QVBoxLayout(self)
        heading = QLabel(
            f"{baseline['network']}  •  {baseline['kind']}  •  {baseline['identifier']}"
        )
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        form = QFormLayout()
        form.addRow("Baseline", QLabel(observation_label(baseline_index, baseline)))
        self.candidate = QComboBox()
        self.candidate.setAccessibleName("Comparison snapshot")
        self.candidate.addItem("Choose another saved entry")
        baseline_key = resource_key(baseline)
        for index, entry in enumerate(self.entries):
            if index != baseline_index and resource_key(entry) == baseline_key:
                self.candidate.addItem(observation_label(index, entry), index)
        form.addRow("Compare with", self.candidate)
        layout.addLayout(form)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        tabs = QTabWidget()
        self.changes = QPlainTextEdit()
        self.changes.setReadOnly(True)
        self.changes.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        tabs.addTab(self.changes, "Field changes")
        self.sources = QPlainTextEdit()
        self.sources.setReadOnly(True)
        tabs.addTab(self.sources, "Coverage and sources")
        layout.addWidget(tabs, 1)
        self.export = ExportControls(self.export_document)
        layout.addWidget(self.export)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.candidate.currentIndexChanged.connect(self.show_comparison)
        self.show_comparison()

    def show_comparison(self):
        """Update the report and export together whenever the comparison selection changes."""
        index = self.candidate.currentData()
        self.result = None
        self.export.setEnabled(False)
        self.changes.clear()
        self.sources.setPlainText(COVERAGE)
        if index is None:
            self.status.setText(
                "Choose a comparison entry. The selected baseline is shown first regardless of date."
                if self.candidate.count() > 1
                else "Save another observation of this resource on this network to compare."
            )
            return
        self.result = compare_entries(self.entries[self.baseline_index], self.entries[index])
        status = self.result["status"]
        if status == "Not comparable":
            self.status.setText(f"{status}. {self.result['note']}")
        else:
            self.status.setText(
                f"{status} in supported fields. See Coverage and sources for the scope of each save."
            )
        changes = []
        for row in self.result["changes"]:
            before = display_value(row["before_present"], row["before"])
            after = display_value(row["after_present"], row["after"])
            changes.append(
                f"{' / '.join(row['field'])}  •  {row['change']}\n"
                f"  Baseline    {before}\n"
                f"  Comparison  {after}"
            )
        self.changes.setPlainText("\n\n".join(changes) if changes else self.status.text())
        # Show source and save times separately because cached data can be saved much
        # later than its original retrieval. A changed timestamp is not a field change.
        sections = [COVERAGE]
        for title, side, scope_key in (
            ("Baseline", "baseline_provenance", "baseline_scope"),
            ("Comparison", "comparison_provenance", "comparison_scope"),
        ):
            scope = {"source_provenance": self.result.get(side), **self.result.get(scope_key, {})}
            sections.append(f"{title}\n{json.dumps(scope, ensure_ascii=False, indent=2)}")
        self.sources.setPlainText("\n\n".join(sections))
        self.export.setEnabled(True)

    def export_document(self):
        """Freeze the selected report, with local names, notes, and labels excluded."""
        return comparison_document(
            self.result, self.baseline_index + 1, self.candidate.currentData() + 1
        )
