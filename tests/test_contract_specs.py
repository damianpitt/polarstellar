"""Validate pinned contract interfaces and conservative event-shape candidates without executing WASM."""

import asyncio
import hashlib
import json
from copy import deepcopy

import httpx
import pytest
from PySide6.QtWidgets import QApplication
from stellar_sdk import scval, xdr
from test_contracts import CONTRACT, event, instance_result

from polarstellar.app.window import MainWindow
from polarstellar.stellar.contract_specs import extract, interpret, readable
from polarstellar.stellar.contracts import PASSPHRASES, ContractProvider, decoded_value
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService
from polarstellar.storage.export import serialize
from polarstellar.storage.investigations import InvestigationStore


def integer(value):
    """Encode a small unsigned LEB section length for valid WASM fixture construction."""
    result = bytearray()
    while True:
        part = value & 127
        value >>= 7
        result.append(part | (128 if value else 0))
        if not value:
            return bytes(result)


def declarations(form="SINGLE_VALUE", value_kind=xdr.SCSpecType.SC_SPEC_TYPE_I128):
    """Build real SDK function, user struct, error enum and event specification entries."""
    number = xdr.SCSpecTypeDef(value_kind)
    symbol = xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_SYMBOL)
    function = xdr.SCSpecEntry(
        xdr.SCSpecEntryKind.SC_SPEC_ENTRY_FUNCTION_V0,
        function_v0=xdr.SCSpecFunctionV0(
            b"Author function docs",
            xdr.SCSymbol(b"read"),
            [xdr.SCSpecFunctionInputV0(b"Input docs", b"key", symbol)],
            [number],
        ),
    )
    struct = xdr.SCSpecEntry(
        xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_STRUCT_V0,
        udt_struct_v0=xdr.SCSpecUDTStructV0(
            b"Record docs", b"", b"Record", [xdr.SCSpecUDTStructFieldV0(b"", b"count", number)]
        ),
    )
    errors = xdr.SCSpecEntry(
        xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_ERROR_ENUM_V0,
        udt_error_enum_v0=xdr.SCSpecUDTErrorEnumV0(
            b"Errors",
            b"",
            b"Errors",
            [xdr.SCSpecUDTErrorEnumCaseV0(b"Invalid input", b"Invalid", xdr.Uint32(1))],
        ),
    )
    declared_event = xdr.SCSpecEntry(
        xdr.SCSpecEntryKind.SC_SPEC_ENTRY_EVENT_V0,
        event_v0=xdr.SCSpecEventV0(
            b"Author event docs",
            b"",
            xdr.SCSymbol(b"Changed"),
            [xdr.SCSymbol(b"change")],
            [
                xdr.SCSpecEventParamV0(
                    b"No inferred units",
                    b"quantity",
                    number,
                    xdr.SCSpecEventParamLocationV0.SC_SPEC_EVENT_PARAM_LOCATION_DATA,
                )
            ],
            getattr(xdr.SCSpecEventDataFormat, "SC_SPEC_EVENT_DATA_FORMAT_" + form),
        ),
    )
    return [function, struct, errors, declared_event]


def module(entries=None):
    """Wrap canonical SCSpecEntry streams in one WASM custom section, without executable instructions."""
    payload = b"contractspecv0"
    payload = (
        integer(len(payload))
        + payload
        + b"".join(
            item.to_xdr_bytes() for item in (entries if entries is not None else declarations())
        )
    )
    return b"\0asm\1\0\0\0\0" + integer(len(payload)) + payload


def specification(entries=None):
    """Add context to a parsed local specification for isolated interpretation tests."""
    wasm = module(entries)
    return {
        **extract(wasm),
        "contract": CONTRACT,
        "network": "Testnet",
        "wasm_hash": hashlib.sha256(wasm).hexdigest(),
        "source": "fixture",
        "fetched_at": "2026-10-20T00:00:00Z",
        "coverage": "Author declarations only; historical compatibility unverified.",
    }


