"""Exercise read-only RPC contracts, exact XDR values, retention and stale event requests."""

import asyncio
import json

import httpx
import pytest
from PySide6.QtWidgets import QApplication
from stellar_sdk import StrKey, scval, xdr

from polarstellar.app.window import MainWindow
from polarstellar.stellar.contracts import (
    ENDPOINTS,
    PASSPHRASES,
    ContractProvider,
    decoded_value,
    instance_key,
    validate_contract,
)
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService
from polarstellar.storage.export import serialize
from polarstellar.storage.investigations import InvestigationStore
from polarstellar.ui.contract_view import ContractView

CONTRACT = StrKey.encode_contract(bytes([4]) * 32)
OTHER = StrKey.encode_contract(bytes([5]) * 32)


def instance_result(kind=xdr.ContractExecutableType.CONTRACT_EXECUTABLE_WASM):
    """Encode an actual SDK contract instance, rather than mocking the decoder's output."""
    key = instance_key(CONTRACT)
    executable = xdr.ContractExecutable(
        kind,
        wasm_hash=xdr.Hash(bytes([7]) * 32)
        if kind == xdr.ContractExecutableType.CONTRACT_EXECUTABLE_WASM
        else None,
    )
    instance = xdr.SCContractInstance(
        executable, xdr.SCMap([xdr.SCMapEntry(scval.to_symbol("counter"), scval.to_int128(2**100))])
    )
    body = xdr.ContractDataEntry(
        xdr.ExtensionPoint(0),
        key.contract_data.contract,
        key.contract_data.key,
        xdr.ContractDataDurability.PERSISTENT,
        xdr.SCVal(xdr.SCValType.SCV_CONTRACT_INSTANCE, instance=instance),
    )
    ledger = xdr.LedgerEntryData(xdr.LedgerEntryType.CONTRACT_DATA, contract_data=body)
    return {
        "latestLedger": 2000,
        "entries": [
            {
                "key": key.to_xdr(),
                "xdr": ledger.to_xdr(),
                "lastModifiedLedgerSeq": 1900,
                "liveUntilLedgerSeq": 5000,
            }
        ],
    }


def event(identifier="event-1", ledger=1500, **changes):
    """Supply exact raw topic/value XDR and an RPC event reference."""
    return {
        "id": identifier,
        "ledger": ledger,
        "ledgerClosedAt": "2026-09-30T00:00:00Z",
        "contractId": CONTRACT,
        "type": "contract",
        "inSuccessfulContractCall": True,
        "topic": [scval.to_symbol("transfer").to_xdr()],
        "value": scval.to_int128(2**100).to_xdr(),
        "txHash": "a" * 64,
        **changes,
    }


def provider(event_pages=None, instance=None, network=Network.TESTNET):
    """Build an RPC fixture with recorded requests and optional instance/event overrides."""
    calls = []
    pages = list(event_pages or [])

    def handler(request):
        """Return only the four read-only RPC methods used by this feature."""
        body = json.loads(request.content)
        calls.append((request.url, body))
        method = body["method"]
        if method == "getNetwork":
            result = {"passphrase": PASSPHRASES[network]}
        elif method == "getHealth":
            result = {"oldestLedger": 100, "latestLedger": 2000, "status": "healthy"}
        elif method == "getLedgerEntries":
            result = instance if instance is not None else instance_result()
        elif method == "getEvents":
            rows, cursor = pages.pop(0)
            result = {"events": rows, "cursor": cursor, "oldestLedger": 100, "latestLedger": 2001}
        else:
            raise AssertionError("Unexpected non-read-only RPC method")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    return ContractProvider(httpx.MockTransport(handler)), calls


def test_validated_ids_and_network_routing():
    """Reject bad checksums before I/O and verify network passphrase before accepting evidence."""
    rpc, calls = provider()
    with pytest.raises(AccountError):
        asyncio.run(
            rpc.inspect(CONTRACT[:-1] + ("A" if CONTRACT[-1] != "A" else "B"), Network.TESTNET)
        )
    assert calls == []
    assert validate_contract(" " + CONTRACT + " ") == CONTRACT
    with pytest.raises(AccountError, match="different network"):
        asyncio.run(rpc.inspect(CONTRACT, Network.MAINNET))
    assert len(calls) == 1 and str(calls[0][0]) == ENDPOINTS[Network.MAINNET]


