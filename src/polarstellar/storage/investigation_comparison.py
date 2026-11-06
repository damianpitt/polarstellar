"""Compare two saved resource observations without reopening or modifying their evidence."""

from polarstellar.stellar.providers import AccountError
from polarstellar.storage.export import document
from polarstellar.storage.watchlist_comparison import MAX_FIELDS, compare, identity

PRIMARY_KIND = {"account": "balances", "asset": "asset", "contract": "contract"}
EXTRA_KINDS = {
    "transactions",
    "operations",
    "payments",
    "counterparties",
    "asset_contract_identity",
    "contract_specification",
    "contract_storage",
}
COVERAGE = (
    "Only account details/balances, asset statistics/flags, or known contract instance fields "
    "are compared. Supplemental evidence is listed for context. Loaded row counts and ledger "
    "windows describe the saved selection, even when they match. Missing or null values are "
    "unknown. Observed differences do not establish transfers, ownership, supply, causation, "
    "or intervening history."
)


def resource_key(entry):
    """Group candidates by exact saved network and resource identity, including the asset issuer."""
    kind = entry.get("kind")
    resource = (
        (entry.get("code"), entry.get("issuer")) if kind == "asset" else (entry.get("identifier"),)
    )
    return kind, entry.get("network"), *resource


def primary_document(entry):
    """Find the single supported resource document and adapt the two saved asset layouts.

    Explorer saves put statistics in rows. Explicit investigation refreshes put
    them in metadata. Normalize a temporary document so both formats compare
    identically while the original JSON stays intact.
    """
    expected_kind = PRIMARY_KIND[entry["kind"]]
    matches = [item for item in entry["evidence"] if item.get("kind") == expected_kind]
    if len(matches) != 1:
        raise ValueError(f"Expected one saved {expected_kind} document per entry.")
    snapshot = matches[0]
    if type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1:
        raise ValueError("Unsupported saved evidence schema.")
    metadata, records = snapshot["metadata"], snapshot["records"]
    if not isinstance(metadata, dict) or not isinstance(records, list):
        raise TypeError("Unsupported saved evidence structure.")
    if expected_kind == "balances" and not {"sequence", "home_domain"} <= metadata.keys():
        raise ValueError("Saved account details are incomplete.")
    if expected_kind != "asset":
        return snapshot
    if "statistics" in metadata:
        if records:
            raise ValueError("Ambiguous asset statistics in metadata and records.")
        return snapshot
    if len(records) > MAX_FIELDS:
        raise ValueError(f"Comparison exceeds {MAX_FIELDS} fields.")
    statistics = {}
    for row in records:
        name = row["statistic"]
        if not isinstance(name, str) or name in statistics:
            raise ValueError("Ambiguous asset statistic name.")
        statistics[name] = row["value"]
    return {**snapshot, "metadata": {**metadata, "statistics": statistics}}


def evidence_scope(entry, snapshot):
    """Summarize stored coverage for one side, keeping supplemental data out of the field diff."""
    kinds = {}
    for item in entry["evidence"]:
        kind = item.get("kind")
        if kind == snapshot["kind"]:
            continue
        # Use a fixed vocabulary so unexpected documents cannot inflate CSV metadata.
        counts = kinds.setdefault(
            kind if kind in EXTRA_KINDS else "other", {"documents": 0, "saved_rows": 0}
        )
        counts["documents"] += 1
        records = item.get("records")
        if isinstance(records, list) and counts["saved_rows"] is not None:
            counts["saved_rows"] += len(records)
        else:
            counts["saved_rows"] = None
    scope = {
        "document": snapshot["kind"],
        "software_version": snapshot.get("software_version"),
        "document_created_at": snapshot.get("exported_at"),
        "supplemental_documents": dict(sorted(kinds.items())),
    }
    if snapshot["kind"] == "contract":
        metadata = snapshot["metadata"]
        scope["events"] = {
            "saved_rows": len(snapshot["records"]),
            "queried": metadata.get("events_queried"),
            "start_ledger": metadata.get("start_ledger"),
            "end_ledger": metadata.get("end_ledger"),
            "query_complete": metadata.get("query_complete"),
        }
    return scope


def compare_entries(baseline, comparison):
    """Return bounded changes, source provenance and saved scope, or a clear refusal.

    The caller chooses which observation is the baseline. Save order and source
    retrieval order may differ, so neither timestamps nor positions reorder the pair.
    """
    result = {"coverage": COVERAGE, "changes": []}
    try:
        key = resource_key(baseline)
        if key[0] not in PRIMARY_KIND:
            raise ValueError("Choose an account, asset, or contract snapshot.")
        if key != resource_key(comparison):
            raise ValueError("Both entries must match by resource, network, and asset issuer.")
        expected = identity(baseline)
        # Older native XLM explorer saves used a trailing colon in the row label.
        # The explicit code and empty issuer still identify the same native asset.
        identifiers = (expected["identifier"],)
        if expected["kind"] == "asset" and not expected["issuer"]:
            identifiers += ("XLM:",)
        for entry in (baseline, comparison):
            if entry["identifier"] not in identifiers:
                raise ValueError("Saved identifier does not match the resource identity.")
        before, after = primary_document(baseline), primary_document(comparison)
        baseline_scope = evidence_scope(baseline, before)
        comparison_scope = evidence_scope(comparison, after)
        result.update(compare(baseline, before, after))
        result.update(
            coverage=COVERAGE,
            baseline_scope=baseline_scope,
            comparison_scope=comparison_scope,
            baseline_provenance=result.pop("previous_provenance"),
            comparison_provenance=result.pop("current_provenance"),
        )
        return result
    except (AccountError, KeyError, TypeError, ValueError, AttributeError) as exc:
        return {**result, "status": "Not comparable", "note": str(exc)}


def comparison_document(result, baseline_number, comparison_number):
    """Freeze only the displayed changes and evidence context, excluding all case annotations."""
    return document(
        "investigation_comparison",
        {
            **{key: value for key, value in result.items() if key != "changes"},
            "baseline_entry": baseline_number,
            "comparison_entry": comparison_number,
        },
        result["changes"],
    )
