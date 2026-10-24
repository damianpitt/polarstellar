"""Derive network/issuer-specific Stellar Asset Contract identities without deploying anything."""

import hashlib
import json
from datetime import UTC, datetime

from stellar_sdk import Asset, StrKey, xdr

from polarstellar.stellar.assets import validate_asset
from polarstellar.stellar.contract_storage import bounded_xdr, ledger_number
from polarstellar.stellar.contracts import ENDPOINTS, PASSPHRASES, instance_key
from polarstellar.stellar.providers import AccountError
from polarstellar.storage.export import plain


def derive(detail):
    """Derive the reserved SAC ID from exact asset XDR and selected network, retaining input provenance.

    This is local deterministic identity, not a deployment check or asset endorsement.
    Native XLM has no issuer; an issued asset whose code is XLM remains a different asset.
    The original Horizon/local asset snapshot is frozen separately from any later RPC read.
    """
    try:
        code, issuer = validate_asset(detail.code, detail.issuer)
        if (code, issuer) != (detail.code, detail.issuer):
            raise ValueError("Asset identity must already be normalized")
        network = detail.network
        asset = Asset(code, issuer) if issuer else Asset.native()
        if detail.asset_type != asset.type:
            raise ValueError("Asset type does not match its code and issuer")
        network_hash = hashlib.sha256(PASSPHRASES[network].encode()).digest()
        preimage = xdr.HashIDPreimage(
            xdr.EnvelopeType.ENVELOPE_TYPE_CONTRACT_ID,
            contract_id=xdr.HashIDPreimageContractID(
                xdr.Hash(network_hash),
                xdr.ContractIDPreimage(
                    xdr.ContractIDPreimageType.CONTRACT_ID_PREIMAGE_FROM_ASSET,
                    from_asset=asset.to_xdr_object(),
                ),
            ),
        )
        contract = StrKey.encode_contract(hashlib.sha256(preimage.to_xdr_bytes()).digest())
        if contract != asset.contract_id(PASSPHRASES[network]):
            raise ValueError("SDK and retained preimage disagree")
        return {
            "code": code,
            "issuer": issuer,
            "network": network.value,
            "contract": contract,
            "asset_xdr": asset.to_xdr_object().to_xdr(),
            "network_id": network_hash.hex(),
            "contract_id_preimage_xdr": preimage.to_xdr(),
            "derived_at": datetime.now(UTC).isoformat(),
            "derivation_source": "Local Stellar asset/network XDR derivation with locked stellar-sdk",
            "asset_metadata": plain(detail),
            "status": "Derived only — deployment not checked",
            "contract_instance": None,
            "coverage": "Reserved SAC identity for this exact asset and network. Derivation alone does not prove deployment. "
            "RPC confirmation is provider-reported current instance evidence, not independent consensus, issuer control or endorsement. "
            "Asset statistics/flags keep their original source and time; no token methods, admin, name or symbol are queried. "
            "Standalone contract tokens and reverse discovery from arbitrary C-addresses are outside this lookup.",
        }
    except Exception as exc:
        raise AccountError(
            "Unable to derive a SAC identity from this asset/network snapshot."
        ) from exc


def confirm(derived, snapshot):
    """Check an RPC instance against the reserved ID and native executable, retaining uncertain results.

    Missing or expired instances are explicitly unconfirmed. A live built-in executable
    at the derived identity is the scope of confirmation; no mutable admin/metadata is
    treated as asset provenance. Malformed/foreign evidence raises without replacing UI state.
    """
    try:
        from polarstellar.stellar.models import Network

        network = Network(derived["network"])
        if (snapshot["contract"], snapshot["network"], snapshot["source"]) != (
            derived["contract"],
            network.value,
            ENDPOINTS[network],
        ):
            raise ValueError("Foreign contract/network/source")
        latest = ledger_number(snapshot["instance_latest_ledger"])
        if type(snapshot["instance_available"]) is not bool:
            raise ValueError("Invalid instance availability flag")
        result = {**derived, "contract_instance": snapshot}
        if not snapshot["instance_available"]:
            result["status"] = "Unconfirmed — no live instance returned"
            return result
        modified = ledger_number(snapshot["last_modified_ledger"])
        if modified > latest:
            raise ValueError("Modification ledger exceeds RPC latest")
        raw = bounded_xdr(snapshot["raw_instance_xdr"], xdr.LedgerEntryData, 262_144)
        body, expected = raw.contract_data, instance_key(derived["contract"]).contract_data
        if (
            raw.type != xdr.LedgerEntryType.CONTRACT_DATA
            or body is None
            or body.contract != expected.contract
            or body.key != expected.key
            or body.durability != expected.durability
            or body.val.type != xdr.SCValType.SCV_CONTRACT_INSTANCE
        ):
            raise ValueError("Wrong raw instance identity")
        executable = body.val.instance.executable.type
        if snapshot["executable"] != executable.name:
            raise ValueError("Executable description differs from raw evidence")
        until = snapshot.get("live_until_ledger")
        if until is None or ledger_number(until) < latest or until == 0:
            result["status"] = "Unconfirmed — instance TTL missing or expired"
        elif executable != xdr.ContractExecutableType.CONTRACT_EXECUTABLE_STELLAR_ASSET:
            result["status"] = "Not confirmed — executable is not a built-in SAC"
        else:
            result["status"] = "Confirmed — live built-in SAC at derived identity"
        return result
    except Exception as exc:
        raise AccountError(
            "RPC SAC identity evidence is invalid or belongs to another context."
        ) from exc


def readable(result):
    """Show identity/status first, then distinct asset metadata and RPC evidence provenance."""
    metadata, snapshot = result["asset_metadata"], result["contract_instance"]
    lines = [
        result["status"],
        f"Contract: {result['contract']}",
        f"Asset: {result['code']} • Issuer: {result['issuer'] or 'None — native XLM'}",
        f"Network: {result['network']}",
        f"Derived: {result['derived_at']}",
        f"Asset metadata source: {metadata['source']}",
        f"Asset metadata retrieved: {metadata['fetched_at']}",
        metadata["cache_status"],
    ]
    if snapshot is not None:
        lines += [
            f"RPC source: {snapshot['source']} • Retrieved: {snapshot['fetched_at']}",
            f"RPC ledger: {snapshot['instance_latest_ledger']} • Modified: {snapshot.get('last_modified_ledger', 'Not provided')}",
            f"Live until ledger: {snapshot.get('live_until_ledger', 'Not provided')}",
        ]
    lines += [
        "",
        result["coverage"],
        "",
        "DERIVATION AND ORIGINAL INSTANCE EVIDENCE",
        json.dumps(result, ensure_ascii=False, indent=2),
    ]
    return "\n".join(lines)
