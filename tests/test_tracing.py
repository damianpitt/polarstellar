"""Prove multi-hop graph bounds, deduplicated evidence and cancellation/network isolation."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication
from stellar_sdk import StrKey

from polarstellar.analysis.tracing import MAX_PAGES, Trace
from polarstellar.app.window import MainWindow
from polarstellar.stellar.activity import ActivityKind, ActivityPage, ActivityRecord
from polarstellar.stellar.models import Network, Transfer
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService
from polarstellar.storage.export import serialize
from polarstellar.storage.investigations import InvestigationStore
from polarstellar.ui.activity_view import ActivityView
from polarstellar.ui.trace_view import TraceDialog

A, B, C, D, E = [StrKey.encode_ed25519_public_key(bytes([i]) * 32) for i in range(5)]


def transfer(token, sender, recipient, amount="1.0000000", asset="XLM"):
    """Build immutable payment evidence with stable operation/transaction identifiers."""
    flow = Transfer(
        Network.TESTNET,
        sender,
        recipient,
        asset,
        Decimal(amount),
        str(token),
        f"{token:064x}",
        "2026-09-29T00:00:00Z",
    )
    return ActivityRecord(str(token), str(token), (flow.created,), flow, "")


def page(address, records=(), cursor=None):
    """Return a page with explicit continuation and original provenance."""
    return ActivityPage(
        address,
        Network.TESTNET,
        ActivityKind.PAYMENTS,
        tuple(records),
        cursor,
        "fixture",
        datetime.now(UTC),
    )


def seeded():
    """Start with one root-to-peer transfer and a continuation for root pagination."""
    return Trace(A, Network.TESTNET, [page(A, [transfer(100, A, B)], "100")])


def test_multihop_exact_dedup_paths_and_cycles():
    """Overlapping account pages must not double totals, and routes must avoid cycles/assets."""
    trace = seeded()
    trace.add_page(
        B,
        page(
            B,
            [
                transfer(100, A, B),
                transfer(90, B, C, "900000000.0000001"),
                transfer(80, B, C, "0.0000002"),
            ],
        ),
    )
    trace.add_page(
        C,
        page(
            C,
            [
                transfer(90, B, C, "900000000.0000001"),
                transfer(80, B, C, "0.0000002"),
                transfer(70, C, A),
                transfer(60, C, D, asset="USD:" + E),
            ],
        ),
    )
    edges, _, distances = trace.build()
    assert next(edge.total for edge in edges if edge.sender == A) == Decimal("1.0000000")
    assert next(edge.total for edge in edges if edge.sender == B) == Decimal("900000000.0000003")
    assert trace.paths(C, "XLM") == [(A, B, C)]
    assert trace.paths(C, "XLM", incoming=True) == [(C, A)]
    assert not trace.paths(D, "XLM")
    assert distances[C] == 1  # Discovery distance is deliberately undirected.


def test_depth_pages_and_account_limits():
    """Stop expansion at the documented depth and resource caps, not via implicit crawling."""
    trace = seeded()
    trace.add_page(B, page(B, [transfer(90, B, C)]))
    trace.add_page(C, page(C, [transfer(80, C, D)]))
    assert "three hops" in trace.reason(D)
    with pytest.raises(ValueError):
        trace.add_page(D, page(D, [transfer(70, D, E)]))
    for i in range(1, MAX_PAGES):
        token = 100 - i
        trace.add_page(A, page(A, [transfer(token, A, B)], str(token)))
    assert "five pages" in trace.reason(A)
    assert len(trace.pages[A]) == 5
    peers = [StrKey.encode_ed25519_public_key(bytes([i]) * 32) for i in range(10, 21)]
    limited = Trace(
        A, Network.TESTNET, [page(A, [transfer(100 - i, A, peer) for i, peer in enumerate(peers)])]
    )
    for peer in peers[:9]:
        limited.add_page(peer, page(peer))
    assert "ten fetched" in limited.reason(peers[9])


def test_mismatch_conflict_exclusion_and_cursor_safety():
    """Invalid page identities/cursors fail before mutation; conflicting observations never add money."""
    trace = seeded()
    for invalid in (
        replace(page(B), network=Network.MAINNET),
        replace(page(B), address=C),
        replace(page(B), kind=ActivityKind.OPERATIONS),
    ):
        with pytest.raises(ValueError):
            trace.add_page(B, invalid)
    assert B not in trace.pages
    with pytest.raises(ValueError, match="advance"):
        trace.add_page(A, page(A, [transfer(100, A, B)]))
    trace.add_page(
        B,
        page(
            B,
            [
                transfer(100, A, B, "2.0000000"),
                transfer(90, B, B),
                ActivityRecord("80", "80", ("today",), None, "Failed transaction"),
            ],
        ),
    )
    edges, excluded, _ = trace.build()
    assert not edges
    assert excluded == {
        "Conflicting or mismatched observation": 1,
        "Self transfer": 1,
        "Failed transaction": 1,
    }


def test_export_provenance_amounts_and_unknown_coverage():
    """Exports carry per-account source/cache/time boundaries and exact edge evidence."""
    trace = seeded()
    trace.add_page(B, replace(page(B, [transfer(90, B, C)]), cache_status="Cached data"))
    snapshot = json.loads(serialize(trace.export("XLM", C), "json"))
    assert snapshot["records"][0]["total"] == "1.0000000"
    assert snapshot["metadata"]["accounts"][1]["pages"][0]["cache_status"] == "Cached data"
    assert snapshot["metadata"]["accounts"][0]["end_of_available_results"] is False
    assert snapshot["metadata"]["accounts"][1]["end_of_available_results"] is True
    assert snapshot["metadata"]["unfetched_accounts"] == [C]
    assert snapshot["metadata"]["paths"] == [[A, B, C]]
    assert "record.evidence" in serialize(snapshot, "csv")


@pytest.fixture
def app():
    """Keep a headless Qt application alive during graph UI tests."""
    return QApplication.instance() or QApplication([])


def payments(service):
    """Seed the real Payments view without requesting external data."""
    view = ActivityView(service, ActivityKind.PAYMENTS)
    view.reset((A, Network.TESTNET))
    view.export_pages = [page(A, [transfer(100, A, B)], "100")]
    view.loaded = True
    return view


def test_dialog_failure_retry_pagination_and_evidence(app):
    """A failed page preserves rows/cursor; retry fetches the same page, then advances per account."""

    async def scenario():
        """Use controlled responses to drive the actual expand controls and transaction links."""
        calls = []

        class Service:
            """Simulate transient failure followed by two successful pages."""

            async def activity(self, address, network, kind, cursor):
                """Record query identity to prove independent pagination."""
                calls.append((address, network, cursor))
                if len(calls) == 1:
                    raise AccountError("Temporary failure")
                if cursor is None:
                    return page(B, [transfer(90, B, C)], "90")
                return page(B, [transfer(80, B, C)])

        view = payments(Service())
        dialog = TraceDialog(view)
        dialog.choose_account(B)
        dialog.start()
        await dialog.task
        assert "retained" in dialog.status.text() and len(dialog.connections) == 1
        dialog.start()
        await dialog.task
        dialog.start()
        await dialog.task
        assert [call[2] for call in calls] == [None, None, "90"]
        assert all(call[:2] == (B, Network.TESTNET) for call in calls)
        row = next(i for i, edge in enumerate(dialog.connections) if edge.sender == B)
        dialog.table.selectRow(row)
        assert dialog.evidence.count() == 2
        hashes = []
        dialog.transaction_requested.connect(hashes.append)
        dialog.evidence.itemDoubleClicked.emit(dialog.evidence.item(0))
        assert hashes == [f"{90:064x}"]
        dialog.reject()

    asyncio.run(scenario())


def test_context_reset_discards_cancellation_resistant_response(app, tmp_path):
    """Switching network closes the expanded graph and prevents old data from being accepted."""

    async def scenario():
        """Hold a provider response until after the explorer changes its network."""
        future = asyncio.get_running_loop().create_future()

        class Provider:
            """Return a stale page even after cancellation to exercise generation protection."""

            async def get_activity(self, *args):
                """Shield the mock response against task cancellation."""
                try:
                    return await asyncio.shield(future)
                except asyncio.CancelledError:
                    return await future

        window = MainWindow(AccountService(Provider()), InvestigationStore(tmp_path / "cases.db"))
        root = window.activity_views[2]
        root.reset((A, Network.TESTNET))
        root.export_pages = [page(A, [transfer(100, A, B)], "100")]
        root.loaded = True
        window.graph.open_trace()
        dialog = window.graph.trace_dialog
        dialog.choose_account(B)
        dialog.start()
        task = dialog.task
        await asyncio.sleep(0)
        window.network.setCurrentText(
            "Testnet"
        )  # UI starts at Mainnet; any context reset closes trace.
        future.set_result(page(B, [transfer(90, B, C)]))
        await task
        assert window.graph.trace_dialog is None
        assert B not in dialog.trace.pages
        window.close()

    asyncio.run(scenario())


def test_saved_trace_offline_and_no_resource_refresh(app, tmp_path):
    """Store all trace metadata/evidence and keep graph snapshots out of single-resource refresh."""
    store = InvestigationStore(tmp_path / "saved.db")
    window = MainWindow(AccountService(object()), store)
    library = window.investigations
    library.name.setText("Trace evidence")
    library.create()
    trace = seeded()
    trace.add_page(B, page(B, [transfer(90, B, C)]))
    snapshot = trace.export("XLM", C)
    window.save_trace(snapshot)
    library.entries.setCurrentRow(0)
    assert not library.refresh_button.isEnabled()
    assert store.list()[0]["entries"][0]["evidence"][0] == snapshot
    assert "expanded_graph" in library.evidence.toPlainText()
    window.close()


def test_seed_cap_is_explicit():
    """Truncating seed pages must be visible in exported coverage metadata."""
    pages = [page(A, [transfer(100 - i, A, B)], str(100 - i)) for i in range(6)]
    trace = Trace(A, Network.TESTNET, pages)
    assert trace.seed_truncated and len(trace.pages[A]) == 5
    assert trace.export()["metadata"]["seed_truncated"]


def test_asset_filter_keeps_evidence_and_unfiltered_coverage(app):
    """Filtering the graph removes other-asset edges but keeps the full fetch provenance."""
    root = payments(object())
    dialog = TraceDialog(root)
    dialog.trace.add_page(B, page(B, [transfer(90, B, C, asset="USD:" + E)]))
    dialog.rebuild()
    dialog.asset.setCurrentIndex(dialog.asset.findData("USD:" + E))
    snapshot = dialog.export_document()
    assert len(snapshot["records"]) == 1
    assert snapshot["records"][0]["asset"] == "USD:" + E
    assert len(snapshot["metadata"]["accounts"]) == 2
    assert dialog.table.rowCount() == 1
    dialog.reject()


def test_route_results_stop_at_cap(monkeypatch):
    """A dense graph yields bounded route output and an explicit possible-truncation flag."""
    from polarstellar.analysis.tracing import MAX_PATHS, Connection

    trace = seeded()
    left = [f"left-{i}" for i in range(4)]
    right = [f"right-{i}" for i in range(26)]
    edges = [Connection(A, node, "XLM", Decimal(1), ()) for node in left]
    edges += [
        Connection(source, target, "XLM", Decimal(1), ()) for source in left for target in right
    ]
    edges += [Connection(node, D, "XLM", Decimal(1), ()) for node in right]
    monkeypatch.setattr(trace, "build", lambda: (edges, {}, {A: 0, D: 1}))
    routes = trace.paths(D, "XLM")
    assert len(routes) == MAX_PATHS
    assert all(len(route) == 4 and len(set(route)) == 4 for route in routes)
    assert trace.export("XLM", D)["metadata"]["paths_may_be_truncated"]
