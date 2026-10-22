"""Preserve muxed event identity and unsigned routing IDs without inferred aliases or transfers."""

import asyncio
import base64
import json
from copy import deepcopy

import pytest
from PySide6.QtWidgets import QApplication
from stellar_sdk import Address, MuxedAccount, StrKey, scval, xdr
from test_contract_event_types import interface, named, result_type, variant
from test_contract_specs import module, observed, rpc_fixture, specification
from test_contract_storage import provider as storage_provider
from test_contract_storage import storage_response
from test_contracts import CONTRACT, event, provider

from polarstellar.app.window import MainWindow
from polarstellar.stellar.contract_specs import interpret
from polarstellar.stellar.contract_storage import entered_key
from polarstellar.stellar.contracts import ENDPOINTS, decoded_value
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService
from polarstellar.storage.export import serialize
from polarstellar.storage.investigations import InvestigationStore
from polarstellar.ui.contract_view import ContractView

ACCOUNT = StrKey.encode_ed25519_public_key(bytes([8]) * 32)
MUXED_TYPE = xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_MUXED_ADDRESS)
ADDRESS_TYPE = xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_ADDRESS)


def muxed(identifier=2**64 - 1):
    """Construct a real SDK M-address; no float or string-to-account normalization is involved."""
    return MuxedAccount(ACCOUNT, identifier).account_muxed


def candidate(value, entries=None, **changes):
    """Interpret actual address XDR against a pinned declared interface without network calls."""
    return interpret(
        observed(value, **changes),
        specification(interface(MUXED_TYPE) if entries is None else entries),
    )


@pytest.fixture
def app():
    """Keep offscreen Qt widgets alive for real contract view/save actions."""
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("identifier", [0, 1, 2**53 + 1, 2**64 - 1])
def test_full_address_underlying_account_and_exact_routing_id(identifier):
    """Zero and integers beyond JSON's safe-number range remain exact decimal strings."""
    full = muxed(identifier)
    value = scval.to_address(full)
    normalized = decoded_value(value.to_xdr())
    assert normalized["value"] == full and normalized["account"] == ACCOUNT
    assert normalized["routing_id"] == str(identifier)
    assert normalized["address_kind"] == "MUXED_ACCOUNT" and normalized["xdr"] == value.to_xdr()
    result = candidate(value)
    assert result["status"] == "Candidate match"
    field = result["fields"][0]["value"]
    assert field == {
        "type": "MUXED_ADDRESS",
        **{key: item for key, item in normalized.items() if key not in ("xdr", "type")},
    }
    assert MuxedAccount.from_account(field["value"]).account_muxed_id == identifier
    assert "no ownership or fund-flow inference" in field["note"]


@pytest.mark.parametrize("address", [ACCOUNT, CONTRACT])
@pytest.mark.parametrize("shape", [ADDRESS_TYPE, MUXED_TYPE])
def test_g_and_c_match_both_address_types_without_invented_routing_ids(address, shape):
    """MuxedAddress also accepts ordinary G/C values, where no routing ID is present."""
    result = candidate(scval.to_address(address), interface(shape))
    decoded = result["fields"][0]["value"]
    assert decoded["value"] == address and "routing_id" not in decoded and "account" not in decoded
    assert decoded["address_kind"] == ("ACCOUNT" if address == ACCOUNT else "CONTRACT")


def test_ordinary_address_refuses_m_and_does_not_hide_a_muxed_candidate():
    """An SDK-readable M-address is a mismatch for declared Address, not an uncertain alternative."""
    value = scval.to_address(muxed())
    assert candidate(value, interface(ADDRESS_TYPE))["status"] == "Raw only"
    entries = interface(ADDRESS_TYPE)
    alternate = deepcopy(entries[-1])
    alternate.event_v0.name = xdr.SCSymbol(b"MuxedChanged")
    alternate.event_v0.params[0].type = MUXED_TYPE
    entries.append(alternate)
    result = candidate(value, entries)
    assert result["status"] == "Candidate match" and result["name"] == "MuxedChanged"


@pytest.mark.parametrize("address", [ACCOUNT, CONTRACT])
def test_g_c_overlapping_address_declarations_remain_ambiguous(address):
    """A wider declared type does not outrank an ordinary one when both match the actual value."""
    entries = interface(ADDRESS_TYPE)
    alternate = deepcopy(entries[-1])
    alternate.event_v0.params[0].type = MUXED_TYPE
    entries.append(alternate)
    result = candidate(scval.to_address(address), entries)
    assert result["status"] == "Raw only" and "Ambiguous" in result["note"]


