"""Offline validation for source and frozen apps using disposable investigation data."""

import asyncio
import json
import platform
import sys
from decimal import Decimal
from pathlib import Path

import certifi
import httpx
import networkx as nx
from PySide6.QtCore import QTimer, qVersion
from PySide6.QtWidgets import QApplication
from stellar_sdk import StrKey, scval

from polarstellar import __version__
from polarstellar.stellar.contracts import decoded_value
from polarstellar.stellar.horizon import HorizonProvider
from polarstellar.stellar.models import Network
from polarstellar.storage.export import serialize, write_export
from polarstellar.storage.investigations import InvestigationStore
from polarstellar.storage.watchlist_transfer import read_file
from polarstellar.storage.watchlists import WatchlistStore


def validate_result_enrichment(address, destination):
    """Decode a local merge envelope/result fixture to exercise SDK result support without network I/O."""
    from stellar_sdk import Account, TransactionBuilder, xdr
    from stellar_sdk import Network as SDKNetwork

    from polarstellar.stellar.transaction import parse_transaction

    envelope = (
        TransactionBuilder(
            Account(address, 1),
            SDKNetwork.TESTNET_NETWORK_PASSPHRASE,
            base_fee=100,
        )
        .append_account_merge_op(destination)
        .set_timeout(0)
        .build()
    )
    result = xdr.TransactionResult(
        xdr.Int64(100),
        xdr.TransactionResultResult(
            xdr.TransactionResultCode.txSUCCESS,
            results=[
                xdr.OperationResult(
                    xdr.OperationResultCode.opINNER,
                    xdr.OperationResultTr(
                        xdr.OperationType.ACCOUNT_MERGE,
                        account_merge_result=xdr.AccountMergeResult(
                            xdr.AccountMergeResultCode.ACCOUNT_MERGE_SUCCESS,
                            xdr.Int64(100000001),
                        ),
                    ),
                ),
            ],
        ),
        xdr.TransactionResultExt(0),
    )
    data = {
        "hash": envelope.hash_hex(),
        "successful": True,
        "operation_count": 1,
        "ledger": 1,
        "fee_charged": "100",
        "source_account": address,
        "memo_type": "none",
        "created_at": "2026-10-14T00:00:00Z",
        "result_xdr": result.to_xdr(),
        "envelope_xdr": envelope.to_xdr(),
    }
    rows = [
        {
            "id": "4294971393",
            "paging_token": "4294971393",
            "type": "account_merge",
            "transaction_hash": data["hash"],
            "transaction_successful": True,
            "source_account": address,
            "account": address,
            "into": destination,
        }
    ]
    parsed = parse_transaction(data, data["hash"], Network.TESTNET, rows, "fixture")
    assert parsed.operations[0].result.merge_amount == Decimal("10.0000001")
    assert not parsed.result_warning


