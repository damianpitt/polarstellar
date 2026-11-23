"""Check reproducible acceptance failures around local exports, stale cases and incomplete rows."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from test_accounts import ADDRESS
from test_investigations import entry

from polarstellar.stellar.activity import ActivityKind, ActivityRecord
from polarstellar.stellar.models import Network
from polarstellar.storage.investigations import InvestigationStore, new_investigation
from polarstellar.ui.activity_view import ActivityView
from polarstellar.ui.export_controls import ExportControls
from polarstellar.ui.investigations_view import InvestigationsView


@pytest.fixture
def app():
    """Keep Qt views alive without external APIs or native file dialogs."""
    return QApplication.instance() or QApplication([])


def test_export_preparation_failure_stays_inside_ui(app, monkeypatch):
    """Do not open a file picker or write files after the selected evidence becomes unavailable."""
    warnings = []

    def unavailable():
        """Represent a loaded selection invalidated immediately before its queued export click."""
        raise ValueError("The selection is no longer available")

    def unexpected_picker(*args):
        """Fail if the user is asked to pick a path for an export that cannot be prepared."""
        pytest.fail("File picker opened without a stable snapshot")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", unexpected_picker)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    controls = ExportControls(unavailable)
    controls.setEnabled(True)
    controls.save("json")
    assert len(warnings) == 1 and "Select or reload" in warnings[0]


def test_incomplete_payment_keeps_unknown_asset_navigation_disabled(app):
    """A short cached record remains inspectable without an out-of-range asset-column lookup."""
    view = ActivityView(object(), ActivityKind.PAYMENTS)
    view.reset((ADDRESS, Network.TESTNET))
    view.records = [ActivityRecord("1", "1", ("2026-11-23T00:00:00Z",))]
    view.table.setRowCount(1)
    view.table.setCurrentCell(0, 0)
    view.update_asset_button()
    assert view.selected_asset() is None and not view.open_asset_button.isEnabled()


def test_stale_annotation_and_evidence_saves_preserve_newer_committed_state(app, tmp_path):
    """Two open case views can save independently without replacing each other's evidence or clean fields."""
    store = InvestigationStore(tmp_path / "cases.sqlite3")
    item = new_investigation("Shared local case")
    item["entries"] = [entry()]
    store.save(item)
    first, second = InvestigationsView(store, object()), InvestigationsView(store, object())
    first.reload(item["id"])
    second.reload(item["id"])
    first.notes.setPlainText("Draft from the first view")
    added = deepcopy(entry())
    added["evidence"][0]["metadata"]["sequence"] = "999"
    second.add_evidence(added)
    store.amend(item["id"], annotations={"labels": "New saved label"})
    first.save_notes()
    saved = store.list()[0]
    assert saved["entries"] == [item["entries"][0], added]
    assert saved["labels"] == "New saved label" and saved["notes"] == "Draft from the first view"
    second.add_evidence(entry())
    assert len(store.list()[0]["entries"]) == 3
    assert store.list()[0]["notes"] == "Draft from the first view"


def test_deleted_case_is_not_recreated_by_a_stale_save(app, tmp_path, monkeypatch):
    """Keep the draft visible and report a missing case instead of resurrecting deleted evidence."""
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    store = InvestigationStore(tmp_path / "cases.sqlite3")
    item = new_investigation("Deleted elsewhere")
    store.save(item)
    view = InvestigationsView(store, object())
    view.reload(item["id"])
    view.notes.setPlainText("Keep this draft available")
    store.delete(item["id"])
    view.save_notes()
    assert store.list() == [] and view.notes.toPlainText() == "Keep this draft available"
    assert "no longer exists" in warnings[0]


def test_concurrent_appends_and_quota_failure_are_atomic(tmp_path, monkeypatch):
    """Serialize appends from independent connections and roll back the complete oversized update."""
    import polarstellar.storage.investigations as module

    store = InvestigationStore(tmp_path / "cases.sqlite3")
    item = new_investigation("Atomic case")
    store.save(item)

    def append(index):
        """Append one observation through its own store/connection."""
        observation = entry()
        observation["identifier"] += f"-fixture-{index}"
        InvestigationStore(store.path).amend(item["id"], evidence=observation)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(append, range(4)))
    saved = store.list()[0]
    assert len(saved["entries"]) == 4
    monkeypatch.setattr(module, "MAX_BYTES", 100)
    with pytest.raises(ValueError, match="20 MB"):
        store.amend(item["id"], annotations={"notes": "Must roll back"}, evidence=entry())
    assert store.list() == [saved]
    with pytest.raises(ValueError, match="Only investigation"):
        store.amend(item["id"], annotations={"entries": []})
