"""Check union/Result/error encodings through real specification XDR and saved UI evidence."""

import asyncio
import json
from copy import deepcopy

import pytest
from PySide6.QtWidgets import QApplication
from stellar_sdk import scval, xdr
from test_contract_specs import declarations, module, observed, rpc_fixture, specification
from test_contracts import CONTRACT

from polarstellar.app.window import MainWindow
from polarstellar.stellar.contract_specs import interpret, readable, typed
from polarstellar.stellar.service import AccountService
from polarstellar.storage.export import serialize
from polarstellar.storage.investigations import InvestigationStore

NUMBER = xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_I128)
ERROR = xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_ERROR)


def named(name):
    """Reference a declared user type without creating a second definition."""
    return xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_UDT, udt=xdr.SCSpecTypeUDT(name))


def result_type(ok=NUMBER, error=None):
    """Build the actual Result spec union; there is no event value wrapper for its branches."""
    return xdr.SCSpecTypeDef(
        xdr.SCSpecType.SC_SPEC_TYPE_RESULT,
        result=xdr.SCSpecTypeResult(ok, named(b"Errors") if error is None else error),
    )


def union_entry():
    """Describe an empty variant and a positional tuple variant containing an exact integer."""
    return xdr.SCSpecEntry(
        xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_UNION_V0,
        udt_union_v0=xdr.SCSpecUDTUnionV0(
            b"Declared state variants",
            b"",
            b"State",
            [
                xdr.SCSpecUDTUnionCaseV0(
                    xdr.SCSpecUDTUnionCaseV0Kind.SC_SPEC_UDT_UNION_CASE_VOID_V0,
                    void_case=xdr.SCSpecUDTUnionCaseVoidV0(b"No data", b"Idle"),
                ),
                xdr.SCSpecUDTUnionCaseV0(
                    xdr.SCSpecUDTUnionCaseV0Kind.SC_SPEC_UDT_UNION_CASE_TUPLE_V0,
                    tuple_case=xdr.SCSpecUDTUnionCaseTupleV0(
                        b"Unscaled count and label",
                        b"Count",
                        [NUMBER, xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_STRING)],
                    ),
                ),
            ],
        ),
    )


def contract_error(code=1, category=xdr.SCErrorType.SCE_CONTRACT):
    """Keep contract integer codes distinct from host error categories sharing the same number."""
    error = (
        xdr.SCError(category, contract_code=xdr.Uint32(code))
        if category == xdr.SCErrorType.SCE_CONTRACT
        else xdr.SCError(category, code=xdr.SCErrorCode(code))
    )
    return xdr.SCVal(xdr.SCValType.SCV_ERROR, error=error)


def variant(name="Count", *payload):
    """Encode the protocol's symbol-first vector, never a string tag or extra nested tuple."""
    return scval.to_vec([scval.to_symbol(name), *payload])


def interface(shape=None, form="SINGLE_VALUE"):
    """Attach a union declaration to the shared real-XDR function/struct/error/event fixture."""
    entries = declarations(form)
    entries[-1].event_v0.params[0].type = named(b"State") if shape is None else shape
    return [*entries[:-1], union_entry(), entries[-1]]


def fields(value, entries=None, **changes):
    """Interpret one observed value without changing its raw RPC representation."""
    return interpret(
        observed(value, **changes), specification(interface() if entries is None else entries)
    )


@pytest.mark.parametrize("empty", [False, True])
def test_named_union_variants_decode_exact_positional_values(empty):
    """Empty and tuple cases use symbol-first vectors and retain the declared variant name."""
    value = (
        variant("Idle")
        if empty
        else variant("Count", scval.to_int128(2**100), scval.to_string("label"))
    )
    result = fields(value)
    assert result["status"] == "Candidate match"
    decoded = result["fields"][0]["value"]
    assert decoded["type"] == "State" and decoded["case"] == ("Idle" if empty else "Count")
    assert decoded["values"] == (
        []
        if empty
        else [{"type": "I128", "value": str(2**100)}, {"type": "STRING", "value": "label"}]
    )
    rendered = readable(specification(interface()))
    assert "Count(i128, string)" in rendered and "Idle()" in rendered