def validate_contract_specification():
    """Parse local union/Result event specs and exact payloads without executing WASM or RPC."""
    import hashlib

    from stellar_sdk import xdr

    from polarstellar.stellar.contract_specs import extract, interpret
    from polarstellar.stellar.contracts import decoded_value

    entry = xdr.SCSpecEntry(
        xdr.SCSpecEntryKind.SC_SPEC_ENTRY_EVENT_V0,
        event_v0=xdr.SCSpecEventV0(
            b"Local fixture",
            b"",
            xdr.SCSymbol(b"Changed"),
            [xdr.SCSymbol(b"change")],
            [
                xdr.SCSpecEventParamV0(
                    b"No implied units",
                    b"quantity",
                    xdr.SCSpecTypeDef(
                        xdr.SCSpecType.SC_SPEC_TYPE_RESULT,
                        result=xdr.SCSpecTypeResult(
                            xdr.SCSpecTypeDef(
                                xdr.SCSpecType.SC_SPEC_TYPE_UDT, udt=xdr.SCSpecTypeUDT(b"State")
                            ),
                            xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_ERROR),
                        ),
                    ),
                    xdr.SCSpecEventParamLocationV0.SC_SPEC_EVENT_PARAM_LOCATION_DATA,
                )
            ],
            xdr.SCSpecEventDataFormat.SC_SPEC_EVENT_DATA_FORMAT_SINGLE_VALUE,
        ),
    )
    union = xdr.SCSpecEntry(
        xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_UNION_V0,
        udt_union_v0=xdr.SCSpecUDTUnionV0(
            b"Local fixture",
            b"",
            b"State",
            [
                xdr.SCSpecUDTUnionCaseV0(
                    xdr.SCSpecUDTUnionCaseV0Kind.SC_SPEC_UDT_UNION_CASE_TUPLE_V0,
                    tuple_case=xdr.SCSpecUDTUnionCaseTupleV0(
                        b"Exact integer",
                        b"Count",
                        [xdr.SCSpecTypeDef(xdr.SCSpecType.SC_SPEC_TYPE_I128)],
                    ),
                )
            ],
        ),
    )
    name = b"contractspecv0"
    payload = bytes([len(name)]) + name + union.to_xdr_bytes() + entry.to_xdr_bytes()
    remaining, length = len(payload), bytearray()
    while True:
        part = remaining & 127
        remaining >>= 7
        length.append(part | (128 if remaining else 0))
        if not remaining:
            break
    wasm = b"\0asm\1\0\0\0\0" + bytes(length) + payload
    contract = StrKey.encode_contract(bytes(32))
    specification = {
        **extract(wasm),
        "wasm_hash": hashlib.sha256(wasm).hexdigest(),
        "contract": contract,
    }
    candidate = interpret(
        {
            "contract": contract,
            "topics": [decoded_value(scval.to_symbol("change").to_xdr())],
            "value": decoded_value(
                scval.to_vec([scval.to_symbol("Count"), scval.to_int128(2**100)]).to_xdr()
            ),
            "successful_call": True,
        },
        specification,
    )
    assert candidate["status"] == "Candidate match"
    decoded = candidate["fields"][0]["value"]
    assert decoded["branch"] == "Ok" and decoded["value"]["case"] == "Count"
    assert decoded["value"]["values"][0]["value"] == str(2**100)
    # A Result error is payload evidence, independent of a successful call flag.
    candidate = interpret(
        {
            "contract": contract,
            "topics": [decoded_value(scval.to_symbol("change").to_xdr())],
            "value": decoded_value(
                xdr.SCVal(
                    xdr.SCValType.SCV_ERROR,
                    error=xdr.SCError(xdr.SCErrorType.SCE_CONTRACT, contract_code=xdr.Uint32(1)),
                ).to_xdr()
            ),
            "successful_call": True,
        },
        specification,
    )
    assert candidate["fields"][0]["value"]["branch"] == "Err"
    assert candidate["call_status"] == "Successful call"


def validate_muxed_event_addresses():
    """Check full M-address/base-account/uint64 routing identity locally, without fetching accounts."""
    from stellar_sdk import MuxedAccount

    from polarstellar.stellar.contract_specs import typed

    account = StrKey.encode_ed25519_public_key(bytes([8]) * 32)
    for identifier in (0, 2**64 - 1):
        full = MuxedAccount(account, identifier).account_muxed
        value = scval.to_address(full)
        fields = typed(value, {"kind": "MUXED_ADDRESS"}, [], [4096])
        assert fields["value"] == full and fields["account"] == account
        assert fields["routing_id"] == str(identifier)
        normalized = decoded_value(value.to_xdr())
        assert normalized["value"] == full and normalized["xdr"] == value.to_xdr()
        # Ordinary Address must not accept a wider muxed identity just because the
        # generic SDK address parser recognizes the M-address encoding.
        try:
            typed(value, {"kind": "ADDRESS"}, [], [4096])
        except ValueError:
            pass
        else:
            raise AssertionError("Muxed account incorrectly matched ordinary Address")


def validate_contract_storage():
    """Check a supplied persistent key and exact local entry evidence without contacting RPC."""
    from stellar_sdk import xdr

    from polarstellar.stellar.contract_storage import entered_key, result_document, storage_key

    contract = StrKey.encode_contract(bytes(32))
    key = storage_key(contract, entered_key("COUNTER", "Symbol"), "Persistent")
    body = xdr.ContractDataEntry(
        xdr.ExtensionPoint(0),
        key.contract_data.contract,
        key.contract_data.key,
        key.contract_data.durability,
        scval.to_int128(2**100),
    )
    entry = xdr.LedgerEntryData(xdr.LedgerEntryType.CONTRACT_DATA, contract_data=body)
    result = result_document(
        {
            "latestLedger": 100,
            "entries": [
                {
                    "key": key.to_xdr(),
                    "xdr": entry.to_xdr(),
                    "lastModifiedLedgerSeq": 90,
                    "liveUntilLedgerSeq": 200,
                }
            ],
        },
        contract,
        "Testnet",
        "local fixture",
        key,
        "Persistent",
    )
    assert result["entry"]["value"]["value"] == str(2**100)
    assert result["status"] == "Returned; live at reported ledger"


