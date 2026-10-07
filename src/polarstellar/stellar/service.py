"""Validate user input before any provider access."""

from stellar_sdk import StrKey

from polarstellar.stellar.activity import ActivityKind, ActivityPage
from polarstellar.stellar.assets import AssetDetail, validate_asset
from polarstellar.stellar.models import Account, Network
from polarstellar.stellar.providers import AccountError, AccountProvider
from polarstellar.stellar.transaction import TransactionDetail, validate_hash


def validate_account(value: str) -> str:
    address = value.strip()
    if not StrKey.is_valid_ed25519_public_key(address):
        raise AccountError("Enter a valid Stellar G-address. Its checksum must be correct.")
    return address


class AccountService:
    def __init__(self, provider: AccountProvider):
        self.provider = provider

    async def lookup(self, value: str, network: Network) -> Account:
        address = validate_account(value)
        account = await self.provider.get_account(address, network)
        if account.address != address or account.network != network:
            raise AccountError(
                "The provider returned an account for a different search or network."
            )
        return account

    async def activity(
        self, value: str, network: Network, kind: ActivityKind, cursor: str | None = None
    ) -> ActivityPage:
        address = validate_account(value)
        page = await self.provider.get_activity(address, network, kind, cursor)
        if (page.address, page.network, page.kind) != (address, network, kind):
            raise AccountError("Activity does not match this account and network.")
        return page

    async def transaction(self, value: str, network: Network) -> TransactionDetail:
        hash_value = validate_hash(value)
        detail = await self.provider.get_transaction(hash_value, network)
        if detail.hash != hash_value or detail.network != network:
            raise AccountError("Transaction does not match this search and network.")
        return detail

    async def discover_assets(self, code, issuer, network, cursor=None):
        """Validate optional filters before I/O and reject pages from another query or network."""
        from polarstellar.stellar.asset_discovery import PAGE_SIZE, filters, token

        code, issuer = filters(code, issuer)
        if cursor is not None:
            token(cursor)
        page = await self.provider.discover_assets(code, issuer, network, cursor)
        if (page.code, page.issuer, page.network, page.cursor) != (code, issuer, network, cursor):
            raise AccountError("Asset discovery page does not match this query and network.")
        # Adapter metadata alone is insufficient: every row must belong to the
        # saved query/network, and the outgoing cursor must identify its final row.
        if len(page.items) > PAGE_SIZE or page.done != (not page.items):
            raise AccountError("Invalid asset discovery page boundary.")
        for item in page.items:
            validate_asset(item.code, item.issuer)
            if (
                not item.issuer
                or item.network != network
                or (code and item.code != code)
                or (issuer and item.issuer != issuer)
            ):
                raise AccountError("Asset discovery row does not match this query and network.")
        expected = (
            f"{page.items[-1].code}_{page.items[-1].issuer}_{page.items[-1].asset_type}"
            if page.items
            else None
        )
        if page.next_cursor != expected:
            raise AccountError("Asset discovery cursor does not match the final row.")
        if page.next_cursor is not None:
            token(page.next_cursor)
        return page

    async def asset(self, code: str, issuer: str, network: Network) -> AssetDetail:
        """Reject invalid input before I/O and ensure returned identity includes the issuer."""
        code, issuer = validate_asset(code, issuer)
        detail = await self.provider.get_asset(code, issuer, network)
        if (detail.code, detail.issuer, detail.network) != (code, issuer, network):
            raise AccountError("Asset does not match this code, issuer, and network.")
        return detail
