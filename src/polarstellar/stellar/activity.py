"""Normalized activity pages and conservative Horizon cursor handling."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum

from polarstellar.stellar.models import Network

PAGE_SIZE = 20


class ActivityKind(str, Enum):
    TRANSACTIONS = "transactions"
    OPERATIONS = "operations"
    PAYMENTS = "payments"


@dataclass(frozen=True)
class ActivityRecord:
    identifier: str
    cursor: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class ActivityPage:
    address: str
    network: Network
    kind: ActivityKind
    records: tuple[ActivityRecord, ...]
    next_cursor: str | None
    source: str
    fetched_at: datetime


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
    value = row[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"Invalid {key}")
    return value


def parse_page(
    data: dict, address: str, network: Network, kind: ActivityKind, source: str, cursor: str | None
) -> ActivityPage:
    rows = data["_embedded"]["records"]
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
        raise ValueError("Invalid activity page")
    records = []
    previous = int(cursor) if cursor is not None else None
    for row in rows:
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
                amount = row.get("amount", row.get("starting_balance", "—"))
                if not isinstance(amount, str):
                    raise ValueError("Invalid payment amount")
                if amount != "—":
                    try:
                        exact = Decimal(amount)
                        if not exact.is_finite() or exact < 0:
                            raise ValueError("Invalid payment amount")
                    except InvalidOperation as exc:
                        raise ValueError("Invalid payment amount") from exc
                asset_type = row.get("asset_type")
                asset = (
                    "XLM"
                    if asset_type == "native" or operation == "create_account"
                    else f"{field(row, 'asset_code')}:{field(row, 'asset_issuer')}"
                    if asset_type in ("credit_alphanum4", "credit_alphanum12")
                    else "—"
                )
                sender = row.get("from", row.get("funder", row.get("source_account", "—")))
                recipient = row.get("to", row.get("into", row.get("account", "—")))
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
        records.append(ActivityRecord(identifier, token, values))
    return ActivityPage(
        address,
        network,
        kind,
        tuple(records),
        records[-1].cursor if len(records) == PAGE_SIZE else None,
        source,
        datetime.now(UTC),
    )
