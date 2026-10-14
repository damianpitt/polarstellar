"""Exercise result/envelope alignment and bounded live effects without inventing financial evidence."""

import asyncio
import json
from dataclasses import replace
from decimal import Decimal, localcontext

import httpx
import pytest
from PySide6.QtWidgets import QApplication
from stellar_sdk import (
    Account,
    FeeBumpTransaction,
    FeeBumpTransactionEnvelope,
    TransactionBuilder,
    xdr,
)
from stellar_sdk import Network as SDKNetwork
from test_accounts import ADDRESS, ISSUER

from polarstellar.stellar.effects import merge_comparison, parse_effects
from polarstellar.stellar.horizon import ENDPOINTS, HorizonProvider
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService
from polarstellar.stellar.transaction import parse_transaction
from polarstellar.storage import codec
from polarstellar.ui.transaction_view import TransactionDialog

OPERATION = "4294971393"


def evidence(success=True, bumped=False, balance=9000000000000000001):
    """Create real SDK envelope/result XDR and matching Horizon metadata/ordered merge operation."""
    inner = (
        TransactionBuilder(Account(ADDRESS, 1), SDKNetwork.TESTNET_NETWORK_PASSPHRASE, base_fee=100)
        .append_account_merge_op(ISSUER)
        .set_timeout(0)
        .build()
    )
    envelope = (
        FeeBumpTransactionEnvelope(
            FeeBumpTransaction(ISSUER, 200, inner), SDKNetwork.TESTNET_NETWORK_PASSPHRASE
        )
        if bumped
        else inner
    )
    operation_result = xdr.OperationResult(
        xdr.OperationResultCode.opINNER,
        xdr.OperationResultTr(
            xdr.OperationType.ACCOUNT_MERGE,
            account_merge_result=xdr.AccountMergeResult(
                xdr.AccountMergeResultCode.ACCOUNT_MERGE_SUCCESS, xdr.Int64(balance)
            ),
        ),
    )
    code = xdr.TransactionResultCode.txSUCCESS if success else xdr.TransactionResultCode.txFAILED
    if bumped:
        result = xdr.TransactionResult(
            xdr.Int64(200),
            xdr.TransactionResultResult(
                xdr.TransactionResultCode.txFEE_BUMP_INNER_SUCCESS
                if success
                else xdr.TransactionResultCode.txFEE_BUMP_INNER_FAILED,
                inner_result_pair=xdr.InnerTransactionResultPair(
                    xdr.Hash(inner.hash()),
                    xdr.InnerTransactionResult(
                        xdr.Int64(100),
                        xdr.InnerTransactionResultResult(code, results=[operation_result]),
                        xdr.InnerTransactionResultExt(0),
                    ),
                ),
            ),
            xdr.TransactionResultExt(0),
        )
    else:
        result = xdr.TransactionResult(
            xdr.Int64(100),
            xdr.TransactionResultResult(code, results=[operation_result]),
            xdr.TransactionResultExt(0),
        )
    data = {
        "hash": envelope.hash_hex(),
        "successful": success,
        "operation_count": 1,
        "ledger": 1,
        "fee_charged": "200" if bumped else "100",
        "source_account": ADDRESS,
        "fee_account": ISSUER if bumped else ADDRESS,
        "created_at": "2026-10-14T00:00:00Z",
        "memo_type": "none",
        "envelope_xdr": envelope.to_xdr(),
        "result_xdr": result.to_xdr(),
    }
    row = {
        "id": OPERATION,
        "paging_token": OPERATION,
        "transaction_hash": data["hash"],
        "transaction_successful": success,
        "type": "account_merge",
        "source_account": ADDRESS,
        "account": ADDRESS,
        "into": ISSUER,
    }
    return data, [row]


def transaction(success=True, bumped=False):
    """Normalize real test evidence with the production parser and fixed Testnet source."""
    data, rows = evidence(success, bumped)
    return parse_transaction(data, data["hash"], Network.TESTNET, rows, ENDPOINTS[Network.TESTNET])


def effect(index, kind="account_credited", amount="900000000000.0000001"):
    """Return a padded effect identity and canonical compound paging token bound to the operation."""
    return {
        "id": f"{int(OPERATION):019d}-{index:010d}",
        "paging_token": f"{OPERATION}-{index}",
        "account": ADDRESS if kind == "account_removed" else ISSUER,
        "type": kind,
        "asset_type": "native",
        "amount": amount,
        "_links": {"operation": {"href": f"{ENDPOINTS[Network.TESTNET]}/operations/{OPERATION}"}},
    }


