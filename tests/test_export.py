"""Check export accuracy, coverage, file safety, and investigation reset behavior."""

import asyncio
import csv
import io
import json
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from test_accounts import ADDRESS, account_data
from test_counterparties import payment

from polarstellar.stellar.activity import ActivityKind, parse_page
from polarstellar.stellar.horizon import parse_account
from polarstellar.stellar.models import Network
from polarstellar.storage.export import (
    account_document,
    activity_document,
    document,
    graph_document,
    serialize,
    transaction_document,
    write_export,
)
from polarstellar.ui.activity_view import ActivityView
from polarstellar.ui.export_controls import ExportControls
from polarstellar.ui.graph_view import GraphView


@pytest.fixture
def app():
    """Keep a Qt application alive for export controls without showing native dialogs."""
    return QApplication.instance() or QApplication([])


def test_exact_snapshot_and_spreadsheet_cells():
    """Preserve exact balances in JSON and neutralize formula-like public text in CSV."""
    account = parse_account(account_data(), ADDRESS, Network.TESTNET, "fixture")
    account = replace(account, home_domain='  =HYPERLINK("bad")')
    snapshot = account_document(account)
    restored = json.loads(serialize(snapshot, "json"))
    assert restored["metadata"]["network"] == "Testnet"
    assert restored["records"][0]["amount"] == format(account.balances[0].amount, "f")
    row = next(csv.DictReader(io.StringIO(serialize(snapshot, "csv"))))
    assert row["metadata.home_domain"].startswith("'  =")
    assert row["record.amount"] == restored["records"][0]["amount"]
    assert restored["metadata"]["home_domain"] == account.home_domain


def test_empty_and_unicode_csv():
    """Empty results retain coverage; quoting survives commas, newlines and Unicode."""
    empty = document("payments", {"coverage": "No records"}, [])
    assert (
        next(csv.DictReader(io.StringIO(serialize(empty, "csv"))))["metadata.coverage"]
        == "No records"
    )
    value = '星, "memo"\nsecond line'
    snapshot = document("test", {}, [{"memo": value}])
    assert next(csv.DictReader(io.StringIO(serialize(snapshot, "csv"))))["record.memo"] == value


def test_atomic_failure_preserves_destination(tmp_path, monkeypatch):
    """An unsuccessful replacement leaves the original file intact and removes temporary data."""
    path = tmp_path / "export.json"
    path.write_text("original", encoding="utf-8")

    def fail(*args):
        """Simulate a filesystem refusing the final replacement."""
        raise OSError("denied")

    monkeypatch.setattr("polarstellar.storage.export.os.replace", fail)
    with pytest.raises(OSError):
        write_export(path, document("test", {}, []), "json")
    assert path.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.iterdir()) == [path]


def test_pages_filters_evidence_and_reset(app):
    """Mixed cache ages survive export and switching networks clears exportable records."""
    first = parse_page(
        {"_embedded": {"records": [payment(i) for i in range(40, 20, -1)]}},
        ADDRESS,
        Network.MAINNET,
        ActivityKind.PAYMENTS,
        "fixture",
        None,
    )
    second = replace(
        parse_page(
            {"_embedded": {"records": [payment(20, transaction_successful=False)]}},
            ADDRESS,
            Network.MAINNET,
            ActivityKind.PAYMENTS,
            "fixture",
            "21",
        ),
        cache_status="Cached data",
    )

    class Service:
        """Return two controlled pages without network access."""

        async def activity(self, address, network, kind, cursor):
            """Serve the older page only when pagination supplies a cursor."""
            return second if cursor else first

    async def scenario():
        """Exercise the real view state transitions and the graph's shared payment data."""
        view = ActivityView(Service(), ActivityKind.PAYMENTS)
        graph = GraphView(view)
        view.reset((ADDRESS, Network.MAINNET))
        assert not view.export.isEnabled()
        view.start()
        await view.task
        assert not activity_document(view)["metadata"]["end_of_available_results"]
        view.start()
        await view.task
        snapshot = activity_document(view)
        assert len(snapshot["records"]) == 21
        assert [page["cache_status"] for page in snapshot["metadata"]["pages"]] == [
            "Live data",
            "Cached data",
        ]
        exported = graph_document(graph)
        assert len(exported["records"][0]["evidence"]) == 20
        assert exported["metadata"]["excluded"] == {"Failed transaction": 1}
        graph.direction.setCurrentText("Incoming")
        assert graph_document(graph)["records"] == []
        view.reset((ADDRESS, Network.TESTNET))
        assert not view.export.isEnabled() and not graph.export.isEnabled()
        assert not view.export_pages
        graph.close()
        view.close()

    asyncio.run(scenario())


def test_partial_transaction_export():
    """An incomplete transaction must carry its warning and expected operation count."""
    from datetime import UTC, datetime

    from polarstellar.stellar.transaction import TransactionDetail

    tx = TransactionDetail(
        "a" * 64,
        Network.TESTNET,
        False,
        1,
        "today",
        ADDRESS,
        ADDRESS,
        100,
        2,
        "none",
        (),
        "Operations unavailable",
        "fixture",
        datetime.now(UTC),
        "{}",
    )
    snapshot = transaction_document(tx)
    assert snapshot["metadata"]["warning"] == "Operations unavailable"
    assert snapshot["metadata"]["operation_count"] == 2
    assert snapshot["metadata"]["fee_stroops"] == "100"
    assert snapshot["records"] == []


def test_save_cancel_success_and_failure(app, tmp_path, monkeypatch):
    """Cancelling writes nothing; successful UTF-8 saves and disk errors receive distinct feedback."""
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append("saved"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append("failed"))
    control = ExportControls(lambda: document("test", {}, [{"memo": "星"}]))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    control.save("json")
    assert not messages and not list(tmp_path.iterdir())
    path = tmp_path / "result"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
    control.save("json")
    assert (
        json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))["records"][0]["memo"]
        == "星"
    )
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *args: (str(tmp_path / "missing" / "x.json"), "")
    )
    control.save("json")
    assert messages == ["saved", "failed"]