def observed(value=None, topics=None, **changes):
    """Construct the same lossless event representation produced by ContractProvider.events."""
    return {
        "id": "event-1",
        "contract": CONTRACT,
        "ledger": 1500,
        "successful_call": True,
        "topics": [
            decoded_value(item.to_xdr())
            for item in (topics if topics is not None else [scval.to_symbol("change")])
        ],
        "value": decoded_value((value if value is not None else scval.to_int128(2**100)).to_xdr()),
        **changes,
    }


def rpc_fixture(wasm=None, changed=False, corrupt=False):
    """Serve pinned instance/code entries through the real read-only RPC provider and record all methods."""
    wasm = module() if wasm is None else wasm
    digest = hashlib.sha256(wasm).digest()
    instance = instance_result()
    decoded = xdr.LedgerEntryData.from_xdr(instance["entries"][0]["xdr"])
    decoded.contract_data.val.instance.executable.wasm_hash = xdr.Hash(digest)
    instance["entries"][0]["xdr"] = decoded.to_xdr()
    calls = []

    def handler(request):
        """Supply instance-only inspection or a fresh instance/code pair for explicit specification lookup."""
        body = json.loads(request.content)
        calls.append(body)
        method = body["method"]
        if method == "getNetwork":
            result = {"passphrase": PASSPHRASES[Network.TESTNET]}
        elif method == "getHealth":
            result = {"oldestLedger": 100, "latestLedger": 2000}
        elif method == "getLedgerEntries":
            if len(body["params"]["keys"]) == 1:
                result = instance
            else:
                fresh = deepcopy(instance["entries"][0])
                if changed:
                    different = xdr.LedgerEntryData.from_xdr(fresh["xdr"])
                    different.contract_data.val.instance.executable.wasm_hash = xdr.Hash(bytes(32))
                    fresh["xdr"] = different.to_xdr()
                code = xdr.LedgerEntryData(
                    xdr.LedgerEntryType.CONTRACT_CODE,
                    contract_code=xdr.ContractCodeEntry(
                        xdr.ContractCodeEntryExt(0),
                        xdr.Hash(digest),
                        wasm + (b"x" if corrupt else b""),
                    ),
                )
                result = {
                    "latestLedger": 2001,
                    "entries": [
                        fresh,
                        {
                            "key": body["params"]["keys"][1],
                            "xdr": code.to_xdr(),
                            "lastModifiedLedgerSeq": 1900,
                        },
                    ],
                }
        elif method == "getEvents":
            result = {
                "events": [event(topic=[scval.to_symbol("change").to_xdr()])],
                "cursor": "next",
                "oldestLedger": 100,
                "latestLedger": 2001,
            }
        else:
            raise AssertionError("Unexpected RPC method")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    return ContractProvider(httpx.MockTransport(handler)), calls


@pytest.fixture
def app():
    """Keep a headless Qt application alive for real optional-load and offline-save actions."""
    return QApplication.instance() or QApplication([])


def test_functions_types_docs_raw_xdr_and_readable_signatures():
    """Declarations retain exact XDR, author docs, inputs/outputs, struct fields and error codes."""
    spec = specification()
    assert spec["status"] == "Available" and len(spec["entries"]) == 4
    assert "read(key: symbol) → i128" in readable(spec)
    assert "quantity: i128" in readable(spec) and "Invalid = 1" in readable(spec)
    assert spec["entries"][0]["doc"] == "Author function docs"
    for original, parsed in zip(declarations(), spec["entries"], strict=True):
        assert original.to_xdr() == parsed["xdr"]


@pytest.mark.parametrize("form", ["SINGLE_VALUE", "VEC", "MAP"])
def test_declared_event_data_layouts_keep_exact_unscaled_integer(form):
    """Every supported event layout produces a candidate with exact integer strings and no inferred decimals."""
    value = scval.to_int128(2**100)
    if form == "VEC":
        value = scval.to_vec([value])
    elif form == "MAP":
        value = xdr.SCVal(
            xdr.SCValType.SCV_MAP,
            map=xdr.SCMap([xdr.SCMapEntry(scval.to_symbol("quantity"), value)]),
        )
    result = interpret(observed(value), specification(declarations(form)))
    assert result["status"] == "Candidate match"
    assert result["fields"][0]["value"]["value"] == str(2**100)
    assert "Historical compatibility" in result["note"]


