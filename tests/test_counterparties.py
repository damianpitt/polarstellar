import asyncio
import os
from decimal import Decimal

import httpx
from PySide6.QtWidgets import QApplication
from stellar_sdk import StrKey
from test_accounts import ADDRESS, ISSUER
from test_activity import record

from polarstellar.analysis.counterparties import analyze
from polarstellar.stellar.activity import ActivityKind, parse_page
from polarstellar.stellar.horizon import HorizonProvider
from polarstellar.stellar.models import Network
from polarstellar.stellar.service import AccountService
from polarstellar.ui.activity_view import ActivityView
from polarstellar.ui.graph_view import GraphView, Node

OTHER = StrKey.encode_ed25519_public_key(bytes([2]) * 32)


def records(rows, network=Network.MAINNET):
    return parse_page(
        {"_embedded": {"records": rows}}, ADDRESS, network, ActivityKind.PAYMENTS, "fixture", None
    ).records


def payment(token, **changes):
    return {**record(token, ActivityKind.PAYMENTS), **changes}


def test_exact_direction_asset_identity_and_evidence():
    rows = records(
        [
            payment(5, amount="900000000.0000001"),
            payment(4, amount="0.0000002"),
            payment(3, **{"from": ISSUER, "to": ADDRESS, "amount": "7.0000000"}),
            payment(2, asset_issuer=OTHER, amount="3.0000000"),
        ]
    )
    result = analyze([*rows, rows[0]], ADDRESS, Network.MAINNET)
    assert len(result.relationships) == 3
    outgoing = result.relationships[0]
    assert outgoing.total == Decimal("900000000.0000003")
    assert outgoing.direction == "Outgoing"
    assert len(outgoing.evidence) == 2
    assert outgoing.evidence[0].operation == "5"
    assert outgoing.evidence[0].transaction == "hash5"
    assert result.graph.number_of_edges() == 3
    assert result.graph.has_edge(ISSUER, ADDRESS)
    assert result.graph.graph["network"] == "Mainnet"


def test_exclusions_and_network_boundaries():
    rows = records(
        [
            payment(8, transaction_successful=False),
            payment(7, type="path_payment_strict_receive"),
            payment(6, type="account_merge"),
            payment(5, to=ADDRESS),
            payment(4, **{"from": ISSUER, "to": OTHER}),
            payment(3, **{"from": "M-unsupported"}),
        ]
    )
    result = analyze(rows, ADDRESS, Network.MAINNET)
    assert not result.relationships
    assert sum(result.excluded.values()) == 6
    foreign = records([payment(1)], Network.TESTNET)
    assert analyze(foreign, ADDRESS, Network.MAINNET).excluded == {"Different network": 1}


def test_account_funding():
    row = payment(
        1, type="create_account", funder=ADDRESS, account=OTHER, starting_balance="2.0000000"
    )
    for key in ("from", "to", "amount", "asset_type", "asset_code", "asset_issuer"):
        row.pop(key)
    relation = analyze(records([row]), ADDRESS, Network.MAINNET).relationships[0]
    assert relation.counterparty == OTHER
    assert relation.asset == "XLM" and relation.total == Decimal(2)


def test_graph_refresh_filters_evidence_and_reset():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])

    async def scenario():
        rows = [
            payment(3),
            payment(2, **{"from": ISSUER, "to": ADDRESS}),
            payment(1, asset_type="native"),
        ]
        service = AccountService(
            HorizonProvider(
                httpx.MockTransport(
                    lambda r: httpx.Response(200, json={"_embedded": {"records": rows}})
                )
            )
        )
        payments = ActivityView(service, ActivityKind.PAYMENTS)
        graph = GraphView(payments)
        payments.reset((ADDRESS, Network.MAINNET))
        payments.start()
        await payments.task
        assert graph.table.rowCount() == 3
        assert len(graph.edges) == 2
        graph.direction.setCurrentText("Incoming")
        assert graph.table.rowCount() == 1
        graph.table.selectRow(0)
        assert graph.evidence.count() == 1
        hashes = []
        graph.transaction_requested.connect(hashes.append)
        graph.evidence.itemDoubleClicked.emit(graph.evidence.item(0))
        assert hashes == ["hash2"]
        graph.asset.setCurrentIndex(graph.asset.findData("XLM"))
        assert graph.table.rowCount() == 0
        payments.reset((ADDRESS, Network.TESTNET))
        assert graph.table.rowCount() == 0 and not graph.edges
        assert "Testnet" in graph.summary.text()
        payments.reset()
        assert not graph.scene.items() and not graph.load.isEnabled()
        graph.close()

    asyncio.run(scenario())
    app.processEvents()


def test_node_drag_updates_arrow():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    from PySide6.QtWidgets import QGraphicsScene

    from polarstellar.ui.graph_view import Edge

    scene = QGraphicsScene()
    source, target = Node(ADDRESS, lambda _: None), Node(ISSUER, lambda _: None)
    scene.addItem(source)
    scene.addItem(target)
    target.setPos(200, 0)
    edge = Edge(scene, source, target, "fixture")
    before = edge.line.line()
    target.setPos(300, 100)
    assert edge.line.line() != before
    assert edge.arrow.polygon().count() == 3
    app.processEvents()
