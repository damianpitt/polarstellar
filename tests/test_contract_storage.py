"""Check explicit storage reads, identity/ledger safeguards and frozen local evidence."""

import asyncio
import base64
import json
from copy import deepcopy

import httpx
import pytest
from PySide6.QtWidgets import QApplication
from stellar_sdk import scval, xdr
from test_contracts import CONTRACT, OTHER, instance_result

from polarstellar.app.window import MainWindow
from polarstellar.stellar.contract_storage import (
    MAX_ENTRY_BYTES,
    bounded_xdr,
    entered_key,
    readable,
    storage_key,
)
from polarstellar.stellar.contracts import ENDPOINTS, PASSPHRASES, ContractProvider
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService
from polarstellar.storage.export import serialize
from polarstellar.storage.investigations import InvestigationStore
from polarstellar.ui.contract_view import ContractView

KEY = scval.to_symbol("COUNTER").to_xdr()


def storage_response(key_xdr=KEY, durability="Persistent", contract=CONTRACT):
    """Encode a real contract-data entry with a large exact integer and matching LedgerKey."""
    key = storage_key(contract, key_xdr, durability)
    body = xdr.ContractDataEntry(
        xdr.ExtensionPoint(0),
        key.contract_data.contract,
        key.contract_data.key,
        key.contract_data.durability,
        scval.to_int128(2**100),
    )
    entry = xdr.LedgerEntryData(xdr.LedgerEntryType.CONTRACT_DATA, contract_data=body)
    return {
        "latestLedger": 2100,
        "entries": [
            {
                "key": key.to_xdr(),
                "xdr": entry.to_xdr(),
                "lastModifiedLedgerSeq": 2050,
                "liveUntilLedgerSeq": 5000,
            }
        ],
    }


def provider(response=None, network=Network.TESTNET):
    """Record exact RPC requests; the known instance read precedes optional storage reads."""
    calls = []

    def handler(request):
        """Return deterministic network/instance/storage responses without external access."""
        body = json.loads(request.content)
        calls.append((str(request.url), body))
        if body["method"] == "getNetwork":
            data = {"passphrase": PASSPHRASES[network]}
        elif body["method"] == "getHealth":
            data = {"oldestLedger": 100, "latestLedger": 2000}
        elif body["method"] == "getLedgerEntries":
            from polarstellar.stellar.contracts import instance_key

            data = (
                instance_result()
                if body["params"]["keys"] == [instance_key(CONTRACT).to_xdr()]
                else response
                if response is not None
                else storage_response()
            )
        else:
            raise AssertionError("No other RPC methods are allowed")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": data})

    return ContractProvider(httpx.MockTransport(handler)), calls


@pytest.fixture
def app():
    """Keep the offscreen Qt application alive throughout each UI scenario."""
    return QApplication.instance() or QApplication([])


def test_symbol_and_compound_xdr_keys_preserve_exact_identity():
    """A symbol and vector key build distinct identities; durability is part of each key."""
    assert entered_key("COUNTER", "Symbol") == KEY
    vector = scval.to_vec([scval.to_symbol("Balance"), scval.to_int128(2**100)]).to_xdr()
    assert entered_key(" " + vector + " ", "ScVal XDR") == vector
    persistent = storage_key(CONTRACT, vector, "Persistent")
    temporary = storage_key(CONTRACT, vector, "Temporary")
    assert persistent.contract_data.key.to_xdr() == vector
    assert persistent.to_xdr() != temporary.to_xdr()


@pytest.mark.parametrize("symbol", ["", " COUNTER", "a b", "a" * 33, "é", "<b>"])
def test_invalid_symbols_rejected_before_network(symbol):
    """Symbol input never trims or silently converts invalid identifiers."""
    with pytest.raises(AccountError):
        entered_key(symbol, "Symbol")


@pytest.mark.parametrize(
    "encoded",
    [
        "bad",
        KEY + "====",
        KEY + "\n",
        "A" * 5500,
        base64.b64encode(scval.to_symbol("x").to_xdr_bytes() + bytes(4)).decode(),
    ],
)
def test_invalid_or_trailing_key_xdr_rejected(encoded):
    """Only canonical, fully consumed bounded ScVal XDR can become a storage key."""
    # Outer whitespace is deliberately accepted for pasting; embedded whitespace is not.
    if encoded == KEY + "\n":
        encoded = KEY[:4] + "\n" + KEY[4:]
    with pytest.raises(AccountError):
        entered_key(encoded, "ScVal XDR")


