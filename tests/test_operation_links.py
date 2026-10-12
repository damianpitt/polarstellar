"""Validate broader operation coverage without inventing amounts or fund continuity."""

import asyncio
import json
from dataclasses import replace
from decimal import Decimal

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from test_tracing import A, B, C, D, page, payments, transfer

from polarstellar.analysis.counterparties import analyze
from polarstellar.analysis.trace_filters import TraceFilters, instant
from polarstellar.analysis.tracing import Trace
from polarstellar.stellar.activity import ActivityKind, parse_page
from polarstellar.stellar.models import Network
from polarstellar.storage import codec
from polarstellar.storage.export import activity_document, serialize
from polarstellar.storage.investigations import InvestigationStore, new_investigation
from polarstellar.ui.trace_view import TraceDialog


def operation_row(token=100, kind="path_payment_strict_send", sender=A, recipient=B):
    """Build Horizon-shaped actual amounts, requested limits and intermediary asset evidence."""
    row = {
        "id": str(token),
        "paging_token": str(token),
        "type": kind,
        "transaction_hash": f"{token:064x}",
        "transaction_successful": True,
        "created_at": "2026-10-01T01:00:00+01:00",
        "source_account": sender,
    }
    if kind == "account_merge":
        row.update(account=sender, into=recipient)
    else:
        row.update(
            {
                "from": sender,
                "to": recipient,
                "source_asset_type": "native",
                "source_amount": "12.0000001",
                "asset_type": "credit_alphanum4",
                "asset_code": "USD",
                "asset_issuer": C,
                "amount": "7.0000002",
                "source_max": "15.0000000",
                "destination_min": "6.0000000",
                "path": [
                    {"asset_type": "credit_alphanum4", "asset_code": "EUR", "asset_issuer": D}
                ],
            }
        )
    return row


def parsed(rows, address=A):
    """Normalize fixtures using the actual Horizon parser and immutable network identity."""
    return parse_page(
        {"_embedded": {"records": rows}},
        address,
        Network.TESTNET,
        ActivityKind.PAYMENTS,
        "fixture",
        None,
    )


@pytest.fixture
def app():
    """Keep a headless Qt application alive for real table/filter/evidence behavior."""
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("kind", ["path_payment_strict_send", "path_payment_strict_receive"])
def test_actual_sides_limits_and_intermediary_assets(kind):
    """Actual sent/received amounts remain distinct; requested limits never masquerade as actuals."""
    record = parsed([operation_row(kind=kind)]).records[0]
    assert record.transfer is None
    link = record.link
    assert link.operation_type == kind and link.source_amount == Decimal("12.0000001")
    assert link.destination_amount == Decimal("7.0000002")
    assert link.source_asset == "XLM" and link.destination_asset == "USD:" + C
    assert link.path == ("EUR:" + D,)
    assert link.limit_name == ("source_max" if kind.endswith("receive") else "destination_min")
    assert codec.loads(codec.dumps(record)) == record
    # The one-hop view's financial totals retain their historical, conservative scope.
    direct = analyze([record], A, Network.TESTNET)
    assert not direct.relationships and direct.excluded


@pytest.mark.parametrize(
    "key,value",
    [
        ("source_amount", None),
        ("source_amount", "NaN"),
        ("source_amount", "1e2"),
        ("source_amount", "-1"),
        ("source_amount", "0"),
        ("source_amount", "0.00000001"),
        ("source_amount", "922337203685.4775808"),
        ("source_asset_type", "unknown"),
        ("asset_issuer", B[:-1] + "!"),
        ("asset_code", "USD_TOO_LONG"),
        ("destination_min", "8"),
        ("path", None),
        ("path", [{}]),
        ("path", [{"asset_type": "native"}] * 6),
        ("transaction_hash", "bad"),
    ],
    ids=[
        "missing-source",
        "nan",
        "exponent",
        "negative",
        "zero",
        "precision",
        "overflow",
        "asset-type",
        "issuer",
        "code",
        "limit-contradiction",
        "missing-path",
        "invalid-path",
        "path-cap",
        "bad-hash",
    ],
)
def test_invalid_path_evidence_retains_row_without_link(key, value):
    """Malformed extra details stay visible but contribute neither relationships nor amounts."""
    row = operation_row()
    row[key] = value
    record = parsed([row]).records[0]
    assert record.link is None and record.transfer is None
    assert record.exclusion == "Incomplete or invalid path payment / merge evidence"
    trace = Trace(A, Network.TESTNET, [parsed([row])])
    assert not trace.links() and trace.build()[2] == {A: 0}
    assert trace.export()["metadata"]["accounts"][0]["loaded_records"] == 1


