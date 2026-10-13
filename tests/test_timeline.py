"""Verify chronological presentation without inferring execution order or financial continuity."""

import asyncio
import json
from dataclasses import replace
from decimal import Decimal, localcontext

import pytest
from PySide6.QtWidgets import QApplication
from test_operation_links import operation_row, parsed
from test_trace_filters import timed
from test_tracing import A, B, C, D, page, payments, transfer

from polarstellar.analysis.timeline import operation_key
from polarstellar.analysis.trace_filters import TraceFilters, instant
from polarstellar.analysis.tracing import Trace
from polarstellar.app.window import MainWindow
from polarstellar.stellar.models import Network
from polarstellar.stellar.service import AccountService
from polarstellar.storage.export import serialize
from polarstellar.storage.investigations import InvestigationStore
from polarstellar.ui.trace_view import TraceDialog


def mixed():
    """Create an intentionally cursor-ordered page whose UTC chronology differs from identifier order."""
    path = operation_row(70)
    path["created_at"] = "2026-10-01T01:00:00Z"
    merge = operation_row(60, "account_merge")
    merge["created_at"] = "2026-10-01T02:00:00Z"
    records = [
        timed(100, A, B, "2026-10-01T08:00:00+07:00", amount="900000000.0000001"),
        timed(90, A, B, "2026-10-01T02:00:00Z"),
        timed(80, A, B, "2026-10-01T01:00:00Z", "create_account", "0.0000002"),
        *parsed([path, merge]).records,
    ]
    return Trace(A, Network.TESTNET, [page(A, records, "60")])


@pytest.fixture
def app():
    """Keep Qt alive for real selection/order/save controls without a public API request."""
    return QApplication.instance() or QApplication([])


def test_utc_order_ties_and_actual_amounts():
    """Offsets compare by instant, ties sort by identifier only for display, and all amount semantics survive."""
    snapshot = mixed().timeline_export()
    rows, timing = snapshot["records"], snapshot["metadata"]["timeline"]
    assert [row["operation"] for row in rows] == ["70", "80", "100", "60", "90"]
    assert rows[2]["original_created"] == "2026-10-01T08:00:00+07:00"
    assert rows[2]["created_utc"] == "2026-10-01T01:00:00+00:00"
    assert rows[2]["source_amount"] == "900000000.0000001"
    assert rows[1]["source_amount"] == "0.0000002"
    assert rows[0]["source_amount"] == "12.0000001" and rows[0]["destination_amount"] == "7.0000002"
    assert rows[3]["source_amount"] is rows[3]["destination_amount"] is None
    assert rows[0]["same_timestamp_loaded_operations"] == 3
    assert rows[3]["same_timestamp_loaded_operations"] == 2
    assert all("Shared timestamp" in row["timing"] for row in rows)
    assert timing["selected_operations_with_shared_loaded_timestamp"] == 5
    assert timing["selected_timestamp_groups"] == 2
    assert rows[0]["seconds_since_earlier_selected_timestamp"] is None
    assert rows[3]["seconds_since_earlier_selected_timestamp"] == "3600"
    assert "not execution order" in timing["ordering"]
    assert "No running balances" in timing["coverage"]
    assert "paths" not in snapshot["metadata"]


def test_newest_reverses_groups_but_not_identifier_ties():
    """Newest-first changes timestamp groups only; arbitrary tie order and gap meaning remain stable."""
    newest = mixed().timeline_export(newest_first=True)
    assert [row["operation"] for row in newest["records"]] == ["60", "90", "70", "80", "100"]
    assert newest["records"][0]["seconds_since_earlier_selected_timestamp"] == "3600"
    assert newest["records"][-1]["seconds_since_earlier_selected_timestamp"] is None
    assert newest["metadata"]["timeline"]["order"] == "Newest first"


@pytest.mark.parametrize(
    "identifiers", [["10", "9", "100"], ["09", "9", "009"]], ids=["numeric", "leading-zeros"]
)
def test_identifier_tie_order_is_deterministic(identifiers):
    """Numeric-looking identifiers sort without ledger decoding; original representations remain a stable tie breaker."""
    expected = ["9", "10", "100"] if identifiers[0] == "10" else ["009", "09", "9"]
    assert sorted(identifiers, key=operation_key) == expected
    assert operation_key("9" * 5000)[1] == 5000
    assert sorted(["unknown", "10"], key=operation_key) == ["10", "unknown"]


