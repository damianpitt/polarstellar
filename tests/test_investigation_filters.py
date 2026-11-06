"""Check offline investigation discovery, stable selection and annotation protection."""

import asyncio

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from test_accounts import ADDRESS, ISSUER, account_data
from test_investigations import entry

from polarstellar.stellar.horizon import parse_account
from polarstellar.stellar.models import Network
from polarstellar.storage.export import serialize
from polarstellar.storage.investigations import (
    InvestigationStore,
    matches_investigation,
    new_investigation,
)
from polarstellar.ui.investigations_view import InvestigationsView


@pytest.fixture
def app():
    """Keep the local library widgets alive without accessing any provider."""
    return QApplication.instance() or QApplication([])


def sample_case():
    """Create a mixed-network case with committed Unicode metadata and full asset identity."""
    item = new_investigation("Straße research")
    asset = {
        "kind": "asset",
        "identifier": f"USD:{ISSUER}",
        "code": "USD",
        "issuer": ISSUER,
        "network": "Mainnet",
        "evidence": [],
    }
    item.update(
        labels=" review , priority, , Straße ", notes="private-note", entries=[entry(), asset]
    )
    return item


@pytest.mark.parametrize(
    "query,label,network,expected",
    [
        (" STRASSE ", " REVIEW ", "Testnet", True),
        ("prior", "", "Mainnet", True),
        ("", "prior", None, False),
        (ISSUER.lower(), "priority", "Mainnet", True),
        (ISSUER, "priority", "Testnet", False),
        ("USD", "", "Testnet", False),
        (ADDRESS, "", "Mainnet", False),
        ("private-note", "", None, False),
        ("example.org", "", None, False),
    ],
)
def test_combined_filter_scope(query, label, network, expected):
    """Match saved metadata or an identity on the selected network, excluding private payloads."""
    assert matches_investigation(sample_case(), query, label, network) is expected


def test_empty_case_has_no_saved_network():
    """An empty case matches its name under All networks until evidence gives it a network."""
    item = new_investigation("Empty case")
    assert matches_investigation(item, "empty")
    assert not matches_investigation(item, "empty", network="Mainnet")


def test_matching_filter_keeps_selection_draft_evidence_and_whole_export(
    app, tmp_path, monkeypatch
):
    """Changing library visibility preserves the open case even when its row moves."""
    store = InvestigationStore(tmp_path / "saved.db")
    item = sample_case()
    store.save(item)
    store.save(new_investigation("Separate"))
    view = InvestigationsView(store, object())
    view.reload(item["id"])
    assert view.library.currentRow() == 1
    view.entries.setCurrentRow(0)
    view.notes.setPlainText("Unsaved draft")
    view.annotations.setChecked(True)

    def unexpected_prompt(*args):
        """Fail if a filter that retains the selected case interrupts the editor."""
        pytest.fail("Matching filter prompted to discard a draft")

    monkeypatch.setattr(QMessageBox, "question", unexpected_prompt)
    view.query.setText("USD")
    view.label_filter.setText("review")
    view.network_filter.setCurrentText("Mainnet")
    view.query.returnPressed.emit()
    assert view.library.currentRow() == 0
    assert view.current["id"] == item["id"] and view.entries.currentRow() == 0
    assert view.notes.toPlainText() == "Unsaved draft" and view.annotations.isChecked()
    exported = view.export_document()
    assert exported["records"] == item["entries"]
    assert "Unsaved draft" not in serialize(exported, "json")
    assert "Showing 1 of 2" in view.filter_count.text()
    view.clear_filters()
    assert view.library.currentRow() == 1 and view.entries.currentRow() == 0


