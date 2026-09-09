"""Account provider contract and user-facing failures."""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from polarstellar.stellar.transaction import TransactionDetail

from polarstellar.stellar.activity import ActivityKind, ActivityPage
from polarstellar.stellar.models import Account, Network


class AccountError(Exception):
    """A lookup failure safe to display without exposing request details."""


class AccountProvider(Protocol):
    async def get_account(self, address: str, network: Network) -> Account: ...

    async def get_activity(
        self, address: str, network: Network, kind: ActivityKind, cursor: str | None = None
    ) -> ActivityPage: ...

    async def get_transaction(self, hash_value: str, network: Network) -> "TransactionDetail": ...
