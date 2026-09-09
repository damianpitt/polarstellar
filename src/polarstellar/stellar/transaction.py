"""Transaction snapshots and readable explanations of parsed Horizon operations."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError


def validate_hash(value: str) -> str:
    result = value.strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", result) is None:
        raise AccountError("Enter a transaction hash containing exactly 64 hexadecimal characters.")
    return result


@dataclass(frozen=True)
class OperationDetail:
    identifier: str
    kind: str
    source: str
    explanation: str
    raw: str


@dataclass(frozen=True)
class TransactionDetail:
    hash: str
    network: Network
    successful: bool
    ledger: int
    created: str
    source_account: str
    fee_account: str
    fee_stroops: int
    operation_count: int
    memo: str
    operations: tuple[OperationDetail, ...]
    warning: str
    source: str
    fetched_at: datetime
    raw: str


def text(row: dict, name: str) -> str:
    value = row[name]
    if not isinstance(value, str):
        raise TypeError("Expected text")
    return value


def amount(row: dict, name: str) -> str:
    value = text(row, name)
    number = Decimal(value)
    if not number.is_finite() or number < 0:
        raise ValueError("Invalid amount")
    return value


def asset(row: dict, prefix: str = "asset_") -> str:
    if row[prefix + "type"] == "native":
        return "XLM"
    return f"{text(row, prefix + 'code')}:{text(row, prefix + 'issuer')}"


def explain(row: dict) -> str:
    kind = row["type"]
    if kind == "payment":
        return f"Send {amount(row, 'amount')} {asset(row)} to {text(row, 'to')}."
    if kind == "create_account":
        return f"Create {text(row, 'account')} with {amount(row, 'starting_balance')} XLM."
    if kind == "account_merge":
        return f"Merge this account into {text(row, 'into')}; the transferred balance is not provided here."
    if kind in ("path_payment_strict_receive", "path_payment_strict_send"):
        return (
            f"Path payment: send {amount(row, 'source_amount')} {asset(row, 'source_asset_')}; "
            f"deliver {amount(row, 'amount')} {asset(row)} to {text(row, 'to')}."
        )
    if kind == "change_trust":
        identity = (
            "liquidity pool " + text(row, "liquidity_pool_id")
            if row.get("asset_type") == "liquidity_pool_shares"
            else asset(row)
        )
        limit = amount(row, "limit")
        return (
            f"Remove trustline for {identity}."
            if Decimal(limit) == 0
            else f"Create or update trustline for {identity}; limit {limit}."
        )
    if kind in ("manage_sell_offer", "manage_buy_offer", "create_passive_sell_offer"):
        quantity = amount(row, "amount")
        offer = str(row.get("offer_id", "new passive offer"))
        if Decimal(quantity) == 0:
            return f"Delete offer {offer}."
        direction = "Buy" if kind == "manage_buy_offer" else "Sell"
        base = asset(row, "buying_asset_" if direction == "Buy" else "selling_asset_")
        quote = asset(row, "selling_asset_" if direction == "Buy" else "buying_asset_")
        price = row["price_r"]
        numerator, denominator = int(price["n"]), int(price["d"])
        if numerator <= 0 or denominator <= 0:
            raise ValueError("Invalid offer price")
        return (
            f"{direction} {quantity} {base} against {quote}; "
            f"price {numerator}/{denominator} {quote} per {base}; offer {offer}. "
            "An offer instruction does not establish that a trade occurred."
        )
    if kind == "set_options":
        names = (
            "home_domain",
            "master_key_weight",
            "low_threshold",
            "med_threshold",
            "high_threshold",
            "signer_key",
            "signer_weight",
            "set_flags_s",
            "clear_flags_s",
            "inflation_dest",
        )
        changes = [
            f"{name.replace('_', ' ')}: {json.dumps(row[name], ensure_ascii=False)}"
            for name in names
            if name in row
        ]
        if not changes:
            return "Account options instruction; no recognized changes provided. See raw operation."
        return "Update account options: " + "; ".join(changes) + "."
    return (
        "Readable decoding is not yet supported for this operation type. Inspect the raw operation."
    )


def decode_operation(row: dict, successful: bool) -> OperationDetail:
    try:
        description = explain(row)
    except (KeyError, TypeError, ValueError, InvalidOperation):
        description = "Incomplete or unsupported operation fields; inspect the raw operation."
    if not successful:
        description = "NOT APPLIED — transaction failed. Intended instruction: " + description
    return OperationDetail(
        text(row, "id"),
        text(row, "type"),
        text(row, "source_account"),
        description,
        json.dumps(row, indent=2, ensure_ascii=False),
    )


def parse_transaction(
    data: dict, hash_value: str, network: Network, rows: list, source: str, warning: str = ""
) -> TransactionDetail:
    if data["hash"] != hash_value or type(data["successful"]) is not bool:
        raise ValueError("Invalid transaction identity or status")
    count = data["operation_count"]
    ledger = data["ledger"]
    fee = text(data, "fee_charged")
    if type(count) is not int or not 0 <= count <= 1000 or type(ledger) is not int or ledger < 0:
        raise ValueError("Invalid transaction metadata")
    if not fee.isascii() or not fee.isdecimal():
        raise ValueError("Invalid fee")
    previous = -1
    for row in rows:
        token = text(row, "paging_token")
        if not token.isascii() or not token.isdecimal() or int(token) <= previous:
            raise ValueError("Operations are not in execution order")
        previous = int(token)
        if (
            row["transaction_hash"] != hash_value
            or row["transaction_successful"] is not data["successful"]
        ):
            raise ValueError("Operation does not belong to transaction")
    if len(rows) > count:
        raise ValueError("Too many operations")
    if len(rows) != count:
        warning = (
            warning or "Operation data is incomplete in Horizon; this is not a complete decode."
        )
    memo = text(data, "memo_type") + (": " + text(data, "memo") if "memo" in data else "")
    return TransactionDetail(
        hash_value,
        network,
        data["successful"],
        ledger,
        text(data, "created_at"),
        text(data, "source_account"),
        text(data, "fee_account") if "fee_account" in data else text(data, "source_account"),
        int(fee),
        count,
        memo,
        tuple(decode_operation(row, data["successful"]) for row in rows),
        warning,
        source,
        datetime.now(UTC),
        json.dumps(data, indent=2, ensure_ascii=False),
    )