def page(rows, cursor=None):
    """Parse an ascending fixture page without trusting returned navigation URLs."""
    return parse_effects(
        {"_embedded": {"records": rows}},
        OPERATION,
        Network.TESTNET,
        ENDPOINTS[Network.TESTNET],
        cursor,
    )


@pytest.fixture
def app():
    """Keep headless Qt alive for result rendering and real asynchronous effects controls."""
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("bumped", [False, True], ids=["standard", "fee-bump"])
@pytest.mark.parametrize("success", [True, False], ids=["applied", "rolled-back"])
def test_result_alignment_exact_merge_and_failed_transaction(bumped, success):
    """Fee-bump and normal results bind to the network hash; rolled-back success codes never expose an applied amount."""
    with localcontext() as context:
        context.prec = 3
        tx = transaction(success, bumped)
    assert not tx.result_warning
    result = tx.operations[0].result
    assert result.code == "ACCOUNT_MERGE_SUCCESS" and result.applied == success
    assert result.merge_amount == (Decimal("900000000000.0000001") if success else None)
    assert result.merge_stroops == ("9000000000000000001" if success else "")
    assert codec.loads(codec.dumps(tx)) == tx


@pytest.mark.parametrize(
    "change",
    [
        "bad-result",
        "trailing-result",
        "hash",
        "fee",
        "source",
        "destination",
        "count",
        "status",
        "network",
        "missing-envelope",
    ],
    ids=str,
)
def test_unverified_results_preserve_basic_transaction(change):
    """Any mismatch refuses optional mapping without losing a valid Horizon header/instruction snapshot."""
    import base64

    data, rows = evidence()
    network = Network.TESTNET
    if change == "bad-result":
        data["result_xdr"] = "bad"
    elif change == "trailing-result":
        data["result_xdr"] = base64.b64encode(base64.b64decode(data["result_xdr"]) + b"x").decode()
    elif change == "hash":
        data["hash"] = rows[0]["transaction_hash"] = "ab" * 32
    elif change == "fee":
        data["fee_charged"] = "101"
    elif change == "source":
        rows[0]["source_account"] = ISSUER
    elif change == "destination":
        rows[0]["into"] = ADDRESS
    elif change == "count":
        rows = []
    elif change == "status":
        data["successful"] = rows[0]["transaction_successful"] = False
    elif change == "network":
        network = Network.MAINNET
    else:
        del data["envelope_xdr"]
    tx = parse_transaction(data, data["hash"], network, rows, ENDPOINTS[network])
    assert tx.result_warning and all(op.result is None for op in tx.operations)
    assert tx.hash == data["hash"] and tx.fee_stroops == int(data["fee_charged"])


def test_missing_results_and_incompatible_cache_are_safe():
    """Basic data remains inspectable without result XDR; old cache fields are never guessed."""
    data, rows = evidence()
    del data["result_xdr"]
    tx = parse_transaction(data, data["hash"], Network.TESTNET, rows, "fixture")
    assert tx.result_code == "Not provided" and tx.operations[0].result is None
    old = json.loads(codec.dumps(tx))
    del old["fields"]["result_code"]
    with pytest.raises(ValueError, match="version"):
        codec.decode(old)


def test_effect_identity_amount_and_merge_comparison():
    """Complete matching credit/removal corroborates a merge; contradiction/partial data never becomes a guessed balance."""
    op = transaction().operations[0]
    complete = page([effect(1), effect(2, "account_removed")])
    assert complete.records[0].amount == Decimal("900000000000.0000001")
    assert "agrees" in merge_comparison(op, [complete])
    wrong = page([effect(1, amount="1"), effect(2, "account_removed")])
    assert "CONTRADICTION" in merge_comparison(op, [wrong])
    partial = replace(complete, next_cursor=complete.records[-1].cursor)
    assert "incomplete" in merge_comparison(op, [partial])
    assert "unknown" in merge_comparison(replace(op, result=None), [complete])
    assert "ambiguous" in merge_comparison(op, [page([effect(1)])])


