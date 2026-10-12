"""Prove local UTC/type filters preserve evidence, full coverage, pagination and expansion bounds."""

import asyncio
import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication
from test_activity import record
from test_tracing import A, B, C, D, page, payments, seeded, transfer

from polarstellar.analysis.trace_filters import TraceFilters, instant
from polarstellar.analysis.tracing import Trace
from polarstellar.stellar.activity import ActivityKind, parse_page
from polarstellar.stellar.models import Network
from polarstellar.storage import codec
from polarstellar.storage.export import serialize
from polarstellar.ui.trace_view import TraceDialog


def timed(token, sender, recipient, created, operation="payment", amount="1.0000000", asset="XLM"):
    """Construct explicit immutable operation type/time evidence, retaining the original timestamp."""
    entry = transfer(token, sender, recipient, amount, asset)
    flow = replace(entry.transfer, created=created, operation_type=operation)
    return replace(entry, transfer=flow, values=(created,))


def sample():
    """Mix operation types, exact amounts, offsets and boundaries on one fetched root page."""
    return Trace(
        A,
        Network.TESTNET,
        [
            page(
                A,
                [
                    timed(100, A, B, "2026-10-01T00:00:00Z", amount="900000000.0000001"),
                    timed(90, A, B, "2026-10-01T08:00:00+07:00", "create_account", "0.0000002"),
                    timed(80, A, B, "2026-10-02T00:00:00Z", amount="2.0000000"),
                ],
                "80",
            )
        ],
    )


@pytest.fixture
def app():
    """Keep Qt alive without a display or public API access."""
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-01",
        "2026-10-01T00:00:00",
        "not-date",
        "2026-02-30T00:00:00Z",
        "2026-10-01T00:00:00+99:00",
        "",
        "2026-10-01T00:00:00.0000001Z",
        "2026-10-01T00:00Z",
    ],
)
def test_invalid_or_naive_time_is_refused(value):
    """Never guess dates, timezone offsets or malformed calendar values."""
    with pytest.raises(ValueError):
        instant(value)


@pytest.mark.parametrize(
    "operations",
    [(), ("set_options",), ("payment", "payment"), ["payment"], ("invoke_host_function",)],
)
def test_invalid_operation_filter_is_refused(operations):
    """Unsupported types, duplicates and invalid containers cannot become filter selections."""
    with pytest.raises(ValueError):
        TraceFilters(operations=operations)


def test_offsets_and_inclusive_boundaries_keep_exact_evidence():
    """Compare real instants, including equal endpoints, without rounding or rewriting original evidence."""
    trace = sample()
    chosen = TraceFilters(instant("2026-10-01T00:00:00Z"), instant("2026-10-01T01:00:00Z"))
    edges, reasons, distances = trace.build(chosen)
    assert edges[0].total == Decimal("900000000.0000003")
    assert [flow.operation_type for flow in edges[0].evidence] == ["payment", "create_account"]
    assert edges[0].evidence[1].created == "2026-10-01T08:00:00+07:00"
    assert reasons == {"Outside selected time range": 1} and distances[B] == 1
    single = TraceFilters(instant("2026-10-01T01:00:00Z"), instant("2026-10-01T01:00:00Z"))
    assert trace.build(single)[0][0].total == Decimal("0.0000002")
    with pytest.raises(ValueError):
        TraceFilters(instant("2026-10-02T00:00:00Z"), instant("2026-10-01T00:00:00Z"))
    with pytest.raises(ValueError):
        TraceFilters(datetime(2026, 10, 1))  # noqa: DTZ001 -- Prove naive API boundaries are rejected.


def test_operation_selection_routes_and_export_counts():
    """Select funding-only evidence consistently in totals/routes/exports while retaining all page coverage."""
    trace = sample()
    funding = TraceFilters(operations=("create_account",))
    assert trace.build(funding)[0][0].total == Decimal("0.0000002")
    assert trace.paths(B, "XLM", view_filters=funding) == [(A, B)]
    snapshot = trace.export("XLM", B, view_filters=funding)
    metadata = snapshot["metadata"]
    assert metadata["filtered_unique_operations"] == {"Operation type filter": 2}
    assert metadata["excluded_unique_operations"] == {}
    assert metadata["accounts"][0]["loaded_records"] == 3
    assert metadata["accounts"][0]["oldest_record_time"] == "2026-10-01T00:00:00+00:00"
    assert metadata["accounts"][0]["newest_record_time"] == "2026-10-02T00:00:00+00:00"
    assert metadata["accounts"][0]["next_cursor"] == "80"
    assert snapshot["records"][0]["evidence"][0]["operation_type"] == "create_account"
    assert "create_account" in serialize(snapshot, "csv")
    assert metadata["trace_filters"]["operation_types"] == ["create_account"]
    assert metadata["paths"] == [[A, B]]


