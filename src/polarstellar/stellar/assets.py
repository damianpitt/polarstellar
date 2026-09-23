"""Validate asset identities and preserve Horizon statistics without inventing supply."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from stellar_sdk import StrKey

from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError

STATES = ("authorized", "authorized_to_maintain_liabilities", "unauthorized")
FLAGS = ("auth_required", "auth_revocable", "auth_immutable", "auth_clawback_enabled")


@dataclass(frozen=True)
class AssetDetail:
    """A network-scoped asset snapshot; missing statistics remain unknown, never zero."""

    code: str
    issuer: str
    network: Network
    asset_type: str
    statistics: dict
    flags: dict
    source: str
    fetched_at: datetime
    coverage: str
    cache_status: str = "Live data (asset caching unavailable)"


def validate_asset(code: str, issuer: str) -> tuple[str, str]:
    """Accept native XLM or an exact, case-sensitive issued code plus checksum-valid issuer."""
    code, issuer = code.strip(), issuer.strip()
    if code == "XLM" and not issuer:
        return code, issuer
    if not re.fullmatch(r"[a-zA-Z0-9]{1,12}", code) or not StrKey.is_valid_ed25519_public_key(
        issuer
    ):
        raise AccountError(
            "Enter a 1–12 character alphanumeric asset code and valid issuer G-address. "
            "For native XLM, leave the issuer empty."
        )
    return code, issuer


def parse_asset(row: dict, code: str, issuer: str, network: Network, source: str) -> AssetDetail:
    """Validate identity and available statistics, retaining absent fields as None.

    Authorization groups and holding locations are presented separately. They are
    not summed into a misleading circulating-supply figure or market valuation.
    """
    expected = "credit_alphanum4" if len(code) <= 4 else "credit_alphanum12"
    if (row["asset_code"], row["asset_issuer"], row["asset_type"]) != (code, issuer, expected):
        raise ValueError("Asset identity mismatch")
    statistics = {}
    for group in ("accounts", "balances"):
        values = row.get(group, {})
        if not isinstance(values, dict):
            raise TypeError("Invalid statistics group")
        for state in STATES:
            statistics[f"{group}.{state}"] = statistic(values.get(state), group == "balances")
    for location in ("claimable_balances", "liquidity_pools", "contracts"):
        statistics[f"num_{location}"] = statistic(row.get(f"num_{location}"), False)
        statistics[f"{location}_amount"] = statistic(row.get(f"{location}_amount"), True)
    flags = row.get("flags", {})
    if not isinstance(flags, dict):
        raise TypeError("Invalid issuer flags")
    for key in FLAGS:
        if flags.get(key) is not None and type(flags[key]) is not bool:
            raise ValueError("Invalid issuer flag")
    return AssetDetail(
        code,
        issuer,
        network,
        expected,
        statistics,
        {key: flags.get(key) for key in FLAGS},
        source,
        datetime.now(UTC),
        "Horizon asset statistics at retrieval time, not circulating supply or "
        "valuation. Missing fields are unknown. Issuer flags are not proof of trust.",
    )


def statistic(value, amount: bool):
    """Validate nonnegative counts or exact decimal amounts while preserving unknown values."""
    if value is None:
        return None
    if not amount:
        if type(value) is not int or value < 0:
            raise ValueError("Invalid count")
        return value
    if not isinstance(value, str):
        raise TypeError("Amount must be exact text")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid amount") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("Invalid amount")
    return parsed
