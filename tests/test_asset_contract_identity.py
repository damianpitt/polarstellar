"""Check deterministic SAC identity, current native executable evidence and frozen asset provenance."""

import asyncio
import base64
import hashlib
import json
from copy import deepcopy
from dataclasses import replace

import httpx
import pytest
from PySide6.QtWidgets import QApplication
from stellar_sdk import Asset, StrKey, xdr
from test_assets import ISSUER, asset_row
from test_contracts import instance_result

from polarstellar.app.window import MainWindow
from polarstellar.stellar.asset_contracts import confirm, derive
from polarstellar.stellar.assets import parse_asset
from polarstellar.stellar.contracts import ENDPOINTS, PASSPHRASES, ContractProvider, instance_key
from polarstellar.stellar.horizon import HorizonProvider
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService
from polarstellar.storage.export import serialize
from polarstellar.storage.investigations import InvestigationStore
from polarstellar.ui.asset_view import AssetView


def detail(network=Network.TESTNET):
    """Retain exact existing Horizon statistics and issuer identity as independent metadata."""
    return parse_asset(asset_row(), "USD", ISSUER, network, "Horizon fixture")


def rpc_fixture(asset=None, kind="native", mutation=None):
    """Serve the derived key through the real RPC inspector, recording every read-only request."""
    asset = asset or detail()
    derived = derive(asset)
    response = instance_result(
        xdr.ContractExecutableType.CONTRACT_EXECUTABLE_STELLAR_ASSET
        if kind != "wasm"
        else xdr.ContractExecutableType.CONTRACT_EXECUTABLE_WASM
    )
    expected = instance_key(derived["contract"])
    row = response["entries"][0]
    raw = xdr.LedgerEntryData.from_xdr(row["xdr"])
    raw.contract_data.contract = expected.contract_data.contract
    row["key"], row["xdr"] = expected.to_xdr(), raw.to_xdr()
    if kind == "missing":
        response["entries"] = []
    if mutation:
        mutation(response)
    calls = []

    def handler(request):
        """Use fixed network passphrases and exact one-instance requests, without live HTTP."""
        body = json.loads(request.content)
        calls.append((str(request.url), body))
        if body["method"] == "getNetwork":
            result = {"passphrase": PASSPHRASES[asset.network]}
        elif body["method"] == "getHealth":
            result = {"oldestLedger": 100, "latestLedger": 2000}
        elif body["method"] == "getLedgerEntries":
            assert body["params"] == {"keys": [expected.to_xdr()]}
            result = response
        else:
            raise AssertionError("Unexpected contract method")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    return ContractProvider(httpx.MockTransport(handler)), calls


@pytest.fixture
def app():
    """Keep Qt widgets alive while exercising real async asset/contract actions."""
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("network", list(Network))
def test_derivation_retains_preimage_exact_asset_and_provenance(network):
    """Independently hash the retained XDR and keep source/time/statistics separate from deployment."""
    asset = detail(network)
    result = derive(asset)
    assert result["contract"] == Asset("USD", ISSUER).contract_id(PASSPHRASES[network])
    assert result["contract"] == StrKey.encode_contract(
        hashlib.sha256(base64.b64decode(result["contract_id_preimage_xdr"])).digest()
    )
    preimage = xdr.HashIDPreimage.from_xdr(result["contract_id_preimage_xdr"])
    assert preimage.contract_id.contract_id_preimage.from_asset.to_xdr() == result["asset_xdr"]
    assert result["asset_metadata"]["statistics"]["balances.authorized"] == "900000000.0000001"
    assert result["asset_metadata"]["source"] == "Horizon fixture"
    assert result["contract_instance"] is None and "not checked" in result["status"]


def test_network_issuer_case_and_native_identity_never_collapse():
    """Same symbols on other networks/issuers and issued XLM are distinct from native XLM."""
    base = detail()
    other = StrKey.encode_ed25519_public_key(bytes([9]) * 32)
    native = asyncio.run(AccountService(HorizonProvider()).asset("XLM", "", Network.TESTNET))
    assets = [
        base,
        replace(base, network=Network.MAINNET),
        replace(base, issuer=other),
        replace(base, code="usd"),
        replace(base, code="XLM"),
        native,
    ]
    assert len({derive(item)["contract"] for item in assets}) == len(assets)
    assert derive(native)["asset_metadata"]["source"] == "Stellar native asset definition"