@pytest.mark.parametrize(
    "value",
    [
        scval.to_vec([]),
        xdr.SCVal(xdr.SCValType.SCV_VEC),
        scval.to_symbol("Idle"),
        scval.to_vec([scval.to_string("Idle")]),
        variant("Unknown"),
        variant("Idle", scval.to_void()),
        variant("Count", scval.to_int128(1)),
        variant("Count", scval.to_int128(1), scval.to_string("x"), scval.to_void()),
        variant("Count", scval.to_uint32(1), scval.to_string("x")),
    ],
)
def test_union_malformed_unknown_and_wrong_arity_stays_raw(value):
    """No string-tag coercion, ignored trailing fields or unknown variants gain a candidate label."""
    assert fields(value)["status"] == "Raw only"


@pytest.mark.parametrize("branch", ["Ok", "Err"])
def test_result_is_unwrapped_value_or_contract_error(branch):
    """Result uses a raw success value or ScError, not an Ok/Err-tagged vector."""
    entries = interface(result_type())
    value = scval.to_int128(2**100) if branch == "Ok" else contract_error()
    result = fields(value, entries)
    assert result["status"] == "Candidate match"
    decoded = result["fields"][0]["value"]
    assert decoded["type"] == "RESULT" and decoded["branch"] == branch
    if branch == "Ok":
        assert decoded["value"] == {"type": "I128", "value": str(2**100)}
    else:
        assert decoded["value"] == {
            "type": "Errors",
            "category": "CONTRACT",
            "code": "1",
            "case": "Invalid",
        }
    assert "not call or transaction outcomes" in result["note"]


@pytest.mark.parametrize("category", list(xdr.SCErrorType))
def test_generic_error_preserves_contract_or_host_category_and_code(category):
    """All SDK-known error categories retain their own numeric code namespace and optional code name."""
    value = contract_error(2, category)
    result = fields(value, interface(result_type(error=ERROR)))
    assert result["status"] == "Candidate match"
    error = result["fields"][0]["value"]["value"]
    assert error["category"] == category.name.removeprefix("SCE_") and error["code"] == "2"
    assert ("code_name" in error) is (category != xdr.SCErrorType.SCE_CONTRACT)
    direct = fields(value, interface(ERROR))
    assert direct["fields"][0]["value"] == error


@pytest.mark.parametrize(
    "value",
    [
        contract_error(99),
        contract_error(1, xdr.SCErrorType.SCE_STORAGE),
        scval.to_uint32(1),
        variant("Err", scval.to_uint32(1)),
        variant("Ok", scval.to_int128(1)),
    ],
)
def test_result_unknown_contract_error_host_error_and_wrapped_values_stay_raw(value):
    """A named contract error cannot label a host code or invent a branch wrapper."""
    assert fields(value, interface(result_type()))["status"] == "Raw only"


@pytest.mark.parametrize("shape", [NUMBER, named(b"Record"), named(b"State"), named(b"Missing")])
def test_unsupported_result_error_arm_cannot_hide_a_competing_candidate(shape):
    """Unsupported/missing error arms remain uncertain even when only an Ok payload is present."""
    entries = interface(result_type(error=shape))
    other = deepcopy(entries[-1])
    other.event_v0.params[0].type = NUMBER
    entries.append(other)
    result = fields(scval.to_int128(1), entries)
    assert result["status"] == "Raw only" and "unsupported/ambiguous" in result["note"]


@pytest.mark.parametrize("mutation", ["union_name", "enum_code", "enum_name", "type_name"])
def test_duplicate_declarations_and_cases_do_not_choose_an_alternative(mutation):
    """Duplicate names/codes must not manufacture uniqueness by dropping the ambiguous type."""
    entries = interface(named(b"Errors") if mutation.startswith("enum") else None)
    if mutation == "union_name":
        entries[-2].udt_union_v0.cases.append(deepcopy(entries[-2].udt_union_v0.cases[0]))
    elif mutation == "type_name":
        entries.insert(0, deepcopy(entries[-2]))
    else:
        case = deepcopy(entries[2].udt_error_enum_v0.cases[0])
        if mutation == "enum_code":
            case.name = b"Other"
        else:
            case.value = xdr.Uint32(2)
        entries[2].udt_error_enum_v0.cases.append(case)
    other = deepcopy(entries[-1])
    other.event_v0.params[0].type = xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_VAL)
    entries.append(other)
    result = fields(contract_error() if mutation.startswith("enum") else variant("Idle"), entries)
    assert result["status"] == "Raw only" and "unsupported/ambiguous" in result["note"]


