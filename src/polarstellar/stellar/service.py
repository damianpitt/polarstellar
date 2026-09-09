"""Validate user input before any provider access."""

from stellar_sdk import StrKey

from polarstellar.stellar.activity import ActivityKind, ActivityPage
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