@pytest.mark.parametrize(
    "change",
    ["operation-link", "foreign-host", "cursor-operation", "reverse", "id", "amount", "issuer"],
    ids=str,
)
def test_bad_effect_page_is_refused(change):
    """Foreign links, malformed identities, nonadvancing cursors and invalid money cannot overwrite accepted evidence."""
    rows = [effect(1), effect(2)]
    if change == "operation-link":
        rows[0]["_links"]["operation"]["href"] += "1"
    elif change == "foreign-host":
        rows[0]["_links"]["operation"]["href"] = "https://evil.invalid/operations/" + OPERATION
    elif change == "cursor-operation":
        rows[0]["paging_token"] = "1-1"
    elif change == "reverse":
        rows.reverse()
    elif change == "id":
        rows[0]["id"] = "1-1"
    elif change == "amount":
        rows[0]["amount"] = "0.00000001"
    else:
        rows[0].update(asset_type="credit_alphanum4", asset_code="USD", asset_issuer="bad")
    with pytest.raises(ValueError):
        page(rows)


def test_unknown_effects_remain_raw_and_no_amount_is_inferred():
    """Readable credit/debit scope does not guess financial semantics for future effect kinds."""
    row = effect(1, "future_effect")
    parsed = page([row]).records[0]
    assert parsed.amount is None and parsed.asset == "" and "future_effect" in parsed.raw


def test_effects_provider_fixed_routing_pagination_and_no_failed_fetch():
    """Live effects use a fixed network URL and compound cursor; failed/foreign operations are rejected before I/O."""
    requests = []

    def handler(request):
        """Serve exactly one full page followed by a terminating empty page."""
        requests.append(request)
        rows = (
            [effect(index) for index in range(1, 21)] if "cursor" not in request.url.params else []
        )
        return httpx.Response(200, json={"_embedded": {"records": rows}})

    async def scenario():
        """Use the production provider/service boundary to validate continuation binding."""
        service = AccountService(HorizonProvider(httpx.MockTransport(handler)))
        tx = transaction()
        first = await service.effects(tx, OPERATION)
        assert first.next_cursor == OPERATION + "-20"
        second = await service.effects(tx, OPERATION, first.next_cursor)
        assert not second.records and second.next_cursor is None
        assert requests[0].url.path == "/operations/" + OPERATION + "/effects"
        assert requests[0].url.host == "horizon-testnet.stellar.org"
        assert requests[1].url.params["cursor"] == first.next_cursor
        with pytest.raises(AccountError):
            await service.effects(transaction(False), OPERATION)
        with pytest.raises(AccountError):
            await service.effects(tx, "1")
        assert len(requests) == 2

    asyncio.run(scenario())


def test_ui_effects_are_explicit_failure_retry_and_export(app):
    """No effects fetch occurs on inspection; failed loads preserve pages/cursors and export retains result and live evidence."""

    async def scenario():
        """Drive production dialog controls with a successful merge and controlled live effects."""
        tx = transaction()
        calls = []

        class Service:
            """Return one transaction and deliberately fail one effects attempt."""

            async def transaction(self, *args):
                """Provide the fully aligned result fixture without accessing Horizon."""
                return tx

            async def effects(self, current, identifier, cursor):
                """Record the exact request and keep the retry on its original cursor."""
                calls.append((current.hash, identifier, cursor))
                if len(calls) == 1:
                    raise AccountError("Temporary failure")
                return page([effect(1), effect(2, "account_removed")])

        dialog = TransactionDialog(Service(), tx.hash, tx.network)
        dialog.start()
        await dialog.task
        assert not calls and "900000000000.0000001 XLM" in dialog.details.toPlainText()
        dialog.start_effects()
        await dialog.effect_task
        assert "retained" in dialog.effect_status.text() and not dialog.effect_pages
        dialog.start_effects()
        await dialog.effect_task
        assert calls[0] == calls[1]
        assert "agrees" in dialog.effects.toPlainText()
        snapshot = dialog.export_document()
        assert snapshot["records"][0]["result"]["merge_stroops"] == "9000000000000000001"
        assert len(snapshot["records"][0]["effect_pages"]) == 1
        assert not dialog.effect_load.isEnabled()
        dialog.reject()

    asyncio.run(scenario())