@pytest.mark.parametrize(
    "changes",
    [
        {"issuer": "bad"},
        {"code": "bad/code"},
        {"asset_type": "native"},
        {"code": " USD"},
        {"network": "elsewhere"},
    ],
)
def test_invalid_identity_refused_before_rpc(changes):
    """A malformed or unnormalized source snapshot cannot send an identity check."""
    rpc, calls = rpc_fixture()
    with pytest.raises(AccountError):
        asyncio.run(rpc.asset_identity(replace(detail(), **changes)))
    assert calls == []


@pytest.mark.parametrize("network", list(Network))
def test_live_confirmation_requires_network_key_native_executable_and_ttl(network):
    """Read only the derived instance; preserve its RPC provenance alongside Horizon metadata."""
    asset = detail(network)
    rpc, calls = rpc_fixture(asset)
    result = asyncio.run(rpc.asset_identity(asset))
    assert result["status"].startswith("Confirmed")
    assert [body["method"] for _, body in calls] == ["getNetwork", "getHealth", "getLedgerEntries"]
    assert all(url == ENDPOINTS[network] for url, _ in calls)
    assert result["contract_instance"]["instance_latest_ledger"] == 2000
    assert result["asset_metadata"]["source"] == "Horizon fixture"


@pytest.mark.parametrize(
    "kind,ttl",
    [("missing", 5000), ("wasm", 5000), ("native", None), ("native", 0), ("native", 1999)],
)
def test_missing_wasm_and_uncertain_lifetime_do_not_confirm(kind, ttl):
    """A reserved ID alone never implies live deployment or native SAC behavior."""

    def mutate(data):
        """Keep fixture absence distinct from a present entry with unknown/expired TTL."""
        if data["entries"]:
            if ttl is None:
                data["entries"][0].pop("liveUntilLedgerSeq")
            else:
                data["entries"][0]["liveUntilLedgerSeq"] = ttl

    rpc, _ = rpc_fixture(kind=kind, mutation=mutate)
    result = asyncio.run(rpc.asset_identity(detail()))
    assert not result["status"].startswith("Confirmed") and result["contract_instance"] is not None


@pytest.mark.parametrize(
    "field",
    [
        "contract",
        "network",
        "source",
        "last_modified_ledger",
        "executable",
        "raw_instance_xdr",
        "instance_available",
    ],
)
def test_foreign_or_inconsistent_evidence_is_refused(field):
    """Labels cannot override mismatched raw identity, ledger bounds or availability types."""
    rpc, _ = rpc_fixture()
    result = asyncio.run(rpc.asset_identity(detail()))
    snapshot = deepcopy(result["contract_instance"])
    snapshot[field] = {
        "contract": StrKey.encode_contract(bytes(32)),
        "network": "Mainnet",
        "source": "https://invalid.example",
        "last_modified_ledger": 2001,
        "executable": "CONTRACT_EXECUTABLE_WASM",
        "raw_instance_xdr": snapshot["raw_instance_xdr"] + "AAAA",
        "instance_available": 1,
    }[field]
    with pytest.raises(AccountError):
        confirm(derive(detail()), snapshot)


def asset_service(asset=None):
    """Inject existing asset lookup behavior without sending Horizon requests during UI checks."""

    class Provider:
        """Return matching asset fixtures while keeping native XLM's local path unchanged."""

        async def get_asset(self, code, issuer, network):
            """Produce exact source identity for the requested network."""
            return asset or detail(network)

    return AccountService(Provider())


def test_wrong_rpc_network_stops_before_instance_query():
    """A network mismatch is refused before sending the expected asset's instance key."""
    rpc, calls = rpc_fixture(detail(Network.MAINNET))
    with pytest.raises(AccountError, match="different network"):
        asyncio.run(rpc.asset_identity(detail(Network.TESTNET)))
    assert [body["method"] for _, body in calls] == ["getNetwork"]


