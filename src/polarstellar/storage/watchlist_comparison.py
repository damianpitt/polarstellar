"""Offline, bounded field comparisons of same-resource watchlist snapshots, without flow inference."""

from decimal import Decimal, InvalidOperation

from polarstellar.storage.export import document
from polarstellar.storage.watchlists import resource, snapshot_digest

MAX_FIELDS = 2000
COVERAGE = (
    "Compared fields in the last two successful snapshots only, not intervening history or an atomic network-wide view. "
    "Missing/null values are unknown, never zero. Observed differences do not establish transfers, ownership, supply or causation. "
    "Ignored fields include export/retrieval timestamps and moving RPC retention/latest-ledger counters; original provenance stays separate."
)


def identity(entry):
    """Validate and return full kind/network/issuer identity, excluding notes and local database IDs."""
    return resource(
        entry["kind"],
        entry.get("code", entry["identifier"]),
        entry["network"],
        entry.get("issuer", ""),
    )


def validate_context(entry, snapshot):
    """Require matching kind/network/full identity before any refresh can save returned evidence."""
    expected = identity(entry)
    metadata = snapshot["metadata"]
    if entry["kind"] == "account":
        valid = snapshot["kind"] == "balances" and (metadata["address"], metadata["network"]) == (
            expected["identifier"],
            expected["network"],
        )
    elif entry["kind"] == "asset":
        valid = snapshot["kind"] == "asset" and (
            metadata["code"],
            metadata["issuer"],
            metadata["network"],
        ) == (expected["code"], expected["issuer"], expected["network"])
    else:
        instance = metadata["instance"]
        valid = snapshot["kind"] == "contract" and (instance["contract"], instance["network"]) == (
            expected["identifier"],
            expected["network"],
        )
    if not valid:
        raise ValueError("Snapshot identity does not match the saved watchlist resource.")
    return metadata


def fields(entry, snapshot):
    """Validate a supported snapshot and flatten only comparable, documented fields.

    Balances are keyed by asset and issuer/pool identity, never row position or symbol
    alone. Contract storage pairs are keyed by exact key XDR and compared as value XDR,
    avoiding changes caused only by newer decoder descriptions. Unknown formats raise
    so callers cannot silently report that incomparable evidence was unchanged.
    """
    metadata = validate_context(entry, snapshot)
    result = {}

    def put(key, value):
        """Bound projection as it grows, and validate finite exact amounts even on first observation."""
        if len(result) >= MAX_FIELDS:
            raise ValueError("Comparison exceeds 2000 fields")
        if amount_field(key) and value is not None:
            equal(key, value, value)
        result[key] = value

    if entry["kind"] == "account":
        for field in ("sequence", "home_domain"):
            if field in metadata:
                put(("account", field), metadata[field])
        seen = set()
        for row in snapshot["records"]:
            key = (row["asset"], row["identity"])
            if not all(isinstance(value, str) for value in key) or key in seen:
                raise ValueError("Ambiguous balance identity")
            seen.add(key)
            for field in ("amount", "limit", "authorized"):
                if field in row:
                    put(("balances", *key, field), row[field])
    elif entry["kind"] == "asset":
        put(("asset", "asset_type"), metadata["asset_type"])
        for group in ("statistics", "flags"):
            if not isinstance(metadata[group], dict):
                raise TypeError("Unsupported asset fields")
            for name, value in metadata[group].items():
                put((group, name), value)
    else:
        instance = metadata["instance"]
        if type(instance["instance_available"]) is not bool:
            raise ValueError("Invalid instance availability")
        for field in (
            "instance_available",
            "executable",
            "wasm_hash",
            "last_modified_ledger",
            "live_until_ledger",
        ):
            if field in instance:
                put(("instance", field), instance[field])
        seen = set()
        for pair in instance.get("instance_storage", []):
            key, value = pair["key"]["xdr"], pair["value"]["xdr"]
            if not isinstance(key, str) or not isinstance(value, str) or key in seen:
                raise ValueError("Ambiguous storage evidence")
            seen.add(key)
            put(("instance_storage", key), value)
    if len(result) > MAX_FIELDS:
        raise ValueError("Comparison exceeds 2000 fields")
    return result