@pytest.mark.parametrize("nested", [False, True])
def test_reserved_keys_and_nesting_limits(nested):
    """Internal instance keys cannot be smuggled inside a vector or queried as user data."""
    value = xdr.SCVal(xdr.SCValType.SCV_LEDGER_KEY_CONTRACT_INSTANCE)
    if nested:
        value = scval.to_vec([value])
    with pytest.raises(AccountError):
        entered_key(value.to_xdr(), "ScVal XDR")
    value = scval.to_uint32(1)
    for _ in range(18):
        value = scval.to_vec([value])
    with pytest.raises(AccountError):
        entered_key(value.to_xdr(), "ScVal XDR")


def test_duplicate_key_map_and_oversized_entry_are_refused():
    """Duplicate map keys and oversized entry bytes cannot become accepted evidence."""
    pair = xdr.SCMapEntry(scval.to_symbol("a"), scval.to_uint32(1))
    value = xdr.SCVal(xdr.SCValType.SCV_MAP, map=xdr.SCMap([pair, pair]))
    with pytest.raises(AccountError):
        entered_key(value.to_xdr(), "ScVal XDR")
    with pytest.raises(ValueError):
        bounded_xdr(
            base64.b64encode(bytes(MAX_ENTRY_BYTES + 1)).decode(),
            xdr.LedgerEntryData,
            MAX_ENTRY_BYTES,
        )


def test_key_node_budget_and_unsupported_input_mode():
    """Even a small encoding cannot force unbounded key traversal or an implicit input conversion."""
    many = scval.to_vec([scval.to_void()] * 512).to_xdr()
    with pytest.raises(AccountError):
        entered_key(many, "ScVal XDR")
    with pytest.raises(AccountError):
        entered_key("COUNTER", "JSON")


@pytest.mark.parametrize("durability", ["Persistent", "Temporary"])
def test_exact_one_key_network_verified_and_large_integer_preserved(durability):
    """Read exactly one LedgerKey and retain source, precise value, raw entry and ledger metadata."""
    response = storage_response(durability=durability)
    rpc, calls = provider(response)
    result = asyncio.run(rpc.storage(CONTRACT, Network.TESTNET, KEY, durability))
    assert [body["method"] for _, body in calls] == ["getNetwork", "getLedgerEntries"]
    assert all(url == ENDPOINTS[Network.TESTNET] for url, _ in calls)
    assert calls[1][1]["params"] == {"keys": [storage_key(CONTRACT, KEY, durability).to_xdr()]}
    assert result["entry"]["value"]["value"] == str(2**100)
    assert result["entry"]["raw_rpc_entry"] == response["entries"][0]
    assert result["latest_ledger"] == 2100
    assert result["entry"]["last_modified_ledger"] == 2050
    assert result["status"] == "Returned; live at reported ledger"
    assert result["durability"] == durability and result["requested_key_xdr"] == KEY
    assert "no inferred units" in result["coverage"]
    assert str(2**100) in readable(result)


def test_mainnet_routing_and_wrong_network_refusal():
    """Mainnet is explicit; a mismatched passphrase stops before querying a key."""
    rpc, calls = provider(network=Network.MAINNET)
    result = asyncio.run(rpc.storage(CONTRACT, Network.MAINNET, KEY, "Persistent"))
    assert result["network"] == Network.MAINNET.value
    assert all(url == ENDPOINTS[Network.MAINNET] for url, _ in calls)
    rpc, calls = provider()
    with pytest.raises(AccountError, match="different network"):
        asyncio.run(rpc.storage(CONTRACT, Network.MAINNET, KEY, "Persistent"))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "contract,key,durability",
    [("bad", KEY, "Persistent"), (CONTRACT, "bad", "Persistent"), (CONTRACT, KEY, "Instance")],
)
def test_invalid_request_never_makes_rpc_call(contract, key, durability):
    """Provider validation is independent of the UI and happens before network verification."""
    rpc, calls = provider()
    with pytest.raises(AccountError):
        asyncio.run(rpc.storage(contract, Network.TESTNET, key, durability))
    assert calls == []


