"""Read-only Stellar RPC contract instances and bounded contract-event history."""

import hashlib
import re
from datetime import UTC, datetime

import httpx
from stellar_sdk import Address, StrKey, scval, xdr
from stellar_sdk import Network as SDKNetwork

from polarstellar.stellar.contract_addresses import address_fields
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError

ENDPOINTS = {
    Network.MAINNET: "https://soroban-rpc.mainnet.stellar.gateway.fm",
    Network.TESTNET: "https://soroban-testnet.stellar.org",
}
PASSPHRASES = {
    Network.MAINNET: SDKNetwork.PUBLIC_NETWORK_PASSPHRASE,
    Network.TESTNET: SDKNetwork.TESTNET_NETWORK_PASSPHRASE,
}
EVENT_LIMIT = 20
MAX_EVENT_PAGES = 50


def validate_contract(value):
    """Require a checksum-valid C-address before sending any RPC request."""
    value = value.strip()
    if not StrKey.is_valid_contract(value):
        raise AccountError("Enter a valid Stellar contract C-address with a correct checksum.")
    return value


def instance_key(contract):
    """Build the known persistent contract-instance key; this does not enumerate storage."""
    return xdr.LedgerKey(
        xdr.LedgerEntryType.CONTRACT_DATA,
        contract_data=xdr.LedgerKeyContractData(
            Address(contract).to_xdr_sc_address(),
            xdr.SCVal(xdr.SCValType.SCV_LEDGER_KEY_CONTRACT_INSTANCE),
            xdr.ContractDataDurability.PERSISTENT,
        ),
    )


def integer(value):
    """Reject boolean, negative and noninteger ledger metadata rather than guessing."""
    if type(value) is not int or value < 0:
        raise ValueError("Invalid ledger number")
    return value


def decoded_value(encoded):
    """Describe common scalar ScVals losslessly; keep complex/unknown values as raw XDR.

    Integers have no implied token decimals. In particular, event integers are not
    automatically treated as monetary amounts or divided by seven decimal places.
    Address scalars retain their exact G/C/M encoding; muxed accounts also expose
    their underlying G-account and exact routing ID without creating account aliases.
    """
    result = {"xdr": encoded}
    try:
        value = xdr.SCVal.from_xdr(encoded)
        result["type"] = value.type.name
        if value.type == xdr.SCValType.SCV_ADDRESS:
            # A successfully parsed prefix is not a valid address payload if bytes
            # were ignored or the original encoding was noncanonical.
            if value.to_xdr() != encoded:
                raise ValueError("Noncanonical or trailing address XDR")
            try:
                result.update(address_fields(value.address))
            except (ValueError, AttributeError, AssertionError):
                result["note"] = "Unsupported contract address variant; original XDR retained."
            return result
        native = scval.to_native(value)
        if type(native) is int:
            result["value"] = str(native)
        elif native is None or isinstance(native, (str, bool)):
            result["value"] = native
        elif isinstance(native, bytes):
            result["bytes_hex"] = native.hex()
        elif isinstance(native, Address):
            result["value"] = native.address
        else:
            result["note"] = (
                "Structured value retained in raw XDR; no application-specific interpretation."
            )
    except Exception:  # noqa: BLE001 -- Unsupported future XDR must remain inspectable as raw evidence.
        result["note"] = "Unable to decode with this SDK; original XDR retained."
    return result


