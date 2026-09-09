import asyncio
import os

import httpx
import pytest
from PySide6.QtWidgets import QApplication
from test_accounts import ADDRESS, ISSUER

from polarstellar.stellar.horizon import HorizonProvider
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService
from polarstellar.stellar.transaction import decode_operation, parse_transaction, validate_hash
from polarstellar.ui.transaction_view import TransactionDialog

HASH = "ab" * 32


def metadata(success=True, count=2):
    return {
        "hash": HASH,
        "successful": success,
        "operation_count": count,
        "ledger": 123,
        "fee_charged": "200",
        "source_account": ADDRESS,
        "fee_account": ISSUER,
        "created_at": "2026-09-09T00:00:00Z",
        "memo_type": "text",
        "memo": "<script>test</script>",
    }


def operation(token, success=True, **fields):
    return dict(
        id=str(token),
        paging_token=str(token),
        transaction_hash=HASH,
        transaction_successful=success,
        type="payment",
        source_account=ISSUER,
        asset_type="native",
        amount="0.0000001",
        to=ADDRESS,
        **fields,
    )


@pytest.mark.parametrize("network", list(Network))
@pytest.mark.parametrize("success", [True, False])
def test_full_ordered_transaction(network, success):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/operations"):
            assert request.url.params["order"] == "asc"
            assert request.url.params["include_failed"] == "true"
            token = 1 if "cursor" not in request.url.params else 2
            return httpx.Response(200, json={"_embedded": {"records": [operation(token, success)]}})
        return httpx.Response(200, json=metadata(success))

    tx = asyncio.run(
        AccountService(HorizonProvider(httpx.MockTransport(handler))).transaction(HASH, network)
    )
    assert tx.fee_stroops == 200 and tx.fee_account == ISSUER
    assert tx.ledger == 123 and tx.network == network
    assert [op.identifier for op in tx.operations] == ["1", "2"]
    assert tx.operations[0].source == ISSUER
    assert "0.0000001 XLM" in tx.operations[0].explanation
    assert ("NOT APPLIED" in tx.operations[0].explanation) == (not success)
    assert not tx.warning
    assert requests[0].url.path == f"/transactions/{HASH}"
    assert requests[0].url.host == (
        "horizon.stellar.org" if network == Network.MAINNET else "horizon-testnet.stellar.org"
    )


@pytest.mark.parametrize("status", [404, 503])
def test_partial_operations_preserve_header(status):
    def handler(request):
        return (
            httpx.Response(status)
            if request.url.path.endswith("operations")
            else httpx.Response(200, json=metadata())
        )

    tx = asyncio.run(
        HorizonProvider(httpx.MockTransport(handler)).get_transaction(HASH, Network.MAINNET)
    )
    assert tx.warning and tx.ledger == 123 and not tx.operations


@pytest.mark.parametrize(
    "change", [{"transaction_hash": "f" * 64}, {"transaction_successful": False}]
)
def test_mismatched_operation_rejected(change):
    row = {**operation(1), **change}
    with pytest.raises(ValueError):
        parse_transaction(metadata(count=1), HASH, Network.MAINNET, [row], "fixture")


def test_duplicate_and_incomplete_operations():
    with pytest.raises(ValueError):
        parse_transaction(
            metadata(), HASH, Network.MAINNET, [operation(1), operation(1)], "fixture"
        )
    partial = parse_transaction(metadata(), HASH, Network.MAINNET, [operation(1)], "fixture")
    assert "incomplete" in partial.warning


def test_validation_and_not_found():
    assert validate_hash(" " + HASH.upper() + " ") == HASH
    with pytest.raises(AccountError):
        validate_hash("../bad")
    provider = HorizonProvider(httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(AccountError, match="Transaction not found"):
        asyncio.run(provider.get_transaction(HASH, Network.TESTNET))


@pytest.mark.parametrize(
    "fields,expected",
    [
        (
            {
                "type": "change_trust",
                "limit": "0",
                "asset_type": "credit_alphanum4",
                "asset_code": "USD",
                "asset_issuer": ISSUER,
            },
            "Remove trustline",
        ),
        (
            {
                "type": "change_trust",
                "limit": "12.0000000",
                "asset_type": "credit_alphanum4",
                "asset_code": "USD",
                "asset_issuer": ISSUER,
            },
            "limit 12.0000000",
        ),
        ({"type": "set_options", "signer_key": ISSUER, "signer_weight": 0}, "signer weight: 0"),
        ({"type": "manage_sell_offer", "amount": "0", "offer_id": "45"}, "Delete offer 45"),
        (
            {
                "type": "manage_sell_offer",
                "amount": "2",
                "offer_id": "45",
                "selling_asset_type": "native",
                "buying_asset_type": "credit_alphanum4",
                "buying_asset_code": "USD",
                "buying_asset_issuer": ISSUER,
                "price_r": {"n": 1, "d": 3},
            },
            "price 1/3",
        ),
        ({"type": "new_unknown_operation"}, "not yet supported"),
        ({"amount": "NaN"}, "Incomplete or unsupported"),
    ],
)
def test_decoder(fields, expected):
    decoded = decode_operation({**operation(1), **fields}, True)
    assert expected in decoded.explanation
    assert '"id": "1"' in decoded.raw


def test_dialog_render_and_late_close():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])

    async def scenario():
        future = asyncio.get_running_loop().create_future()

        class Delayed:
            async def transaction(self, *args):
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    await future
                return parse_transaction(
                    metadata(count=1), HASH, Network.MAINNET, [operation(1)], "fixture"
                )

        dialog = TransactionDialog(Delayed(), HASH, Network.MAINNET)
        dialog.start()
        task = dialog.task
        await asyncio.sleep(0)
        dialog.reject()
        future.set_result(None)
        await task
        assert "Loading" in dialog.details.toPlainText()
        dialog.start()
        await dialog.task
        assert "0.0000200 XLM" in dialog.details.toPlainText()
        assert "<script>test</script>" in dialog.details.toPlainText()
        assert "1. PAYMENT" in dialog.details.toPlainText()
        dialog.reject()

    asyncio.run(scenario())
    app.processEvents()
