"""Verify saved graph/timeline comparisons preserve exact evidence and separate selection scope."""

from copy import deepcopy
from decimal import localcontext

import pytest
from PySide6.QtWidgets import QApplication
from stellar_sdk import StrKey
from test_timeline import mixed
from test_tracing import A, B, C, E, page, seeded, transfer

from polarstellar.analysis.tracing import Trace
from polarstellar.stellar.models import Network
from polarstellar.storage.export import serialize
from polarstellar.storage.investigation_comparison import compare_entries
from polarstellar.storage.investigations import InvestigationStore, new_investigation
from polarstellar.ui.investigation_comparison_dialog import InvestigationComparisonDialog
from polarstellar.ui.investigations_view import InvestigationsView


def saved(snapshot):
    """Wrap an actual graph/timeline export in the production saved investigation layout."""
    return {
        "kind": snapshot["kind"],
        "identifier": snapshot["metadata"]["root"],
        "network": snapshot["metadata"]["network"],
        "evidence": [snapshot],
    }


def test_expansion_reports_new_evidence_with_original_sources():
    """A newly fetched peer introduces observations while baseline coverage stays visibly partial."""
    trace = seeded()
    baseline = saved(trace.export())
    trace.add_page(B, page(B, [transfer(90, B, C, "0.0000001")]))
    candidate = saved(trace.export())
    originals = deepcopy((baseline, candidate))
    result = compare_entries(baseline, candidate)
    assert result["status"] == "Evidence differs"
    assert {row["field"][0] for row in result["changes"]} == {"relationships", "operations"}
    assert all(row["change"] == "Newly reported" for row in result["changes"])
    assert result["baseline_provenance"]["accounts"][0]["end_of_available_results"] is False
    assert len(result["comparison_provenance"]["accounts"]) == 2
    assert (baseline, candidate) == originals
    reverse = compare_entries(candidate, baseline)
    assert all(row["change"] == "No longer reported" for row in reverse["changes"])


def test_exact_totals_and_operation_amounts_survive_small_decimal_context():
    """Compare full exact values without rounding, net deltas, or cross-hop totals."""
    first = Trace(A, Network.TESTNET, [page(A, [transfer(100, A, B, "900000000.0000001")])])
    second = Trace(A, Network.TESTNET, [page(A, [transfer(100, A, B, "900000000.0000002")])])
    with localcontext() as context:
        context.prec = 2
        result = compare_entries(saved(first.export()), saved(second.export()))
    edge = next(row for row in result["changes"] if row["field"][0] == "relationships")
    assert edge["before"]["total"] == "900000000.0000001"
    assert edge["after"]["total"] == "900000000.0000002"
    assert "delta" not in edge


def test_legacy_graph_and_reordered_evidence_remain_comparable():
    """Read graphs without record_kind/type fields and avoid changes from order or decimal formatting."""
    trace = seeded()
    trace.add_page(A, page(A, [transfer(90, A, B)]))
    baseline = saved(trace.export())
    candidate = deepcopy(baseline)
    first, second = baseline["evidence"][0], candidate["evidence"][0]
    first["records"][0].pop("record_kind")
    for snapshot in (first, second):
        for row in snapshot["records"][0]["evidence"]:
            row.pop("operation_type")
            row["amount"] = "1.0"
    second["records"].reverse()
    second["records"][0]["evidence"].reverse()
    second["records"][0]["total"] += "0"
    second["records"][0]["evidence"][0]["amount"] += "0"
    second["metadata"]["accounts"][0]["pages"][0]["fetched_at"] = "2026-11-21T00:00:00Z"
    result = compare_entries(baseline, candidate)
    assert result["status"] == "No differences in compared evidence"
    assert result["baseline_provenance"] != result["comparison_provenance"]


def test_same_code_assets_and_directions_stay_distinct():
    """Keep issued XLM separate by issuer and keep sender/recipient direction in relationship identity."""
    first = Trace(A, Network.TESTNET, [page(A, [transfer(100, A, B, asset="XLM:" + C)])])
    second = Trace(A, Network.TESTNET, [page(A, [transfer(100, B, A, asset="XLM:" + E)])])
    result = compare_entries(saved(first.export()), saved(second.export()))
    edges = [row for row in result["changes"] if row["field"][0] == "relationships"]
    assert len(edges) == 2
    assert {row["field"][-1] for row in edges} == {"XLM:" + C, "XLM:" + E}


def test_timeline_display_order_changes_scope_and_preserves_sources():
    """Reversing display groups keeps matched operations stable and preserves original timing context."""
    trace = mixed()
    baseline = saved(trace.timeline_export())
    candidate = saved(trace.timeline_export(newest_first=True))
    result = compare_entries(baseline, candidate)
    assert result["status"] == "No differences in compared evidence"
    assert result["baseline_scope"]["selection"]["timeline"]["order"] == "Oldest first"
    assert result["comparison_scope"]["selection"]["timeline"]["order"] == "Newest first"
    assert result["baseline_scope"]["timeline_observations"][0]["observed_in"]


def test_asset_filter_hides_evidence_without_claiming_zero():
    """Record changed selection and report operations outside it as unreported."""
    trace = mixed()
    result = compare_entries(
        saved(trace.timeline_export()), saved(trace.timeline_export("USD:" + C))
    )
    assert result["status"] == "Evidence differs"
    assert result["comparison_scope"]["selection"]["asset_filter"] == "USD:" + C
    assert all(row["change"] == "No longer reported" for row in result["changes"])
    assert all(row["after"] is None and not row["after_present"] for row in result["changes"])