def test_filters_do_not_relax_expansion_depth_or_remove_discovered_nodes():
    """A narrow time window hides edges, but discovery and all expansion caps remain unfiltered."""
    trace = seeded()
    trace.add_page(B, page(B, [transfer(90, B, C)]))
    trace.add_page(C, page(C, [transfer(80, C, D)]))
    later = TraceFilters(start=instant("2026-10-01T00:00:00Z"))
    edges, _, distances = trace.build(later)
    assert not edges and distances[D] == 3
    assert "three hops" in trace.reason(D)
    assert trace.export(view_filters=later)["metadata"]["unfetched_accounts"] == [D]
    assert not trace.paths(D, "XLM", view_filters=later)


def test_invalid_times_and_types_are_excluded_separately_from_filters():
    """Malformed dates/types cannot add money, while unknown loaded times remain explicit in coverage."""
    trace = Trace(
        A,
        Network.TESTNET,
        [
            page(
                A,
                [
                    timed(100, A, B, "missing"),
                    timed(90, A, B, "2026-10-01T00:00:00Z", "account_merge"),
                    timed(80, A, B, "2026-10-01T00:00:00Z", "create_account"),
                ],
            )
        ],
    )
    metadata = trace.export(view_filters=TraceFilters(operations=("payment",)))["metadata"]
    assert metadata["excluded_unique_operations"] == {
        "Invalid/unknown transfer time": 1,
        "Unsupported transfer operation type": 1,
    }
    assert metadata["filtered_unique_operations"] == {"Operation type filter": 1}
    assert metadata["accounts"][0]["unknown_record_times"] == 1


def test_dedup_and_conflict_checks_happen_before_filtering():
    """A conflict remains excluded even if one conflicting observation falls outside the selected time."""
    trace = sample()
    duplicate = timed(100, A, B, "2026-10-01T00:00:00Z", amount="900000000.0000001")
    trace.add_page(B, page(B, [duplicate, timed(70, B, C, "2026-10-01T00:00:00Z")]))
    assert trace.build(TraceFilters(end=instant("2026-10-01T00:00:00Z")))[0][0].total == Decimal(
        "900000000.0000001"
    )
    trace.pages[B][0] = page(
        B,
        [replace(duplicate, transfer=replace(duplicate.transfer, created="2026-10-03T00:00:00Z"))],
    )
    snapshot = trace.export(view_filters=TraceFilters(end=instant("2026-10-01T00:00:00Z")))
    assert not snapshot["records"]
    assert (
        snapshot["metadata"]["excluded_unique_operations"]["Conflicting or mismatched observation"]
        == 1
    )


def test_parser_preserves_funding_type_and_cache_roundtrip():
    """Horizon funding is explicitly typed and new cache records retain it; old transfer schemas fail closed."""
    row = record(100, ActivityKind.PAYMENTS)
    row.update(
        type="create_account",
        funder=row.pop("from"),
        account=row.pop("to"),
        starting_balance="1.0000001",
    )
    for key in ("amount", "asset_type", "asset_code", "asset_issuer"):
        row.pop(key, None)
    from test_accounts import ADDRESS

    parsed = parse_page(
        {"_embedded": {"records": [row]}},
        ADDRESS,
        Network.TESTNET,
        ActivityKind.PAYMENTS,
        "fixture",
        None,
    )
    flow = parsed.records[0].transfer
    assert flow.operation_type == "create_account" and flow.asset == "XLM"
    assert codec.loads(codec.dumps(parsed)) == parsed
    legacy = json.loads(codec.dumps(flow))
    del legacy["fields"]["operation_type"]
    with pytest.raises(ValueError, match="version"):
        codec.decode(legacy)