def test_different_routing_ids_never_collapse_to_the_same_evidence_value():
    """Sharing a G-account is not permission to merge two complete M-address identities."""
    first = candidate(scval.to_address(muxed(0)))["fields"][0]["value"]
    second = candidate(scval.to_address(muxed(1)))["fields"][0]["value"]
    assert first["account"] == second["account"] == ACCOUNT
    assert first["value"] != second["value"] and first["routing_id"] != second["routing_id"]


@pytest.mark.parametrize(
    "value",
    [
        scval.to_string(muxed()),
        scval.to_bytes(muxed().encode()),
        scval.to_uint64(2**64 - 1),
        scval.to_void(),
        scval.to_vec([scval.to_address(muxed())]),
    ],
)
def test_strings_bytes_numbers_and_wrappers_are_not_coerced_to_addresses(value):
    """Only ScVal Address encodings can satisfy a declared MuxedAddress field."""
    assert candidate(value)["status"] == "Raw only"


@pytest.mark.parametrize("kind", ["claimable_balance", "liquidity_pool"])
def test_other_sdk_address_variants_remain_raw(kind):
    """B/L-style addresses are not silently accepted by the G/C/M contract type rule."""
    address = (
        Address.from_raw_claimable_balance(bytes(33))
        if kind == "claimable_balance"
        else Address.from_raw_liquidity_pool(bytes(32))
    )
    value = scval.to_address(address.address)
    assert candidate(value)["status"] == "Raw only"
    assert candidate(value, interface(ADDRESS_TYPE))["status"] == "Raw only"
    normalized = decoded_value(value.to_xdr())
    assert "value" not in normalized and normalized["xdr"] == value.to_xdr()


@pytest.mark.parametrize("kind", ["truncated", "trailing", "unknown", "wrong_public_key"])
def test_malformed_or_unknown_address_xdr_preserves_original_encoding(kind):
    """Neither prefix parses nor unfamiliar address discriminants become typed identities."""
    raw = scval.to_address(muxed()).to_xdr_bytes()
    if kind == "truncated":
        raw = raw[:-1]
    elif kind == "trailing":
        raw += bytes(4)
    elif kind == "unknown":
        raw = (
            int(xdr.SCValType.SCV_ADDRESS).to_bytes(4, "big") + (99).to_bytes(4, "big") + bytes(40)
        )
    else:
        raw = (
            int(xdr.SCValType.SCV_ADDRESS).to_bytes(4, "big")
            + bytes(4)
            + (99).to_bytes(4, "big")
            + bytes(32)
        )
    encoded = base64.b64encode(raw).decode()
    normalized = decoded_value(encoded)
    assert (
        normalized["xdr"] == encoded and "value" not in normalized and "account" not in normalized
    )
    ev = observed()
    ev["value"] = normalized
    assert interpret(ev, specification(interface(MUXED_TYPE)))["status"] == "Raw only"


@pytest.mark.parametrize("form", ["SINGLE_VALUE", "VEC", "MAP", "TOPIC"])
def test_muxed_addresses_across_dynamic_topics_and_data_layouts(form):
    """The same M-address retains identity independently of where its typed field is encoded."""
    entries = interface(MUXED_TYPE, "SINGLE_VALUE" if form == "TOPIC" else form)
    value, topics = scval.to_address(muxed(0)), None
    if form == "VEC":
        value = scval.to_vec([value])
    elif form == "MAP":
        value = xdr.SCVal(
            xdr.SCValType.SCV_MAP,
            map=xdr.SCMap([xdr.SCMapEntry(scval.to_symbol("quantity"), value)]),
        )
    elif form == "TOPIC":
        entries[-1].event_v0.params[
            0
        ].location = xdr.SCSpecEventParamLocationV0.SC_SPEC_EVENT_PARAM_LOCATION_TOPIC_LIST
        topics, value = [scval.to_symbol("change"), value], scval.to_void()
    result = candidate(value, entries, topics=topics)
    assert result["fields"][0]["value"]["value"] == muxed(0)
    assert result["fields"][0]["value"]["routing_id"] == "0"


