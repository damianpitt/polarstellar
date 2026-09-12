"""Provider-independent account snapshots with exact monetary values."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Network(str, Enum):
    """The network identity that must remain part of every lookup and cache key."""

    MAINNET = "Mainnet"
    TESTNET = "Testnet"


@dataclass(frozen=True)
class Balance:
    """An exact asset balance with issuer identity and trustline information."""

    asset: str
    identity: str
    amount: Decimal
    limit: Decimal | None
    authorized: bool | None


@dataclass(frozen=True)
class Account:
    """An account snapshot with original retrieval time and an explicit live/cache label."""

    address: str
    network: Network
    sequence: str
    home_domain: str
    balances: tuple[Balance, ...]
    source: str
    fetched_at: datetime
    cache_status: str = "Live data"


@dataclass(frozen=True)
class Transfer:
    """A successful direct transfer used as evidence in counterparty analysis."""

    network: Network
    sender: str
    recipient: str
    asset: str
    amount: Decimal
    operation: str
    transaction: str
    created: str