def test_missing_actuals_never_replaced_by_limits_and_zero_minimum_allowed():
    """Missing actual amounts cannot be filled from limits; a zero requested destination minimum is valid."""
    row = operation_row(kind="path_payment_strict_receive")
    del row["source_amount"]
    assert parsed([row]).records[0].link is None
    row = operation_row()
    row["destination_min"] = "0.0000000"
    assert parsed([row]).records[0].link.limit_amount == 0
    row = operation_row(kind="path_payment_strict_receive")
    row["source_max"] = "1"
    assert parsed([row]).records[0].link is None


def test_merge_unknown_balance_uses_explicit_removed_account():
    """Merge balance is unknown even if a provider adds amount; endpoints come from account/into."""
    row = operation_row(kind="account_merge")
    row.update(amount="999.0000000", source_account=C)
    record = parsed([row]).records[0]
    assert record.values[4:8] == (A, B, "—", "XLM")
    link = record.link
    assert link.sender == A and link.source_amount is link.destination_amount is None
    assert "unknown" in link.description()
    trace = Trace(A, Network.TESTNET, [parsed([row])])
    assert not trace.build()[0] and trace.build()[2][B] == 1
    assert not trace.paths(B, "XLM")
    snapshot = json.loads(serialize(trace.export(), "json"))
    assert snapshot["records"][0]["source_amount"] is None
    assert snapshot["records"][0]["record_kind"] == "operation_link"
    assert "unknown" in snapshot["metadata"]["operation_link_semantics"]
    del row["account"]
    assert parsed([row]).records[0].link is None


def test_dedup_network_conflict_filters_and_no_conversion_route():
    """One operation across account pages counts once; conversion links discover peers but never route money."""
    first = parsed([operation_row()])
    trace = Trace(A, Network.TESTNET, [first])
    trace.add_page(B, replace(first, address=B))
    assert len(trace.links()) == 1 and trace.build()[2][B] == 1
    # A separate expansion after the conversion can be inspected but cannot produce
    # a false A→B→C same-asset route through the path payment.
    trace = Trace(A, Network.TESTNET, [first])
    trace.add_page(B, page(B, [transfer(90, B, C)]))
    assert trace.reason(C) == "" and not trace.paths(C, "XLM")
    assert trace.links(asset="XLM") == trace.links(asset="USD:" + C)
    assert not trace.links(asset="USD:" + D) and not trace.links(asset="EUR:" + D)
    hidden = TraceFilters(operations=("payment",))
    assert not trace.links(hidden) and trace.build(hidden)[2][B] == 1
    snapshot = trace.export(view_filters=hidden)
    assert snapshot["metadata"]["filtered_unique_operations"] == {"Operation type filter": 1}
    assert snapshot["metadata"]["matching_unique_operations_before_asset_filter"] == 1
    outside = TraceFilters(start=instant("2026-10-02T00:00:00Z"))
    assert not trace.links(outside) and trace.build(outside)[2][C] == 2
    # Contradictions are excluded even when a selected window would hide one copy.
    wrong = replace(first.records[0], link=replace(first.records[0].link, source_amount=Decimal(3)))
    conflict = Trace(A, Network.TESTNET, [first])
    conflict.add_page(B, page(B, [wrong]))
    assert not conflict.links()
    assert conflict.build()[1] == {"Conflicting or mismatched observation": 1}
    foreign = replace(
        first.records[0], link=replace(first.records[0].link, network=Network.MAINNET)
    )
    assert Trace(A, Network.TESTNET, [page(A, [foreign])]).build()[1] == conflict.build()[1]


