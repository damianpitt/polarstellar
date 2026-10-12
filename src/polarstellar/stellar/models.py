"""Provider-independent account snapshots with exact monetary values."""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from stellar_sdk import StrKey


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
    """Successful direct transfer evidence with exact amount, time and original operation type.

    An omitted type remains unknown for constructor compatibility and is excluded
    from expanded tracing. Horizon explicitly sets payment/create_account; old cached
    schemas are not guessed.
    """

    network: Network
    sender: str
    recipient: str
    asset: str
    amount: Decimal
    operation: str
    transaction: str
    created: str
    operation_type: str = ""


@dataclass(frozen=True)
class OperationLink:
    """An observed path payment or merge, kept separate from direct-transfer totals.

    Source and destination amounts are actual Horizon observations, never the
    requested maximum/minimum. Merge amounts stay None because the operation
    resource does not report the transferred balance. Path entries identify
    intermediary assets, not accounts, liquidity providers or ownership.
    """

    network: Network
    sender: str
    recipient: str
    source_asset: str
    source_amount: Decimal | None
    destination_asset: str
    destination_amount: Decimal | None
    operation: str
    transaction: str
    created: str
    operation_type: str
    path: tuple[str, ...] = ()
    limit_name: str = ""
    limit_amount: Decimal | None = None

    def __post_init__(self):
        """Refuse inconsistent link semantics, including corrupt cached JSON reconstructed as this class.

        A typed cache field alone cannot guarantee that a path amount exists or that
        a merge has no numeric balance. Validate the combination before the UI can
        format amounts; cache errors fall back to a live lookup in the provider.
        Timestamp validity is checked by tracing, so malformed times stay countable.
        """
        if not isinstance(self.network, Network) or not isinstance(self.path, tuple):
            raise TypeError("Operation links require a network and immutable asset path")
        if not all(
            StrKey.is_valid_ed25519_public_key(party) for party in (self.sender, self.recipient)
        ):
            raise ValueError("Unsupported operation endpoint")
        if (
            not self.operation.isascii()
            or not self.operation.isdecimal()
            or not re.fullmatch(r"[0-9a-fA-F]{64}", self.transaction)
        ):
            raise ValueError("Invalid operation or transaction identity")
        for asset in (self.source_asset, self.destination_asset, *self.path):
            if asset != "XLM":
                code, separator, issuer = asset.partition(":")
                if (
                    not separator
                    or not re.fullmatch(r"[A-Za-z0-9]{1,12}", code)
                    or not StrKey.is_valid_ed25519_public_key(issuer)
                ):
                    raise ValueError("Invalid operation asset identity")
        if self.operation_type == "account_merge":
            if (
                self.source_asset != "XLM"
                or self.destination_asset != "XLM"
                or self.source_amount is not None
                or self.destination_amount is not None
                or self.path
                or self.limit_name
                or self.limit_amount is not None
            ):
                raise ValueError("Merge amount must remain unknown")
            return
        expected = {
            "path_payment_strict_send": "destination_min",
            "path_payment_strict_receive": "source_max",
        }.get(self.operation_type)
        if expected is None or self.limit_name != expected or len(self.path) > 5:
            raise ValueError("Invalid path payment semantics")
        for amount in (self.source_amount, self.destination_amount, self.limit_amount):
            if (
                not isinstance(amount, Decimal)
                or not amount.is_finite()
                or amount < 0
                or amount > Decimal("922337203685.4775807")
                or amount.as_tuple().exponent < -7
            ):
                raise ValueError("Invalid path payment amount")
        if (
            self.source_amount == 0
            or self.destination_amount == 0
            or (expected == "source_max" and self.source_amount > self.limit_amount)
            or (expected == "destination_min" and self.destination_amount < self.limit_amount)
        ):
            raise ValueError("Invalid path payment actual amount or limit")

    def matches_asset(self, asset):
        """Match an endpoint asset exactly, including issuer; None selects all observations."""
        return asset is None or asset in (self.source_asset, self.destination_asset)

    def description(self):
        """Describe both endpoint amounts or an unknown merge balance without inventing totals."""
        if self.operation_type == "account_merge":
            return "Account merge • XLM balance amount unknown (not reported by Horizon)"
        return (
            f"Sent {self.source_amount:f} {self.source_asset} → "
            f"Received {self.destination_amount:f} {self.destination_asset}"
        )