def test_prefix_string_compatibility_and_failed_call_context():
    """String prefixes can match symbol declarations while failed-call observations remain explicitly unapplied."""
    result = interpret(
        observed(topics=[scval.to_string("change")], successful_call=False), specification()
    )
    assert result["status"] == "Candidate match" and "Failed call" in result["call_status"]
    assert (
        interpret(observed(topics=[scval.to_symbol("other")]), specification())["status"]
        == "Raw only"
    )


def test_ambiguous_and_mismatched_events_never_choose_a_definition():
    """Duplicate matching declarations, wrong scalar types and extra fields retain raw evidence without guessing."""
    entries = declarations()
    entries.append(entries[-1])
    assert "Ambiguous" in interpret(observed(), specification(entries))["note"]
    original = observed(scval.to_uint64(1))
    before = deepcopy(original)
    assert interpret(original, specification())["status"] == "Raw only" and original == before
    assert interpret(observed(), None)["status"] == "Raw only"


@pytest.mark.parametrize(
    "wasm",
    [b"bad", b"\0asm\1\0\0\0\0\x80", b"\0asm\1\0\0\0\0\x05ab", b"\0asm\1\0\0\0" + b"x" * 2_000_000],
    ids=["magic", "leb", "truncated", "size"],
)
def test_bad_wasm_is_bounded_and_refused(wasm):
    """Malformed or oversized WASM is never executed and cannot replace an accepted specification."""
    with pytest.raises(ValueError):
        extract(wasm)


def test_absent_empty_duplicate_and_oversized_entry_streams():
    """Absence/empty interfaces are distinct from malformed duplicates and bounded entry overflow."""
    assert extract(b"\0asm\1\0\0\0")["status"] == "No embedded specification"
    assert extract(module([]))["status"] == "Empty embedded specification"
    with pytest.raises(ValueError, match="Duplicate"):
        extract(module() + module()[8:])
    with pytest.raises(ValueError, match="256 entries"):
        extract(module([declarations()[0]] * 257))


def test_rpc_spec_load_is_explicit_and_bound_to_code_hash():
    """Basic inspection never fetches code; optional lookup verifies network, fresh instance and content hash."""

    async def scenario():
        """Inspect first, then request the pinned interface using only allowed ledger reads."""
        provider, calls = rpc_fixture()
        snapshot = await provider.inspect(CONTRACT, Network.TESTNET)
        assert len(calls) == 3 and len(calls[-1]["params"]["keys"]) == 1
        spec = await provider.specification(snapshot, Network.TESTNET)
        assert spec["wasm_hash"] == snapshot["wasm_hash"] and len(spec["entries"]) == 4
        assert spec["code_latest_ledger"] == 2001
        assert [item["method"] for item in calls[3:]] == ["getNetwork", "getLedgerEntries"]
        assert len(calls[-1]["params"]["keys"]) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changed,corrupt", [(True, False), (False, True)], ids=["upgrade", "hash-mismatch"]
)
def test_changed_instance_or_wrong_code_cannot_label_events(changed, corrupt):
    """A different executable or mismatched content hash must refuse optional interface evidence."""

    async def scenario():
        """Obtain an otherwise valid instance then fail only the pinned-code lookup."""
        provider, _ = rpc_fixture(changed=changed, corrupt=corrupt)
        snapshot = await provider.inspect(CONTRACT, Network.TESTNET)
        with pytest.raises(AccountError, match="Specification unavailable"):
            await provider.specification(snapshot, Network.TESTNET)

    asyncio.run(scenario())


