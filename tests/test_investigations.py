"""Exercise durable evidence, privacy defaults, refresh races and offline reopening."""

import asyncio
import json
import sqlite3

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from test_accounts import ADDRESS, account_data

from polarstellar.app.window import MainWindow
from polarstellar.stellar.horizon import parse_account
from polarstellar.stellar.models import Network
from polarstellar.stellar.service import AccountService
from polarstellar.storage.export import account_document, serialize
from polarstellar.storage.investigations import InvestigationStore, new_investigation
from polarstellar.ui.investigations_view import InvestigationsView


def entry():
    """Construct account evidence with original source and exact financial values."""
    account = parse_account(account_data(), ADDRESS, Network.TESTNET, "fixture")
    return {
        "kind": "account",
        "identifier": ADDRESS,
        "network": "Testnet",
        "evidence": [account_document(account)],
    }


def test_storage_restart_delete_and_schema(tmp_path):
    """Persist annotations/evidence across restart and refuse unknown schema versions."""
    path = tmp_path / "investigations.sqlite3"
    store = InvestigationStore(path)
    assert store.list() == [] and not path.exists()
    item = new_investigation("Investigation α")
    item.update(notes="Private interpretation", labels="review", entries=[entry()])
    store.save(item)
    assert InvestigationStore(path).list() == [item]
    with pytest.raises(ValueError):
        store.save({**item, "name": " "})
    assert store.list() == [item]
    store.delete(item["id"])
    assert store.list() == []
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="version"):
        store.list()


@pytest.fixture
def app():
    """Keep widgets alive without contacting a network or opening native windows."""
    return QApplication.instance() or QApplication([])


def test_offline_annotations_export_and_delete(app, tmp_path, monkeypatch):
    """Opening evidence is offline, exports omit annotations by default, and deletion is explicit."""
    store = InvestigationStore(tmp_path / "saved.db")
    view = InvestigationsView(store, object())
    view.name.setText("Private case")
    view.create()
    view.add_evidence(entry())
    view.notes.setPlainText("My conclusion")
    view.labels.setText("personal")
    view.save_notes()
    reopened = InvestigationsView(store, object())
    reopened.library.setCurrentRow(0)
    reopened.entries.setCurrentRow(0)
    assert "fixture" in reopened.evidence.toPlainText()
    assert reopened.notes.toPlainText() == "My conclusion"
    payload = serialize(reopened.export_document(), "json")
    assert "My conclusion" not in payload and "Private case" not in payload
    reopened.annotations.setChecked(True)
    assert "My conclusion" in serialize(reopened.export_document(), "csv")
    assert json.loads(serialize(reopened.export_document(), "json"))["records"][0] == entry_for(
        view
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    reopened.delete()
    assert len(store.list()) == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    reopened.delete()
    assert store.list() == []


def entry_for(view):
    """Read the stored entry without generating new retrieval timestamps."""
    return view.current["entries"][0]


def test_refresh_keeps_history_and_correct_owner(app, tmp_path):
    """Late refresh must target its original investigation and preserve intervening annotations."""

    async def scenario():
        """Switch cases during refresh and update the original case before completion."""
        future = asyncio.get_running_loop().create_future()

        class Provider:
            """Controlled live provider for proving cache bypass and race safety."""

            async def get_account(self, address, network):
                """Wait until a test explicitly releases the new account snapshot."""
                assert network == Network.TESTNET
                return await future

        class Cache:
            """Expose an underlying live provider; cached lookups must never be called."""

            provider = Provider()

        store = InvestigationStore(tmp_path / "saved.db")
        view = InvestigationsView(store, Cache())
        view.name.setText("First")
        view.create()
        view.add_evidence(entry())
        first_id = view.current["id"]
        original = entry_for(view)
        view.entries.setCurrentRow(0)
        view.start_refresh()
        task = view.task
        await asyncio.sleep(0)
        other = new_investigation("Other")
        store.save(other)
        first = next(item for item in store.list() if item["id"] == first_id)
        first["notes"] = "Added during refresh"
        store.save(first)
        view.reload(other["id"])
        future.set_result(parse_account(account_data(), ADDRESS, Network.TESTNET, "live"))
        await task
        first = next(item for item in store.list() if item["id"] == first_id)
        assert len(first["entries"]) == 2 and first["entries"][0] == original
        assert first["notes"] == "Added during refresh"
        assert view.current["id"] == other["id"] and not view.current["entries"]

    asyncio.run(scenario())


def test_window_save_and_cache_independence(app, tmp_path):
    """Capture an account from the explorer without sharing storage with the optional cache."""

    async def scenario():
        """Save and reopen a displayed account on its original network."""

        class Provider:
            """Return deterministic account evidence."""

            async def get_account(self, address, network):
                """Supply a snapshot without external I/O."""
                return parse_account(account_data(), address, network, "fixture")

        store = InvestigationStore(tmp_path / "saved.db")
        window = MainWindow(AccountService(Provider()), store)
        window.investigations.name.setText("Case")
        window.investigations.create()
        window.search.setText(ADDRESS)
        window.start_search()
        await window.task
        window.save_resource()
        window.invalidate()
        assert store.list()[0]["entries"][0]["identifier"] == ADDRESS
        assert window.investigations.current is not None
        window.close()

    asyncio.run(scenario())


def test_refresh_failure_and_deleted_case(app, tmp_path, monkeypatch):
    """Failed refresh preserves history; deleting a case prevents a late response recreating it."""
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))

    async def scenario():
        """Exercise both failure and deletion while a live lookup is pending."""
        future = asyncio.get_running_loop().create_future()

        class Provider:
            """Wait for controlled completion or failure."""

            async def get_account(self, *args):
                """Return a test-controlled future."""
                return await future

        store = InvestigationStore(tmp_path / "cases.db")
        view = InvestigationsView(store, Provider())
        view.name.setText("Case")
        view.create()
        view.add_evidence(entry())
        original = store.list()
        view.entries.setCurrentRow(0)
        view.start_refresh()
        task = view.task
        future.set_exception(ValueError("offline"))
        await task
        assert errors and store.list() == original
        future = asyncio.get_running_loop().create_future()
        view.entries.setCurrentRow(0)
        view.start_refresh()
        task = view.task
        await asyncio.sleep(0)
        store.delete(view.current["id"])
        future.set_result(parse_account(account_data(), ADDRESS, Network.TESTNET, "live"))
        await task
        assert store.list() == []

    asyncio.run(scenario())


