"""Exercise saved transaction comparison through raw evidence, pagination and the library UI."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication
from test_enrichment import OPERATION, effect, page, transaction
from test_transactions import HASH, metadata, operation

from polarstellar.stellar.models import Network
from polarstellar.stellar.transaction import parse_transaction
from polarstellar.storage.export import plain, serialize, transaction_document
from polarstellar.storage.investigation_comparison import compare_entries
from polarstellar.storage.investigations import InvestigationStore, new_investigation
from polarstellar.ui.investigation_comparison_dialog import InvestigationComparisonDialog
from polarstellar.ui.investigations_view import InvestigationsView


def saved_transaction(success=True, bumped=False):
    """Capture a genuine SDK result fixture using the current saved investigation envelope."""
    parsed = transaction(success, bumped)
    return {
        "kind": "transaction",
        "identifier": parsed.hash,
        "network": parsed.network.value,
        "evidence": [transaction_document(parsed)],
    }


def with_effects(entry, pages):
    """Add explicitly accepted pages in the same layout produced by the transaction inspector."""
    result = deepcopy(entry)
    result["evidence"][0]["records"][0]["effect_pages"] = plain(pages)
    return result


@pytest.mark.parametrize("bumped", [False, True])
def test_legacy_and_enriched_saves_compare_same_transaction_offline(bumped):
    """Old saves without effects remain compatible, retaining verified merge result context."""
    baseline = saved_transaction(bumped=bumped)
    candidate = with_effects(baseline, [])
    document = candidate["evidence"][0]
    document["metadata"].update(source="new provider", fetched_at="2026-11-21T00:00:00Z")
    document["records"][0]["explanation"] = "New presentation"
    original = deepcopy(baseline)
    result = compare_entries(baseline, candidate)
    assert result["status"] == "No differences in compared evidence"
    assert result["comparison_provenance"]["source"] == "new provider"
    before = result["baseline_scope"]["operations"][0]
    after = result["comparison_scope"]["operations"][0]
    assert before["saved_result"]["merge_amount"] == "900000000000.0000001"
    assert not before["effects"]["saved"] and after["effects"]["saved"]
    assert after["effects"]["end_of_available_results"] is None
    assert baseline == original


def test_newly_loaded_effects_are_observations_with_exact_raw_amounts():
    """Show loaded effect fields and their sources without claiming funds moved between saves."""
    baseline = saved_transaction()
    candidate = with_effects(baseline, [page([effect(1)])])
    original = deepcopy(candidate)
    result = compare_entries(baseline, candidate)
    assert result["status"] == "Evidence differs"
    amount = next(row for row in result["changes"] if row["field"][-1] == "amount")
    assert amount["after"] == "900000000000.0000001" and not amount["before_present"]
    assert all(row["change"] == "Newly reported" for row in result["changes"])
    assert result["baseline_scope"]["operations"][0]["effects"]["pages"] == []
    scope = result["comparison_scope"]["operations"][0]["effects"]
    assert scope["pages"][0]["loaded_effect_count"] == 1
    assert (
        scope["pages"][0]["source"]
        == candidate["evidence"][0]["records"][0]["effect_pages"][0]["source"]
    )
    assert scope["end_of_available_results"] is True
    reverse = compare_entries(candidate, baseline)
    assert all(row["change"] == "No longer reported" for row in reverse["changes"])
    assert candidate == original


def test_effect_retrieval_times_and_link_presentation_do_not_create_field_changes():
    """Compare canonical effect identity and preserve original page times separately."""
    baseline = with_effects(saved_transaction(), [page([effect(1)])])
    candidate = deepcopy(baseline)
    page_data = candidate["evidence"][0]["records"][0]["effect_pages"][0]
    page_data["fetched_at"] = "2026-11-21T00:00:00Z"
    record = page_data["records"][0]
    raw = json.loads(record["raw"])
    raw["id"] = f"{OPERATION}-1"
    raw["_links"]["self"] = {"href": "https://example.invalid/unfetched"}
    record.update(identifier=raw["id"], description="New explanation", raw=json.dumps(raw))
    result = compare_entries(baseline, candidate)
    assert result["status"] == "No differences in compared evidence"
    assert result["baseline_scope"] != result["comparison_scope"]


def test_partial_operation_selection_is_distinct_from_missing_instruction():
    """Retain full and partial operation counts while comparing observed rows by their saved IDs."""
    parsed = parse_transaction(
        metadata(), HASH, Network.TESTNET, [operation(1), operation(2)], "fixture"
    )
    baseline = {
        "kind": "transaction",
        "identifier": HASH,
        "network": "Testnet",
        "evidence": [
            transaction_document(
                replace(parsed, operations=parsed.operations[:1], warning="Partial")
            )
        ],
    }
    candidate = {**baseline, "evidence": [transaction_document(parsed)]}
    result = compare_entries(baseline, candidate)
    assert result["status"] == "Evidence differs"
    assert not result["baseline_scope"]["operation_selection_complete"]
    assert result["comparison_scope"]["operation_selection_complete"]
    assert all(row["field"][:2] == ["operations", "2"] for row in result["changes"])


def test_full_effect_page_remains_partial_until_explicit_continuation():
    """Expose the open cursor and preserve already loaded evidence when another page is added."""
    first = page([effect(index) for index in range(1, 21)])
    second = page([effect(21)], first.next_cursor)
    baseline = with_effects(saved_transaction(), [first])
    candidate = with_effects(saved_transaction(), [first, second])
    result = compare_entries(baseline, candidate)
    assert result["baseline_scope"]["operations"][0]["effects"]["end_of_available_results"] is False
    assert (
        result["comparison_scope"]["operations"][0]["effects"]["end_of_available_results"] is True
    )
    assert all(row["field"][2] == f"{OPERATION}-21" for row in result["changes"])


@pytest.mark.parametrize(
    "mutation",
    [
        "entry_network",
        "hash",
        "raw_hash",
        "raw_operation_hash",
        "operation_identity",
        "effect_network",
        "effect_operation",
        "effect_cursor",
        "effect_source",
        "duplicate_page",
        "summary",
        "duplicate_json",
        "unknown_schema",
    ],
)
def test_incompatible_saved_evidence_returns_no_partial_changes(mutation):
    """Fail closed on foreign identity, inconsistent summaries and invalid pagination."""
    baseline = with_effects(saved_transaction(), [page([effect(1)])])
    candidate = deepcopy(baseline)
    snapshot = candidate["evidence"][0]
    row = snapshot["records"][0]
    effect_page = row["effect_pages"][0]
    if mutation == "entry_network":
        candidate["network"] = "Mainnet"
    elif mutation == "hash":
        snapshot["metadata"]["hash"] = "a" * 64
    elif mutation in ("raw_hash", "raw_operation_hash"):
        target = snapshot["metadata"] if mutation == "raw_hash" else row
        raw = json.loads(target["raw"])
        raw["hash" if mutation == "raw_hash" else "transaction_hash"] = "a" * 64
        target["raw"] = json.dumps(raw)
    elif mutation == "operation_identity":
        row["identifier"] = "1234"
    elif mutation == "effect_network":
        effect_page["network"] = "Mainnet"
    elif mutation == "effect_operation":
        effect_page["operation"] = "1234"
    elif mutation == "effect_cursor":
        effect_page["cursor"] = f"{OPERATION}-2"
    elif mutation == "effect_source":
        effect_page["source"] = "different source"
    elif mutation == "duplicate_page":
        row["effect_pages"].append(deepcopy(effect_page))
    elif mutation == "summary":
        snapshot["metadata"]["fee_stroops"] = "999"
    elif mutation == "duplicate_json":
        row["raw"] = row["raw"][:-1] + ', "id": "999"}'
    else:
        snapshot["schema_version"] = 2
    result = compare_entries(baseline, candidate)
    assert result["status"] == "Not comparable" and result["changes"] == []


def test_failed_transaction_keeps_result_context_and_refuses_applied_effects():
    """Preserve failed instruction observations while rejecting an impossible effects selection."""
    baseline = saved_transaction(success=False)
    result = compare_entries(baseline, deepcopy(baseline))
    assert result["status"] == "No differences in compared evidence"
    assert result["baseline_scope"]["operations"][0]["saved_result"]["applied"] is False
    candidate = with_effects(baseline, [page([effect(1)])])
    assert compare_entries(baseline, candidate)["status"] == "Not comparable"


def test_reported_metadata_difference_is_evidence_disagreement():
    """A consistent provider discrepancy remains inspectable without implying transaction mutation."""
    baseline = saved_transaction()
    candidate = deepcopy(baseline)
    metadata = candidate["evidence"][0]["metadata"]
    raw = json.loads(metadata["raw"])
    raw["ledger"] += 1
    metadata.update(ledger=raw["ledger"], raw=json.dumps(raw))
    result = compare_entries(baseline, candidate)
    assert result["changes"][0]["field"] == ["transaction", "ledger"]
    assert result["status"] == "Evidence differs"


@pytest.mark.parametrize("limit", ["fields", "raw_bytes", "projected_bytes"])
def test_comparison_limits_refuse_whole_report(limit, monkeypatch):
    """Large observations produce an explicit refusal with original snapshots still available."""
    import polarstellar.storage.transaction_comparison as module

    attribute = {
        "fields": "MAX_FIELDS",
        "raw_bytes": "MAX_RAW_BYTES",
        "projected_bytes": "MAX_PROJECTION_BYTES",
    }[limit]
    monkeypatch.setattr(module, attribute, 1)
    baseline = saved_transaction()
    result = compare_entries(baseline, deepcopy(baseline))
    assert result["status"] == "Not comparable" and result["changes"] == []


def test_library_transaction_dialog_exports_selected_comparison_privately(tmp_path):
    """Reach comparison through the production library and export the frozen displayed result."""
    app = QApplication.instance() or QApplication([])
    baseline = saved_transaction()
    candidate = with_effects(baseline, [page([effect(1)])])
    foreign = {**candidate, "network": "Mainnet"}
    item = new_investigation("Private case")
    item.update(
        entries=[baseline, foreign, candidate], notes="Private note", labels="Private label"
    )
    store = InvestigationStore(tmp_path / "saved.sqlite3")
    store.save(item)
    view = InvestigationsView(store, object())
    view.reload(item["id"])
    view.notes.setPlainText("Draft note")
    view.entries.setCurrentRow(0)
    view.compare_button.click()
    dialog = view.findChild(InvestigationComparisonDialog)
    assert dialog.candidate.count() == 2
    dialog.candidate.setCurrentIndex(1)
    assert "900000000000.0000001" in dialog.changes.toPlainText()
    assert "loaded_effect_count" in dialog.sources.toPlainText()
    snapshot = dialog.export_document()
    assert snapshot["metadata"]["comparison_entry"] == 3
    for format_name in ("json", "csv"):
        output = serialize(snapshot, format_name)
        assert all(
            text not in output
            for text in ("Private case", "Private note", "Private label", "Draft note")
        )
    view.reload(item["id"])
    assert dialog.export_document()["records"] == snapshot["records"]
    assert store.list() == [item] and view.notes.toPlainText() == "Draft note"
    dialog.reject()
    assert app is not None