def test_microsecond_gap_preserves_precision_under_small_decimal_context():
    """Gap calculations use integer microseconds and never depend on monetary decimal context or floats."""
    trace = Trace(
        A,
        Network.TESTNET,
        [
            page(
                A,
                [
                    timed(100, A, B, "2026-10-01T00:00:00Z"),
                    timed(90, A, B, "2026-10-01T00:00:00.000001Z"),
                    timed(80, A, B, "2026-10-03T00:00:00.000002Z"),
                ],
            )
        ],
    )
    with localcontext() as context:
        context.prec = 3
        snapshot = trace.timeline_export()
    assert [row["seconds_since_earlier_selected_timestamp"] for row in snapshot["records"]] == [
        None,
        "0.000001",
        "172800.000001",
    ]
    assert "not proof of inactivity" in snapshot["metadata"]["timeline"]["gap_scope"]


def test_hidden_ties_and_full_coverage_survive_type_time_asset_and_account_filters():
    """Selecting one operation cannot erase known same-time peers or loaded page coverage."""
    trace = mixed()
    selected = TraceFilters(operations=("account_merge",), start=instant("2026-10-01T02:00:00Z"))
    snapshot = trace.timeline_export("XLM", selected, B)
    row = snapshot["records"][0]
    assert row["operation"] == "60" and row["same_timestamp_loaded_operations"] == 2
    assert row["same_timestamp_selected_operations"] == 1
    assert snapshot["metadata"]["timeline"]["account_focus"] == B
    assert snapshot["metadata"]["accounts"][0]["loaded_records"] == 5
    assert snapshot["metadata"]["accounts"][0]["next_cursor"] == "60"
    assert snapshot["metadata"]["filtered_unique_operations"] == {"Operation type filter": 4}
    assert len(trace.timeline_export("USD:" + C)["records"]) == 1
    assert not trace.timeline_export("USD:" + D)["records"]
    assert not trace.timeline_export("EUR:" + D)["records"]
    assert not trace.timeline_export(account=C)["records"]
    assert trace.build()[2][B] == 1  # Local focus never changes discovery/eligibility.


def test_duplicate_provenance_is_preserved_without_duplicate_timeline_rows():
    """One operation observed on two accounts appears once with both original source/cache/page entries."""
    trace = mixed()
    observed = trace.pages[A][0].records[0]
    duplicate_page = replace(
        page(B, [observed]), source="other-fixture", cache_status="Cached data"
    )
    trace.add_page(B, duplicate_page)
    rows = trace.timeline_export()["records"]
    entry = next(row for row in rows if row["operation"] == "100")
    assert len(rows) == 5 and len(entry["observed_in"]) == 2
    assert entry["same_timestamp_loaded_operations"] == 3
    assert entry["observed_in"][0]["record_cursor"] == "100"
    assert entry["observed_in"][1]["source"] == "other-fixture"
    assert entry["observed_in"][1]["cache_status"] == "Cached data"
    assert entry["observed_in"][1]["fetched_at"] == duplicate_page.fetched_at.isoformat()


def test_conflicts_invalid_times_failed_and_foreign_observations_never_ordered():
    """Timeline reuses all structural exclusions before selection and shared-timestamp counting."""
    trace = mixed()
    conflict = replace(
        trace.pages[A][0].records[0],
        transfer=replace(trace.pages[A][0].records[0].transfer, amount=Decimal(3)),
    )
    trace.add_page(B, page(B, [conflict]))
    rows = trace.timeline_export()["records"]
    assert "100" not in [row["operation"] for row in rows]
    assert (
        next(row for row in rows if row["operation"] == "70")["same_timestamp_loaded_operations"]
        == 2
    )
    malformed = timed(100, A, B, "bad-time")
    foreign = replace(
        transfer(90, A, B), transfer=replace(transfer(90, A, B).transfer, network=Network.MAINNET)
    )
    failed = parsed([{**operation_row(80), "transaction_successful": False}]).records[0]
    invalid = Trace(A, Network.TESTNET, [page(A, [malformed, foreign, failed])]).timeline_export()
    assert not invalid["records"]
    assert invalid["metadata"]["excluded_unique_operations"] == {
        "Invalid/unknown transfer time": 1,
        "Conflicting or mismatched observation": 1,
        "Failed transaction": 1,
    }
    assert invalid["metadata"]["accounts"][0]["unknown_record_times"] == 1


