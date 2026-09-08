"""Account provider contract and user-facing failures."""

from typing import Protocol

from polarstellar.stellar.activity import ActivityKind, ActivityPage
from polarstellar.stellar.models import Account, Network


class AccountError(Exception):
    """A lookup failure safe to display without exposing request details."""


class AccountProvider(Protocol):
    async def get_account(self, address: str, network: Network) -> Account: ...

    async def get_activity(
        self, address: str, network: Network, kind: ActivityKind, cursor: str | None = None
    ) -> ActivityPage: ...
