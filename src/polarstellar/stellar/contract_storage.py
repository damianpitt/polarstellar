"""Validate explicitly supplied storage keys and retain bounded, read-only RPC evidence."""

import base64
import json
import re
from datetime import UTC, datetime

from stellar_sdk import Address, StrKey, scval, xdr
from xdrlib3 import Unpacker

from polarstellar.stellar.contracts import decoded_value
from polarstellar.stellar.providers import AccountError

MAX_KEY_BYTES = 4096
MAX_ENTRY_BYTES = 262_144
DURABILITIES = {
    "Persistent": xdr.ContractDataDurability.PERSISTENT,
    "Temporary": xdr.ContractDataDurability.TEMPORARY,
}
COVERAGE = (
    "One explicitly supplied contract-data key and durability at RPC retrieval time. "
    "No storage enumeration, historical lookup, restoration or contract execution. "
    "RPC-reported evidence; not an independent consensus proof. Raw integers have no inferred units. "
    "No entry returned does not prove nonexistence: it may be absent, expired, archived or unavailable."
)


def bounded_xdr(encoded, cls, limit):
    """Decode canonical base64/XDR with a byte/depth bound and require full input consumption.

    Both user keys and RPC entry payloads pass here before further processing. Refusing
    trailing bytes, alternate padding and unsupported unions prevents a partial parse
    from silently accepting evidence different from the bytes saved in exports.
    """
    if not isinstance(encoded, str) or len(encoded) > 4 * ((limit + 2) // 3):
        raise ValueError("XDR exceeds the inspection size limit")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > limit or base64.b64encode(raw).decode("ascii") != encoded:
        raise ValueError("XDR must use canonical base64 within the size limit")
    unpacker = Unpacker(raw)
    value = cls.unpack(unpacker, depth_limit=32)
    if unpacker.get_position() != len(raw) or value.to_xdr_bytes() != raw:
        raise ValueError("XDR is incomplete, noncanonical or contains trailing bytes")
    return value


def check_key(value, budget=None, depth=0):
    """Reject reserved/internal values anywhere in a supplied key, with bounded traversal.

    Instance storage already has its own inspector. Internal instance/nonce values
    are not accepted as arbitrary user keys, including inside vectors or maps.
    Muxed account addresses are prohibited in Soroban storage keys, even when nested.
    Refuse those keys locally rather than removing their routing ID or querying an alias.
    The remaining shape is preserved exactly; no application-specific meaning is assumed.
    """
    budget = [512] if budget is None else budget
    budget[0] -= 1
    if depth > 16 or budget[0] < 0:
        raise ValueError("Storage key exceeds nesting or node limits")
    if value.type in (
        xdr.SCValType.SCV_CONTRACT_INSTANCE,
        xdr.SCValType.SCV_LEDGER_KEY_CONTRACT_INSTANCE,
        xdr.SCValType.SCV_LEDGER_KEY_NONCE,
    ):
        raise ValueError("Reserved instance/nonce values are not user storage keys")
    if (
        value.type == xdr.SCValType.SCV_ADDRESS
        and value.address is not None
        and value.address.type == xdr.SCAddressType.SC_ADDRESS_TYPE_MUXED_ACCOUNT
    ):
        raise ValueError("Muxed accounts cannot be used as contract storage keys")
    if value.type == xdr.SCValType.SCV_VEC:
        if value.vec is None:
            raise ValueError("Null key vector")
        for item in value.vec.sc_vec:
            check_key(item, budget, depth + 1)
    if value.type == xdr.SCValType.SCV_MAP:
        if value.map is None:
            raise ValueError("Null key map")
        keys = [pair.key.to_xdr() for pair in value.map.sc_map]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate map keys")
        for pair in value.map.sc_map:
            check_key(pair.key, budget, depth + 1)
            check_key(pair.val, budget, depth + 1)


def entered_key(text, mode):
    """Convert an explicit symbol or ScVal XDR into a checked canonical key, without I/O."""
    try:
        if mode == "Symbol":
            # Symbols are exact identifiers: do not silently remove meaningful input.
            if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,32}", text):
                raise ValueError("Symbol must contain 1–32 ASCII letters, digits or underscores")
            value = scval.to_symbol(text)
        elif mode == "ScVal XDR":
            value = bounded_xdr(text.strip(), xdr.SCVal, MAX_KEY_BYTES)
        else:
            raise ValueError("Choose Symbol or ScVal XDR")
        check_key(value)
        return value.to_xdr()
    except Exception as exc:
        raise AccountError(
            "Enter a symbol (1–32 ASCII letters, digits or underscores), or canonical base64 "
            "ScVal XDR up to 4096 bytes. Reserved, muxed-account, malformed or overly complex keys are refused."
        ) from exc


