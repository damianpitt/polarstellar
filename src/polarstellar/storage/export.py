"""Portable investigation snapshots and atomic, UTF-8 CSV/JSON file writing."""

import csv
import io
import json
import os
import tempfile
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from polarstellar import __version__
from polarstellar.stellar.activity import HEADERS


def plain(value):
    """Convert domain snapshots to JSON values without rounding monetary amounts."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {field.name: plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


def document(kind, metadata, rows):
    """Freeze an export envelope before a file dialog can process other UI events."""
    return plain(
        {
            "schema_version": 1,
            "software_version": __version__,
            "exported_at": datetime.now(UTC),
            "kind": kind,
            "metadata": metadata,
            "records": rows,
        }
    )


def account_document(account):
    """Export the account snapshot, with one CSV/JSON record per asset balance."""
    metadata = plain(account)
    del metadata["balances"]
    metadata["coverage"] = "Account snapshot; not historical balances."
    return document("balances", metadata, account.balances)


def activity_document(view):
    """Capture loaded activity and every page's provenance, including mixed cache ages."""
    pages = []
    for page in view.export_pages:
        item = plain(page)
        del item["records"]
        pages.append(item)
    times = [record.values[0] for record in view.records]
    metadata = {
        "address": view.context[0],
        "network": view.context[1],
        "pages": pages,
        "next_cursor": view.cursor,
        "end_of_available_results": view.done,
        "loaded_record_count": len(view.records),
        "oldest_record_time": min(times) if times else None,
        "newest_record_time": max(times) if times else None,
        "coverage": "Loaded records only; Horizon history may be limited, even at end of results.",
    }
    rows = []
    for record in view.records:
        row = dict(zip(HEADERS[view.kind], record.values, strict=True))
        row.update(
            identifier=record.identifier,
            cursor=record.cursor,
            transfer=record.transfer,
            exclusion=record.exclusion,
        )
        rows.append(row)
    return document(view.kind.value, metadata, rows)


def graph_document(view):
    """Export all filtered table relationships and their evidence, without the canvas cap."""
    metadata = activity_document(view.payments)["metadata"]
    metadata.update(
        filters={"asset": view.asset.currentData(), "direction": view.direction.currentText()},
        excluded=view.analysis.excluded,
        coverage="Filtered relationships from loaded successful direct payments/funding only; "
        "not lifetime totals or proof of ownership. Exclusions cover all loaded payment records.",
    )
    return document("counterparties", metadata, view.relationships)


def transaction_document(transaction):
    """Keep transaction metadata, raw evidence and any partial-operation warning together."""
    metadata = plain(transaction)
    del metadata["operations"]
    # Fees remain strings so JSON consumers never lose precision in large integers.
    metadata["fee_stroops"] = str(transaction.fee_stroops)
    metadata["coverage"] = "Loaded transaction operations; inspect warning and operation_count."
    return document("transaction", metadata, transaction.operations)


def csv_cell(value):
    """Encode nested evidence as JSON and neutralize spreadsheet formula prefixes."""
    text = (
        json.dumps(value, ensure_ascii=False)
        if isinstance(value, (dict, list))
        else ("" if value is None else str(value))
    )
    # Quoting alone does not stop spreadsheet formulas. Prefix suspicious cells with
    # an apostrophe; JSON remains the lossless choice for the original text values.
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def serialize(snapshot, format_name):
    """Render JSON or a rectangular CSV carrying metadata on every row, even if empty."""
    if format_name == "json":
        return json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if format_name != "csv":
        raise ValueError("Choose CSV or JSON.")
    common = {key: value for key, value in snapshot.items() if key not in ("records", "metadata")}
    common.update({"metadata." + key: value for key, value in snapshot["metadata"].items()})
    columns = list(dict.fromkeys(key for row in snapshot["records"] for key in row))
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([*common, *["record." + key for key in columns]])
    # An empty successful response still deserves an auditable metadata row.
    for row in snapshot["records"] or [{}]:
        writer.writerow(
            [csv_cell(value) for value in common.values()]
            + [csv_cell(row.get(key)) for key in columns]
        )
    return output.getvalue()


def write_export(path, snapshot, format_name):
    """Replace the chosen destination only after a complete temporary file is written.

    Failures propagate to the UI. Temporary files live beside the destination so
    replacement is atomic on the same filesystem, and are cleaned up on failure.
    """
    contents = serialize(snapshot, format_name)
    destination = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=destination.parent,
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
