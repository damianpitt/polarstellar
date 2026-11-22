"""Validate public account authority and reserve sponsorship reported by Horizon."""

import json
from dataclasses import dataclass

from stellar_sdk import SignerKey, StrKey


def signed_payload_key(value):
    """Validate a P-address and its canonical payload padding using the SDK's signer representation."""
    if not StrKey.is_valid_ed25519_signed_payload(value):
        return False
    decoded = SignerKey.ed25519_signed_payload(value).to_signed_payload_signer()
    return SignerKey.ed25519_signed_payload(decoded).encoded_signer_key == value


SIGNER_VALIDATORS = {
    "ed25519_public_key": StrKey.is_valid_ed25519_public_key,
    "preauth_tx": StrKey.is_valid_pre_auth_tx,
    "sha256_hash": StrKey.is_valid_sha256_hash,
    "ed25519_signed_payload": signed_payload_key,
}
THRESHOLDS = ("low_threshold", "med_threshold", "high_threshold")
FLAGS = ("auth_required", "auth_revocable", "auth_immutable", "auth_clawback_enabled")
COUNTS = ("num_sponsoring", "num_sponsored", "subentry_count", "last_modified_ledger")
MAX_SIGNERS = 100
MAX_RAW_BYTES = 2_000_000


def integer(value, maximum):
    """Accept a reported unsigned integer within its protocol range, rejecting bools and strings."""
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("Expected an unsigned integer in range.")
    return value


def sponsor(value):
    """Validate an optional reserve sponsor as an exact G-address; absence remains unreported."""
    if value is not None and (
        not isinstance(value, str) or not StrKey.is_valid_ed25519_public_key(value)
    ):
        raise ValueError("Invalid reserve sponsor G-address.")
    return value


@dataclass(frozen=True)
class AccountSigner:
    """One reported signer key/type/weight with its optional reserve sponsor.

    Unknown future types retain their bounded key text. Only known key encodings
    are validated, and none of these observations establish who controls a key.
    """

    key: str
    kind: str
    weight: int
    sponsor: str | None = None

    def __post_init__(self):
        """Enforce the same validation for provider input and decoded cache objects."""
        if (
            not isinstance(self.key, str)
            or not 1 <= len(self.key) <= 512
            or not isinstance(self.kind, str)
            or not 1 <= len(self.kind) <= 64
        ):
            raise ValueError("Invalid signer key or type.")
        integer(self.weight, 255)
        sponsor(self.sponsor)
        validator = SIGNER_VALIDATORS.get(self.kind)
        if validator is not None and not validator(self.key):
            raise ValueError("Signer key encoding does not match its type.")


@dataclass(frozen=True)
class AccountAuthority:
    """Optional public configuration from the account's original source and retrieval time.

    None means unavailable/unreported. An empty signer tuple is an explicitly
    reported empty selection. Warnings describe omitted invalid fields, and raw
    JSON retains the reported configuration for inspection.
    """

    low_threshold: int | None = None
    med_threshold: int | None = None
    high_threshold: int | None = None
    auth_required: bool | None = None
    auth_revocable: bool | None = None
    auth_immutable: bool | None = None
    auth_clawback_enabled: bool | None = None
    signers: tuple[AccountSigner, ...] | None = None
    sponsor: str | None = None
    num_sponsoring: int | None = None
    num_sponsored: int | None = None
    subentry_count: int | None = None
    last_modified_ledger: int | None = None
    warnings: tuple[str, ...] = ()
    raw: str = ""

    def __post_init__(self):
        """Reject malformed normalized/cache state before it can be displayed as reported configuration."""
        for name in THRESHOLDS:
            value = getattr(self, name)
            if value is not None:
                integer(value, 255)
        for name in COUNTS:
            value = getattr(self, name)
            if value is not None:
                integer(value, 2**32 - 1)
        for name in FLAGS:
            if getattr(self, name) is not None and type(getattr(self, name)) is not bool:
                raise ValueError("Expected a reported boolean flag.")
        sponsor(self.sponsor)
        if self.signers is not None and (
            not isinstance(self.signers, tuple)
            or len(self.signers) > MAX_SIGNERS
            or not all(isinstance(item, AccountSigner) for item in self.signers)
            or len({item.key for item in self.signers}) != len(self.signers)
        ):
            raise ValueError("Invalid or duplicate signer selection.")
        if not isinstance(self.warnings, tuple) or not all(
            isinstance(item, str) for item in self.warnings
        ):
            raise ValueError("Invalid authority warnings.")
        if not isinstance(self.raw, str) or len(self.raw.encode("utf-8")) > MAX_RAW_BYTES:
            raise ValueError("Invalid or oversized raw authority fields.")