def test_fee_bump_inner_hash_and_negative_result_balance_are_refused():
    """Hash binding covers both fee-bump envelopes; negative balances are never interpreted as applied XLM."""
    data, rows = evidence(bumped=True)
    result = xdr.TransactionResult.from_xdr(data["result_xdr"])
    result.result.inner_result_pair.transaction_hash = xdr.Hash(bytes(32))
    data["result_xdr"] = result.to_xdr()
    tx = parse_transaction(data, data["hash"], Network.TESTNET, rows, "fixture")
    assert tx.result_warning and tx.operations[0].result is None
    data, rows = evidence(balance=-1)
    tx = parse_transaction(data, data["hash"], Network.TESTNET, rows, "fixture")
    assert tx.result_warning and tx.operations[0].result is None


def test_corrupt_cached_merge_result_amount_is_refused():
    """A typed cached decimal cannot claim a balance inconsistent with its exact ledger result."""
    payload = json.loads(codec.dumps(transaction()))
    payload["fields"]["operations"][0]["fields"]["result"]["fields"]["merge_amount"] = {
        "decimal": "1"
    }
    with pytest.raises(ValueError, match="stroops"):
        codec.decode(payload)


def test_live_effects_service_rejects_foreign_query_metadata():
    """Adapter results from another network cannot enter the selected operation evidence."""

    class Provider:
        """Return a structurally valid page with the wrong network identity."""

        async def get_effects(self, *args):
            """Deliberately corrupt metadata to test the service boundary."""
            return replace(page([effect(1)]), network=Network.MAINNET)

    with pytest.raises(AccountError, match="network"):
        asyncio.run(AccountService(Provider()).effects(transaction(), OPERATION))


def test_ui_pagination_cap_preserves_cursor_and_full_export(app):
    """Explicit clicks stop at five full pages while preserving an incomplete-comparison notice."""

    async def scenario():
        """Load five fixed ascending pages and refuse a sixth request without dropping evidence."""
        tx = transaction()
        requests = []

        class Service:
            """Serve full fixture pages under the same operation identity."""

            async def transaction(self, *args):
                """Return an aligned transaction without additional requests."""
                return tx

            async def effects(self, current, identifier, cursor):
                """Use the saved cursor to generate one ascending twenty-row page."""
                requests.append(cursor)
                start = int(cursor.split("-")[1]) + 1 if cursor else 1
                return page(
                    [effect(index, "future_effect") for index in range(start, start + 20)], cursor
                )

        dialog = TransactionDialog(Service(), tx.hash, tx.network)
        dialog.start()
        await dialog.task
        for _ in range(5):
            dialog.start_effects()
            await dialog.effect_task
        assert not dialog.effect_load.isEnabled()
        assert "five-page limit" in dialog.effects.toPlainText()
        dialog.start_effects()
        assert len(requests) == 5
        rows = dialog.export_document()["records"][0]["effect_pages"]
        assert sum(len(item["records"]) for item in rows) == 100
        assert rows[-1]["next_cursor"] == OPERATION + "-100"
        assert "incomplete" in dialog.export_document()["records"][0]["effect_comparison"]
        dialog.reject()

    asyncio.run(scenario())


def test_effect_cancel_and_reload_reject_late_provider_response(app):
    """Effects cancellation preserves existing results while ignoring cancellation-resistant late pages."""

    async def scenario():
        """Deliver a stale page only after cancelling its dedicated effects generation."""
        tx = transaction()
        future = asyncio.get_running_loop().create_future()

        class Service:
            """Hold effects even when the underlying transport cannot abort immediately."""

            async def transaction(self, *args):
                """Return the known transaction fixture."""
                return tx

            async def effects(self, *args):
                """Ignore cancellation so the dialog's stale-result protection is exercised."""
                try:
                    return await asyncio.shield(future)
                except asyncio.CancelledError:
                    return await future

        dialog = TransactionDialog(Service(), tx.hash, tx.network)
        dialog.start()
        await dialog.task
        dialog.start_effects()
        pending = dialog.effect_task
        await asyncio.sleep(0)
        dialog.cancel_effects()
        await asyncio.sleep(0)
        future.set_result(page([effect(1)]))
        await pending
        assert not dialog.effect_pages and dialog.transaction is tx
        assert dialog.export.isEnabled() and dialog.effect_load.isEnabled()
        dialog.reject()

    asyncio.run(scenario())


