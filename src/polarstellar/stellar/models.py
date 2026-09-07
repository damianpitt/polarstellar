"""Provider-independent account snapshots with exact monetary values."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Network(str, Enum):
    MAINNET = "Mainnet"
    TESTNET = "Testnet"


@dataclass(frozen=True)
class Balance:
    asset: str
    identity: str
    amount: Decimal
    limit: Decimal | None
    authorized: bool | None


@dataclass(frozen=True)
class Account:
    address: str
    network: Network
    sequence: str
    home_domain: str
    balances: tuple[Balance, ...]
    source: str
    fetched_at: datetime