def parse_authority(data, balance_warnings=()):
    """Parse optional groups independently, preserving balances when authority fields are invalid."""
    values, warnings = {}, list(balance_warnings)
    raw_fields = {
        name: data[name]
        for name in ("thresholds", "flags", "signers", "sponsor", *COUNTS)
        if name in data
    }
    # Keep the asset/issuer and row position for reported balance sponsors without
    # duplicating full balances or unrelated account data in the raw authority tab.
    raw_fields["balance_sponsors"] = [
        {
            "balance_row": index,
            **{
                name: row[name]
                for name in (
                    "asset_type",
                    "asset_code",
                    "asset_issuer",
                    "liquidity_pool_id",
                    "sponsor",
                )
                if name in row
            },
        }
        for index, row in enumerate(data["balances"], 1)
        if "sponsor" in row
    ]

    def read(name, value, parser):
        """Contain one invalid optional field and leave an explicit warning in its place."""
        if value is None:
            return
        try:
            values[name] = parser(value)
        except (KeyError, TypeError, ValueError, AttributeError):
            warnings.append(f"{name} is unavailable because the reported value is invalid.")

    for group, names in (("thresholds", THRESHOLDS), ("flags", FLAGS)):
        fields = data.get(group)
        if fields is None:
            continue
        if not isinstance(fields, dict):
            warnings.append(f"{group} are unavailable because the reported group is invalid.")
            continue
        for name in names:
            read(name, fields.get(name), boolean if group == "flags" else weight)
    for name in COUNTS:
        read(name, data.get(name), count)
    read("sponsor", data.get("sponsor"), sponsor)
    read("signers", data.get("signers"), parse_signers)
    signers = values.get("signers") or ()
    if any(item.kind not in SIGNER_VALIDATORS for item in signers):
        warnings.append("Unrecognized signer types retain their keys without encoding validation.")
    try:
        raw = json.dumps(raw_fields, ensure_ascii=False, allow_nan=False, indent=2)
    except (TypeError, ValueError):
        raw = ""
        warnings.append("Raw authority fields could not be retained as valid JSON.")
    if len(raw.encode("utf-8")) > MAX_RAW_BYTES:
        raw = ""
        warnings.append("Raw authority fields exceed 2 MB and were not retained.")
    if not values and not warnings and raw_fields == {"balance_sponsors": []}:
        return None
    return AccountAuthority(**values, warnings=tuple(warnings), raw=raw)


def boolean(value):
    """Keep reported false distinct from missing flags and refuse truthy non-booleans."""
    if type(value) is not bool:
        raise ValueError("Expected a boolean.")
    return value


def weight(value):
    """Validate a signer/threshold uint8 including a genuinely reported zero."""
    return integer(value, 255)


def count(value):
    """Validate account reserve/subentry counts and ledger numbers as uint32 values."""
    return integer(value, 2**32 - 1)


def parse_signers(rows):
    """Require an unambiguous whole signer list, so an invalid row cannot imply a complete partial set."""
    if not isinstance(rows, list) or len(rows) > MAX_SIGNERS:
        raise ValueError("Unsupported signer selection.")
    result = tuple(
        AccountSigner(row["key"], row["type"], row["weight"], sponsor(row.get("sponsor")))
        for row in rows
    )
    if len({item.key for item in result}) != len(result):
        raise ValueError("Duplicate signer keys.")
    return result


def authority_from_plain(value):
    """Validate saved JSON authority fields for comparison without changing the original snapshot."""
    if not isinstance(value, dict):
        raise TypeError("Unsupported saved authority fields.")
    fields = dict(value)
    if fields.get("signers") is not None:
        fields["signers"] = tuple(AccountSigner(**row) for row in fields["signers"])
    if "warnings" in fields:
        fields["warnings"] = tuple(fields["warnings"])
    return AccountAuthority(**fields)