def test_failed_self_and_unknown_time_remain_excluded():
    """Failed transactions, self conversion and ambiguous timestamps cannot discover additional accounts."""
    failed = operation_row()
    failed["transaction_successful"] = False
    bad_time = operation_row(90)
    bad_time["created_at"] = "2026-10-01"
    self_transfer = operation_row(80, sender=A, recipient=A)
    trace = Trace(A, Network.TESTNET, [parsed([failed, bad_time, self_transfer])])
    assert not trace.links() and trace.build()[2] == {A: 0}
    assert trace.build()[1] == {
        "Failed transaction": 1,
        "Invalid/unknown transfer time": 1,
        "Self transfer": 1,
    }


def test_operation_discovery_keeps_three_hop_limit():
    """Path/merge discovery consumes the same depth allowance as direct endpoints, with no intermediate accounts."""
    trace = Trace(A, Network.TESTNET, [parsed([operation_row()])])
    trace.add_page(B, parsed([operation_row(90, "account_merge", B, C)], B))
    trace.add_page(C, parsed([operation_row(80, "path_payment_strict_receive", C, D)], C))
    assert "three hops" in trace.reason(D)
    assert trace.build()[2] == {A: 0, B: 1, C: 2, D: 3}
    with pytest.raises(ValueError, match="three hops"):
        trace.add_page(D, page(D))


def test_ui_filters_evidence_exports_saved_snapshot_and_old_schema(app, tmp_path):
    """Real controls expose both amount sides and transaction hash; mixed exports remain readable offline."""
    view = payments(object())
    view.export_pages = [parsed([operation_row(), operation_row(90, "account_merge")])]
    dialog = TraceDialog(view)
    assert dialog.operation_table.rowCount() == 2 and dialog.table.rowCount() == 0
    assert len(dialog.edges) == 2 and dialog.edges[0].line.pen().style() == Qt.PenStyle.DashLine
    dialog.tables.setCurrentIndex(1)
    dialog.operation_table.selectRow(0)
    assert "Sent 12.0000001 XLM" in dialog.evidence.item(0).text()
    assert "Received 7.0000002 USD:" in dialog.evidence.item(0).text()
    assert "Requested destination_min: 6.0000000" in dialog.evidence.item(0).text()
    hashes = []
    dialog.transaction_requested.connect(hashes.append)
    dialog.evidence.itemDoubleClicked.emit(dialog.evidence.item(0))
    assert hashes == [f"{100:064x}"]
    dialog.operation_type.setCurrentIndex(dialog.operation_type.findText("Account merges"))
    dialog.apply_filters()
    assert len(dialog.links) == 1 and dialog.operation_table.rowCount() == 1
    assert "unknown" in dialog.operation_table.item(0, 3).text()
    snapshot = dialog.export_document()
    assert snapshot["metadata"]["accounts"][0]["loaded_records"] == 2
    assert "record.source_amount" in serialize(snapshot, "csv")
    store = InvestigationStore(tmp_path / "investigations.db")
    saved = new_investigation("Broader evidence")
    saved["entries"].append(
        {
            "kind": "expanded_graph",
            "identifier": A,
            "network": "Testnet",
            "evidence": [snapshot],
        }
    )
    store.save(saved)
    assert store.list()[0]["entries"][0]["evidence"] == [snapshot]
    dialog.clear_filters()
    assert len(dialog.links) == 2
    view.records = list(view.export_pages[0].records)
    activity = activity_document(view)
    assert activity["records"][0]["link"]["source_amount"] == "12.0000001"
    legacy = json.loads(codec.dumps(view.export_pages[0].records[0]))
    del legacy["fields"]["link"]
    with pytest.raises(ValueError, match="version"):
        codec.decode(legacy)
    dialog.reject()


