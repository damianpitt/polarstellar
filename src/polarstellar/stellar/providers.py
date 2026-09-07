"""Account provider contract and user-facing failures."""

from typing import Protocol

from polarstellar.stellar.models import Account, Network


class AccountError(Exception):
    """A lookup failure safe to display without exposing request details."""


class AccountProvider(Protocol):
    async def get_account(self, address: str, network: Network) -> Account: ...