@pytest.mark.parametrize("kind", ["vector", "map", "tuple", "option", "struct", "union", "result"])
def test_nested_muxed_fields_in_supported_composites(kind):
    """Nested decoding preserves exact identities instead of flattening them into underlying accounts."""
    value = scval.to_address(muxed())
    shape, encoded = MUXED_TYPE, value
    entries = interface()
    if kind == "vector":
        shape = xdr.SCSpecTypeDef(
            xdr.SCSpecType.SC_SPEC_TYPE_VEC, vec=xdr.SCSpecTypeVec(MUXED_TYPE)
        )
        encoded = scval.to_vec([value])
    elif kind == "tuple":
        shape = xdr.SCSpecTypeDef(
            xdr.SCSpecType.SC_SPEC_TYPE_TUPLE, tuple=xdr.SCSpecTypeTuple([MUXED_TYPE])
        )
        encoded = scval.to_vec([value])
    elif kind == "map":
        shape = xdr.SCSpecTypeDef(
            xdr.SCSpecType.SC_SPEC_TYPE_MAP, map=xdr.SCSpecTypeMap(MUXED_TYPE, MUXED_TYPE)
        )
        encoded = xdr.SCVal(xdr.SCValType.SCV_MAP, map=xdr.SCMap([xdr.SCMapEntry(value, value)]))
    elif kind == "option":
        shape = xdr.SCSpecTypeDef(
            xdr.SCSpecType.SC_SPEC_TYPE_OPTION, option=xdr.SCSpecTypeOption(MUXED_TYPE)
        )
    elif kind == "struct":
        shape = named(b"Record")
        entries[1].udt_struct_v0.fields[0].type = MUXED_TYPE
        encoded = xdr.SCVal(
            xdr.SCValType.SCV_MAP, map=xdr.SCMap([xdr.SCMapEntry(scval.to_symbol("count"), value)])
        )
    elif kind == "union":
        shape = named(b"State")
        entries[-2].udt_union_v0.cases[1].tuple_case.type = [MUXED_TYPE]
        encoded = variant("Count", value)
    else:
        shape = result_type(ok=MUXED_TYPE)
    entries[-1].event_v0.params[0].type = shape
    result = candidate(encoded, entries)
    assert result["status"] == "Candidate match"
    text = json.dumps(result["fields"][0]["value"])
    assert muxed() in text and ACCOUNT in text and str(2**64 - 1) in text


def test_g_address_and_separate_id_fields_are_not_synthesized_into_m_address():
    """A schema field called to_muxed_id does not authorize inferring a combined destination or transfer."""
    entries = interface(ADDRESS_TYPE, "MAP")
    entries[-1].event_v0.params[0].name = b"to"
    entries[-1].event_v0.params.append(
        xdr.SCSpecEventParamV0(
            b"Unverified routing field",
            b"to_muxed_id",
            xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_U64),
            xdr.SCSpecEventParamLocationV0.SC_SPEC_EVENT_PARAM_LOCATION_DATA,
        )
    )
    value = xdr.SCVal(
        xdr.SCValType.SCV_MAP,
        map=xdr.SCMap(
            [
                xdr.SCMapEntry(scval.to_symbol("to"), scval.to_address(ACCOUNT)),
                xdr.SCMapEntry(scval.to_symbol("to_muxed_id"), scval.to_uint64(2**64 - 1)),
            ]
        ),
    )
    result = candidate(value, entries)
    assert result["fields"][0]["value"]["value"] == ACCOUNT
    assert "routing_id" not in result["fields"][0]["value"]
    assert result["fields"][1]["value"]["value"] == str(2**64 - 1)
    assert muxed() not in json.dumps(result)


@pytest.mark.parametrize("successful", [True, False, None])
def test_call_status_and_no_specification_keep_raw_muxed_identity(successful):
    """Address structure neither asserts call success nor depends on a loaded author specification."""
    ev = observed(scval.to_address(muxed()), successful_call=successful)
    original = deepcopy(ev)
    assert interpret(ev, None)["status"] == "Raw only"
    result = interpret(ev, specification(interface(MUXED_TYPE)))
    assert result["status"] == "Candidate match"
    assert result["call_status"] == (
        "Successful call"
        if successful is True
        else "Failed call; not evidence of applied changes"
        if successful is False
        else "Unknown call status"
    )
    assert ev == original and ev["value"]["routing_id"] == str(2**64 - 1)


