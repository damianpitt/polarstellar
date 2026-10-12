"""Immutable local trace filters with explicit timezone and inclusive time boundaries."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

DIRECT_TYPES = ("payment", "create_account")
LINK_TYPES = ("path_payment_strict_send", "path_payment_strict_receive", "account_merge")
OPERATION_TYPES = (*DIRECT_TYPES, *LINK_TYPES)
FILTER_REASONS = ("Operation type filter", "Outside selected time range")


def instant(value):
    """Parse an ISO-8601 date-time with an explicit timezone and normalize it to UTC.

    Date-only and naive local times are refused: guessing the computer's timezone
    would silently change which transactions contribute. The original evidence
    timestamp is not rewritten; normalization is for comparison/coverage only.
    """
    # Reject sub-microsecond precision rather than letting datetime silently truncate it.
    pattern = r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})"
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(pattern, value):
        raise ValueError("Use an ISO date-time with timezone, e.g. 2026-10-01T00:00:00Z.")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("Missing timezone")
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Time must be a valid ISO date-time with an explicit timezone.") from exc


@dataclass(frozen=True)
class TraceFilters:
    """Choose a bounded inclusive interval and supported operation types, without requesting data."""

    start: datetime | None = None
    end: datetime | None = None
    operations: tuple[str, ...] = OPERATION_TYPES

    def __post_init__(self):
        """Reject invalid ranges/types before any view can replace its applied filters."""
        for name, bound in (("start", self.start), ("end", self.end)):
            if bound is not None and (not isinstance(bound, datetime) or bound.utcoffset() is None):
                raise ValueError("Trace time boundaries must include a timezone.")
            if bound is not None:
                object.__setattr__(self, name, bound.astimezone(UTC))
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("From time must be earlier than or equal to Until time.")
        if (
            not isinstance(self.operations, tuple)
            or not self.operations
            or any(kind not in OPERATION_TYPES for kind in self.operations)
            or len(set(self.operations)) != len(self.operations)
        ):
            raise ValueError("Choose supported payment, funding, path-payment or merge types.")

    def reason(self, flow):
        """Return one deterministic filtering reason; an operation is never counted twice."""
        if flow.operation_type not in self.operations:
            return FILTER_REASONS[0]
        created = instant(flow.created)
        if (self.start is not None and created < self.start) or (
            self.end is not None and created > self.end
        ):
            return FILTER_REASONS[1]
        return ""

    def metadata(self):
        """Describe applied UTC boundaries and types separately from unsubmitted UI edits."""
        return {
            "from_utc": self.start.astimezone(UTC).isoformat() if self.start else None,
            "until_utc": self.end.astimezone(UTC).isoformat() if self.end else None,
            "operation_types": list(self.operations),
            "time_bounds": "inclusive",
            "scope": "Local selection of loaded successful direct transfers and separate path/merge observations",
        }
