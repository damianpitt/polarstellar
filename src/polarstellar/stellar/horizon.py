"""Read-only Horizon adapter. Each cancellable lookup owns its HTTP client."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from polarstellar.stellar.activity import PAGE_SIZE, ActivityKind, ActivityPage, parse_page
from polarstellar.stellar.models import Account, Balance, Network
from polarstellar.stellar.providers import AccountError

ENDPOINTS = {
    Network.MAINNET: "https://horizon.stellar.org",
    Network.TESTNET: "https://horizon-testnet.stellar.org",
}


def money(value: object) -> Decimal:
    if not isinstance(value, str):
        raise TypeError("Expected an exact decimal string")
    result = Decimal(value)
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid amount")
    return result


def parse_account(data: dict, address: str, network: Network, source: str) -> Account:
    if data["account_id"] != address:
        raise ValueError("Account identity mismatch")
    balances = []
    for row in data["balances"]:
        kind = row["asset_type"]
        if kind == "native":
            asset, identity, limit, authorized = "XLM", "Native", None, None
        elif kind in ("credit_alphanum4", "credit_alphanum12", "liquidity_pool_shares"):
            if kind == "liquidity_pool_shares":
                asset, identity = "Pool shares", row["liquidity_pool_id"]
            else:
                asset, identity = row["asset_code"], row["asset_issuer"]
            limit = money(row["limit"])
            authorized = row.get("is_authorized")
            if authorized is not None and not isinstance(authorized, bool):
                raise ValueError("Invalid authorization")
        else:
            raise ValueError("Unsupported balance type")
        if not isinstance(asset, str) or not isinstance(identity, str):
            raise TypeError("Invalid asset identity")
        balances.append(Balance(asset, identity, money(row["balance"]), limit, authorized))
    sequence = data["sequence"]
    domain = data.get("home_domain", "")
    if not isinstance(sequence, str) or not sequence.isdecimal() or not isinstance(domain, str):
        raise ValueError("Invalid account details")
    return Account(address, network, sequence, domain, tuple(balances), source, datetime.now(UTC))


class HorizonProvider:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    async def get_account(self, address: str, network: Network) -> Account:
        data = await self._get(address, network)
        try:
            return parse_account(data, address, network, ENDPOINTS[network])
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            raise AccountError("Horizon returned incomplete or unsupported account data.") from exc

    async def get_activity(
        self, address: str, network: Network, kind: ActivityKind, cursor: str | None = None
    ) -> ActivityPage:
        params = {"order": "desc", "limit": str(PAGE_SIZE), "include_failed": "true"}
        if cursor is not None:
            if not cursor.isascii() or not cursor.isdecimal():
                raise AccountError("Invalid activity cursor. Start a new search.")
            params["cursor"] = cursor
        data = await self._get(address, network, "/" + kind.value, params)
        try:
            return parse_page(data, address, network, kind, ENDPOINTS[network], cursor)
        except (ValueError, KeyError, TypeError) as exc:
            raise AccountError("Horizon returned incomplete or invalid activity data.") from exc

    async def _get(
        self, address: str, network: Network, suffix: str = "", params: dict | None = None
    ) -> dict:
        source = ENDPOINTS[network]
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=15.0) as client:
                response = await client.get(f"{source}/accounts/{address}{suffix}", params=params)
                if response.status_code == 404:
                    raise AccountError(
                        f"Account not found on {network.value}. It may not be funded."
                    )
                if response.status_code == 429:
                    raise AccountError("Horizon is rate limiting requests. Please try again later.")
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException as exc:
            raise AccountError("The request timed out. Please try again.") from exc
        except httpx.HTTPStatusError as exc:
            raise AccountError("Horizon is unavailable. Please try again later.") from exc
        except httpx.RequestError as exc:
            raise AccountError(
                "Unable to connect to Horizon. Check your connection and try again."
            ) from exc
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            raise AccountError("Horizon returned incomplete or unsupported account data.") from exc