def test_ui_load_interpretation_exports_and_offline_save(app, tmp_path):
    """Save raw events plus candidate annotations and a separate full interface artifact, without repeating all specs per event."""

    async def scenario():
        """Exercise actual contract view/window actions with pinned RPC fixtures and isolated storage."""
        provider, calls = rpc_fixture()
        store = InvestigationStore(tmp_path / "cases.db")
        window = MainWindow(AccountService(object()), store, contract_provider=provider)
        window.network.setCurrentText("Testnet")
        view = window.contracts
        view.address.setText(CONTRACT)
        view.start()
        await view.task
        assert view.specification is None and len(calls) == 3
        view.start_events()
        await view.task
        original_events = deepcopy(view.events)
        view.start_specification()
        await view.task
        assert view.specification["status"] == "Available"
        assert "read(key: symbol)" in view.spec_details.toPlainText()
        # Give long interfaces the available pane, then restore both evidence panes.
        view.detail_tabs.setCurrentIndex(2)
        assert view.table.isHidden() and view.raw.isHidden()
        view.detail_tabs.setCurrentIndex(0)
        assert not view.table.isHidden() and not view.raw.isHidden()
        view.table.selectRow(0)
        assert "Candidate match" in view.raw.toPlainText()
        assert "quantity (DATA)" in view.raw.toPlainText()
        exported = view.export_document()
        assert exported["records"][0]["interpretation"]["status"] == "Candidate match"
        assert view.events == original_events
        assert "entries" not in exported["metadata"]["specification"]
        spec_document = view.specification_document()
        assert (
            spec_document["kind"] == "contract_specification" and len(spec_document["records"]) == 4
        )
        assert "record.xdr" in serialize(spec_document, "csv")
        window.investigations.name.setText("Declared interface")
        window.investigations.create()
        window.pages.setCurrentIndex(6)
        window.save_resource()
        saved = store.list()[0]["entries"][0]["evidence"]
        assert [item["kind"] for item in saved] == ["contract", "contract_specification"]
        view.reset()
        assert view.specification is None and not view.spec_export.isEnabled()
        assert saved[1]["records"] == spec_document["records"]
        window.close()

    asyncio.run(scenario())


def test_topic_fields_and_struct_values_keep_names_and_locations():
    """Structured fields use symbol maps and topic parameters remain distinct from event data."""
    entries = declarations()
    entries[-1].event_v0.params[0].type = xdr.SCSpecTypeDef(
        xdr.SCSpecType.SC_SPEC_TYPE_UDT, udt=xdr.SCSpecTypeUDT(b"Record")
    )
    entries[-1].event_v0.params.insert(
        0,
        xdr.SCSpecEventParamV0(
            b"",
            b"tag",
            xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_SYMBOL),
            xdr.SCSpecEventParamLocationV0.SC_SPEC_EVENT_PARAM_LOCATION_TOPIC_LIST,
        ),
    )
    data = xdr.SCVal(
        xdr.SCValType.SCV_MAP,
        map=xdr.SCMap([xdr.SCMapEntry(scval.to_symbol("count"), scval.to_int128(2**100))]),
    )
    result = interpret(
        observed(data, [scval.to_symbol("change"), scval.to_symbol("tag-value")]),
        specification(entries),
    )
    assert result["status"] == "Candidate match"
    assert result["fields"][0]["location"] == "TOPIC_LIST"
    assert result["fields"][1]["value"]["fields"][0]["value"]["value"] == str(2**100)
    duplicate = deepcopy(data)
    duplicate.map.sc_map.append(duplicate.map.sc_map[0])
    assert (
        interpret(
            observed(duplicate, [scval.to_symbol("change"), scval.to_symbol("tag-value")]),
            specification(entries),
        )["status"]
        == "Raw only"
    )


