"""Normalized activity pages and conservative Horizon cursor handling."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum

from stellar_sdk import StrKey

from polarstellar.stellar.models import Network, OperationLink, Transfer

PAGE_SIZE = 20


class ActivityKind(str, Enum):
    """The three independently paginated account activity resources."""

    TRANSACTIONS = "transactions"
    OPERATIONS = "operations"
    PAYMENTS = "payments"


@dataclass(frozen=True)
class ActivityRecord:
    """A display record with optional normalized transfer evidence."""

    identifier: str
    cursor: str
    values: tuple[str, ...]
    transfer: Transfer | None = None
    exclusion: str = "Not a payment record"
    link: OperationLink | None = None


@dataclass(frozen=True)
class ActivityPage:
    """One page of activity, retaining network, cursor, provenance, and cache status."""

    address: str
    network: Network
    kind: ActivityKind
    records: tuple[ActivityRecord, ...]
    next_cursor: str | None
    source: str
    fetched_at: datetime
    cache_status: str = "Live data"


HEADERS = {
    ActivityKind.TRANSACTIONS: (
        "Created (UTC)",
        "Hash",
        "Status",
        "Source account",
        "Operations",
        "Fee (stroops)",
    ),
    ActivityKind.OPERATIONS: (
        "Created (UTC)",
        "Operation ID",
        "Type",
        "Status",
        "Source account",
        "Transaction",
    ),
    ActivityKind.PAYMENTS: (
        "Created (UTC)",
        "Operation ID",
        "Type",
        "Status",
        "From",
        "To",
        "Amount / destination amount",
        "Asset",
        "Transaction",
    ),
}


def field(row: dict, key: str) -> str:
    """Read a required nonempty string; malformed provider fields fail the page validation."""
    value = row[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"Invalid {key}")
    return value


def ledger_amount(row, key, allow_zero=False):
    """Read a positive seven-decimal ledger amount without float conversion.

    Callers permit zero for requested destination minima or observed zero-valued
    effects; normalized path-payment actual amounts stay positive. Missing/invalid
    values raise rather than substituting a limit.
    """
    value = field(row, key)
    # Limits are ledger amounts too. Reject exponent forms and extra precision;
    # do not round or substitute a requested limit for an actual amount.
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,7})?", value) or len(value) > 32:
        raise ValueError(f"Invalid {key}")
    exact = Decimal(value)
    if exact < 0 or (exact == 0 and not allow_zero) or exact > Decimal("922337203685.4775807"):
        raise ValueError(f"Invalid {key}")
    return exact


def asset_identity(row, prefix=""):
    """Validate one classic asset and return XLM or CODE:ISSUER, retaining issuer identity."""
    kind = field(row, prefix + "asset_type")
    if kind == "native":
        return "XLM"
    code, issuer = field(row, prefix + "asset_code"), field(row, prefix + "asset_issuer")
    valid_code = (
        re.fullmatch(r"[A-Za-z0-9]{1,4}", code)
        if kind == "credit_alphanum4"
        else re.fullmatch(r"[A-Za-z0-9]{5,12}", code)
        if kind == "credit_alphanum12"
        else None
    )
    if not valid_code or not StrKey.is_valid_ed25519_public_key(issuer):
        raise ValueError("Invalid asset identity")
    return f"{code}:{issuer}"


def operation_link(row, network, operation, sender, recipient, identifier, transaction, created):
    """Normalize complete path/merge evidence; callers retain malformed rows as explicit exclusions."""
    if (
        not identifier.isascii()
        or not identifier.isdecimal()
        or not re.fullmatch(r"[0-9a-fA-F]{64}", transaction)
    ):
        raise ValueError("Invalid operation or transaction identity")
    if operation == "account_merge":
        # Even an unexpected amount field cannot override the resource's missing
        # merge balance semantics. Effects/result decoding would be a separate feature.
        return OperationLink(
            network,
            sender,
            recipient,
            "XLM",
            None,
            "XLM",
            None,
            identifier,
            transaction,
            created,
            operation,
        )
    path = row["path"]
    if not isinstance(path, list) or len(path) > 5:
        raise ValueError("Invalid intermediary asset path")
    identities = tuple(asset_identity(item) for item in path)
    limit_name = "source_max" if operation == "path_payment_strict_receive" else "destination_min"
    sent, received, limit = (
        ledger_amount(row, "source_amount"),
        ledger_amount(row, "amount"),
        ledger_amount(row, limit_name, allow_zero=limit_name == "destination_min"),
    )
    if (limit_name == "source_max" and sent > limit) or (
        limit_name == "destination_min" and received < limit
    ):
        raise ValueError("Actual amount contradicts operation limit")
    return OperationLink(
        network,
        sender,
        recipient,
        asset_identity(row, "source_"),
        sent,
        asset_identity(row),
        received,
        identifier,
        transaction,
        created,
        operation,
        identities,
        limit_name,
        limit,
    )


def parse_page(
    data: dict, address: str, network: Network, kind: ActivityKind, source: str, cursor: str | None
) -> ActivityPage:
    """Validate cursor-ordered activity and preserve successful typed evidence and explicit exclusions.

    Additional path/merge evidence is normalized independently of direct transfers;
    incomplete details remain visible in activity but cannot become graph links.
    """
    rows = data["_embedded"]["records"]
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
        raise ValueError("Invalid activity page")
    records = []
    previous = int(cursor) if cursor is not None else None
    for row in rows:
        transfer = None
        link = None
        exclusion = "Not a payment record"
        token = field(row, "paging_token")
        if not token.isascii() or not token.isdecimal():
            raise ValueError("Invalid paging token")
        if previous is not None and int(token) >= previous:
            raise ValueError("Activity cursor did not advance")
        previous = int(token)
        created = field(row, "created_at")
        success = row[
            "successful" if kind == ActivityKind.TRANSACTIONS else "transaction_successful"
        ]
        if not isinstance(success, bool):
            raise TypeError("Invalid status")
        status = "Success" if success else "Failed"
        if kind == ActivityKind.TRANSACTIONS:
            identifier = field(row, "hash")
            count = row["operation_count"]
            fee = field(row, "fee_charged")
            if type(count) is not int or count < 0 or not fee.isdecimal():
                raise ValueError("Invalid transaction fields")
            values = (created, identifier, status, field(row, "source_account"), str(count), fee)
        else:
            identifier = field(row, "id")
            operation = field(row, "type")
            transaction = field(row, "transaction_hash")
            if kind == ActivityKind.OPERATIONS:
                values = (
                    created,
                    identifier,
                    operation,
                    status,
                    field(row, "source_account"),
                    transaction,
                )
            else:
                # Horizon payments includes create-account and account-merge operations.
                # Merge amounts are not present in these records; never invent a value.
                additional = operation in (
                    "path_payment_strict_send",
                    "path_payment_strict_receive",
                    "account_merge",
                )
                amount = (
                    "—"
                    if operation == "account_merge"
                    else row.get("amount", row.get("starting_balance", "—"))
                )
                if additional and not isinstance(amount, str):
                    amount = "—"
                if not isinstance(amount, str):
                    raise ValueError("Invalid payment amount")
                if amount != "—" and not additional:
                    try:
                        exact = Decimal(amount)
                        if not exact.is_finite() or exact < 0:
                            raise ValueError("Invalid payment amount")
                    except InvalidOperation as exc:
                        raise ValueError("Invalid payment amount") from exc
                asset_type = row.get("asset_type")
                if additional:
                    try:
                        asset = "XLM" if operation == "account_merge" else asset_identity(row)
                    except (ValueError, KeyError, TypeError):
                        asset = "—"
                else:
                    asset = (
                        "XLM"
                        if asset_type == "native" or operation == "create_account"
                        else f"{field(row, 'asset_code')}:{field(row, 'asset_issuer')}"
                        if asset_type in ("credit_alphanum4", "credit_alphanum12")
                        else "—"
                    )
                sender = row.get("from", row.get("funder", row.get("source_account", "—")))
                recipient = row.get("to", row.get("into", row.get("account", "—")))
                # Use the operation's real endpoint fields, not a guessed transaction
                # source. Missing endpoints cannot establish a relationship.
                if operation == "account_merge":
                    sender, recipient = row.get("account", "—"), row.get("into", "—")
                elif additional:
                    sender, recipient = row.get("from", "—"), row.get("to", "—")
                if not isinstance(sender, str) or not isinstance(recipient, str):
                    raise ValueError("Invalid payment parties")
                values = (
                    created,
                    identifier,
                    operation,
                    status,
                    sender,
                    recipient,
                    amount,
                    asset,
                    transaction,
                )
                if not success:
                    exclusion = "Failed transaction"
                elif operation in (
                    "path_payment_strict_send",
                    "path_payment_strict_receive",
                    "account_merge",
                ):
                    if not (
                        StrKey.is_valid_ed25519_public_key(sender)
                        and StrKey.is_valid_ed25519_public_key(recipient)
                    ):
                        exclusion = "Unsupported account identifier"
                    else:
                        try:
                            link = operation_link(
                                row,
                                network,
                                operation,
                                sender,
                                recipient,
                                identifier,
                                transaction,
                                created,
                            )
                            exclusion = "Path payment / merge: inspect expanded graph"
                        except (ValueError, KeyError, TypeError):
                            exclusion = "Incomplete or invalid path payment / merge evidence"
                elif operation not in ("payment", "create_account"):
                    exclusion = "Unsupported operation"
                elif not (
                    StrKey.is_valid_ed25519_public_key(sender)
                    and StrKey.is_valid_ed25519_public_key(recipient)
                ):
                    exclusion = "Unsupported account identifier"
                elif asset == "—" or amount == "—":
                    exclusion = "Missing asset or amount"
                else:
                    transfer = Transfer(
                        network,
                        sender,
                        recipient,
                        asset,
                        Decimal(amount),
                        identifier,
                        transaction,
                        created,
                        operation,
                    )
                    exclusion = ""
        records.append(ActivityRecord(identifier, token, values, transfer, exclusion, link))
    return ActivityPage(
        address,
        network,
        kind,
        tuple(records),
        records[-1].cursor if len(records) == PAGE_SIZE else None,
        source,
        datetime.now(UTC),
    )