class ContractProvider:
    """Use public RPC endpoints with network verification and no transaction-writing methods."""

    def __init__(self, transport=None):
        """Allow deterministic transports in tests; production uses normal HTTPS requests."""
        self.transport = transport

    async def request(self, client, network, method, params=None):
        """Call an allowlisted read-only method and turn transport/RPC failures into safe UI errors."""
        if method not in ("getNetwork", "getHealth", "getLedgerEntries", "getEvents"):
            raise ValueError("RPC method is not read-only or is unsupported.")
        try:
            response = await client.post(
                ENDPOINTS[network],
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
            )
            if response.status_code == 429:
                raise AccountError("RPC rate limit reached. Wait and retry.")
            response.raise_for_status()
            if len(response.content) > 5_000_000:
                raise AccountError("RPC response exceeds the inspection size limit.")
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError("Invalid RPC envelope")
            if payload.get("jsonrpc") != "2.0" or payload.get("id") != 1:
                raise ValueError("Invalid RPC envelope")
            if "error" in payload:
                raise AccountError(
                    "RPC could not serve this request. Event history may have expired; "
                    "retry or inspect again to start a new ledger window."
                )
            result = payload["result"]
            if not isinstance(result, dict):
                raise TypeError("Invalid RPC result")
            return result
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise AccountError(
                "Unable to read valid RPC data. Check connectivity and retry."
            ) from exc

    async def verify(self, client, network):
        """Check endpoint passphrase on every lookup/page so networks cannot silently mix."""
        result = await self.request(client, network, "getNetwork")
        if result.get("passphrase") != PASSPHRASES[network]:
            raise AccountError(
                "RPC endpoint serves a different network. No contract data accepted."
            )

    async def inspect(self, value, network):
        """Read the contract instance and retention range; absent instance is not proof of nonexistence."""
        contract = validate_contract(value)
        key = instance_key(contract)
        async with httpx.AsyncClient(transport=self.transport, timeout=20) as client:
            await self.verify(client, network)
            health = await self.request(client, network, "getHealth")
            data = await self.request(client, network, "getLedgerEntries", {"keys": [key.to_xdr()]})
        try:
            oldest, latest = integer(health["oldestLedger"]), integer(health["latestLedger"])
            if oldest > latest:
                raise ValueError("Invalid retention range")
            result = {
                "contract": contract,
                "network": network.value,
                "source": ENDPOINTS[network],
                "fetched_at": datetime.now(UTC).isoformat(),
                "cache_status": "Live RPC data",
                "oldest_available_ledger": oldest,
                "latest_available_ledger": latest,
                "instance_latest_ledger": integer(data["latestLedger"]),
                "coverage": "Known contract instance only; not arbitrary storage, source code, "
                "function discovery, or proof of ownership. Events have provider-limited retention.",
            }
            entries = data.get("entries", [])
            if not isinstance(entries, list) or len(entries) > 1:
                raise ValueError("Invalid instance response")
            if not entries:
                result.update(
                    instance_available=False,
                    warning="No live instance returned. It may be "
                    "absent, archived, or unavailable on this network; events can still be queried.",
                )
                return result
            entry = entries[0]
            if entry["key"] != key.to_xdr():
                raise ValueError("Wrong contract key")
            ledger = xdr.LedgerEntryData.from_xdr(entry["xdr"])
            body = ledger.contract_data
            if (
                ledger.type != xdr.LedgerEntryType.CONTRACT_DATA
                or body is None
                or (
                    body.contract != key.contract_data.contract
                    or body.key != key.contract_data.key
                    or body.durability != xdr.ContractDataDurability.PERSISTENT
                    or body.val.type != xdr.SCValType.SCV_CONTRACT_INSTANCE
                )
            ):
                raise ValueError("Wrong contract instance")
            instance = body.val.instance
            executable = instance.executable
            result.update(
                instance_available=True,
                executable=executable.type.name,
                wasm_hash=executable.wasm_hash.hash.hex() if executable.wasm_hash else None,
                last_modified_ledger=integer(entry["lastModifiedLedgerSeq"]),
                live_until_ledger=integer(entry["liveUntilLedgerSeq"])
                if "liveUntilLedgerSeq" in entry
                else None,
                instance_storage=[
                    {
                        "key": decoded_value(pair.key.to_xdr()),
                        "value": decoded_value(pair.val.to_xdr()),
                    }
                    for pair in instance.storage.sc_map
                ]
                if instance.storage
                else [],
                raw_instance_xdr=entry["xdr"],
            )
            return result
        except Exception as exc:
            raise AccountError("RPC returned an invalid or unsupported contract instance.") from exc

    async def specification(self, snapshot, network):
        """Explicitly fetch code bound to the inspected executable, verifying a fresh instance alongside it.

        The instance and code are requested together. Changed/archived executables,
        wrong keys or code hashes are refused rather than labeling another interface
        as belonging to this inspection. This does not prove historical event ABI.
        """
        from polarstellar.stellar.contract_specs import MAX_WASM, extract

        contract = validate_contract(snapshot["contract"])
        if snapshot["network"] != network.value or snapshot["source"] != ENDPOINTS[network]:
            raise AccountError("Specification context does not match this RPC network.")
        base = {
            "contract": contract,
            "network": network.value,
            "source": ENDPOINTS[network],
            "fetched_at": datetime.now(UTC).isoformat(),
            "entries": [],
            "wasm_hash": snapshot.get("wasm_hash"),
            "coverage": "Declared interface of the inspected executable; author documentation is untrusted. Not proof of behavior, ownership or historical event compatibility. No contract execution.",
        }
        if (
            not snapshot.get("instance_available")
            or snapshot.get("executable") != "CONTRACT_EXECUTABLE_WASM"
        ):
            return {**base, "status": "Unavailable for absent/native instance", "spec_sha256": None}
        digest = snapshot.get("wasm_hash")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise AccountError("Invalid inspected WASM hash. Inspect again.")
        instance = instance_key(contract)
        code_key = xdr.LedgerKey(
            xdr.LedgerEntryType.CONTRACT_CODE,
            contract_code=xdr.LedgerKeyContractCode(xdr.Hash(bytes.fromhex(digest))),
        )
        async with httpx.AsyncClient(transport=self.transport, timeout=20) as client:
            await self.verify(client, network)
            data = await self.request(
                client,
                network,
                "getLedgerEntries",
                {"keys": [instance.to_xdr(), code_key.to_xdr()]},
            )
        try:
            entries = data["entries"]
            if not isinstance(entries, list) or len(entries) != 2:
                raise ValueError("Code or instance unavailable")
            keyed = {entry["key"]: entry for entry in entries}
            if set(keyed) != {instance.to_xdr(), code_key.to_xdr()}:
                raise ValueError("Wrong ledger keys")
            fresh = xdr.LedgerEntryData.from_xdr(keyed[instance.to_xdr()]["xdr"])
            body = fresh.contract_data
            if (
                fresh.type != xdr.LedgerEntryType.CONTRACT_DATA
                or body is None
                or body.contract != instance.contract_data.contract
                or body.key != instance.contract_data.key
                or body.durability != xdr.ContractDataDurability.PERSISTENT
                or body.val.type != xdr.SCValType.SCV_CONTRACT_INSTANCE
            ):
                raise ValueError("Wrong refreshed instance")
            executable = body.val.instance.executable
            if (
                executable.type != xdr.ContractExecutableType.CONTRACT_EXECUTABLE_WASM
                or executable.wasm_hash.hash.hex() != digest
            ):
                raise ValueError("Executable changed since inspection")
            code_entry = keyed[code_key.to_xdr()]
            ledger = xdr.LedgerEntryData.from_xdr(code_entry["xdr"])
            code = ledger.contract_code
            if (
                ledger.type != xdr.LedgerEntryType.CONTRACT_CODE
                or code is None
                or code.hash.hash.hex() != digest
                or len(code.code) > MAX_WASM
                or hashlib.sha256(code.code).hexdigest() != digest
            ):
                raise ValueError("WASM hash/content mismatch or size limit")
            latest = integer(data["latestLedger"])
            modified = integer(code_entry["lastModifiedLedgerSeq"])
            if (
                modified > latest
                or integer(keyed[instance.to_xdr()]["lastModifiedLedgerSeq"]) > latest
            ):
                raise ValueError("Invalid code ledger metadata")
            return {
                **base,
                **extract(code.code),
                "code_latest_ledger": latest,
                "code_last_modified_ledger": modified,
                "wasm_size": len(code.code),
                "code_key_xdr": code_key.to_xdr(),
                "fetched_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            raise AccountError(
                "Specification unavailable, oversized, invalid or executable changed. Existing instance/events retained; inspect again if code changed."
            ) from exc

    async def storage(self, contract, network, key_xdr, durability):
        """Read exactly one supplied storage key after checking the endpoint's network.

        This is a fresh current-state lookup, separate from the captured event window
        and optional cache. Validation happens before I/O. It neither discovers keys
        nor restores archived entries or invokes any contract function.
        """
        from polarstellar.stellar.contract_storage import result_document, storage_key

        contract = validate_contract(contract)
        key = storage_key(contract, key_xdr, durability)
        async with httpx.AsyncClient(transport=self.transport, timeout=20) as client:
            await self.verify(client, network)
            data = await self.request(client, network, "getLedgerEntries", {"keys": [key.to_xdr()]})
        return result_document(data, contract, network.value, ENDPOINTS[network], key, durability)

    async def events(self, contract, network, start, end, cursor=None):
        """Fetch one ascending page and retain a fixed ledger boundary despite moving chain state."""
        contract = validate_contract(contract)
        integer(start)
        integer(end)
        if start > end:
            raise AccountError("Event start ledger must not exceed the captured latest ledger.")
        params = {
            "filters": [{"type": "contract", "contractIds": [contract]}],
            "pagination": {"limit": EVENT_LIMIT},
        }
        if cursor is None:
            params.update(startLedger=start, endLedger=end + 1)
        else:
            if not isinstance(cursor, str) or not cursor or len(cursor) > 256:
                raise AccountError("Invalid event cursor. Inspect again to restart.")
            # RPC explicitly forbids ledger bounds alongside a cursor. Filter later
            # pages locally at the captured end, so newer events cannot enter this snapshot.
            params["pagination"]["cursor"] = cursor
        async with httpx.AsyncClient(transport=self.transport, timeout=20) as client:
            await self.verify(client, network)
            data = await self.request(client, network, "getEvents", params)
        try:
            rows = data["events"]
            next_cursor = data["cursor"]
            if not isinstance(rows, list) or len(rows) > EVENT_LIMIT:
                raise ValueError("Invalid event page")
            if not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 256:
                raise ValueError("Invalid event cursor")
            if rows and next_cursor == cursor:
                raise ValueError("Event cursor did not advance")
            events = []
            beyond = False
            previous_ledger = start
            for row in rows:
                ledger = integer(row["ledger"])
                if (
                    row["contractId"] != contract
                    or row["type"] != "contract"
                    or ledger < previous_ledger
                ):
                    raise ValueError("Wrong event identity or order")
                previous_ledger = ledger
                if ledger > end:
                    beyond = True
                    continue
                if not isinstance(row["id"], str) or not row["id"]:
                    raise ValueError("Missing event identity")
                if not isinstance(row["topic"], list) or not all(
                    isinstance(topic, str) for topic in row["topic"]
                ):
                    raise ValueError("Invalid topics")
                if not isinstance(row["value"], str) or not isinstance(row["ledgerClosedAt"], str):
                    raise TypeError("Invalid event data")
                successful = row.get("inSuccessfulContractCall")
                if successful is not None and type(successful) is not bool:
                    raise ValueError("Invalid event status")
                events.append(
                    {
                        "id": row["id"],
                        "ledger": ledger,
                        "closed_at": row["ledgerClosedAt"],
                        "contract": contract,
                        "successful_call": successful,
                        "transaction_hash": row.get("txHash"),
                        "topics": [decoded_value(topic) for topic in row["topic"]],
                        "value": decoded_value(row["value"]),
                        "raw": row,
                    }
                )
            return {
                "events": events,
                "next_cursor": next_cursor,
                "done": not rows or beyond,
                "start_ledger": start,
                "end_ledger": end,
                "source": ENDPOINTS[network],
                "network": network.value,
                "contract": contract,
                "oldest_available_ledger": integer(data["oldestLedger"]),
                "latest_available_ledger": integer(data["latestLedger"]),
                "fetched_at": datetime.now(UTC).isoformat(),
            }
        except (ValueError, KeyError, TypeError) as exc:
            raise AccountError(
                "RPC returned invalid event data; previous events retained."
            ) from exc