@pytest.mark.parametrize("field", ["contract", "key", "durability", "value"])
def test_raw_instance_identity_cannot_be_replaced_by_a_confirmed_label(field):
    """Recheck all inner raw instance components rather than trusting normalized descriptions."""
    asset = detail()
    rpc, _ = rpc_fixture(asset)
    result = asyncio.run(rpc.asset_identity(asset))
    snapshot = deepcopy(result["contract_instance"])
    raw = xdr.LedgerEntryData.from_xdr(snapshot["raw_instance_xdr"])
    if field == "contract":
        raw.contract_data.contract = instance_key(
            StrKey.encode_contract(bytes(32))
        ).contract_data.contract
    elif field == "key":
        raw.contract_data.key = xdr.SCVal(xdr.SCValType.SCV_VOID)
    elif field == "durability":
        raw.contract_data.durability = xdr.ContractDataDurability.TEMPORARY
    else:
        raw.contract_data.val = xdr.SCVal(xdr.SCValType.SCV_VOID)
    snapshot["raw_instance_xdr"] = raw.to_xdr()
    with pytest.raises(AccountError):
        confirm(derive(asset), snapshot)


def test_ui_explicit_check_export_save_and_contract_shortcut(app, tmp_path):
    """Keep local derivation, optional RPC confirmation and independent evidence documents reviewable."""

    async def scenario():
        """Inspect an asset, check it, edit form text and use only the accepted identity."""
        rpc, calls = rpc_fixture()
        store = InvestigationStore(tmp_path / "cases.db")
        window = MainWindow(asset_service(), store, contract_provider=rpc)
        window.network.setCurrentText("Testnet")
        window.open_asset("USD", ISSUER)
        view = window.assets
        await view.task
        assert calls == [] and view.identity["status"].startswith("Derived")
        local = view.identity_document()
        assert (
            local["records"] == [] and local["metadata"]["contract"] == derive(detail())["contract"]
        )
        view.start_identity()
        await view.task
        assert view.identity["status"].startswith("Confirmed")
        snapshot = view.identity_document()
        assert snapshot["kind"] == "asset_contract_identity"
        assert "900000000.0000001" in serialize(snapshot, "csv")
        assert json.loads(serialize(snapshot, "json"))["records"][0]["raw_instance_xdr"]
        window.investigations.name.setText("SAC identity")
        window.investigations.create()
        window.save_resource()
        saved = store.list()[0]["entries"][0]["evidence"]
        assert [item["kind"] for item in saved] == ["asset", "asset_contract_identity"]
        view.code.setText("OTHER")
        view.inspect_contract()
        await window.contracts.task
        assert window.contracts.snapshot["contract"] == snapshot["metadata"]["contract"]
        window.network.setCurrentText("Mainnet")
        assert view.identity is None and not view.verify_contract.isEnabled()
        assert saved[1]["records"] == snapshot["records"]
        window.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel", "reset", "failure", "foreign"])
def test_late_failed_or_foreign_checks_preserve_or_clear_the_correct_context(app, action):
    """Cancellations retain accepted evidence; reset clears it; failed/foreign reads never replace it."""

    async def scenario():
        """Release an optional check after cancellation/reset, or return a deliberate failure."""
        rpc, _ = rpc_fixture()
        view = AssetView(asset_service(), lambda: Network.TESTNET, rpc)
        view.open_asset("USD", ISSUER)
        await view.task
        accepted = deepcopy(view.identity)
        complete = await rpc.asset_identity(view.snapshot)
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(*args):
            """Simulate a cancellation-resistant provider and foreign/error completion modes."""
            entered.set()
            if action == "failure":
                raise AccountError("Fixture failed")
            if action == "foreign":
                return {**complete, "network": "Mainnet"}
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return complete

        rpc.asset_identity = delayed
        view.start_identity()
        task = view.task
        await entered.wait()
        if action == "cancel":
            view.cancel_lookup()
        elif action == "reset":
            view.reset()
        release.set()
        await task
        assert view.identity == (None if action == "reset" else accepted)
        assert view.identity_export.isEnabled() is (action != "reset")
        view.close()

    asyncio.run(scenario())