def test_unknown_matching_types_do_not_hide_candidate_ambiguity():
    """An unsupported potentially matching declaration cannot be discarded to manufacture one confident candidate."""
    entries = declarations()
    other = deepcopy(entries[-1])
    other.event_v0.params[0].type = xdr.SCSpecTypeDef(
        xdr.SCSpecType.SC_SPEC_TYPE_UDT, udt=xdr.SCSpecTypeUDT(b"Unknown")
    )
    entries.append(other)
    result = interpret(observed(), specification(entries))
    assert result["status"] == "Raw only" and "unsupported/ambiguous" in result["note"]


def test_map_extra_fields_and_malformed_values_remain_raw():
    """Unexpected data fields, invalid XDR and too many topics never gain declared field names."""
    data = xdr.SCVal(
        xdr.SCValType.SCV_MAP,
        map=xdr.SCMap(
            [
                xdr.SCMapEntry(scval.to_symbol("quantity"), scval.to_int128(1)),
                xdr.SCMapEntry(scval.to_symbol("extra"), scval.to_int128(1)),
            ]
        ),
    )
    assert interpret(observed(data), specification(declarations("MAP")))["status"] == "Raw only"
    bad = observed()
    bad["value"]["xdr"] = "invalid"
    assert interpret(bad, specification())["status"] == "Raw only"
    assert (
        interpret(observed(topics=[scval.to_symbol("change")] * 5), specification())["status"]
        == "Raw only"
    )


def test_type_recursion_and_spec_byte_limits_are_bounded():
    """Nested types and oversized specification sections fail before unbounded interpretation work."""
    from polarstellar.stellar.contract_specs import type_description

    definition = xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_I128)
    for _ in range(18):
        definition = xdr.SCSpecTypeDef(
            xdr.SCSpecType.SC_SPEC_TYPE_VEC, vec=xdr.SCSpecTypeVec(definition)
        )
    with pytest.raises(ValueError, match="nesting"):
        type_description(definition)
    name = b"contractspecv0"
    payload = integer(len(name)) + name + bytes(262145)
    with pytest.raises(ValueError, match="256 KiB"):
        extract(b"\0asm\1\0\0\0\0" + integer(len(payload)) + payload)


def test_absent_native_instance_does_not_fetch_synthetic_specification():
    """Native/absent instances return an explicit unavailable status without inventing a WASM interface."""

    async def scenario():
        """Inspect a valid context and then simulate the known native-executable case."""
        provider, calls = rpc_fixture()
        snapshot = await provider.inspect(CONTRACT, Network.TESTNET)
        snapshot.update(executable="CONTRACT_EXECUTABLE_STELLAR_ASSET", wasm_hash=None)
        before = len(calls)
        assert (await provider.specification(snapshot, Network.TESTNET))["status"].startswith(
            "Unavailable"
        )
        assert len(calls) == before
        with pytest.raises(AccountError, match="network"):
            await provider.specification(snapshot, Network.MAINNET)
        assert len(calls) == before

    asyncio.run(scenario())


def test_specification_cancellation_rejects_late_results(app):
    """Cancellation-resistant optional metadata cannot annotate a reset contract or another network."""

    async def scenario():
        """Release the old spec only after resetting the real view and its generation."""
        provider, _ = rpc_fixture()
        snapshot = await provider.inspect(CONTRACT, Network.TESTNET)
        result = await provider.specification(snapshot, Network.TESTNET)
        future = asyncio.get_running_loop().create_future()

        class Delayed:
            """Delegate instance inspection but hold metadata beyond cancellation."""

            async def inspect(self, *args):
                """Return the same inspected identity with valid retention bounds."""
                return snapshot

            async def specification(self, *args):
                """Simulate a transport that cannot abort its response immediately."""
                try:
                    return await asyncio.shield(future)
                except asyncio.CancelledError:
                    return await future

        from polarstellar.ui.contract_view import ContractView

        view = ContractView(Delayed(), lambda: Network.TESTNET)
        view.address.setText(CONTRACT)
        view.start()
        await view.task
        view.start_specification()
        pending = view.task
        await asyncio.sleep(0)
        view.reset()
        await asyncio.sleep(0)
        future.set_result(result)
        await pending
        assert view.snapshot is None and view.specification is None and not view.events

    asyncio.run(scenario())


