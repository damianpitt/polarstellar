"""Bounded, read-only contract interface extraction and conservative event-shape interpretation."""

import base64
import hashlib

from stellar_sdk import scval, xdr
from xdrlib3 import Unpacker

MAX_WASM = 2_000_000
MAX_SPEC = 262_144
MAX_ENTRIES = 256


class UnsupportedShape(ValueError):
    """A potentially matching declaration cannot be safely interpreted with this decoder."""


def leb(data, position):
    """Read one bounded unsigned WASM section length, refusing overflow and truncated bytes."""
    result = 0
    for shift in range(0, 35, 7):
        if position >= len(data):
            raise ValueError("Truncated WASM length")
        value = data[position]
        position += 1
        result |= (value & 127) << shift
        if not value & 128:
            if result > 2**32 - 1:
                raise ValueError("WASM length overflow")
            return result, position
    raise ValueError("WASM length exceeds five bytes")


def text(value):
    """Decode author-supplied names/docs strictly; they are displayed as plain text, never instructions."""
    return value.decode("utf-8")


def type_description(value, depth=0):
    """Describe scalar/composite/declared types without guessing units or contract behavior."""
    if depth > 16:
        raise ValueError("Specification type nesting exceeds limit")
    kind = value.type.name.removeprefix("SC_SPEC_TYPE_")
    result = {"kind": kind}
    if kind == "OPTION":
        result["value"] = type_description(value.option.value_type, depth + 1)
    elif kind == "RESULT":
        result.update(
            ok=type_description(value.result.ok_type, depth + 1),
            error=type_description(value.result.error_type, depth + 1),
        )
    elif kind == "VEC":
        result["element"] = type_description(value.vec.element_type, depth + 1)
    elif kind == "MAP":
        result.update(
            key=type_description(value.map.key_type, depth + 1),
            value=type_description(value.map.value_type, depth + 1),
        )
    elif kind == "TUPLE":
        result["items"] = [type_description(item, depth + 1) for item in value.tuple.value_types]
    elif kind == "BYTES_N":
        result["length"] = value.bytes_n.n.uint32
    elif kind == "UDT":
        result["name"] = text(value.udt.name)
    return result


def describe(entry):
    """Convert one function/event/type declaration into readable data while preserving its exact XDR."""
    kind = entry.kind.name.removeprefix("SC_SPEC_ENTRY_")
    body = getattr(entry, kind.lower())
    name = body.name.sc_symbol if hasattr(body.name, "sc_symbol") else body.name
    result = {"kind": kind, "name": text(name), "doc": text(body.doc), "xdr": entry.to_xdr()}
    if hasattr(body, "lib"):
        result["library"] = text(body.lib)
    if kind == "FUNCTION_V0":
        result["inputs"] = [
            {"name": text(item.name), "doc": text(item.doc), "type": type_description(item.type)}
            for item in body.inputs
        ]
        result["outputs"] = [type_description(item) for item in body.outputs]
    elif kind == "EVENT_V0":
        result["prefix_topics"] = [text(item.sc_symbol) for item in body.prefix_topics]
        result["data_format"] = body.data_format.name.removeprefix("SC_SPEC_EVENT_DATA_FORMAT_")
        result["params"] = [
            {
                "name": text(item.name),
                "doc": text(item.doc),
                "type": type_description(item.type),
                "location": item.location.name.removeprefix("SC_SPEC_EVENT_PARAM_LOCATION_"),
            }
            for item in body.params
        ]
    elif kind == "UDT_STRUCT_V0":
        result["fields"] = [
            {"name": text(item.name), "doc": text(item.doc), "type": type_description(item.type)}
            for item in body.fields
        ]
    elif kind == "UDT_UNION_V0":
        cases = []
        for item in body.cases:
            case = item.void_case or item.tuple_case
            cases.append(
                {
                    "name": text(case.name),
                    "doc": text(case.doc),
                    "types": [type_description(t) for t in case.type] if item.tuple_case else [],
                }
            )
        result["cases"] = cases
    else:
        result["cases"] = [
            {"name": text(item.name), "doc": text(item.doc), "value": item.value.uint32}
            for item in body.cases
        ]
    return result