@pytest.mark.parametrize(
    "case",
    [
        "outer_key",
        "inner_contract",
        "inner_key",
        "inner_durability",
        "duplicate",
        "trailing",
        "wrong_type",
        "future_modified",
        "bool_latest",
        "bad_ttl",
        "missing_entries",
    ],
)
def test_mismatched_or_malformed_rpc_evidence_is_refused(case):
    """No outer key, inner identity, canonical payload or ledger mismatch is accepted."""
    response = storage_response()
    item = response["entries"][0]
    if case == "outer_key":
        item["key"] = storage_key(OTHER, KEY, "Persistent").to_xdr()
    elif case.startswith("inner_"):
        wrong = storage_response(
            contract=OTHER if case == "inner_contract" else CONTRACT,
            key_xdr=scval.to_symbol("OTHER").to_xdr() if case == "inner_key" else KEY,
            durability="Temporary" if case == "inner_durability" else "Persistent",
        )
        item["xdr"] = wrong["entries"][0]["xdr"]
    elif case == "duplicate":
        response["entries"].append(deepcopy(item))
    elif case == "trailing":
        item["xdr"] = base64.b64encode(base64.b64decode(item["xdr"]) + bytes(4)).decode()
    elif case == "wrong_type":
        item["xdr"] = xdr.LedgerEntryData(
            xdr.LedgerEntryType.CONTRACT_CODE,
            contract_code=xdr.ContractCodeEntry(
                xdr.ContractCodeEntryExt(0), xdr.Hash(bytes(32)), b"x"
            ),
        ).to_xdr()
    elif case == "future_modified":
        item["lastModifiedLedgerSeq"] = 2101
    elif case == "bool_latest":
        response["latestLedger"] = True
    elif case == "bad_ttl":
        item["liveUntilLedgerSeq"] = -1
    else:
        response.pop("entries")
    rpc, _ = provider(response)
    with pytest.raises(AccountError, match="storage evidence"):
        asyncio.run(rpc.storage(CONTRACT, Network.TESTNET, KEY, "Persistent"))


@pytest.mark.parametrize(
    "ttl,status",
    [
        (None, "Returned; TTL unknown"),
        (0, "Returned; TTL expired"),
        (2099, "Returned; TTL expired"),
        (2100, "Returned; live at reported ledger"),
    ],
)
def test_ttl_uncertainty_never_hides_raw_entry(ttl, status):
    """Absent, zero and expired TTL remain explicit instead of asserting a live value."""
    response = storage_response()
    if ttl is None:
        response["entries"][0].pop("liveUntilLedgerSeq")
    else:
        response["entries"][0]["liveUntilLedgerSeq"] = ttl
    rpc, _ = provider(response)
    result = asyncio.run(rpc.storage(CONTRACT, Network.TESTNET, KEY, "Persistent"))
    assert result["status"] == status and result["entry"]["raw_entry_xdr"]


def test_no_entry_returned_is_an_exportable_uncertain_observation(app):
    """A missing entry keeps request/ledger evidence and zero rows in both export formats."""

    async def scenario():
        """Load the inspected contract, then explicitly read an absent key."""
        rpc, calls = provider({"latestLedger": 2100, "entries": []})
        view = ContractView(rpc, lambda: Network.TESTNET)
        view.address.setText(CONTRACT)
        view.start()
        await view.task
        assert len(calls) == 3 and view.storage_result is None
        view.storage_key.setText("COUNTER")
        view.start_storage()
        await view.task
        exported = view.storage_document()
        assert exported["records"] == []
        assert exported["metadata"]["status"] == "No entry returned"
        assert "does not prove nonexistence" in exported["metadata"]["coverage"]
        assert "No entry returned" in serialize(exported, "csv")
        assert json.loads(serialize(exported, "json"))["metadata"]["requested_key_xdr"] == KEY
        view.close()

    asyncio.run(scenario())