def test_empty_timeline_csv_keeps_scope_and_selection():
    """A valid empty snapshot still carries privacy/coverage/query metadata and one auditable CSV row."""
    trace = Trace(A, Network.TESTNET)
    snapshot = trace.timeline_export(account=B)
    assert not snapshot["records"]
    assert snapshot["metadata"]["timeline"]["oldest_selected_time"] is None
    assert snapshot["metadata"]["timeline"]["selected_unique_operations"] == 0
    assert snapshot["metadata"]["timeline"]["account_focus"] == B
    assert len(serialize(snapshot, "csv").splitlines()) == 2
    assert json.loads(serialize(snapshot, "json"))["kind"] == "trace_timeline"


def test_ui_local_order_focus_evidence_and_active_snapshot(app):
    """Visible-tab exports follow local timeline controls; transaction opening always uses the matching row."""
    dialog = TraceDialog(payments(object()))
    dialog.trace = mixed()
    dialog.rebuild()
    assert dialog.active_document()["kind"] == "expanded_graph"
    dialog.tables.setCurrentIndex(2)
    assert dialog.timeline_table.rowCount() == 5
    assert dialog.canvas.isHidden() and not dialog.target.isEnabled()
    assert "execution order" in dialog.paths.toPlainText()
    assert "Save timeline" in dialog.save.text()
    before = dialog.active_document()
    assert before["kind"] == "trace_timeline"
    hashes = []
    dialog.transaction_requested.connect(hashes.append)
    dialog.timeline_table.selectRow(0)
    dialog.timeline_table.itemDoubleClicked.emit(dialog.timeline_table.item(0, 0))
    assert hashes == [f"{70:064x}"]
    assert "Requested destination_min" in dialog.evidence.item(0).text()
    assert "Retrieved" in dialog.evidence.item(0).text()
    dialog.timeline_order.setCurrentIndex(1)
    assert dialog.timeline_rows[0]["operation"] == "60"
    dialog.timeline_account.setCurrentIndex(dialog.timeline_account.findData(B))
    assert len(dialog.timeline_rows) == 5
    dialog.operation_type.setCurrentIndex(dialog.operation_type.findText("Account merges"))
    dialog.apply_filters()
    assert len(dialog.timeline_rows) == 1 and dialog.timeline_rows[0]["source_amount"] is None
    assert dialog.active_document()["metadata"]["timeline"]["order"] == "Newest first"
    dialog.from_time.setText("invalid")
    dialog.apply_filters()
    assert dialog.active_document()["records"][0]["operation"] == "60"
    # Previously frozen snapshots do not change when controls or evidence change.
    assert len(before["records"]) == 5 and before["metadata"]["timeline"]["order"] == "Oldest first"
    dialog.clear_filters()
    assert len(dialog.timeline_rows) == 5 and dialog.timeline_order.currentIndex() == 1
    dialog.tables.setCurrentIndex(0)
    assert dialog.active_document()["kind"] == "expanded_graph"
    assert not dialog.canvas.isHidden() and dialog.target.isEnabled()
    assert "Routes use direct payment" in dialog.paths.toPlainText()
    dialog.reject()