def extract(wasm):
    """Locate exactly one contractspecv0 custom section and parse bounded canonical XDR entries.

    This reads bytes only: no WASM compilation, execution, imports or function calls.
    Missing specs are a supported result; malformed/oversized specs raise without
    replacing the already usable contract instance/events in the owning view.
    """
    if len(wasm) > MAX_WASM or wasm[:8] != b"\x00asm\x01\x00\x00\x00":
        raise ValueError("Invalid or oversized WASM module")
    position, sections, payload = 8, 0, None
    while position < len(wasm):
        sections += 1
        if sections > 4096:
            raise ValueError("Too many WASM sections")
        kind = wasm[position]
        position += 1
        size, position = leb(wasm, position)
        end = position + size
        if end > len(wasm):
            raise ValueError("Truncated WASM section")
        if kind == 0:
            length, offset = leb(wasm, position)
            if offset + length > end:
                raise ValueError("Invalid custom section name")
            name = text(wasm[offset : offset + length])
            if name == "contractspecv0":
                if payload is not None:
                    raise ValueError("Duplicate contract specification section")
                payload = wasm[offset + length : end]
        position = end
    if payload is None:
        return {"status": "No embedded specification", "entries": [], "spec_sha256": None}
    if len(payload) > MAX_SPEC:
        raise ValueError("Specification exceeds 256 KiB")
    unpacker, entries = Unpacker(payload), []
    while unpacker.get_position() < len(payload):
        if len(entries) >= MAX_ENTRIES:
            raise ValueError("Specification exceeds 256 entries")
        start = unpacker.get_position()
        entry = xdr.SCSpecEntry.unpack(unpacker, depth_limit=32)
        if entry.to_xdr_bytes() != payload[start : unpacker.get_position()]:
            raise ValueError("Noncanonical specification XDR")
        entries.append(describe(entry))
    # Duplicate declarations are retained for inspection. Interpreting ambiguous
    # event names/type references must never silently choose the first definition.
    return {
        "status": "Available" if entries else "Empty embedded specification",
        "entries": entries,
        "spec_sha256": hashlib.sha256(payload).hexdigest(),
    }


def sc_value(encoded):
    """Decode bounded canonical ScVal XDR with an explicit recursion limit before interpretation."""
    if not isinstance(encoded, str) or len(encoded) > 90_000:
        raise ValueError("Event value too large")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > 65_536:
        raise ValueError("Event value too large")
    unpacker = Unpacker(raw)
    value = xdr.SCVal.unpack(unpacker, depth_limit=32)
    if unpacker.get_position() != len(raw) or value.to_xdr_bytes() != raw:
        raise ValueError("Invalid ScVal XDR")
    return value


def typed(value, definition, declarations, budget, depth=0):
    """Match supported declared shapes and preserve exact integers; refuse unknown or ambiguous encodings."""
    budget[0] -= 1
    if depth > 16 or budget[0] < 0:
        raise ValueError("Interpretation complexity limit")
    kind = definition["kind"]
    actual = value.type.name.removeprefix("SCV_")

    def nested(item, shape):
        """Share one per-event node budget across every recursively decoded field."""
        return typed(item, shape, declarations, budget, depth + 1)

    if kind == "VAL":
        return {
            "type": "VAL",
            "xdr": value.to_xdr(),
            "note": "Generic value; no application meaning inferred",
        }
    if kind in (
        "BOOL",
        "VOID",
        "U32",
        "I32",
        "U64",
        "I64",
        "U128",
        "I128",
        "U256",
        "I256",
        "TIMEPOINT",
        "DURATION",
        "STRING",
        "SYMBOL",
        "ADDRESS",
        "BYTES",
        "ERROR",
    ):
        if actual != kind:
            raise ValueError("Declared scalar type mismatch")
        native = scval.to_native(value)
        if type(native) is int:
            native = str(native)
        elif isinstance(native, bytes):
            native = {"bytes_hex": native.hex()}
        elif hasattr(native, "address"):
            native = native.address
        elif native is not None and not isinstance(native, (str, bool)):
            raise UnsupportedShape("Unsupported scalar representation")
        return {"type": kind, "value": native}
    if kind == "BYTES_N":
        if actual != "BYTES" or len(value.bytes.sc_bytes) != definition["length"]:
            raise ValueError("Fixed bytes mismatch")
        return {"type": kind, "bytes_hex": value.bytes.sc_bytes.hex()}
    if kind == "OPTION":
        return {
            "type": kind,
            "value": None if actual == "VOID" else nested(value, definition["value"]),
        }
    if kind in ("VEC", "TUPLE"):
        if actual != "VEC" or value.vec is None:
            raise ValueError("Vector mismatch")
        values = value.vec.sc_vec
        shapes = [definition["element"]] * len(values) if kind == "VEC" else definition["items"]
        if len(values) != len(shapes):
            raise ValueError("Tuple length mismatch")
        return {
            "type": kind,
            "values": [nested(item, shape) for item, shape in zip(values, shapes, strict=True)],
        }
    if kind == "MAP":
        if actual != "MAP" or value.map is None:
            raise ValueError("Map mismatch")
        keys = [pair.key.to_xdr() for pair in value.map.sc_map]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate map keys")
        return {
            "type": kind,
            "entries": [
                {
                    "key": nested(pair.key, definition["key"]),
                    "value": nested(pair.val, definition["value"]),
                }
                for pair in value.map.sc_map
            ],
        }
    if kind == "UDT":
        choices = [
            item
            for item in declarations
            if item["kind"].startswith("UDT_") and item["name"] == definition["name"]
        ]
        if len(choices) != 1:
            raise UnsupportedShape("Unknown/ambiguous user type")
        declaration = choices[0]
        if declaration["kind"] in ("UDT_ENUM_V0", "UDT_ERROR_ENUM_V0"):
            if actual != "U32":
                raise ValueError("Enum encoding mismatch")
            cases = [item for item in declaration["cases"] if item["value"] == value.u32.uint32]
            if len(cases) != 1:
                raise ValueError("Unknown/ambiguous enum case")
            return {
                "type": definition["name"],
                "case": cases[0]["name"],
                "value": str(value.u32.uint32),
            }
        if declaration["kind"] == "UDT_STRUCT_V0":
            fields = declaration["fields"]
            if len({item["name"] for item in fields}) != len(fields):
                raise UnsupportedShape("Ambiguous struct fields")
            # Named structs use symbol-keyed maps; tuple structs use numeric names.
            if fields and all(item["name"] == str(index) for index, item in enumerate(fields)):
                return nested(value, {"kind": "TUPLE", "items": [item["type"] for item in fields]})
            if actual != "MAP" or value.map is None:
                raise ValueError("Struct encoding mismatch")
            values = symbol_map(value)
            if set(values) != {item["name"] for item in fields}:
                raise ValueError("Struct fields mismatch")
            return {
                "type": definition["name"],
                "fields": [
                    {"name": item["name"], "value": nested(values[item["name"]], item["type"])}
                    for item in fields
                ],
            }
    raise UnsupportedShape("Unsupported declared shape; raw evidence retained")