def test_ui_applied_state_bad_edits_and_clear_keep_pagination(app):
    """Edits are not applied until chosen; invalid ranges keep old exports and Clear makes no requests."""
    dialog = TraceDialog(payments(object()))
    dialog.trace = sample()
    dialog.rebuild()
    dialog.operation_type.setCurrentIndex(2)
    assert dialog.export_document()["metadata"]["trace_filters"]["operation_types"] == [
        "payment",
        "create_account",
        "path_payment_strict_send",
        "path_payment_strict_receive",
        "account_merge",
    ]
    dialog.apply_filters()
    assert dialog.table.rowCount() == 1 and dialog.connections[0].total == Decimal("0.0000002")
    before = dialog.export_document()["metadata"]["trace_filters"]
    dialog.from_time.setText("2026-10-03T00:00:00Z")
    dialog.until_time.setText("2026-10-01T00:00:00Z")
    dialog.apply_filters()
    assert "retained" in dialog.filter_status.text()
    assert dialog.export_document()["metadata"]["trace_filters"] == before
    dialog.table.selectRow(0)
    assert "create_account" in dialog.evidence.item(0).text()
    hashes = []
    dialog.transaction_requested.connect(hashes.append)
    dialog.evidence.itemDoubleClicked.emit(dialog.evidence.item(0))
    assert hashes == [f"{90:064x}"]
    cursor = dialog.trace.pages[A][-1].next_cursor
    dialog.clear_filters()
    assert dialog.connections[0].total == Decimal("900000002.0000003")
    assert dialog.trace.pages[A][-1].next_cursor == cursor
    assert dialog.export_document()["metadata"]["trace_filters"]["from_utc"] is None
    dialog.reject()


def test_filter_changes_during_fetch_keep_query_and_apply_to_new_page(app):
    """Applying local filters during a fetch neither cancels it nor changes its account/network/cursor."""

    async def scenario():
        """Hold an expansion while selecting funding-only, then deliver mixed typed evidence."""
        future = asyncio.get_running_loop().create_future()
        calls = []

        class Service:
            """Record one controlled request without accessing Horizon."""

            async def activity(self, *args):
                """Capture query before waiting for the test's next page."""
                calls.append(args)
                return await future

        dialog = TraceDialog(payments(Service()))
        dialog.choose_account(B)
        dialog.start()
        pending = dialog.task
        await asyncio.sleep(0)
        dialog.operation_type.setCurrentIndex(2)
        dialog.apply_filters()
        assert dialog.task is pending
        future.set_result(
            page(
                B,
                [
                    timed(90, B, C, "2026-10-01T00:00:00Z", "create_account"),
                    timed(80, B, C, "2026-10-01T00:00:00Z"),
                ],
                "80",
            )
        )
        await pending
        assert calls == [(B, Network.TESTNET, ActivityKind.PAYMENTS, None)]
        assert (
            len(dialog.connections) == 1
            and dialog.connections[0].evidence[0].operation_type == "create_account"
        )
        assert B in [dialog.account.itemData(i) for i in range(dialog.account.count())]
        dialog.reject()

    asyncio.run(scenario())


def test_unknown_operation_type_never_assumed_payment():
    """Untyped evidence is excluded rather than treated as payment/funding by default."""
    entry = timed(100, A, B, "2026-10-01T00:00:00Z")
    unknown = replace(entry, transfer=replace(entry.transfer, operation_type=""))
    trace = Trace(A, Network.TESTNET, [page(A, [unknown])])
    edges, excluded, _ = trace.build()
    assert not edges and excluded == {"Unsupported transfer operation type": 1}


def test_incoming_routes_and_saved_filter_metadata_preserve_asset_identity():
    """Incoming route queries use the same time/type selection and never combine different issuers."""
    first, other = "USD:" + C, "USD:" + D
    trace = Trace(
        A,
        Network.TESTNET,
        [
            page(
                A,
                [
                    timed(100, B, A, "2026-10-01T00:00:00Z", asset=first),
                    timed(90, B, A, "2026-10-02T00:00:00Z", asset=first),
                    timed(80, B, A, "2026-10-01T00:00:00Z", asset=other),
                ],
            )
        ],
    )
    chosen = TraceFilters(end=instant("2026-10-01T00:00:00Z"), operations=("payment",))
    snapshot = trace.export(first, B, incoming=True, view_filters=chosen)
    assert snapshot["metadata"]["paths"] == [[B, A]]
    assert len(snapshot["records"]) == 1 and snapshot["records"][0]["asset"] == first
    assert snapshot["records"][0]["total"] == "1.0000000"
    assert snapshot["metadata"]["direction"] == "To root"
    assert snapshot["metadata"]["trace_filters"]["until_utc"] == "2026-10-01T00:00:00+00:00"