@pytest.mark.parametrize(
    "kind",
    [
        xdr.ContractExecutableType.CONTRACT_EXECUTABLE_WASM,
        xdr.ContractExecutableType.CONTRACT_EXECUTABLE_STELLAR_ASSET,
    ],
)
def test_instance_details_and_exact_storage(kind):
    """Inspect WASM and native asset executables while preserving large integer precision."""
    rpc, calls = provider(instance=instance_result(kind))
    snapshot = asyncio.run(rpc.inspect(CONTRACT, Network.TESTNET))
    assert snapshot["instance_available"]
    assert snapshot["executable"] == kind.name
    assert snapshot["instance_storage"][0]["value"]["value"] == str(2**100)
    assert snapshot["live_until_ledger"] == 5000
    assert [body["method"] for _, body in calls] == ["getNetwork", "getHealth", "getLedgerEntries"]


def test_absent_instance_and_mismatched_key():
    """An absent live entry remains uncertain; a wrong ledger key is rejected entirely."""
    rpc, _ = provider(instance={"latestLedger": 2000, "entries": []})
    snapshot = asyncio.run(rpc.inspect(CONTRACT, Network.TESTNET))
    assert not snapshot["instance_available"] and "archived" in snapshot["warning"]
    wrong = instance_result()
    wrong["entries"][0]["key"] = instance_key(OTHER).to_xdr()
    rpc, _ = provider(instance=wrong)
    with pytest.raises(AccountError, match="invalid"):
        asyncio.run(rpc.inspect(CONTRACT, Network.TESTNET))


def test_event_params_bounds_precision_and_missing_status():
    """Cursor requests omit ledger bounds and locally drop newer events beyond the frozen end."""
    first = event()
    first.pop("inSuccessfulContractCall")
    rpc, calls = provider([([first], "cursor-1"), ([event("event-2", 2001)], "cursor-2")])

    async def scenario():
        """Fetch the initial bounded page and its cursor continuation."""
        initial = await rpc.events(CONTRACT, Network.TESTNET, 1001, 2000)
        later = await rpc.events(CONTRACT, Network.TESTNET, 1001, 2000, "cursor-1")
        assert initial["events"][0]["value"]["value"] == str(2**100)
        assert initial["events"][0]["successful_call"] is None
        assert later["done"] and later["events"] == []

    asyncio.run(scenario())
    queries = [body["params"] for _, body in calls if body["method"] == "getEvents"]
    assert queries[0]["startLedger"] == 1001 and queries[0]["endLedger"] == 2001
    assert "startLedger" not in queries[1] and "endLedger" not in queries[1]
    assert queries[1]["pagination"] == {"limit": 20, "cursor": "cursor-1"}


@pytest.mark.parametrize("row", [event(contractId=OTHER), event(ledger=99), event(type="system")])
def test_event_identity_and_range_rejection(row):
    """Never accept another contract, event type, or out-of-range older event."""
    rpc, _ = provider([([row], "cursor")])
    with pytest.raises(AccountError):
        asyncio.run(rpc.events(CONTRACT, Network.TESTNET, 100, 2000))


def test_unknown_xdr_preserved():
    """Future or malformed values remain raw evidence rather than guessed decoded amounts."""
    result = decoded_value("unsupported-xdr")
    assert result["xdr"] == "unsupported-xdr" and "note" in result


@pytest.fixture
def app():
    """Keep widgets alive throughout each headless test."""
    return QApplication.instance() or QApplication([])


def test_events_ui_dedup_export_save_and_reset(app, tmp_path):
    """Events deduplicate across pages and survive offline save/export with original provenance."""

    async def scenario():
        """Inspect through MainWindow's C-address entry point and explicitly page through events."""
        rpc, _ = provider(
            [
                ([event()], "cursor-1"),
                ([event(), event("event-2", 1501)], "cursor-2"),
                ([], "cursor-2"),
            ]
        )
        store = InvestigationStore(tmp_path / "saved.db")
        window = MainWindow(AccountService(object()), store, rpc)
        window.network.setCurrentText("Testnet")
        window.search.setText(CONTRACT)
        window.start_search()
        await window.contracts.task
        view = window.contracts
        assert window.pages.currentIndex() == 6 and view.begin == 1001
        for _ in range(3):
            view.start_events()
            await view.task
        assert len(view.events) == 2 and view.done and not view.more.isEnabled()
        view.table.selectRow(0)
        assert view.open_transaction.isEnabled() and str(2**100) in view.raw.toPlainText()
        payload = json.loads(serialize(view.export_document(), "json"))
        assert len(payload["metadata"]["pages"]) == 3
        assert payload["records"][0]["raw"]["value"] == event()["value"]
        assert "record.topics" in serialize(view.export_document(), "csv")
        window.investigations.name.setText("Contract evidence")
        window.investigations.create()
        window.save_resource()
        assert store.list()[0]["entries"][0]["kind"] == "contract"
        window.network.setCurrentText("Mainnet")
        assert view.snapshot is None and not view.export.isEnabled()
        window.close()

    asyncio.run(scenario())