def test_asset_transaction_capture_and_refresh(app, tmp_path):
    """Save and refresh the other supported resource types with their original networks."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from test_accounts import ISSUER
    from test_assets import asset_row

    from polarstellar.stellar.assets import parse_asset
    from polarstellar.stellar.transaction import TransactionDetail

    asset = parse_asset(asset_row(), "USD", ISSUER, Network.TESTNET, "fixture")
    tx = TransactionDetail(
        "a" * 64,
        Network.TESTNET,
        True,
        1,
        "today",
        ADDRESS,
        ADDRESS,
        100,
        0,
        "none",
        (),
        "",
        "fixture",
        datetime.now(UTC),
        "{}",
    )

    class Provider:
        """Return deterministic assets and transactions for explicit live refresh."""

        async def get_asset(self, code, issuer, network):
            """Check that the saved code and issuer are reused exactly."""
            assert (code, issuer, network) == ("USD", ISSUER, Network.TESTNET)
            return asset

        async def get_transaction(self, value, network):
            """Check that refresh retains the saved transaction's network."""
            assert value == tx.hash and network == Network.TESTNET
            return tx

    async def scenario():
        """Capture both resources and append refreshed evidence without replacing either."""
        store = InvestigationStore(tmp_path / "saved.db")
        window = MainWindow(AccountService(Provider()), store)
        library = window.investigations
        library.name.setText("Assets and transactions")
        library.create()
        window.pages.setCurrentIndex(5)
        window.assets.snapshot = asset
        window.save_resource()
        window.save_transaction(SimpleNamespace(transaction=tx))
        assert [item["kind"] for item in library.current["entries"]] == ["asset", "transaction"]
        for row in (0, 1):
            library.entries.setCurrentRow(row)
            library.start_refresh()
            await library.task
        assert len(store.list()[0]["entries"]) == 4
        assert all(item["network"] == "Testnet" for item in store.list()[0]["entries"])
        window.close()

    asyncio.run(scenario())