def test_unverified_results_are_not_reused_as_complete_cached_transactions(tmp_path):
    """Refused optional enrichment stays retryable instead of being cached as a complete day-long snapshot."""
    from polarstellar.storage.database import SnapshotCache
    from polarstellar.storage.provider import CachedProvider

    async def scenario():
        """Call an enabled cache twice and verify a later successful provider result replaces refused enrichment."""
        good = transaction()
        calls = []

        class Provider:
            """Return a refused optional result once, then a fully verified transaction."""

            async def get_transaction(self, *args):
                """Preserve identity while simulating improved availability on a live retry."""
                calls.append(args)
                return replace(good, result_warning="Refused") if len(calls) == 1 else good

        provider = CachedProvider(Provider(), SnapshotCache(tmp_path / "cache.db"))
        provider.set_enabled(True)
        assert (await provider.get_transaction(good.hash, good.network)).result_warning
        assert not (await provider.get_transaction(good.hash, good.network)).result_warning
        assert len(calls) == 2
        cached = await provider.get_transaction(good.hash, good.network)
        assert len(calls) == 2 and "Local cache" in cached.cache_status

    asyncio.run(scenario())


def test_enriched_inspector_snapshot_saves_loaded_effects_offline(app, tmp_path):
    """The real save action captures enrichment/provenance, rather than exporting only the base transaction."""
    from polarstellar.app.window import MainWindow
    from polarstellar.storage.export import serialize
    from polarstellar.storage.investigations import InvestigationStore

    tx = transaction()
    store = InvestigationStore(tmp_path / "cases.db")
    window = MainWindow(AccountService(object()), store)
    window.investigations.name.setText("Verified operation details")
    window.investigations.create()
    dialog = TransactionDialog(object(), tx.hash, tx.network, window)
    dialog.transaction = tx
    dialog.effect_pages[OPERATION] = [page([effect(1), effect(2, "account_removed")])]
    window.save_transaction(dialog)
    saved = store.list()[0]["entries"][0]["evidence"][0]
    assert len(saved["records"][0]["effect_pages"]) == 1
    assert "agrees" in saved["records"][0]["effect_comparison"]
    assert "record.result" in serialize(saved, "csv")
    assert saved["records"][0]["result"]["merge_amount"] == "900000000000.0000001"
    dialog.reject()
    window.close()


def test_wrong_operation_position_refuses_result_alignment():
    """Wrong ledger/index IDs cannot associate otherwise valid XDR with a different operation row."""
    data, rows = evidence()
    rows[0]["id"] = rows[0]["paging_token"] = str(int(OPERATION) + 1)
    tx = parse_transaction(data, data["hash"], Network.TESTNET, rows, "fixture")
    assert tx.result_warning and tx.operations[0].result is None


def test_operation_selection_during_fetch_keeps_original_owner(app):
    """Switching the selected operation cannot reassign an accepted page or trigger a second request."""

    async def scenario():
        """Use two display operations to isolate async ownership from result-decoding tests."""
        original = transaction()
        second_id = str(int(OPERATION) + 1)
        raw = json.loads(original.operations[0].raw)
        raw.update(id=second_id, paging_token=second_id)
        second = replace(
            original.operations[0], identifier=second_id, raw=json.dumps(raw), result=None
        )
        tx = replace(original, operations=(*original.operations, second), operation_count=2)
        future = asyncio.get_running_loop().create_future()
        calls = []

        class Service:
            """Hold the original selection's effects while allowing local selection to change."""

            async def transaction(self, *args):
                """Provide a two-operation display fixture; XDR alignment is tested separately."""
                return tx

            async def effects(self, current, identifier, cursor):
                """Freeze owner identity before the operation selector changes."""
                calls.append((identifier, cursor))
                return await future

        dialog = TransactionDialog(Service(), tx.hash, tx.network)
        dialog.start()
        await dialog.task
        dialog.start_effects()
        pending = dialog.effect_task
        await asyncio.sleep(0)
        dialog.effect_operation.setCurrentIndex(1)
        assert dialog.effect_task is pending and len(calls) == 1
        future.set_result(page([effect(1)]))
        await pending
        assert dialog.effect_operation.currentData() == second_id
        assert OPERATION in dialog.effect_pages and second_id not in dialog.effect_pages
        assert len(dialog.export_document()["records"][0]["effect_pages"]) == 1
        assert not dialog.export_document()["records"][1]["effect_pages"]
        dialog.reject()

    asyncio.run(scenario())