def test_filter_change_during_merge_fetch(app):
    """In-flight expansion honors newly applied merge filters without changing its request."""

    async def scenario():
        """Deliver controlled pages through the production async dialog without HTTP requests."""
        future = asyncio.get_running_loop().create_future()

        class Service:
            """Hold one page until the test deliberately releases it."""

            async def activity(self, *args):
                """Return fixture evidence after the simulated provider finishes."""
                return await future

        dialog = TraceDialog(payments(Service()))
        dialog.choose_account(B)
        dialog.start()
        pending = dialog.task
        await asyncio.sleep(0)
        dialog.operation_type.setCurrentIndex(dialog.operation_type.findText("Account merges"))
        dialog.apply_filters()
        future.set_result(parsed([operation_row(90, "account_merge", B, C)], B))
        await pending
        assert len(dialog.links) == 1 and dialog.trace.build()[2][C] == 2
        dialog.reject()

    asyncio.run(scenario())


def test_mixed_exports_keep_amounts_separate_and_count_unique_operations():
    """Direct totals remain exact beside individual conversions/unknown merges, including CSV nulls."""
    first = parsed([operation_row(100), operation_row(90, "account_merge")])
    mixed = replace(first, records=(*first.records, transfer(80, A, B, "3.0000001")))
    trace = Trace(A, Network.TESTNET, [mixed])
    snapshot = trace.export("XLM", B)
    assert snapshot["metadata"]["matching_unique_operations_before_asset_filter"] == 3
    assert snapshot["metadata"]["separate_operation_links"] == 2
    assert snapshot["metadata"]["paths"] == [[A, B]]
    direct, path, merge = snapshot["records"]
    assert direct["record_kind"] == "direct_transfer_total" and direct["total"] == "3.0000001"
    assert "total" not in path and "total" not in merge
    assert path["source_amount"] == "12.0000001" and path["destination_amount"] == "7.0000002"
    assert merge["source_amount"] is None
    selected = trace.export("USD:" + C)
    assert len(selected["records"]) == 1 and selected["records"][0]["operation"] == "100"
    assert selected["metadata"]["accounts"][0]["loaded_records"] == 3
    assert "record.record_kind" in serialize(snapshot, "csv")


def test_cancel_resistant_merge_response_cannot_mutate_trace(app):
    """Closing/cancelling invalidates a late provider response even if it ignores cancellation."""

    async def scenario():
        """Resolve an ignored-cancellation page after cancelling the real expansion dialog."""
        future = asyncio.get_running_loop().create_future()

        class Service:
            """Simulate an external provider which cannot abort its request immediately."""

            async def activity(self, *args):
                """Wait for the response despite cancellation, allowing stale-result guards to run."""
                try:
                    return await asyncio.shield(future)
                except asyncio.CancelledError:
                    return await future

        dialog = TraceDialog(payments(Service()))
        dialog.choose_account(B)
        dialog.start()
        pending = dialog.task
        await asyncio.sleep(0)
        dialog.cancel_request()
        await asyncio.sleep(0)
        future.set_result(parsed([operation_row(90, "account_merge", B, C)], B))
        await pending
        assert B not in dialog.trace.pages and not dialog.links
        dialog.reject()

    asyncio.run(scenario())


def test_inconsistent_cached_link_semantics_fail_closed():
    """Typed but inconsistent cache fields cannot turn unknown merge balances or missing path amounts into UI crashes."""
    path = json.loads(codec.dumps(parsed([operation_row()]).records[0].link))
    path["fields"]["source_amount"] = None
    with pytest.raises(ValueError, match="amount"):
        codec.decode(path)
    merge = json.loads(codec.dumps(parsed([operation_row(kind="account_merge")]).records[0].link))
    merge["fields"]["source_amount"] = {"decimal": "1.0000000"}
    with pytest.raises(ValueError, match="unknown"):
        codec.decode(merge)


def test_same_asset_path_stays_separate_from_direct_routes():
    """Same endpoint asset does not imply identical sent/received amounts or a direct transfer."""
    row = operation_row()
    row["asset_type"] = "native"
    trace = Trace(A, Network.TESTNET, [parsed([row])])
    assert len(trace.links(asset="XLM")) == 1
    assert not trace.build()[0] and not trace.paths(B, "XLM")
    assert trace.export("XLM")["records"][0]["destination_amount"] == "7.0000002"
