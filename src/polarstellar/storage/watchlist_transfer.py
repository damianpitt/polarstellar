"""Bounded bookmark-only JSON transfer and local filters; imported content is never executed."""

import json
from datetime import UTC, datetime
from pathlib import Path

from polarstellar import __version__
from polarstellar.storage.watchlists import resource, text

FORMAT = "polarstellar.watchlist"
MAX_BYTES = 10_000_000


def matches(entry, query="", network="All networks", kind="All types"):
    """Apply combined local filters to committed identity/annotations without provider access.

    Text search is a case-insensitive substring over identifiers, code, issuer, label
    and notes. It does not canonicalize identity: issued codes themselves remain
    case-sensitive, and network/type selectors match the saved values exactly.
    """
    return (
        (network == "All networks" or entry["network"] == network)
        and (kind == "All types" or entry["kind"] == kind.lower())
        and query.strip().casefold()
        in "\n".join(
            entry.get(key, "") for key in ("identifier", "code", "issuer", "label", "notes")
        ).casefold()
    )


def portable(item, entries=None, annotations=False, filtered=False):
    """Freeze committed bookmarks for export, omitting local names/notes and all snapshots by default.

    Database IDs, creation history, snapshots, search text and private file paths are
    never exported. The scope records whether the whole list or filtered entries were
    chosen, without leaking the actual search text. JSON is the lossless import format.
    """
    rows = []
    for entry in item["entries"] if entries is None else entries:
        keys = ["kind", "network", "identifier"]
        if entry["kind"] == "asset":
            keys += ["code", "issuer"]
        if annotations:
            keys += ["label", "notes"]
        rows.append({key: entry[key] for key in keys})
    watchlist = {"entries": rows}
    if annotations:
        watchlist["name"] = item["name"]
    payload = {
        "format": FORMAT,
        "schema_version": 1,
        "software_version": __version__,
        "exported_at": datetime.now(UTC).isoformat(),
        "scope": "filtered_entries" if filtered else "whole_list",
        "annotations_included": annotations,
        "watchlist": watchlist,
    }
    # Validate our own envelope and its pretty-printed file size. A successful
    # export must remain importable under the same byte limit, even near quota.
    validate(payload)
    if (
        len(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")) + 1
        > MAX_BYTES
    ):
        raise ValueError("Portable export exceeds 10 MB. Export fewer visible entries.")
    return payload


def keys(value, expected, title):
    """Reject unexpected fields and wrong shapes, including IDs/snapshots and shadow identity fields."""
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"Invalid {title} fields in portable watchlist.")


def string(value, limit, title, required=False):
    """Validate annotation/metadata types explicitly; numbers and containers are not coerced to text."""
    if not isinstance(value, str):
        raise TypeError(f"Invalid {title} text in portable watchlist.")
    return text(value, limit, title, required)


def validate(payload):
    """Validate the entire schema and every identity before opening or writing a database.

    Portable schema 1 is independent of the SQLite schema version. Imported names and
    notes are local annotations, never facts about the network. Duplicate identities
    within a file are refused rather than guessing which annotation should win.
    """
    keys(
        payload,
        (
            "format",
            "schema_version",
            "software_version",
            "exported_at",
            "scope",
            "annotations_included",
            "watchlist",
        ),
        "document",
    )
    if (
        payload["format"] != FORMAT
        or type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
    ):
        raise ValueError("Unsupported portable watchlist format or schema version.")
    string(payload["software_version"], 80, "Software version", True)
    try:
        stamp = datetime.fromisoformat(string(payload["exported_at"], 64, "Export time", True))
        if stamp.utcoffset() is None:
            raise ValueError("Timezone required")
    except ValueError as exc:
        raise ValueError("Portable export time must include a valid timezone.") from exc
    if (
        payload["scope"] not in ("whole_list", "filtered_entries")
        or type(payload["annotations_included"]) is not bool
    ):
        raise ValueError("Invalid portable scope or annotation preference.")
    annotations = payload["annotations_included"]
    item = payload["watchlist"]
    keys(item, ("entries", "name") if annotations else ("entries",), "watchlist")
    name = string(item["name"], 100, "List name", True) if annotations else "Imported watchlist"
    rows = item["entries"]
    if not isinstance(rows, list) or len(rows) > 500:
        raise ValueError("Portable watchlist must contain at most 500 entries.")
    result, seen = [], set()
    for entry in rows:
        if not isinstance(entry, dict):
            raise TypeError("Invalid portable entry.")
        kind = entry.get("kind")
        if kind not in ("account", "asset", "contract"):
            raise ValueError("Unsupported portable resource type.")
        expected = ["kind", "network", "identifier"]
        if kind == "asset":
            expected += ["code", "issuer"]
        if annotations:
            expected += ["label", "notes"]
        keys(entry, expected, "entry")
        network = string(entry["network"], 16, "Network", True)
        identifier = string(entry["identifier"], 100, "Identifier", True)
        code = string(entry["code"], 12, "Asset code", True) if kind == "asset" else identifier
        issuer = string(entry["issuer"], 56, "Asset issuer") if kind == "asset" else ""
        identity = resource(kind, code, network, issuer)
        # No whitespace repair, implicit network conversion or identifier/issuer guessing.
        # This also distinguishes native XLM from issued assets named XLM.
        if any(entry[key] != value for key, value in identity.items()):
            raise ValueError("Portable entry identity is inconsistent or noncanonical.")
        key = (kind, network, identifier)
        if key in seen:
            raise ValueError("Duplicate resource identity in portable watchlist.")
        seen.add(key)
        result.append(
            {
                **identity,
                "label": string(entry["label"], 200, "Label") if annotations else "",
                "notes": string(entry["notes"], 10_000, "Notes") if annotations else "",
            }
        )
    # Bounding serialized input covers callers using the API directly rather than read_file.
    try:
        size = len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("Invalid portable JSON text.") from exc
    if size > MAX_BYTES:
        raise ValueError("Portable watchlist exceeds the 10 MB limit.")
    return name, result


def unique_pairs(pairs):
    """Reject duplicate JSON keys at every depth instead of silently taking the final value."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field in portable watchlist.")
        result[key] = value
    return result


def reject_constant(value):
    """Refuse JSON extensions such as NaN/Infinity rather than accepting nonportable numbers."""
    raise ValueError("Nonstandard JSON numeric constant in portable watchlist.")


def read_file(path):
    """Read at most 10 MB plus one byte, parse UTF-8/BOM JSON, and validate before any storage writes."""
    with Path(path).open("rb") as source:
        data = source.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Portable watchlist file exceeds the 10 MB limit.")
    try:
        payload = json.loads(
            data.decode("utf-8-sig"), object_pairs_hook=unique_pairs, parse_constant=reject_constant
        )
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("Choose a valid UTF-8 portable watchlist JSON file.") from exc
    validate(payload)
    return payload