def test_event_cursor_error_retains_rows(app):
    """A nonadvancing cursor must fail without replacing earlier events or provenance."""

    async def scenario():
        """Attempt a repeated page after one successful page."""
        rpc, _ = provider([([event()], "same"), ([event()], "same")])
        view = ContractView(rpc, lambda: Network.TESTNET)
        view.address.setText(CONTRACT)
        view.start()
        await view.task
        for _ in range(2):
            view.start_events()
            await view.task
        assert len(view.events) == 1 and len(view.pages) == 1
        assert "retained" in view.status.text() and view.more.isEnabled()
        view.reset()

    asyncio.run(scenario())


def test_stale_inspection_after_reset(app):
    """Even a provider that ignores cancellation cannot restore data after a network/context reset."""

    async def scenario():
        """Delay an otherwise valid snapshot until after resetting the Contracts view."""
        rpc, _ = provider()
        snapshot = await rpc.inspect(CONTRACT, Network.TESTNET)
        future = asyncio.get_running_loop().create_future()

        class Delayed:
            """Model an RPC transport finishing after its caller cancels."""

            async def inspect(self, *args):
                """Shield the result so generation protection, not cancellation, is exercised."""
                try:
                    return await asyncio.shield(future)
                except asyncio.CancelledError:
                    return await future

        view = ContractView(Delayed(), lambda: Network.TESTNET)
        view.address.setText(CONTRACT)
        view.start()
        task = view.task
        await asyncio.sleep(0)
        view.reset()
        future.set_result(snapshot)
        await task
        assert view.snapshot is None and not view.export.isEnabled()

    asyncio.run(scenario())


def test_rpc_errors_and_rate_limit():
    """Provider errors and rate limiting produce recoverable user-facing messages."""

    async def scenario():
        """Exercise both HTTP rate limits and a JSON-RPC error without exposing server text."""
        for response in (
            httpx.Response(429),
            httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"message": "sensitive internal details"},
                },
            ),
        ):
            rpc = ContractProvider(httpx.MockTransport(lambda request, reply=response: reply))
            with pytest.raises(AccountError) as failure:
                await rpc.inspect(CONTRACT, Network.TESTNET)
            assert "sensitive" not in str(failure.value)

    asyncio.run(scenario())


def test_event_start_outside_retention(app):
    """An explicit expired ledger range is not silently changed to a different query."""

    async def scenario():
        """Ask for a ledger older than provider retention and inspect the visible error."""
        rpc, calls = provider()
        view = ContractView(rpc, lambda: Network.TESTNET)
        view.address.setText(CONTRACT)
        view.start_ledger.setText("1")
        view.start()
        await view.task
        assert "retention" in view.status.text() and view.snapshot is None
        assert all(body["method"] != "getEvents" for _, body in calls)
        view.reset()

    asyncio.run(scenario())


def test_event_page_cap(app, monkeypatch):
    """A page cap stops loading while exports continue to mark query coverage incomplete."""

    async def scenario():
        """Exercise the same bound logic with a one-page test cap."""
        monkeypatch.setattr("polarstellar.ui.contract_view.MAX_EVENT_PAGES", 1)
        rpc, _ = provider([([event()], "cursor")])
        view = ContractView(rpc, lambda: Network.TESTNET)
        view.address.setText(CONTRACT)
        view.start()
        await view.task
        view.start_events()
        await view.task
        assert not view.more.isEnabled() and not view.done
        assert view.export_document()["metadata"]["query_complete"] is False
        view.reset()

    asyncio.run(scenario())