def test_declined_filter_restores_controls_then_acceptance_can_hide_case(
    app, tmp_path, monkeypatch
):
    """A rejected change restores active controls without replacing edits or stored evidence."""
    store = InvestigationStore(tmp_path / "saved.db")
    item = sample_case()
    store.save(item)
    view = InvestigationsView(store, object())
    view.reload(item["id"])
    view.query.setText("USD")
    view.label_filter.setText("review")
    view.network_filter.setCurrentText("Mainnet")
    view.apply_filters()
    view.name.setText("Draft name")
    view.labels.setText("Draft label")
    view.notes.setPlainText("Draft note")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    view.query.setText("absent")
    view.label_filter.clear()
    view.network_filter.setCurrentText("Testnet")
    view.apply_filters()
    assert (view.query.text(), view.label_filter.text(), view.network_filter.currentData()) == (
        "USD",
        "review",
        "Mainnet",
    )
    assert view.draft_annotations() == {
        "name": "Draft name",
        "labels": "Draft label",
        "notes": "Draft note",
    }
    assert view.current["id"] == item["id"] and view.library.currentRow() == 0
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    view.query.setText("absent")
    view.apply_filters()
    assert view.current is None and view.library.count() == 0 and not view.export.isEnabled()
    assert "Showing 0 of 1" in view.filter_count.text()
    assert store.list() == [item]


def test_select_uses_saved_id_when_reload_changes_filtered_row_order(app, tmp_path, monkeypatch):
    """Approve a specific case after a refresh reorders the filtered list during the prompt."""
    store = InvestigationStore(tmp_path / "saved.db")
    first = new_investigation("Group one")
    second = new_investigation("Group two")
    for item in (second, first, new_investigation("Hidden")):
        store.save(item)
    view = InvestigationsView(store, object())
    view.reload(first["id"])
    view.query.setText("group")
    view.apply_filters()
    view.notes.setPlainText("Draft for group one")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    view.library.setCurrentRow(1)
    assert view.library.currentRow() == 0 and view.current["id"] == first["id"]

    def reorder(*args):
        """Simulate a background refresh moving the target above the selected case."""
        store.save(second)
        view.reload(first["id"])
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", reorder)
    view.library.setCurrentRow(1)
    assert view.current["id"] == second["id"] and view.library.currentRow() == 0


def test_saved_rename_and_create_reveal_case_under_incompatible_filters(app, tmp_path):
    """Keep a successful write open even when its new metadata falls outside the active scope."""
    store = InvestigationStore(tmp_path / "saved.db")
    item = sample_case()
    store.save(item)
    view = InvestigationsView(store, object())
    view.reload(item["id"])
    view.label_filter.setText("review")
    view.apply_filters()
    view.labels.setText("archived")
    view.save_notes()
    assert view.current["id"] == item["id"] and view.current["labels"] == "archived"
    assert not view.label_filter.text() and "Filters cleared" in view.filter_count.text()
    view.network_filter.setCurrentText("Mainnet")
    view.apply_filters()
    view.name.setText("New empty case")
    view.create()
    assert view.current["name"] == "New empty case" and not view.current["entries"]
    assert view.network_filter.currentData() is None
    assert view.library.count() == 2


def test_refresh_keeps_filter_and_only_unsaved_fields(app, tmp_path):
    """Keep a local draft while reloading other committed annotations and fresh evidence."""

    async def scenario():
        """Pause the explicit refresh while the user edits a note and another writer saves a label."""
        future = asyncio.get_running_loop().create_future()

        class Provider:
            """Expose a test-controlled provider for the single explicit refresh."""

            async def get_account(self, address, network):
                """Return the fixture only after the competing annotation edit."""
                assert (address, network) == (ADDRESS, Network.TESTNET)
                return await future

        store = InvestigationStore(tmp_path / "saved.db")
        item = sample_case()
        store.save(item)
        view = InvestigationsView(store, Provider())
        view.reload(item["id"])
        view.network_filter.setCurrentText("Testnet")
        view.apply_filters()
        view.entries.setCurrentRow(0)
        view.start_refresh()
        task = view.task
        await asyncio.sleep(0)
        view.notes.setPlainText("Unsaved note")
        store.save({**item, "labels": "New saved label"})
        future.set_result(parse_account(account_data(), ADDRESS, Network.TESTNET, "live"))
        await task
        assert view.current["id"] == item["id"] and view.entries.currentRow() == 0
        assert view.notes.toPlainText() == "Unsaved note"
        assert view.labels.text() == "New saved label"
        assert len(view.current["entries"]) == 3 and view.network_filter.currentData() == "Testnet"

    asyncio.run(scenario())