@pytest.mark.parametrize("nested", ["direct", "vector", "map_key", "map_value"])
def test_muxed_storage_keys_refused_before_network_access(nested):
    """Event address support cannot enable host-prohibited muxed storage keys or strip their IDs."""
    value = scval.to_address(muxed(0))
    if nested == "vector":
        value = scval.to_vec([value])
    elif nested.startswith("map"):
        other = scval.to_symbol("a")
        pair = xdr.SCMapEntry(value, other) if nested == "map_key" else xdr.SCMapEntry(other, value)
        value = xdr.SCVal(xdr.SCValType.SCV_MAP, map=xdr.SCMap([pair]))
    with pytest.raises(AccountError, match="muxed-account"):
        entered_key(value.to_xdr(), "ScVal XDR")
    rpc, calls = provider()
    with pytest.raises(AccountError):
        asyncio.run(rpc.storage(CONTRACT, Network.TESTNET, value.to_xdr(), "Persistent"))
    assert calls == []


def test_returned_storage_value_can_preserve_muxed_identity():
    """Storage key restrictions do not erase a returned M-address value under an ordinary key."""
    response = storage_response()
    entry = xdr.LedgerEntryData.from_xdr(response["entries"][0]["xdr"])
    entry.contract_data.val = scval.to_address(muxed())
    response["entries"][0]["xdr"] = entry.to_xdr()
    rpc, _ = storage_provider(response)
    result = asyncio.run(
        rpc.storage(CONTRACT, Network.TESTNET, entered_key("COUNTER", "Symbol"), "Persistent")
    )
    assert result["entry"]["value"]["value"] == muxed()
    assert result["entry"]["value"]["routing_id"] == str(2**64 - 1)


@pytest.mark.parametrize("network", list(Network))
def test_rpc_ui_exports_and_saved_evidence_preserve_full_identity(app, tmp_path, network):
    """Inspect an actual network-bound RPC event and freeze full/routing/raw evidence offline."""

    async def scenario():
        """Exercise the explicit inspection/spec/event workflow without additional identity lookups."""
        value = scval.to_address(muxed())
        rpc, calls = rpc_fixture(module(interface(MUXED_TYPE)), event_value=value, network=network)
        store = InvestigationStore(tmp_path / "cases.db")
        window = MainWindow(AccountService(object()), store, contract_provider=rpc)
        window.network.setCurrentText(network.value)
        view = window.contracts
        view.address.setText(CONTRACT)
        view.start()
        await view.task
        view.start_events()
        await view.task
        raw = deepcopy(view.events)
        view.start_specification()
        await view.task
        view.table.selectRow(0)
        assert muxed() in view.raw.toPlainText() and ACCOUNT in view.raw.toPlainText()
        assert str(2**64 - 1) in view.raw.toPlainText()
        exported = view.export_document()
        assert muxed() in serialize(exported, "csv")
        record = json.loads(serialize(exported, "json"))["records"][0]
        assert record["value"]["value"] == muxed() and record["value"]["account"] == ACCOUNT
        assert record["value"]["xdr"] == record["raw"]["value"] == value.to_xdr()
        assert view.events == raw and exported["metadata"]["instance"]["network"] == network.value
        assert record["interpretation"]["network"] == network.value
        assert record["interpretation"]["fields"][0]["value"]["routing_id"] == str(2**64 - 1)
        window.investigations.name.setText("Muxed event")
        window.investigations.create()
        window.pages.setCurrentIndex(6)
        window.save_resource()
        saved = store.list()[0]["entries"][0]["evidence"]
        assert saved[0]["records"] == exported["records"]
        view.reset()
        assert saved[0]["records"][0]["value"]["routing_id"] == str(2**64 - 1)
        assert len(calls) == 7
        window.close()

    asyncio.run(scenario())


def test_page_conflict_between_muxed_identities_never_merges_their_evidence(app):
    """An event ID reappearing with a different M-address is a conflict, even with the same G-account."""

    async def scenario():
        """Use two real RPC pages whose only changed evidence is the event's routing ID."""
        rpc, calls = provider(
            [
                ([event(value=scval.to_address(muxed(0)).to_xdr())], "cursor-1"),
                ([event(value=scval.to_address(muxed(1)).to_xdr())], "cursor-2"),
            ]
        )
        view = ContractView(rpc, lambda: Network.TESTNET)
        view.address.setText(CONTRACT)
        view.start()
        await view.task
        view.start_events()
        await view.task
        accepted = deepcopy(view.events)
        view.start_events()
        await view.task
        assert "Conflicting event evidence" in view.status.text()
        assert view.events == accepted and view.cursor == "cursor-1"
        assert all(url == ENDPOINTS[Network.TESTNET] for url, _ in calls)
        view.close()

    asyncio.run(scenario())
