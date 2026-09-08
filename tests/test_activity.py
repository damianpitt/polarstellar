import asyncio
import os
import tomllib
from pathlib import Path

import httpx
import pytest
from PySide6.QtWidgets import QApplication
from test_accounts import ADDRESS, ISSUER

from polarstellar import __version__
from polarstellar.stellar.activity import ActivityKind
from polarstellar.stellar.horizon import HorizonProvider
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService
from polarstellar.ui.activity_view import ActivityView


def record(token, kind):
    row = {
        "paging_token": str(token),
        "created_at": "2026-09-09T00:00:00Z",
        "source_account": ADDRESS,
    }
    if kind == ActivityKind.TRANSACTIONS:
        row.update(hash=f"hash{token}", successful=False, operation_count=2, fee_charged="200")
    else:
        row.update(
            id=str(token),
            type="payment",
            transaction_successful=True,
            transaction_hash=f"hash{token}",
            amount="0.0000001",
            asset_type="credit_alphanum4",
            asset_code="USD",
            asset_issuer=ISSUER,
            to=ISSUER,
        )
        row["from"] = ADDRESS
    return row


def payload(tokens, kind):
    return {"_embedded": {"records": [record(token, kind) for token in tokens]}}


@pytest.mark.parametrize("kind", list(ActivityKind))
@pytest.mark.parametrize("network", list(Network))
def test_pagination_routing_and_end(kind, network):
    requests = []

    def handler(request):
        requests.append(request)
        cursor = request.url.params.get("cursor")
        tokens = (
            range(100, 80, -1) if cursor is None else range(80, 60, -1) if cursor == "81" else []
        )
        return httpx.Response(200, json=payload(tokens, kind))

    async def scenario():
        service = AccountService(HorizonProvider(httpx.MockTransport(handler)))
        first = await service.activity(ADDRESS, network, kind)
        second = await service.activity(ADDRESS, network, kind, first.next_cursor)
        last = await service.activity(ADDRESS, network, kind, second.next_cursor)
        assert len(first.records) == len(second.records) == 20
        assert not last.records and last.next_cursor is None
        assert not {r.identifier for r in first.records} & {r.identifier for r in second.records}
        for request in requests:
            assert request.url.path == f"/accounts/{ADDRESS}/{kind.value}"
            assert request.url.host == (
                "horizon.stellar.org"
                if network == Network.MAINNET
                else "horizon-testnet.stellar.org"
            )
            assert request.url.params["order"] == "desc"
            assert request.url.params["include_failed"] == "true"
        if kind == ActivityKind.TRANSACTIONS:
            assert first.records[0].values[2] == "Failed"
        if kind == ActivityKind.PAYMENTS:
            assert first.records[0].values[6:8] == ("0.0000001", f"USD:{ISSUER}")

    asyncio.run(scenario())


def test_nonadvancing_page_and_malformed_response():
    async def scenario():
        for data in [payload([10], ActivityKind.OPERATIONS), {"_embedded": {"records": None}}]:
            service = AccountService(
                HorizonProvider(
                    httpx.MockTransport(lambda r, data=data: httpx.Response(200, json=data))
                )
            )
            with pytest.raises(AccountError, match="invalid activity"):
                await service.activity(ADDRESS, Network.MAINNET, ActivityKind.OPERATIONS, "10")

    asyncio.run(scenario())


def test_ui_retry_keeps_rows_and_cursor():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 2:
            return httpx.Response(503)
        tokens = range(100, 80, -1) if len(requests) == 1 else [80]
        return httpx.Response(200, json=payload(tokens, ActivityKind.PAYMENTS))

    async def scenario():
        view = ActivityView(
            AccountService(HorizonProvider(httpx.MockTransport(handler))), ActivityKind.PAYMENTS
        )
        view.reset((ADDRESS, Network.TESTNET))
        view.start()
        task = view.task
        view.start()
        assert view.task is task  # Double-click cannot schedule a second page.
        await task
        assert view.table.rowCount() == 20 and view.cursor == "81"
        view.start()
        await view.task
        assert view.table.rowCount() == 20 and view.cursor == "81"
        assert view.more.text() == "Retry"
        view.start()
        await view.task
        assert view.table.rowCount() == 21 and not view.more.isEnabled()
        assert requests[1].url == requests[2].url
        view.close()

    asyncio.run(scenario())
    app.processEvents()


def test_ui_reset_rejects_late_page():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])

    async def scenario():
        from polarstellar.stellar.activity import parse_page

        future = asyncio.get_running_loop().create_future()

        class Delayed:
            async def activity(self, address, network, kind, cursor):
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    await future
                return parse_page(payload([10], kind), address, network, kind, "fixture", cursor)

        view = ActivityView(Delayed(), ActivityKind.TRANSACTIONS)
        view.reset((ADDRESS, Network.MAINNET))
        view.start()
        task = view.task
        await asyncio.sleep(0)
        view.reset((ADDRESS, Network.TESTNET))
        future.set_result(None)
        await task
        assert view.table.rowCount() == 0 and view.cursor is None
        assert view.more.isEnabled()
        view.close()

    asyncio.run(scenario())
    app.processEvents()


def test_version_and_changelog_agree():
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text())
    assert metadata["project"]["version"] == __version__
    assert f"## [{__version__}]" in (root / "CHANGELOG.md").read_text()


def test_create_account_and_merge_payment_fields():
    from polarstellar.stellar.activity import parse_page

    kind = ActivityKind.PAYMENTS
    create = record(20, kind)
    for key in ("from", "to", "amount", "asset_type", "asset_code", "asset_issuer"):
        create.pop(key)
    create.update(
        type="create_account", funder=ADDRESS, account=ISSUER, starting_balance="2.0000000"
    )
    merge = {
        **create,
        "id": "19",
        "paging_token": "19",
        "type": "account_merge",
        "account": ADDRESS,
        "into": ISSUER,
    }
    merge.pop("starting_balance")
    page = parse_page(
        {"_embedded": {"records": [create, merge]}}, ADDRESS, Network.TESTNET, kind, "fixture", None
    )
    assert page.records[0].values[4:8] == (ADDRESS, ISSUER, "2.0000000", "XLM")
    assert page.records[1].values[5:7] == (ISSUER, "—")