def validate_asset_contract_identity():
    """Exercise native-XLM derivation and a local built-in SAC instance without HTTP or deployment."""
    from dataclasses import replace
    from datetime import UTC, datetime

    from stellar_sdk import xdr

    from polarstellar.stellar.asset_contracts import confirm, derive
    from polarstellar.stellar.assets import AssetDetail
    from polarstellar.stellar.contracts import ENDPOINTS, instance_key

    asset = AssetDetail(
        "XLM",
        "",
        Network.TESTNET,
        "native",
        {},
        {},
        "Local definition",
        datetime.now(UTC),
        "Local smoke fixture",
    )
    derived = derive(asset)
    assert derived["contract"] != derive(replace(asset, network=Network.MAINNET))["contract"]
    key = instance_key(derived["contract"]).contract_data
    executable = xdr.ContractExecutable(
        xdr.ContractExecutableType.CONTRACT_EXECUTABLE_STELLAR_ASSET
    )
    body = xdr.ContractDataEntry(
        xdr.ExtensionPoint(0),
        key.contract,
        key.key,
        key.durability,
        xdr.SCVal(
            xdr.SCValType.SCV_CONTRACT_INSTANCE, instance=xdr.SCContractInstance(executable, None)
        ),
    )
    raw = xdr.LedgerEntryData(xdr.LedgerEntryType.CONTRACT_DATA, contract_data=body)
    result = confirm(
        derived,
        {
            "contract": derived["contract"],
            "network": "Testnet",
            "source": ENDPOINTS[Network.TESTNET],
            "instance_latest_ledger": 100,
            "instance_available": True,
            "last_modified_ledger": 90,
            "live_until_ledger": 200,
            "raw_instance_xdr": raw.to_xdr(),
            "executable": executable.type.name,
        },
    )
    assert result["status"].startswith("Confirmed")


