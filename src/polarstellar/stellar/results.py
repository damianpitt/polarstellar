"""Validate Horizon result XDR against the searched envelope before assigning operation results."""

import base64
from dataclasses import dataclass
from decimal import Decimal

from stellar_sdk import FeeBumpTransactionEnvelope, TransactionEnvelope, xdr
from stellar_sdk import Network as SDKNetwork

from polarstellar.stellar.models import Network


@dataclass(frozen=True)
class ResultEvidence:
    """An aligned Horizon-reported result code and optional successful merge balance.

    Structural/envelope checks bind this interpretation to the requested transaction;
    they are not independent consensus verification. Failed transactions never expose
    an applied balance, even when an earlier operation has an individual success code.
    """

    code: str
    applied: bool
    merge_stroops: str = ""
    merge_amount: Decimal | None = None

    def __post_init__(self):
        """Reject impossible cached balance/status combinations before they can be displayed as applied."""
        if self.merge_amount is None:
            if self.merge_stroops:
                raise ValueError("Merge stroops require an exact amount")
            return
        if (
            not self.applied
            or self.code != "ACCOUNT_MERGE_SUCCESS"
            or not self.merge_stroops.isascii()
            or not self.merge_stroops.isdecimal()
            or len(self.merge_stroops) > 19
        ):
            raise ValueError("Invalid applied merge result")
        number = int(self.merge_stroops)
        expected = Decimal(f"{number // 10_000_000}.{number % 10_000_000:07d}")
        if number > 2**63 - 1 or str(number) != self.merge_stroops or self.merge_amount != expected:
            raise ValueError("Merge amount does not match ledger stroops")


def xdr_bytes(value):
    """Bound and strictly decode optional base64 XDR; reject malformed and oversized inputs."""
    if not isinstance(value, str) or len(value) > 1_000_000:
        raise ValueError("Missing/oversized XDR")
    return base64.b64decode(value, validate=True)