def amount_field(key):
    """Identify exact amount fields whose decimal text may differ only in trailing zeros."""
    return (key[0] == "balances" and key[-1] in ("amount", "limit")) or (
        key[0] == "statistics" and (key[-1].startswith("balances.") or key[-1].endswith("_amount"))
    )


def equal(key, before, after):
    """Compare finite decimal amounts exactly, with no arithmetic or ambient rounding context."""
    if amount_field(key) and before is not None and after is not None:
        if not isinstance(before, str) or not isinstance(after, str):
            raise ValueError("Amount evidence must be exact decimal text")
        try:
            left, right = Decimal(before), Decimal(after)
        except InvalidOperation as exc:
            raise ValueError("Invalid amount evidence") from exc
        if not left.is_finite() or not right.is_finite():
            raise ValueError("Nonfinite amount evidence")
        return left == right
    return type(before) is type(after) and before == after


def provenance(snapshot):
    """Retain original source/retrieval context without counting it as a resource-state change."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("metadata"), dict):
        return None
    metadata = snapshot["metadata"]
    source = metadata.get("instance", metadata)
    if not isinstance(source, dict):
        return None
    return {
        key: source[key]
        for key in (
            "source",
            "fetched_at",
            "cache_status",
            "instance_latest_ledger",
            "last_modified_ledger",
            "live_until_ledger",
        )
        if key in source
    }


def compare(entry, previous, current):
    """Report bounded before/after observations; unsupported history never becomes a false no-change claim."""
    result = {
        "identity": identity(entry),
        "previous_provenance": provenance(previous),
        "current_provenance": provenance(current),
        "coverage": COVERAGE,
        "changes": [],
    }
    try:
        after = fields(entry, current)
        if previous is None:
            return {
                **result,
                "status": "Baseline only",
                "note": "No previous successful snapshot is available.",
            }
        before = fields(entry, previous)
        keys = sorted(set(before) | set(after))
        if len(keys) > MAX_FIELDS:
            raise ValueError("Comparison exceeds 2000 fields")
        changes = []
        for key in keys:
            old_present, new_present = key in before, key in after
            if old_present != new_present or not equal(key, before[key], after[key]):
                changes.append(
                    {
                        "field": list(key),
                        "before_present": old_present,
                        "after_present": new_present,
                        "before": before.get(key),
                        "after": after.get(key),
                        "change": "Newly reported"
                        if not old_present
                        else "No longer reported"
                        if not new_present
                        else "Changed",
                    }
                )
        return {
            **result,
            "status": "Changed" if changes else "No observed changes",
            "note": "Compared supported fields only; source times and raw snapshots remain available.",
            "changes": changes,
        }
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return {**result, "status": "Not comparable", "note": str(exc)}


def compare_entry(entry):
    """Check pair binding before comparing saved state, including writes from older native app versions."""
    previous, current = entry.get("previous_snapshot"), entry.get("snapshot")
    result = compare(entry, previous, current)
    try:
        verified = entry.get("snapshot_pair_current_digest") == snapshot_digest(current)
    except (TypeError, ValueError):
        verified = False
    if previous is not None and not verified:
        return {
            **result,
            "status": "Not comparable",
            "changes": [],
            "note": "Stored pair is unverified: current snapshot was replaced without pair metadata, possibly by an older app. Refresh again to establish a new pair.",
        }
    return result


def comparison_document(entry):
    """Export changes/provenance without repeating entire source snapshots in every CSV row."""
    result = compare_entry(entry)
    return document(
        "watchlist_comparison",
        {key: value for key, value in result.items() if key != "changes"},
        result["changes"],
    )


def pair_document(entry):
    """Freeze the full retained snapshot pair separately from portable bookmark-only transfers."""
    result = compare_entry(entry)
    return document(
        "watchlist_snapshot_pair",
        {
            "identity": identity(entry),
            "coverage": COVERAGE,
            "comparison_status": result["status"],
            "comparison_note": result["note"],
        },
        [{"previous": entry.get("previous_snapshot"), "current": entry.get("snapshot")}],
    )