async def validate(window, root, screenshot=None):
    """Exercise bundled dependencies and Qt navigation without contacting any public endpoint.

    This is a startup/integration smoke check, not a substitute for live network or
    clean-machine usability testing. All persistence stays under the temporary root.
    """
    address = StrKey.encode_ed25519_public_key(bytes(32))
    issuer = StrKey.encode_ed25519_public_key(bytes([1]) * 32)
    checks = []
    ticks = []
    timer = QTimer(window)
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()

    def respond(request):
        """Serve exact fixture balances; unexpected network paths fail instead of going live."""
        if request.url.path != f"/accounts/{address}":
            raise AssertionError("Unexpected smoke-test request")
        return httpx.Response(
            200,
            json={
                "account_id": address,
                "sequence": "123",
                "home_domain": "",
                "balances": [
                    {"asset_type": "native", "balance": "100.0000001"},
                    {
                        "asset_type": "credit_alphanum4",
                        "asset_code": "TEST",
                        "asset_issuer": issuer,
                        "balance": "7.0000002",
                        "limit": "1000.0000000",
                        "is_authorized": True,
                    },
                ],
            },
        )

    # Replace only the transport in the injected provider, keeping service parsing,
    # network identity checks, async UI updates and the cache wrapper active.
    window.service.provider.provider = HorizonProvider(httpx.MockTransport(respond))
    window.search.setText(address)
    window.start_search()
    await window.task
    assert window.account is not None and window.account.balances[0].amount == Decimal(
        "100.0000001"
    )
    checks.append("account_lookup_exact_amounts")
    window.investigations.name.setText("Disposable native smoke check")
    window.investigations.create()
    window.save_resource()
    reopened = InvestigationStore(root / "investigations.sqlite3").list()
    assert reopened[0]["entries"][0]["network"] == Network.MAINNET.value
    checks.append("sqlite_saved_investigation_restart")
    # Exercise the production bookmark controls using the same disposable storage.
    # Adding/reopening the entry must stay offline and preserve its selected network.
    window.watchlists.name.setText("Disposable watchlist")
    window.watchlists.create()
    window.watch_resource()
    bookmarks = WatchlistStore(root / "watchlists.sqlite3").list()
    assert bookmarks[0]["entries"][0]["identifier"] == address
    assert bookmarks[0]["entries"][0]["network"] == Network.MAINNET.value
    # Bookmark portability is offline and independent of saved network snapshots.
    portable = window.watchlists.export_document()
    write_export(root / "watchlist.json", portable, "json")
    imported = WatchlistStore(root / "watchlists.sqlite3").import_portable(
        read_file(root / "watchlist.json")
    )
    restored = next(
        item
        for item in WatchlistStore(root / "watchlists.sqlite3").list()
        if item["id"] == imported
    )
    assert restored["entries"][0]["identifier"] == address
    assert restored["entries"][0]["snapshot"] is None
    checks.append("sqlite_watchlist_restart")
    snapshot = window.investigations.export_document()
    write_export(root / "evidence.json", snapshot, "json")
    write_export(root / "evidence.csv", snapshot, "csv")
    assert (
        json.loads((root / "evidence.json").read_text(encoding="utf-8"))["kind"] == "investigation"
    )
    assert "100.0000001" in serialize(snapshot, "csv")
    checks.append("csv_json_exports")
    value = decoded_value(scval.to_int128(2**100).to_xdr())
    assert value["value"] == str(2**100)
    validate_result_enrichment(address, issuer)
    validate_contract_specification()
    validate_muxed_event_addresses()
    validate_asset_contract_identity()
    validate_contract_storage()
    checks.append("stellar_sdk_xdr_exact_integer")
    graph = nx.DiGraph([(address, issuer)])
    assert nx.shortest_path(graph, address, issuer) == [address, issuer]
    # Exercise the new trace controls locally; opening/applying a trace is not a
    # request to expand accounts or fetch additional activity.
    from polarstellar.stellar.activity import ActivityKind, parse_page
    from polarstellar.ui.trace_view import TraceDialog

    trace = TraceDialog(window.activity_views[2], window)
    # Local fixture records exercise the two distinct semantic containers without
    # adding network requests to packaging/source startup validation.
    common = {
        "transaction_successful": True,
        "created_at": "2026-10-01T00:00:00Z",
        "transaction_hash": "0" * 64,
        "source_account": address,
    }
    trace.trace.add_page(
        address,
        parse_page(
            {
                "_embedded": {
                    "records": [
                        {
                            **common,
                            "id": "2",
                            "paging_token": "2",
                            "type": "account_merge",
                            "account": address,
                            "into": issuer,
                        },
                        {
                            **common,
                            "id": "1",
                            "paging_token": "1",
                            "type": "path_payment_strict_send",
                            "from": address,
                            "to": issuer,
                            "source_asset_type": "native",
                            "source_amount": "1.0000001",
                            "asset_type": "native",
                            "amount": "1.0000000",
                            "destination_min": "0",
                            "path": [],
                        },
                    ]
                }
            },
            address,
            trace.trace.network,
            ActivityKind.PAYMENTS,
            "fixture",
            None,
        ),
    )
    trace.rebuild()
    assert len(trace.links) == 2 and not trace.connections
    trace.from_time.setText("2026-10-01T00:00:00Z")
    trace.operation_type.setCurrentIndex(trace.operation_type.findText("Account merges"))
    trace.apply_filters()
    exported = trace.export_document()
    assert exported["metadata"]["trace_filters"]["operation_types"] == ["account_merge"]
    assert exported["records"][0]["source_amount"] is None
    assert not exported["metadata"]["paths"]
    # A hidden path-payment peer still makes the merge timestamp ambiguous.
    # Switching timeline controls is local and retains the unknown merge amount.
    trace.tables.setCurrentIndex(2)
    timeline = trace.active_document()
    assert timeline["kind"] == "trace_timeline"
    assert timeline["records"][0]["same_timestamp_loaded_operations"] == 2
    assert timeline["records"][0]["source_amount"] is None
    trace.timeline_order.setCurrentIndex(1)
    assert trace.active_document()["metadata"]["timeline"]["order"] == "Newest first"
    trace.reject()
    checks.append("networkx_relationship_model")
    # TLS is constructed locally to verify packaged CA certificates and SSL support.
    assert Path(certifi.where()).is_file()
    async with httpx.AsyncClient():
        pass
    checks.append("httpx_tls_certificate_bundle")
    # Opening the discovery tab itself must remain offline; only Search fetches pages.
    window.navigation.setCurrentRow(5)
    window.assets.tabs.setCurrentIndex(1)
    await asyncio.sleep(0.01)
    window.assets.tabs.setCurrentIndex(0)
    for index in (5, 6, 7, 8, 0):
        window.navigation.setCurrentRow(index)
        await asyncio.sleep(0.01)
    assert window.pages.currentIndex() == 0 and ticks
    checks.append("qt_navigation_and_async_event_loop")
    timer.stop()
    if screenshot:
        window.grab().save(str(screenshot))
        assert Path(screenshot).is_file()
    window.close()
    return {
        "status": "passed",
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "platform": platform.system(),
        "qt_version": qVersion(),
        "qt_platform_plugin": QApplication.platformName(),
        "architecture": platform.machine(),
        "checks": checks,
        "network_requests": "fixture transport only",
        "storage": "temporary directory, removed on exit",
    }