def test_optional_failure_preserves_events_and_previous_specification(app):
    """A failed reload keeps accepted event pages, raw evidence, cursors and the previously loaded specification."""

    async def scenario():
        """Raise on a second explicit spec request after accepted events and annotations exist."""
        provider, _ = rpc_fixture()
        from polarstellar.ui.contract_view import ContractView

        view = ContractView(provider, lambda: Network.TESTNET)
        view.address.setText(CONTRACT)
        view.start()
        await view.task
        view.start_events()
        await view.task
        view.start_specification()
        await view.task
        before = view.export_document()

        async def fail(*args):
            """Represent temporary metadata unavailability without changing the source context."""
            raise AccountError("Metadata unavailable")

        provider.specification = fail
        view.start_specification()
        await view.task
        after = view.export_document()
        assert after["records"] == before["records"]
        assert after["metadata"] == before["metadata"]
        assert "retained" in view.status.text()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind", ["VEC", "OPTION", "BYTES_N", "UDT"], ids=["vector", "optional", "fixed-bytes", "enum"]
)
def test_supported_compound_event_values(kind):
    """Common nested declared types retain typed values without interpreting amounts or missing variants."""
    entries = declarations()
    number = xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_I128)
    if kind == "VEC":
        definition = xdr.SCSpecTypeDef(
            xdr.SCSpecType.SC_SPEC_TYPE_VEC, vec=xdr.SCSpecTypeVec(number)
        )
        value = scval.to_vec([scval.to_int128(2**100)])
    elif kind == "OPTION":
        definition = xdr.SCSpecTypeDef(
            xdr.SCSpecType.SC_SPEC_TYPE_OPTION, option=xdr.SCSpecTypeOption(number)
        )
        value = scval.to_void()
    elif kind == "BYTES_N":
        definition = xdr.SCSpecTypeDef(
            xdr.SCSpecType.SC_SPEC_TYPE_BYTES_N, bytes_n=xdr.SCSpecTypeBytesN(xdr.Uint32(2))
        )
        value = scval.to_bytes(b"ab")
    else:
        definition = xdr.SCSpecTypeDef(
            xdr.SCSpecType.SC_SPEC_TYPE_UDT, udt=xdr.SCSpecTypeUDT(b"Errors")
        )
        value = scval.to_uint32(1)
    entries[-1].event_v0.params[0].type = definition
    result = interpret(observed(value), specification(entries))
    assert result["status"] == "Candidate match"
    assert result["fields"][0]["declared_type"]["kind"] == kind
    assert "No inferred units" in readable(specification(entries))


def test_foreign_specification_does_not_replace_accepted_state(app):
    """Wrong contract/hash metadata is rejected before replacing a usable interface or its annotations."""

    async def scenario():
        """Return a foreign identity after accepting the real RPC result once."""
        provider, _ = rpc_fixture()
        from polarstellar.ui.contract_view import ContractView

        view = ContractView(provider, lambda: Network.TESTNET)
        view.address.setText(CONTRACT)
        view.start()
        await view.task
        view.start_specification()
        await view.task
        before = view.specification_document()
        result = deepcopy(view.specification)
        result["wasm_hash"] = "0" * 64

        async def foreign(*args):
            """Simulate a stale adapter result from another executable."""
            return result

        provider.specification = foreign
        view.start_specification()
        await view.task
        assert view.specification_document()["records"] == before["records"]
        assert view.specification["wasm_hash"] == before["metadata"]["wasm_hash"]
        assert "retained" in view.status.text()

    asyncio.run(scenario())


def test_foreign_event_contract_cannot_use_this_specification():
    """A matching payload from another contract cannot gain labels from this contract's declaration."""
    from test_contracts import OTHER

    result = interpret(observed(contract=OTHER), specification())
    assert result["status"] == "Raw only" and "identity" in result["note"]
