"""Encode cache snapshots as plain JSON without executable object serialization."""

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import UnionType
from typing import get_args, get_origin, get_type_hints

from polarstellar.stellar.activity import ActivityKind, ActivityPage, ActivityRecord
from polarstellar.stellar.models import Account, Balance, Network, Transfer
from polarstellar.stellar.transaction import OperationDetail, TransactionDetail

# Cache files can only reconstruct these known data containers, never arbitrary classes.
TYPES = {
    cls.__name__: cls
    for cls in (
        Account,
        Balance,
        Transfer,
        ActivityPage,
        ActivityRecord,
        TransactionDetail,
        OperationDetail,
    )
}
ENUMS = {"Network": Network, "ActivityKind": ActivityKind}


def encode(value):
    """Convert a snapshot into JSON values while preserving exact decimals and timestamps."""
    if isinstance(value, Enum):
        return {"enum": type(value).__name__, "value": value.value}
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if is_dataclass(value):
        return {
            "type": type(value).__name__,
            "fields": {field.name: encode(getattr(value, field.name)) for field in fields(value)},
        }
    if isinstance(value, tuple):
        return [encode(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ValueError("Unsupported cache value")


def matches(value, annotation) -> bool:
    """Check decoded field types so corrupt JSON cannot masquerade as a valid snapshot."""
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is UnionType:
        return any(matches(value, choice) for choice in args)
    if origin is tuple:
        return isinstance(value, tuple) and all(matches(item, args[0]) for item in value)
    return type(value) is annotation


def decode(value):
    """Restore supported data containers; malformed or unknown encodings fail closed."""
    if isinstance(value, list):
        return tuple(decode(item) for item in value)
    if not isinstance(value, dict):
        return value
    if set(value) == {"decimal"}:
        number = Decimal(value["decimal"])
        if not number.is_finite():
            raise ValueError("Non-finite cache amount")
        return number
    if set(value) == {"datetime"}:
        timestamp = datetime.fromisoformat(value["datetime"])
        if timestamp.tzinfo is None:
            raise ValueError("Cache timestamp must include its timezone")
        return timestamp
    if set(value) == {"enum", "value"}:
        return ENUMS[value["enum"]](value["value"])
    if set(value) == {"type", "fields"}:
        cls = TYPES[value["type"]]
        if set(value["fields"]) != {field.name for field in fields(cls)}:
            raise ValueError("Cache fields do not match this application version")
        restored = {key: decode(item) for key, item in value["fields"].items()}
        hints = get_type_hints(cls)
        if not all(matches(item, hints[key]) for key, item in restored.items()):
            raise ValueError("Invalid cached field type")
        return cls(**restored)
    raise ValueError("Unknown cache encoding")


def dumps(snapshot) -> str:
    """Serialize a snapshot without rounding money or changing its original retrieval time."""
    return json.dumps(encode(snapshot), ensure_ascii=False)


def loads(payload: str):
    """Decode JSON only; no Python code or imports are loaded from the cache file."""
    return decode(json.loads(payload))
