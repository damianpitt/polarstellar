"""Bounded comparison of saved transaction observations, including explicitly loaded effects."""

import json

from polarstellar.stellar.effects import MAX_PAGES, PAGE_SIZE, operation_id, parse_effects
from polarstellar.stellar.models import Network
from polarstellar.stellar.transaction import parse_transaction, validate_hash
from polarstellar.storage.export import plain
from polarstellar.storage.watchlist_comparison import MAX_FIELDS, field_changes, provenance

MAX_RAW_BYTES = 2_000_000
MAX_PROJECTION_BYTES = 4_000_000
COVERAGE = (
    "Compared saved transaction summary, reported operation fields and loaded effect fields only. "
    "Newly reported or unreported evidence describes the two saved selections. "
    "An unreported operation or effect is unknown. A difference for the same transaction hash "
    "does not establish a change to the finalized transaction or independently verify consensus. "
    "Source times, pagination and saved result interpretations stay in Coverage and sources. "
    "Raw decimal text and XDR are preserved exactly; presentation-only explanations are excluded."
)


def canonical(value):
    """Encode JSON deterministically while rejecting nonfinite values and preserving exact strings."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def raw_object(value):
    """Read bounded raw evidence as a JSON object, refusing duplicate names and ambiguous values."""
    if not isinstance(value, str):
        raise TypeError("Raw evidence must be saved JSON text.")
    if len(value.encode("utf-8")) > MAX_RAW_BYTES:
        raise ValueError("Raw evidence exceeds the 2 MB comparison limit.")

    def unique_object(pairs):
        """Reject duplicate JSON members before they can overwrite evidence silently."""
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate field in raw evidence.")
            result[key] = item
        return result

    result = json.loads(value, object_pairs_hook=unique_object)
    if not isinstance(result, dict):
        raise TypeError("Raw evidence must be a JSON object.")
    canonical(result)
    return result


def same_value(_key, before, after):
    """Compare raw JSON values exactly, ignoring object key order but preserving types and array order."""
    return canonical(before) == canonical(after)


def project(snapshot, expected):
    """Validate saved identity and produce fields and independent coverage for one observation.

    Reuse transaction/effect parsers to bind raw records to their saved hash,
    operation and network. Parsing here performs no requests. Decoder explanations
    and saved result interpretations are retained as context, outside the raw diff.
    """
    metadata = snapshot["metadata"]
    network = Network(expected["network"])
    if (metadata["hash"], metadata["network"]) != (expected["identifier"], network.value):
        raise ValueError("Transaction evidence does not match the saved hash and network.")
    rows = snapshot["records"]
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("Unsupported saved operation selection.")
    raw_rows = [raw_object(row["raw"]) for row in rows]
    raw = raw_object(metadata["raw"])
    parsed = parse_transaction(raw, expected["identifier"], network, raw_rows, metadata["source"])
    normalized = plain(parsed)
    fields, operations_scope = {}, []
    size = 0

    def put(key, value):
        """Stop before oversized evidence can produce a partial comparison report."""
        nonlocal size
        size += len(canonical(value).encode("utf-8"))
        if len(fields) >= MAX_FIELDS or size > MAX_PROJECTION_BYTES:
            raise ValueError("Comparison exceeds 2000 fields or 4 MB of projected values.")
        fields[key] = value

    for name in (
        "successful",
        "ledger",
        "created",
        "source_account",
        "fee_account",
        "operation_count",
        "memo",
    ):
        if not same_value(None, metadata[name], normalized[name]):
            raise ValueError(f"Saved transaction {name} disagrees with raw evidence.")
        put(("transaction", name), metadata[name])
    if metadata["fee_stroops"] != str(parsed.fee_stroops):
        raise ValueError("Saved fee disagrees with raw evidence.")
    put(("transaction", "fee_stroops"), metadata["fee_stroops"])
    for name in ("envelope_xdr", "result_xdr", "result_meta_xdr"):
        if name in raw:
            put(("transaction", name), raw[name])

    for position, (row, raw_row, operation) in enumerate(
        zip(rows, raw_rows, parsed.operations, strict=True), 1
    ):
        identifier = operation_id(row["identifier"])
        if identifier != raw_row["paging_token"] or (identifier, row["kind"], row["source"]) != (
            operation.identifier,
            operation.kind,
            operation.source,
        ):
            raise ValueError("Saved operation disagrees with its raw identity.")
        # Position refers to the loaded ordered selection. It is kept in scope,
        # because a partial save can shift positions without changing an instruction.
        for name, value in raw_row.items():
            if name not in ("_links", "paging_token"):
                put(("operations", identifier, name), value)
        operations_scope.append(
            {
                "operation": identifier,
                "loaded_position": position,
                "saved_result": row.get("result"),
                "effects": effect_fields(row, metadata["successful"], network, put),
            }
        )
    scope = {
        "document_created_at": snapshot.get("exported_at"),
        "software_version": snapshot.get("software_version"),
        "reported_operation_count": parsed.operation_count,
        "loaded_operation_count": len(rows),
        "operation_selection_complete": len(rows) == parsed.operation_count,
        "saved_warning": metadata.get("warning"),
        "saved_result_code": metadata.get("result_code"),
        "saved_result_warning": metadata.get("result_warning"),
        "operations": operations_scope,
    }
    return fields, scope


def effect_fields(operation, successful, network, put):
    """Validate each saved effects page and project raw fields by canonical effect cursor.

    Page boundaries and retrieval times remain separate from effect content.
    Legacy saves without effects metadata remain readable and explicitly unknown.
    No amount is summed, and a failed transaction cannot acquire applied effects.
    """
    pages = operation.get("effect_pages", [])
    if not isinstance(pages, list) or len(pages) > MAX_PAGES:
        raise ValueError("Unsupported saved effect pages.")
    if pages and not successful:
        raise ValueError("Failed transaction has incompatible loaded effects.")
    identifier = operation["identifier"]
    scope, cursor = [], None
    for number, page in enumerate(pages, 1):
        if (page["operation"], page["network"], page["cursor"]) != (
            identifier,
            network.value,
            cursor,
        ) or (number > 1 and cursor is None):
            raise ValueError("Saved effect pages have incompatible identity or cursor order.")
        records = page["records"]
        if not isinstance(records, list) or len(records) > PAGE_SIZE:
            raise ValueError("Unsupported saved effects selection.")
        raw_records = [raw_object(record["raw"]) for record in records]
        parsed = parse_effects(
            {"_embedded": {"records": raw_records}}, identifier, network, page["source"], cursor
        )
        if page["next_cursor"] != parsed.next_cursor:
            raise ValueError("Saved effect continuation disagrees with its raw evidence.")
        for record, raw, normalized in zip(
            records, raw_records, plain(parsed)["records"], strict=True
        ):
            for name in ("identifier", "cursor", "kind", "account", "asset", "amount"):
                if not same_value(None, record[name], normalized[name]):
                    raise ValueError("Saved effect disagrees with raw evidence.")
            token = record["cursor"]
            for name, value in raw.items():
                # Effect IDs may use padded numeric text. The validated canonical
                # cursor identifies the same effect across save formats.
                if name not in ("_links", "id", "paging_token"):
                    put(("effects", identifier, token, name), value)
        scope.append(
            {
                "source": page["source"],
                "fetched_at": page["fetched_at"],
                "cursor": page["cursor"],
                "next_cursor": page["next_cursor"],
                "loaded_effect_count": len(records),
            }
        )
        cursor = page["next_cursor"]
    return {
        "saved": "effect_pages" in operation,
        "pages": scope,
        "end_of_available_results": cursor is None if pages else None,
        "page_limit_reached": len(pages) == MAX_PAGES and cursor is not None,
        "coverage": "Loaded pages only. End of available results does not prove complete history.",
    }


def compare_transactions(baseline, comparison):
    """Report observed differences for one hash/network, retaining each side's provenance and scope."""
    expected = {
        "kind": "transaction",
        "identifier": validate_hash(baseline["metadata"]["hash"]),
        "network": Network(baseline["metadata"]["network"]).value,
    }
    before, baseline_scope = project(baseline, expected)
    after, comparison_scope = project(comparison, expected)
    changes = field_changes(before, after, same_value)
    return {
        "identity": expected,
        "status": "Evidence differs" if changes else "No differences in compared evidence",
        "note": "Compare the two saved observations with the coverage and sources for each.",
        "coverage": COVERAGE,
        "baseline_provenance": provenance(baseline),
        "comparison_provenance": provenance(comparison),
        "baseline_scope": baseline_scope,
        "comparison_scope": comparison_scope,
        "changes": changes,
    }