@pytest.mark.parametrize("kind", ["expanded_graph", "trace_timeline"])
def test_merge_amount_stays_unknown_in_graph_and_timeline(kind):
    """Preserve an account merge's unknown balance while comparing other saved observations."""
    trace = mixed()
    snapshot = trace.export() if kind == "expanded_graph" else trace.timeline_export()
    result = compare_entries(saved(snapshot), saved(deepcopy(snapshot)))
    assert result["status"] == "No differences in compared evidence"
    corrupted = deepcopy(snapshot)
    row = next(row for row in corrupted["records"] if row.get("operation_type") == "account_merge")
    if kind == "trace_timeline":
        row["evidence"]["source_amount"] = "0.0000000"
    else:
        row["source_amount"] = "0.0000000"
    assert compare_entries(saved(snapshot), saved(corrupted))["status"] == "Not comparable"


@pytest.mark.parametrize(
    "mutation",
    [
        "entry_network",
        "root",
        "page_network",
        "flow_network",
        "issuer",
        "duplicate",
        "total",
        "timeline_source",
        "timeline_summary",
        "unknown_kind",
    ],
)
def test_incompatible_evidence_returns_no_partial_changes(mutation):
    """Refuse foreign identities, invalid totals and contradictions before reporting a diff."""
    timeline = mutation.startswith("timeline")
    baseline = saved(mixed().timeline_export() if timeline else seeded().export())
    candidate = deepcopy(baseline)
    snapshot = candidate["evidence"][0]
    if mutation == "entry_network":
        candidate["network"] = "Mainnet"
    elif mutation == "root":
        snapshot["metadata"]["root"] = B
    elif mutation == "page_network":
        snapshot["metadata"]["accounts"][0]["pages"][0]["network"] = "Mainnet"
    elif mutation == "flow_network":
        snapshot["records"][0]["evidence"][0]["network"] = "Mainnet"
    elif mutation == "issuer":
        snapshot["records"][0]["evidence"][0]["asset"] = "USD:invalid"
    elif mutation == "duplicate":
        snapshot["records"].append(deepcopy(snapshot["records"][0]))
    elif mutation == "total":
        snapshot["records"][0]["total"] = "0.0000000"
    elif mutation == "timeline_source":
        snapshot["records"][0]["observed_in"][0]["account"] = B
    elif mutation == "timeline_summary":
        snapshot["records"][0]["source_amount"] = "0.0000000"
    else:
        snapshot["records"][0]["record_kind"] = "unsupported"
    result = compare_entries(baseline, candidate)
    assert result["status"] == "Not comparable" and result["changes"] == []


@pytest.mark.parametrize("limit", ["MAX_FIELDS", "MAX_PROJECTION_BYTES"])
def test_bounded_comparison_fails_without_partial_output(limit, monkeypatch):
    """Stop an oversized projection without changing the saved snapshot."""
    import polarstellar.storage.trace_comparison as module

    baseline = saved(seeded().export())
    monkeypatch.setattr(module, limit, 1)
    result = compare_entries(baseline, deepcopy(baseline))
    assert result["status"] == "Not comparable" and result["changes"] == []


@pytest.mark.parametrize("kind", ["expanded_graph", "trace_timeline"])
def test_all_thousand_loaded_operations_can_be_compared(kind):
    """Support the existing trace resource limit instead of silently truncating a valid timeline."""
    peers = [StrKey.encode_ed25519_public_key(bytes([index]) * 32) for index in range(10, 19)]
    trace = Trace(A, Network.TESTNET)
    token = 10000
    for address in [A, *peers]:
        for index in range(5):
            records = []
            for offset in range(20):
                recipient = peers[offset % len(peers)] if address == A else address
                records.append(transfer(token, A, recipient))
                token -= 1
            trace.add_page(
                address, page(address, records, records[-1].cursor if index < 4 else None)
            )
    baseline = saved(trace.export() if kind == "expanded_graph" else trace.timeline_export())
    result = compare_entries(baseline, deepcopy(baseline))
    assert result["status"] == "No differences in compared evidence"
    assert result["baseline_scope"]["compared_operation_count"] == 1000


def test_library_compare_and_exports_preserve_drafts_and_full_evidence(tmp_path):
    """Reach graph comparison through the normal library controls and keep annotations private."""
    app = QApplication.instance() or QApplication([])
    trace = seeded()
    baseline = saved(trace.export())
    trace.add_page(B, page(B, [transfer(90, B, C)]))
    candidate = saved(trace.export())
    timeline = saved(trace.timeline_export())
    item = new_investigation("Private graph case")
    item.update(
        entries=[baseline, timeline, candidate], notes="Private notes", labels="Private label"
    )
    store = InvestigationStore(tmp_path / "saved.sqlite3")
    store.save(item)
    view = InvestigationsView(store, object())
    view.reload(item["id"])
    view.entries.setCurrentRow(0)
    view.notes.setPlainText("Draft graph notes")
    view.annotations.setChecked(True)
    view.compare_button.click()
    dialog = view.findChild(InvestigationComparisonDialog)
    assert dialog.candidate.count() == 2
    dialog.candidate.setCurrentIndex(1)
    result = dialog.export_document()
    assert result["metadata"]["comparison_entry"] == 3
    assert "supporting_operations" in dialog.changes.toPlainText()
    assert "next_cursor" in dialog.sources.toPlainText()
    for format_name in ("json", "csv"):
        contents = serialize(result, format_name)
        assert all(
            value not in contents
            for value in (item["name"], item["labels"], item["notes"], "Draft graph notes")
        )
    view.reload(item["id"])
    assert dialog.export_document()["records"] == result["records"]
    assert view.notes.toPlainText() == "Draft graph notes" and store.list() == [item]
    dialog.reject()
    assert app is not None
