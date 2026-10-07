"""Validate issued-asset catalog queries and Horizon's nonnumeric paging tokens."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from stellar_sdk import StrKey

from polarstellar.stellar.assets import AssetDetail, parse_asset, validate_asset
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError

PAGE_SIZE = 20
MAX_PAGES = 50


def filters(code, issuer):
    """Accept optional exact filters; blank values browse the catalog without guessing an issuer."""
    code, issuer = code.strip(), issuer.strip()
    if code and not re.fullmatch(r"[a-zA-Z0-9]{1,12}", code):
        raise AccountError("Asset code filter must contain 1–12 alphanumeric characters.")
    if issuer and not StrKey.is_valid_ed25519_public_key(issuer):
        raise AccountError("Issuer filter must be a checksum-valid G-address.")
    return code, issuer


def token(value):
    """Validate Horizon's code_issuer_type token, never a URL or an arbitrary query fragment.

    Asset cursors differ from numeric activity cursors. Passing the token as a query
    parameter to our fixed /assets endpoint avoids following provider-supplied URLs.
    """
    if not isinstance(value, str) or len(value) > 100:
        raise AccountError("Invalid asset cursor. Start a new discovery search.")
    parts = value.split("_")
    if len(parts) != 4 or parts[2] != "credit" or parts[3] not in ("alphanum4", "alphanum12"):
        raise AccountError("Invalid asset cursor. Start a new discovery search.")
    code, issuer = validate_asset(parts[0], parts[1])
    expected = "alphanum4" if len(code) <= 4 else "alphanum12"
    if parts[3] != expected or value != f"{code}_{issuer}_credit_{expected}":
        raise AccountError("Asset cursor type does not match its code.")
    return value


@dataclass(frozen=True)
class AssetPage:
    """One live page bound to exact filters/network, with its own retrieval provenance."""

    code: str
    issuer: str
    network: Network
    cursor: str | None
    next_cursor: str | None
    items: tuple[AssetDetail, ...]
    source: str
    fetched_at: datetime
    done: bool


def parse_catalog(data, code, issuer, network, source, cursor):
    """Validate the complete page before displaying any row; empty pages confirm the current end.

    Short pages still allow another request. Discovery is a changing catalog, not an
    atomic ledger snapshot or proof of all historical assets. Native/pool/contract
    tokens are not accepted as issued asset records.
    """
    rows = data["_embedded"]["records"]
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
        raise ValueError("Invalid asset discovery page size")
    items, seen, last = [], set(), None
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("asset_code"), str)
            or not isinstance(row.get("asset_issuer"), str)
        ):
            raise TypeError("Invalid asset identity fields")
        row_code, row_issuer = validate_asset(row["asset_code"], row["asset_issuer"])
        if not row_issuer or (code and row_code != code) or (issuer and row_issuer != issuer):
            raise ValueError("Asset discovery row does not match query")
        paging = token(row["paging_token"])
        if paging != f"{row_code}_{row_issuer}_{row['asset_type']}" or paging in seen:
            raise ValueError("Invalid or duplicate asset paging identity")
        seen.add(paging)
        items.append(parse_asset(row, row_code, row_issuer, network, source))
        last = paging
    if rows and last == cursor:
        raise AccountError("Asset cursor did not advance. Restart discovery.")
    return AssetPage(
        code, issuer, network, cursor, last, tuple(items), source, datetime.now(UTC), not rows
    )