def symbol_map(value):
    """Read a symbol-keyed event/struct map without flattening duplicate or unexpected keys."""
    if value.type != xdr.SCValType.SCV_MAP or value.map is None:
        raise ValueError("Expected map")
    result = {}
    for pair in value.map.sc_map:
        if pair.key.type != xdr.SCValType.SCV_SYMBOL:
            raise ValueError("Expected symbol map key")
        name = text(pair.key.sym.sc_symbol)
        if name in result:
            raise ValueError("Duplicate symbol key")
        result[name] = pair.val
    return result


def interpret(event, specification):
    """Annotate a uniquely matching declared event shape, never asserting historical ABI or financial semantics."""
    base = {
        "status": "Raw only",
        "note": "No compatible specification loaded; no application meaning inferred.",
    }
    successful = event.get("successful_call")
    base["call_status"] = (
        "Successful call"
        if successful is True
        else "Failed call; not evidence of applied changes"
        if successful is False
        else "Unknown call status"
    )
    if not specification or specification.get("status") != "Available":
        return base
    base.update(
        wasm_hash=specification["wasm_hash"],
        spec_sha256=specification["spec_sha256"],
        contract=specification.get("contract"),
        network=specification.get("network"),
    )
    if not event.get("contract") or event["contract"] != specification.get("contract"):
        return {
            **base,
            "note": "Event contract identity is missing or differs from the inspected specification; raw evidence retained.",
        }
    try:
        if len(event["topics"]) > 4:
            raise ValueError("Too many event topics")
        topics = [sc_value(item["xdr"]) for item in event["topics"]]
        value = sc_value(event["value"]["xdr"])
        matches, unsupported, budget = [], False, [4096]
        for declaration in specification["entries"]:
            budget[0] -= 1
            if budget[0] < 0:
                raise ValueError("Event interpretation complexity limit")
            if declaration["kind"] != "EVENT_V0":
                continue
            try:
                prefix = declaration["prefix_topics"]
                if len(topics) < len(prefix):
                    continue
                if any(
                    topic.type not in (xdr.SCValType.SCV_SYMBOL, xdr.SCValType.SCV_STRING)
                    or scval.to_native(topic) != name
                    for topic, name in zip(topics, prefix, strict=False)
                ):
                    continue
                topic_params = [
                    item for item in declaration["params"] if item["location"] == "TOPIC_LIST"
                ]
                data_params = [item for item in declaration["params"] if item["location"] == "DATA"]
                names = [item["name"] for item in declaration["params"]]
                if len(set(names)) != len(names) or len(topics) != len(prefix) + len(topic_params):
                    continue
                form = declaration["data_format"]
                if form == "SINGLE_VALUE":
                    if not data_params and value.type == xdr.SCValType.SCV_VOID:
                        data = []
                    elif len(data_params) == 1:
                        data = [value]
                    else:
                        continue
                elif (
                    form == "VEC" and value.type == xdr.SCValType.SCV_VEC and value.vec is not None
                ):
                    data = value.vec.sc_vec
                elif form == "MAP":
                    mapping = symbol_map(value)
                    if set(mapping) != {item["name"] for item in data_params}:
                        continue
                    data = [mapping[item["name"]] for item in data_params]
                else:
                    continue
                if len(data) != len(data_params):
                    continue
                fields = []
                for parameter, raw in [
                    *zip(topic_params, topics[len(prefix) :], strict=True),
                    *zip(data_params, data, strict=True),
                ]:
                    fields.append(
                        {
                            "name": parameter["name"],
                            "location": parameter["location"],
                            "declared_type": parameter["type"],
                            "value": typed(
                                raw, parameter["type"], specification["entries"], budget
                            ),
                        }
                    )
                matches.append({"name": declaration["name"], "fields": fields})
            except UnsupportedShape:
                unsupported = True
                continue
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
        if budget[0] < 0:
            raise ValueError("Event interpretation complexity limit")
        if unsupported:
            return {
                **base,
                "note": "A potentially matching declaration has an unsupported/ambiguous type; raw evidence retained.",
            }
        if len(matches) == 1:
            return {
                **base,
                "status": "Candidate match",
                **matches[0],
                "note": "Shape matches the inspected executable's declared specification. Historical compatibility, author claims, token decimals and financial meaning are unverified.",
            }
        base["note"] = (
            "Ambiguous matching event declarations; raw evidence retained."
            if matches
            else "No supported declared event shape matches; raw evidence retained."
        )
    except Exception:  # noqa: BLE001 -- Unknown/malformed future XDR must remain visible as raw event evidence.
        base["note"] = (
            "Event value exceeds supported decoding/complexity limits; raw evidence retained."
        )
    return base