def test_saved_timeline_reopens_offline_and_is_not_refreshable(app, tmp_path):
    """Save through the actual window/library path; timeline snapshots retain their own kind and original evidence."""
    store = InvestigationStore(tmp_path / "cases.db")
    window = MainWindow(AccountService(object()), store)
    library = window.investigations
    library.name.setText("Chronology")
    library.create()
    snapshot = mixed().timeline_export(account=B, newest_first=True)
    window.save_trace(snapshot)
    library.entries.setCurrentRow(0)
    assert not library.refresh_button.isEnabled()
    assert "trace_timeline" in library.evidence.toPlainText()
    saved = store.list()[0]["entries"][0]
    assert saved["kind"] == "trace_timeline" and saved["evidence"] == [snapshot]
    assert '"record_kind": "timeline_operation"' in serialize(snapshot, "json")
    assert "record.observed_in" in serialize(snapshot, "csv")
    window.close()


def test_timeline_controls_during_fetch_keep_request_and_reorder_new_evidence(app):
    """Local chronological queries neither cancel nor change a pending frozen account/network request."""

    async def scenario():
        """Release a page after adjusting focus/order and verify accepted observations appear in the requested view."""
        future = asyncio.get_running_loop().create_future()
        calls = []

        class Service:
            """Record a controlled expansion request with no HTTP connection."""

            async def activity(self, *args):
                """Hold the response while local controls change."""
                calls.append(args)
                return await future

        dialog = TraceDialog(payments(Service()))
        dialog.choose_account(B)
        dialog.start()
        pending = dialog.task
        await asyncio.sleep(0)
        dialog.tables.setCurrentIndex(2)
        dialog.timeline_order.setCurrentIndex(1)
        dialog.timeline_account.setCurrentIndex(dialog.timeline_account.findData(B))
        assert dialog.task is pending
        future.set_result(page(B, [timed(90, B, C, "2026-10-02T00:00:00Z")]))
        await pending
        assert calls == [(B, Network.TESTNET, dialog.trace.pages[A][0].kind, None)]
        assert dialog.timeline_rows[0]["operation"] == "90"
        assert dialog.timeline_account.currentData() == B
        assert len(dialog.timeline_rows) == 2
        dialog.reject()

    asyncio.run(scenario())


def test_timeline_retains_all_operations_at_existing_resource_bounds():
    """The chronological table/export introduces no hidden row cap beyond the existing fetch bounds."""
    from stellar_sdk import StrKey

    peers = [StrKey.encode_ed25519_public_key(bytes([i]) * 32) for i in range(10, 19)]
    trace = Trace(A, Network.TESTNET)
    token = 10000
    for address in [A, *peers]:
        for index in range(5):
            records = []
            for offset in range(20):
                recipient = peers[offset % len(peers)] if address == A else address
                records.append(transfer(token, A, recipient))
                token -= 1
            trace.add_page(
                address, page(address, records, records[-1].cursor if index < 4 else None)
            )
    snapshot = trace.timeline_export()
    assert len(snapshot["records"]) == 1000
    assert snapshot["metadata"]["timeline"]["selected_timestamp_groups"] == 1
    assert snapshot["records"][0]["same_timestamp_loaded_operations"] == 1000
    assert snapshot["metadata"]["limits"]["accounts"] == 10
    assert all(len(pages) == 5 for pages in trace.pages.values())


def test_cancel_resistant_response_never_enters_timeline(app):
    """A provider ignoring cancellation cannot change a frozen timeline or any accepted page."""

    async def scenario():
        """Resolve an old page after invalidating the request generation in the real dialog."""
        future = asyncio.get_running_loop().create_future()

        class Service:
            """Simulate a provider whose response cannot be immediately aborted."""

            async def activity(self, *args):
                """Continue to completion while letting stale-result validation protect the view."""
                try:
                    return await asyncio.shield(future)
                except asyncio.CancelledError:
                    return await future

        dialog = TraceDialog(payments(Service()))
        dialog.tables.setCurrentIndex(2)
        before = dialog.active_document()["records"]
        dialog.choose_account(B)
        dialog.start()
        pending = dialog.task
        await asyncio.sleep(0)
        dialog.cancel_request()
        await asyncio.sleep(0)
        future.set_result(page(B, [timed(90, B, C, "2026-10-02T00:00:00Z")]))
        await pending
        assert B not in dialog.trace.pages and dialog.timeline_rows == before
        assert dialog.active_document()["records"] == before
        dialog.reject()

    asyncio.run(scenario())
