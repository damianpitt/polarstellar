"""Bounded, explicitly paginated operation effects with strict operation and network binding."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from stellar_sdk import StrKey

from polarstellar.stellar.activity import asset_identity, field, ledger_amount
from polarstellar.stellar.models import Network

PAGE_SIZE = 20
MAX_PAGES = 5


def operation_id(value):
    """Accept only canonical positive numeric operation IDs, never a URL or arbitrary path."""
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value):
        raise ValueError("Invalid operation ID")
    if int(value) > 2**63 - 1:
        raise ValueError("Operation ID exceeds ledger range")
    return value


def cursor_index(value, identifier):
    """Validate Horizon's operation-effect cursor and return its within-operation index."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,20}-[0-9]{1,10}", value):
        raise ValueError("Invalid effect cursor")
    operation, index = map(int, value.split("-"))
    if operation != int(identifier) or not 0 < index <= 2**32 - 1:
        raise ValueError("Effect cursor belongs to another operation")
    # Paging tokens are canonical; effect IDs separately contain padded components.
    if value != f"{operation}-{index}":
        raise ValueError("Noncanonical effect cursor")
    return index


@dataclass(frozen=True)
class EffectRecord:
    """One bound effect, preserving raw data when its type has no readable normalization."""

    identifier: str
    cursor: str
    kind: str
    account: str
    description: str
    asset: str
    amount: Decimal | None
    raw: str


@dataclass(frozen=True)
class EffectPage:
    """A live effects page carrying its requested cursor and original source/retrieval provenance."""

    operation: str
    network: Network
    cursor: str | None
    records: tuple[EffectRecord, ...]
    next_cursor: str | None
    source: str
    fetched_at: datetime


def parse_effects(data, identifier, network, source, cursor=None):
    """Bind every ascending effect to this operation/source and validate page size, identity and cursor.

    Links in returned data are compared as strings, never fetched. Unknown effect types
    retain raw evidence; known credit/debit amounts retain exact ledger decimals/issuers.
    A bad page raises before it can overwrite previously accepted UI evidence.
    """
    operation_id(identifier)
    previous = cursor_index(cursor, identifier) if cursor is not None else 0
    rows = data["_embedded"]["records"]
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
        raise ValueError("Invalid effects page")
    records, seen = [], set()
    for row in rows:
        token = field(row, "paging_token")
        index = cursor_index(token, identifier)
        if index <= previous:
            raise ValueError("Effect cursor did not advance")
        previous = index
        identity = field(row, "id")
        if (
            not re.fullmatch(r"[0-9]{1,20}-[0-9]{1,10}", identity)
            or tuple(map(int, identity.split("-"))) != (int(identifier), index)
            or identity in seen
        ):
            raise ValueError("Invalid effect identity")
        seen.add(identity)
        if row["_links"]["operation"]["href"] != f"{source}/operations/{identifier}":
            raise ValueError("Effect operation/source mismatch")
        account, kind = field(row, "account"), field(row, "type")
        if not StrKey.is_valid_ed25519_public_key(account):
            raise ValueError("Invalid effect account")
        asset, amount = "", None
        if kind in ("account_credited", "account_debited"):
            asset, amount = asset_identity(row), ledger_amount(row, "amount", allow_zero=True)
            description = f"{'Credit' if kind == 'account_credited' else 'Debit'} {amount:f} {asset} for {account}."
        elif kind == "account_removed":
            description = f"Account removed: {account}. No amount reported by this effect."
        elif kind == "account_created":
            amount, asset = ledger_amount(row, "starting_balance", allow_zero=True), "XLM"
            description = f"Account created: {account}, starting balance {amount:f} XLM."
        else:
            description = "Additional effect details remain raw; no transfer amount inferred."
        records.append(
            EffectRecord(
                identity,
                token,
                kind,
                account,
                description,
                asset,
                amount,
                json.dumps(row, indent=2, ensure_ascii=False),
            )
        )
    return EffectPage(
        identifier,
        network,
        cursor,
        tuple(records),
        records[-1].cursor if len(records) == PAGE_SIZE else None,
        source,
        datetime.now(UTC),
    )


def merge_comparison(operation, pages):
    """Cross-check a verified merge result with a fully exhausted, bounded effects selection.

    Never sum arbitrary credits into a merge balance. Require exactly one native
    destination credit and one source-account removal and a matching result amount.
    Partial, missing, duplicate or contradictory effects remain explicit; the result
    and effects are exported independently rather than silently replacing evidence.
    """
    if operation.kind != "account_merge":
        return "No merge comparison for this operation type."
    if operation.result is None or operation.result.merge_amount is None:
        return "Merge amount unknown: no verified applied result balance. Effects do not supply a guessed replacement."
    if not pages or pages[-1].next_cursor is not None:
        return "Merge result available; effects comparison incomplete (load more within the five-page limit)."
    raw = json.loads(operation.raw)
    records = [record for page in pages for record in page.records]
    credits = [
        record
        for record in records
        if record.kind == "account_credited"
        and record.account == raw.get("into")
        and record.asset == "XLM"
    ]
    removed = [
        record
        for record in records
        if record.kind == "account_removed" and record.account == raw.get("account")
    ]
    if len(credits) != 1 or len(removed) != 1:
        return "Merge effects incomplete or ambiguous: require one destination XLM credit and one source removal."
    if credits[0].amount != operation.result.merge_amount:
        return "CONTRADICTION: merge result balance and destination credit disagree; retain both sources for inspection."
    return "Merge result balance agrees with the destination XLM credit and source removal in loaded effects. Horizon-reported evidence, not independent consensus verification."