def type_label(shape):
    """Render a compact interface type without implying units or financial meaning."""
    kind = shape["kind"]
    if kind == "UDT":
        return shape["name"]
    if kind == "OPTION":
        return f"Option<{type_label(shape['value'])}>"
    if kind == "RESULT":
        return f"Result<{type_label(shape['ok'])}, {type_label(shape['error'])}>"
    if kind == "VEC":
        return f"Vec<{type_label(shape['element'])}>"
    if kind == "MAP":
        return f"Map<{type_label(shape['key'])}, {type_label(shape['value'])}>"
    if kind == "TUPLE":
        return "(" + ", ".join(type_label(item) for item in shape["items"]) + ")"
    if kind == "BYTES_N":
        return f"Bytes[{shape['length']}]"
    return kind.lower()


def readable(specification):
    """Present author-declared signatures, documentation, types and event field locations as plain English."""
    lines = [
        specification["status"],
        specification["coverage"],
        f"WASM hash: {specification.get('wasm_hash')}",
        f"Source: {specification['source']} • Retrieved: {specification['fetched_at']}",
        "Author-declared interface; documentation is not verified behavior. Exact declaration XDR is retained in specification exports.",
    ]
    for entry in specification["entries"]:
        kind, name = entry["kind"], entry["name"]
        lines += ["", f"{kind.removesuffix('_V0').replace('_', ' ').title()}: {name}"]
        if entry.get("library"):
            lines.append("Declared library: " + entry["library"])
        if kind == "FUNCTION_V0":
            inputs = ", ".join(
                f"{item['name']}: {type_label(item['type'])}" for item in entry["inputs"]
            )
            lines.append(
                f"{name}({inputs}) → "
                + (", ".join(type_label(item) for item in entry["outputs"]) or "void")
            )
            for item in entry["inputs"]:
                if item["doc"]:
                    lines.append(f"  {item['name']}: {item['doc']}")
        elif kind == "EVENT_V0":
            lines += [
                "Prefix topics: " + (", ".join(entry["prefix_topics"]) or "None"),
                "Data layout: " + entry["data_format"],
            ]
            lines += [
                f"  {item['name']}: {type_label(item['type'])} • {item['location']} • {item['doc']}"
                for item in entry["params"]
            ]
        elif kind == "UDT_STRUCT_V0":
            lines += [
                f"  {item['name']}: {type_label(item['type'])} • {item['doc']}"
                for item in entry["fields"]
            ]
        else:
            for item in entry["cases"]:
                value = (
                    " = " + str(item["value"])
                    if "value" in item
                    else "(" + ", ".join(type_label(t) for t in item["types"]) + ")"
                )
                lines.append(f"  {item['name']}{value} • {item['doc']}")
        if entry["doc"]:
            lines.append("Author documentation: " + entry["doc"])
    return "\n".join(lines)