def storage_key(contract, key_xdr, durability):
    """Build one exact contract-data LedgerKey after validating C-address, key and durability."""
    try:
        if not isinstance(contract, str) or not StrKey.is_valid_contract(contract):
            raise ValueError("Invalid contract ID")
        if durability not in DURABILITIES:
            raise ValueError("Choose persistent or temporary storage")
        value = bounded_xdr(key_xdr, xdr.SCVal, MAX_KEY_BYTES)
        check_key(value)
        return xdr.LedgerKey(
            xdr.LedgerEntryType.CONTRACT_DATA,
            contract_data=xdr.LedgerKeyContractData(
                Address(contract).to_xdr_sc_address(), value, DURABILITIES[durability]
            ),
        )
    except Exception as exc:
        raise AccountError("Invalid contract storage key or durability; no lookup sent.") from exc


def ledger_number(value):
    """Require a real uint32 ledger number; booleans and out-of-range values are invalid."""
    if type(value) is not int or not 0 <= value <= 2**32 - 1:
        raise ValueError("Invalid ledger number")
    return value


def result_document(data, contract, network, source, key, durability):
    """Bind a single-key RPC response to its complete request and retain exact evidence.

    The endpoint's reported latest ledger is distinct from both the earlier contract
    inspection and event window. Missing entries remain uncertain. Returned entries
    must match outer and inner identities; malformed metadata rejects the whole read.
    """
    try:
        latest = ledger_number(data["latestLedger"])
        entries = data["entries"]
        if not isinstance(entries, list) or len(entries) > 1:
            raise ValueError("Expected zero or one entry")
        result = {
            "contract": contract,
            "network": network,
            "source": source,
            "fetched_at": datetime.now(UTC).isoformat(),
            "requested_key_xdr": key.contract_data.key.to_xdr(),
            "requested_key": decoded_value(key.contract_data.key.to_xdr()),
            "durability": durability,
            "ledger_key_xdr": key.to_xdr(),
            "latest_ledger": latest,
            "status": "No entry returned",
            "coverage": COVERAGE,
            "entry": None,
        }
        if not entries:
            return result
        entry = entries[0]
        if entry["key"] != key.to_xdr():
            raise ValueError("RPC returned another LedgerKey")
        ledger = bounded_xdr(entry["xdr"], xdr.LedgerEntryData, MAX_ENTRY_BYTES)
        body = ledger.contract_data
        expected = key.contract_data
        if (
            ledger.type != xdr.LedgerEntryType.CONTRACT_DATA
            or body is None
            or body.contract != expected.contract
            or body.key != expected.key
            or body.durability != expected.durability
        ):
            raise ValueError("Returned entry does not match the requested contract/key/durability")
        modified = ledger_number(entry["lastModifiedLedgerSeq"])
        if modified > latest:
            raise ValueError("Entry modification is newer than RPC latest ledger")
        until = (
            ledger_number(entry["liveUntilLedgerSeq"]) if "liveUntilLedgerSeq" in entry else None
        )
        result["status"] = (
            "Returned; TTL unknown"
            if until is None
            else "Returned; TTL expired"
            if until < latest or until == 0
            else "Returned; live at reported ledger"
        )
        # An expired/unknown TTL never erases returned raw evidence or becomes a live claim.
        result["entry"] = {
            "last_modified_ledger": modified,
            "live_until_ledger": until,
            "value": decoded_value(body.val.to_xdr()),
            "raw_entry_xdr": entry["xdr"],
            "raw_rpc_entry": dict(entry),
        }
        return result
    except Exception as exc:
        raise AccountError(
            "RPC returned invalid, oversized or mismatched storage evidence. Previous result retained."
        ) from exc


def readable(result):
    """Present the accepted request/result first and original RPC entry evidence below it."""
    lines = [
        f"Last accepted lookup: {result['status']}",
        f"{result['network']} • {result['durability']} • RPC latest ledger: {result['latest_ledger']}",
    ]
    if result["entry"] is not None:
        entry = result["entry"]
        lines += [
            # Keep common decoded values above long encodings; exact XDR remains below.
            "Value: "
            + json.dumps(
                {key: value for key, value in entry["value"].items() if key != "xdr"},
                ensure_ascii=False,
            ),
            f"Last modified ledger: {entry['last_modified_ledger']}",
            "Live until ledger: "
            + (
                "Not provided"
                if entry["live_until_ledger"] is None
                else str(entry["live_until_ledger"])
            ),
        ]
    lines += [
        f"Contract: {result['contract']}",
        f"Source: {result['source']} • Retrieved: {result['fetched_at']}",
        "Requested key: " + json.dumps(result["requested_key"], ensure_ascii=False),
        "LedgerKey XDR: " + result["ledger_key_xdr"],
    ]
    lines += ["", result["coverage"], "", "ORIGINAL RPC ENTRY EVIDENCE"]
    lines.append(json.dumps(result["entry"], ensure_ascii=False, indent=2))
    return "\n".join(lines)