def decode_results(data, rows, network):
    """Return aligned result evidence, top-level code and a recoverable optional-data warning.

    Require a complete ordered operation list and an envelope hash on the selected
    network. Match envelope/result operation types and sources before mapping codes;
    merge endpoints must match the envelope too. Fee-bump inner hashes/status are
    checked explicitly. Missing or incompatible XDR never breaks basic inspection.
    """
    empty = (None,) * len(rows)
    if "result_xdr" not in data:
        return empty, "Not provided", ""
    try:
        raw_envelope, raw_result = xdr_bytes(data["envelope_xdr"]), xdr_bytes(data["result_xdr"])
        envelope_xdr = xdr.TransactionEnvelope.from_xdr_bytes(raw_envelope)
        result = xdr.TransactionResult.from_xdr_bytes(raw_result)
        # SDK parsers can stop before trailing bytes; require an exact round trip.
        if envelope_xdr.to_xdr_bytes() != raw_envelope or result.to_xdr_bytes() != raw_result:
            raise ValueError("Trailing or noncanonical XDR")
        passphrase = (
            SDKNetwork.PUBLIC_NETWORK_PASSPHRASE
            if network == Network.MAINNET
            else SDKNetwork.TESTNET_NETWORK_PASSPHRASE
        )
        bumped = envelope_xdr.type == xdr.EnvelopeType.ENVELOPE_TYPE_TX_FEE_BUMP
        envelope = (FeeBumpTransactionEnvelope if bumped else TransactionEnvelope).from_xdr(
            data["envelope_xdr"], passphrase
        )
        if envelope.hash_hex() != data["hash"] or result.fee_charged.int64 != int(
            data["fee_charged"]
        ):
            raise ValueError("Envelope hash or result fee mismatch")
        outer = result.result
        code = outer.code.name
        if bumped:
            pair = outer.inner_result_pair
            inner_envelope = envelope.transaction.inner_transaction_envelope
            if pair is None or pair.transaction_hash.hash != inner_envelope.hash():
                raise ValueError("Fee-bump inner hash mismatch")
            expected = (
                xdr.TransactionResultCode.txFEE_BUMP_INNER_SUCCESS
                if data["successful"]
                else xdr.TransactionResultCode.txFEE_BUMP_INNER_FAILED
            )
            if outer.code != expected:
                raise ValueError("Fee-bump status mismatch")
            decoded = pair.result.result
            code += " / " + decoded.code.name
            tx = inner_envelope.transaction
        else:
            decoded, tx = outer, envelope.transaction
        if (decoded.code == xdr.TransactionResultCode.txSUCCESS) != data["successful"]:
            raise ValueError("Result status mismatch")
        expected_fee_account = (
            envelope.transaction.fee_source.account_id if bumped else tx.source.account_id
        )
        if (
            data["source_account"] != tx.source.account_id
            or data.get("fee_account", data["source_account"]) != expected_fee_account
        ):
            raise ValueError("Transaction source or fee-payer mismatch")
        operations = tx.operations
        if len(rows) != data["operation_count"] or len(operations) != len(rows):
            raise ValueError("Cannot align incomplete operations")
        results = decoded.results
        if results is None:
            return empty, code, "Result contains no per-operation results; no balance inferred."
        if len(results) != len(rows):
            raise ValueError("Operation result count mismatch")
        evidence = []
        transaction_position = None
        for index, (row, instruction, outcome) in enumerate(
            zip(rows, operations, results, strict=True), 1
        ):
            identifier = row["id"]
            if (
                not identifier.isascii()
                or not identifier.isdecimal()
                or len(identifier) > 20
                or identifier != row["paging_token"]
            ):
                raise ValueError("Invalid ordered operation identity")
            toid = int(identifier)
            # Horizon TOIDs retain ledger/transaction/operation positions. Check
            # their ledger and one-based operation index before assigning XDR codes.
            if (
                toid > 2**63 - 1
                or str(toid) != identifier
                or toid >> 32 != data["ledger"]
                or toid & 4095 != index
                or (toid >> 12) & 1048575 == 0
            ):
                raise ValueError("Operation position does not match transaction metadata")
            if transaction_position is not None and toid >> 12 != transaction_position:
                raise ValueError("Operations span different transaction positions")
            transaction_position = toid >> 12
            kind = instruction.to_xdr_object().body.type
            source = instruction.source or tx.source
            if kind.name.lower() != row["type"] or source.account_id != row["source_account"]:
                raise ValueError("Envelope operation identity mismatch")
            if outcome.tr is None:
                if data["successful"]:
                    raise ValueError("Successful transaction has failed operation result")
                evidence.append(ResultEvidence(outcome.code.name, False))
                continue
            if outcome.tr.type != kind:
                raise ValueError("Result operation type mismatch")
            fields = [
                value
                for name, value in vars(outcome.tr).items()
                if name.endswith("_result") and value is not None
            ]
            if len(fields) != 1:
                raise ValueError("Unsupported result union")
            operation_result = fields[0]
            status = operation_result.code.name
            if data["successful"] and operation_result.code.value != 0:
                raise ValueError("Successful transaction has failed operation code")
            stroops, amount = "", None
            if kind == xdr.OperationType.ACCOUNT_MERGE:
                if (
                    row.get("account") != source.account_id
                    or row.get("into") != instruction.destination.account_id
                ):
                    raise ValueError("Merge endpoints mismatch")
                if (
                    data["successful"]
                    and operation_result.code == xdr.AccountMergeResultCode.ACCOUNT_MERGE_SUCCESS
                ):
                    number = operation_result.source_account_balance.int64
                    if number < 0:
                        raise ValueError("Negative merged balance")
                    stroops = str(number)
                    # Build the decimal string directly so the global Decimal context
                    # cannot round a signed-64-bit ledger balance during conversion.
                    amount = Decimal(f"{number // 10_000_000}.{number % 10_000_000:07d}")
            evidence.append(ResultEvidence(status, data["successful"], stroops, amount))
        return tuple(evidence), code, ""
    except Exception:  # noqa: BLE001 -- Optional SDK decoding must not hide valid basic transaction data.
        return (
            empty,
            "Unverified/unavailable",
            "Result enrichment refused: missing, malformed, incompatible or mismatched envelope/result/operation evidence. No amount inferred.",
        )