def test_ui_exports_frozen_save_and_context_reset(app, tmp_path):
    """Save storage once as separate evidence, preserving values after form edits and reset."""

    async def scenario():
        """Exercise the real storage tab, resource save and generic offline artifact renderer."""
        rpc, _ = provider()
        store = InvestigationStore(tmp_path / "cases.db")
        window = MainWindow(AccountService(object()), store, contract_provider=rpc)
        window.network.setCurrentText("Testnet")
        view = window.contracts
        view.address.setText(CONTRACT)
        view.start()
        await view.task
        view.storage_key.setText("COUNTER")
        view.start_storage()
        await view.task
        assert "Last accepted lookup" in view.storage_details.toPlainText()
        view.detail_tabs.setCurrentIndex(3)
        assert view.table.isHidden() and view.raw.isHidden()
        view.detail_tabs.setCurrentIndex(0)
        assert not view.table.isHidden() and not view.raw.isHidden()
        exported = view.storage_document()
        assert exported["kind"] == "contract_storage"
        assert str(2**100) in serialize(exported, "csv")
        view.storage_key.setText("DIFFERENT")
        view.storage_durability.setCurrentText("Temporary")
        assert view.storage_document()["metadata"]["requested_key_xdr"] == KEY
        assert view.storage_document()["metadata"]["durability"] == "Persistent"
        assert view.export_document()["metadata"]["storage_lookup"]["latest_ledger"] == 2100
        window.investigations.name.setText("Explicit storage")
        window.investigations.create()
        window.pages.setCurrentIndex(6)
        window.save_resource()
        saved = store.list()[0]["entries"][0]["evidence"]
        assert [item["kind"] for item in saved] == ["contract", "contract_storage"]
        assert saved[1]["records"] == exported["records"]
        window.network.setCurrentText("Mainnet")
        assert view.storage_result is None and not view.storage_export.isEnabled()
        assert not view.storage_load.isEnabled()
        assert saved[1]["metadata"]["network"] == "Testnet"
        window.close()

    asyncio.run(scenario())


def test_optional_failure_and_foreign_results_preserve_previous_storage(app):
    """A failed read, wrong response identity or invalid form never relabels the accepted result."""

    async def scenario():
        """Start with accepted evidence, then substitute failure and wrong-key providers."""
        rpc, calls = provider()
        view = ContractView(rpc, lambda: Network.TESTNET)
        view.address.setText(CONTRACT)
        view.start()
        await view.task
        view.storage_key.setText("COUNTER")
        view.start_storage()
        await view.task
        before = deepcopy(view.storage_result)
        count = len(calls)
        view.storage_key.setText("bad symbol")
        view.start_storage()
        assert len(calls) == count and view.storage_result == before
        view.storage_key.setText("COUNTER")

        async def fail(*args):
            """Fail without touching previously accepted UI state."""
            raise AccountError("Fixture failure")

        rpc.storage = fail
        view.start_storage()
        await view.task
        assert view.storage_result == before and view.storage_export.isEnabled()
        assert "Fixture failure" in view.status.text()

        async def foreign(*args):
            """Return a plausible but differently bound response for the view to refuse."""
            return {**before, "network": "Mainnet"}

        rpc.storage = foreign
        view.start_storage()
        await view.task
        assert "another key" in view.status.text() and view.storage_result == before
        assert view.spec_load.isEnabled() and view.storage_load.isEnabled()
        view.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("reset", [False, True])
def test_cancellation_resistant_response_cannot_replace_or_repopulate(app, reset):
    """Generation checks reject a late result even when the underlying provider ignores cancellation."""

    async def scenario():
        """Pause a read, cancel/reset its context and then release its original response."""
        rpc, _ = provider()
        view = ContractView(rpc, lambda: Network.TESTNET)
        view.address.setText(CONTRACT)
        view.start()
        await view.task
        view.storage_key.setText("COUNTER")
        view.start_storage()
        await view.task
        before = deepcopy(view.storage_result)
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(*args):
            """Simulate a transport which completes despite task cancellation."""
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return {**before, "latest_ledger": 2200}

        rpc.storage = delayed
        view.start_storage()
        task = view.task
        await entered.wait()
        if reset:
            view.reset()
        else:
            view.cancel_request()
        release.set()
        await task
        assert view.storage_result == (None if reset else before)
        assert view.storage_export.isEnabled() is (not reset)
        view.close()

    asyncio.run(scenario())