def test_error_enum_requires_scerror_while_ordinary_enum_requires_u32():
    """Correct the prior error-enum/U32 conflation without changing genuine ordinary enum behavior."""
    entries = interface(named(b"Errors"))
    assert fields(scval.to_uint32(1), entries)["status"] == "Raw only"
    assert fields(contract_error(), entries)["fields"][0]["value"]["case"] == "Invalid"
    ordinary = xdr.SCSpecEntry(
        xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_ENUM_V0,
        udt_enum_v0=xdr.SCSpecUDTEnumV0(
            b"", b"", b"Color", [xdr.SCSpecUDTEnumCaseV0(b"", b"Red", xdr.Uint32(1))]
        ),
    )
    entries.insert(0, ordinary)
    entries[-1].event_v0.params[0].type = named(b"Color")
    assert fields(scval.to_uint32(1), entries)["fields"][0]["value"]["case"] == "Red"
    assert fields(contract_error(), entries)["status"] == "Raw only"


def test_result_ok_does_not_accept_ambiguous_declared_error_cases():
    """An Ok payload cannot hide an ambiguous error declaration or outrank a plain scalar match."""
    entries = interface(result_type())
    entries[2].udt_error_enum_v0.cases.append(deepcopy(entries[2].udt_error_enum_v0.cases[0]))
    alternate = deepcopy(entries[-1])
    alternate.event_v0.params[0].type = NUMBER
    entries.append(alternate)
    result = fields(scval.to_int128(1), entries)
    assert result["status"] == "Raw only" and "unsupported/ambiguous" in result["note"]


@pytest.mark.parametrize("form", ["SINGLE_VALUE", "VEC", "MAP", "TOPIC"])
def test_nested_union_result_composites_across_event_locations(form):
    """Union payloads nest typed Result fields in all layouts and dynamic topics with exact values."""
    entries = interface(form="SINGLE_VALUE" if form == "TOPIC" else form)
    entries[-2].udt_union_v0.cases[1].tuple_case.type = [result_type()]
    value = variant("Count", scval.to_int128(2**100))
    topics = None
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
    result = fields(value, entries, topics=topics)
    decoded = result["fields"][0]["value"]
    assert decoded["values"][0]["branch"] == "Ok"
    assert decoded["values"][0]["value"]["value"] == str(2**100)


def test_nested_result_ok_union_and_err_nested_in_struct():
    """Result success can be a union, while a struct field can contain a typed error Result."""
    entries = interface(result_type(ok=named(b"State")))
    decoded = fields(variant("Idle"), entries)["fields"][0]["value"]
    assert decoded["branch"] == "Ok" and decoded["value"]["case"] == "Idle"
    entries = interface(named(b"Record"))
    entries[1].udt_struct_v0.fields[0].type = result_type()
    value = xdr.SCVal(
        xdr.SCValType.SCV_MAP,
        map=xdr.SCMap([xdr.SCMapEntry(scval.to_symbol("count"), contract_error())]),
    )
    decoded = fields(value, entries)["fields"][0]["value"]["fields"][0]["value"]
    assert decoded["branch"] == "Err" and decoded["value"]["case"] == "Invalid"


def test_recursive_union_depth_and_shared_work_limits_fail_closed():
    """A depth-limited alternative cannot be discarded in favor of a generic successful match."""
    # Result wrappers add interpretation depth without adding XDR container depth.
    # This reaches the interpreter's limit rather than merely failing XDR parsing.
    shape = named(b"State")
    for _ in range(8):
        shape = result_type(ok=shape, error=ERROR)
    entries = interface(shape)
    payload_shape = NUMBER
    for _ in range(8):
        payload_shape = result_type(ok=payload_shape, error=ERROR)
    entries[-2].udt_union_v0.cases[1].tuple_case.type = [payload_shape]
    value = variant("Count", scval.to_int128(1))
    generic = deepcopy(entries[-1])
    generic.event_v0.params[0].type = xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_VAL)
    entries.insert(0, generic)
    result = fields(value, entries)
    assert result["status"] == "Raw only" and "complexity limits" in result["note"]
    parsed = specification(interface())["entries"]
    with pytest.raises(ValueError, match="complexity"):
        typed(variant("Idle"), {"kind": "UDT", "name": "State"}, parsed, [2])


