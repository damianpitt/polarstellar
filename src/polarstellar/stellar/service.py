"""Validate user input before any provider access."""

from stellar_sdk import StrKey

from polarstellar.stellar.models import Account, Network
from polarstellar.stellar.providers import AccountError, AccountProvider


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