def test_shared_lookup_budget_exhaustion_discards_an_earlier_candidate():
    """Repeated named-type lookups are bounded across fields and all candidate declarations."""
    vector = xdr.SCSpecTypeDef(
        xdr.SCSpecType.SC_SPEC_TYPE_VEC, vec=xdr.SCSpecTypeVec(named(b"State"))
    )
    entries = interface(vector)
    template = deepcopy(entries[1])
    # Valid unused declarations increase lookup work while staying below the 256-entry cap.
    for number in range(60):
        entry = deepcopy(template)
        entry.udt_struct_v0.name = f"Unused{number}".encode()
        entries.insert(0, entry)
    generic = deepcopy(entries[-1])
    generic.event_v0.params[0].type = xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_VAL)
    entries.insert(0, generic)
    result = fields(scval.to_vec([variant("Idle")] * 100), entries)
    assert result["status"] == "Raw only" and "complexity limits" in result["note"]


@pytest.mark.parametrize("union", [False, True])
def test_overlapping_supported_types_remain_ambiguous(union):
    """A newly supported type does not outrank another matching declaration."""
    entries = interface() if union else interface(result_type())
    alternate = deepcopy(entries[-1])
    alternate.event_v0.params[0].type = (
        xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_VAL) if union else NUMBER
    )
    entries.append(alternate)
    result = fields(variant("Idle") if union else scval.to_int128(1), entries)
    assert result["status"] == "Raw only" and "Ambiguous" in result["note"]


@pytest.mark.parametrize("successful", [True, False, None])
def test_result_branch_never_overwrites_call_status_or_raw_evidence(successful):
    """An Ok payload in a failed call is still labeled failed; Err can occur in a successful call."""
    entries = interface(result_type())
    event = observed(contract_error(), successful_call=successful)
    original = deepcopy(event)
    result = interpret(event, specification(entries))
    assert result["fields"][0]["value"]["branch"] == "Err"
    assert result["call_status"] == (
        "Successful call"
        if successful is True
        else "Failed call; not evidence of applied changes"
        if successful is False
        else "Unknown call status"
    )
    assert event == original
    result = interpret(observed(scval.to_int128(1), successful_call=False), specification(entries))
    assert result["fields"][0]["value"]["branch"] == "Ok" and "Failed call" in result["call_status"]


def test_ui_rpc_export_and_saved_snapshot_preserve_union_result_evidence(tmp_path):
    """The real explicit spec/event flow exposes nested fields and freezes original evidence offline."""
    app = QApplication.instance() or QApplication([])

    async def scenario():
        """Load a pinned declared interface, interpret a nested event, export and save it once."""
        entries = interface()
        entries[-2].udt_union_v0.cases[1].tuple_case.type = [result_type()]
        value = variant("Count", contract_error())
        rpc, calls = rpc_fixture(module(entries), event_value=value)
        store = InvestigationStore(tmp_path / "cases.db")
        window = MainWindow(AccountService(object()), store, contract_provider=rpc)
        window.network.setCurrentText("Testnet")
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
        assert '"case": "Count"' in view.raw.toPlainText()
        assert '"branch": "Err"' in view.raw.toPlainText()
        exported = view.export_document()
        assert "Invalid" in serialize(exported, "csv")
        assert json.loads(serialize(exported, "json"))["records"][0]["value"] == raw[0]["value"]
        assert view.events == raw
        assert raw[0]["raw"]["value"] == raw[0]["value"]["xdr"] == value.to_xdr()
        window.investigations.name.setText("Nested payload")
        window.investigations.create()
        window.pages.setCurrentIndex(6)
        window.save_resource()
        saved = store.list()[0]["entries"][0]["evidence"]
        assert saved[0]["records"] == exported["records"]
        view.reset()
        assert saved[0]["records"][0]["interpretation"]["fields"][0]["value"]["case"] == "Count"
        assert len(calls) == 7  # Existing inspect/event/spec calls only; no type-driven RPC reads.
        window.close()

    asyncio.run(scenario())
    assert app is not None
