"""Verify offline comparison across real saved evidence layouts and library controls."""

import asyncio
import json
from copy import deepcopy

import pytest
from PySide6.QtWidgets import QApplication
from test_accounts import ADDRESS, ISSUER
from test_assets import asset_row
from test_contracts import CONTRACT
from test_contracts import provider as rpc_fixture
from test_investigations import entry

from polarstellar.stellar.assets import parse_asset
from polarstellar.stellar.horizon import HorizonProvider
from polarstellar.stellar.models import Network
from polarstellar.stellar.service import AccountService
from polarstellar.storage.export import document, plain, serialize
from polarstellar.storage.investigation_comparison import MAX_FIELDS, compare_entries
from polarstellar.storage.investigations import InvestigationStore, new_investigation
from polarstellar.ui.asset_view import AssetView
from polarstellar.ui.investigation_comparison_dialog import InvestigationComparisonDialog
from polarstellar.ui.investigations_view import InvestigationsView


@pytest.fixture
def app():
    """Keep Qt alive for offline library and asset widgets."""
    return QApplication.instance() or QApplication([])


def test_account_changes_preserve_exact_values_and_report_separate_coverage():
    """Compare known fields and distinguish a missing trustline from a zero balance."""
    baseline = entry()
    baseline["evidence"].append(document("payments", {}, [{"identifier": "1"}]))
    candidate = deepcopy(baseline)
    candidate["evidence"].pop()
    after = candidate["evidence"][0]
    after["records"][0]["amount"] = "0.0000000"
    after["records"].pop()
    after["metadata"].update(source="another source", fetched_at="2026-11-05T01:00:00Z")
    originals = deepcopy((baseline, candidate))
    report = compare_entries(baseline, candidate)
    assert report["status"] == "Changed"
    assert report["comparison_provenance"]["source"] == "another source"
    assert report["baseline_scope"]["supplemental_documents"]["payments"]["saved_rows"] == 1
    assert report["comparison_scope"]["supplemental_documents"] == {}
    assert any(change["after"] == "0.0000000" for change in report["changes"])
    assert any(
        not change["after_present"] and change["after"] is None for change in report["changes"]
    )
    assert (baseline, candidate) == originals
    reverse = compare_entries(candidate, baseline)["changes"]
    assert any(
        row["before"] == "0.0000000" and row["after"] == "123456789.1234567" for row in reverse
    )


def test_coverage_and_timestamps_alone_do_not_change_supported_fields():
    """A fresh document can omit loaded pages while reporting the same exact balances."""
    baseline = entry()
    candidate = deepcopy(baseline)
    baseline["evidence"].append(document("counterparties", {}, []))
    candidate["evidence"][0]["exported_at"] = "2026-11-06T01:00:00Z"
    candidate["evidence"][0]["records"][0]["amount"] += "0"
    report = compare_entries(baseline, candidate)
    assert report["status"] == "No observed changes" and report["changes"] == []
    assert report["baseline_scope"] != report["comparison_scope"]


@pytest.mark.parametrize(
    "mutation", ["network", "identifier", "body_network", "version", "duplicate"]
)
def test_unverified_or_ambiguous_identity_never_produces_changes(mutation):
    """Refuse cross-network, wrong-resource, duplicate and unknown-schema evidence."""
    baseline, candidate = entry(), entry()
    snapshot = candidate["evidence"][0]
    if mutation == "network":
        candidate["network"] = "Mainnet"
    elif mutation == "identifier":
        candidate["identifier"] = ISSUER
    elif mutation == "body_network":
        snapshot["metadata"]["network"] = "Mainnet"
    elif mutation == "version":
        snapshot["schema_version"] = 2
    else:
        candidate["evidence"].append(deepcopy(snapshot))
    report = compare_entries(baseline, candidate)
    assert report["status"] == "Not comparable" and report["changes"] == []


def test_account_pair_refuses_partial_or_oversized_projection():
    """An empty partial account or an over-limit pair never gets a no-change report."""
    baseline, candidate = entry(), entry()
    snapshot = candidate["evidence"][0]
    original = deepcopy(snapshot)
    snapshot["metadata"].pop("sequence")
    assert compare_entries(baseline, candidate)["status"] == "Not comparable"
    candidate["evidence"][0] = original
    original["records"] = [
        {**original["records"][0], "identity": str(index)} for index in range(MAX_FIELDS + 1)
    ]
    result = compare_entries(baseline, candidate)
    assert "exceeds" in result["note"] and result["changes"] == []


def test_asset_explorer_and_refresh_layouts_match_without_losing_issuer(app):
    """Compare row-based explorer evidence to older metadata-based refresh evidence."""
    asset = parse_asset(asset_row(), "USD", ISSUER, Network.TESTNET, "fixture")
    view = AssetView(AccountService(object()), lambda: Network.TESTNET)
    view.snapshot = asset
    baseline = {
        "kind": "asset",
        "identifier": f"USD:{ISSUER}",
        "code": "USD",
        "issuer": ISSUER,
        "network": "Testnet",
        "evidence": [view.export_document(), view.identity_document()],
    }
    candidate = {**baseline, "evidence": [document("asset", plain(asset), [])]}
    result = compare_entries(baseline, candidate)
    assert result["status"] == "No observed changes"
    assert result["identity"]["issuer"] == ISSUER
    assert "asset_contract_identity" in result["baseline_scope"]["supplemental_documents"]
    candidate["evidence"][0]["metadata"]["statistics"]["balances.authorized"] = "900000000.0000002"
    changes = compare_entries(baseline, candidate)["changes"]
    assert changes[0]["before"] == "900000000.0000001"
    assert changes[0]["after"] == "900000000.0000002"
    assert compare_entries(baseline, {**candidate, "issuer": ADDRESS})["status"] == "Not comparable"
    baseline["evidence"][0]["records"].append(baseline["evidence"][0]["records"][0])
    assert compare_entries(baseline, candidate)["status"] == "Not comparable"


def test_native_asset_legacy_identifier_stays_distinct_from_issued_xlm():
    """Read the old XLM: display identifier without folding in an issued asset named XLM."""
    asset = asyncio.run(HorizonProvider().get_asset("XLM", "", Network.TESTNET))
    baseline = {
        "kind": "asset",
        "identifier": "XLM:",
        "code": "XLM",
        "issuer": "",
        "network": "Testnet",
        "evidence": [document("asset", plain(asset), [])],
    }
    assert compare_entries(baseline, {**baseline, "identifier": "XLM"})["status"] == (
        "No observed changes"
    )
    issued = {**baseline, "identifier": f"XLM:{ISSUER}", "issuer": ISSUER}
    assert compare_entries(baseline, issued)["status"] == "Not comparable"


def test_contract_instance_and_event_coverage_stay_separate():
    """Show the saved event window separately and compare only known instance state."""
    provider, _ = rpc_fixture()
    instance = asyncio.run(provider.inspect(CONTRACT, Network.TESTNET))
    baseline = {
        "kind": "contract",
        "identifier": CONTRACT,
        "network": "Testnet",
        "evidence": [document("contract", {"instance": instance, "events_queried": False}, [])],
    }
    candidate = deepcopy(baseline)
    primary = candidate["evidence"][0]
    primary["records"].append({"id": "event"})
    primary["metadata"].update(
        events_queried=True, start_ledger=10, end_ledger=20, query_complete=False
    )
    primary["metadata"]["instance"]["instance_latest_ledger"] += 1
    report = compare_entries(baseline, candidate)
    assert report["status"] == "No observed changes"
    assert report["baseline_scope"]["events"]["queried"] is False
    assert report["comparison_scope"]["events"]["saved_rows"] == 1
    assert report["comparison_scope"]["events"]["query_complete"] is False
    primary["metadata"]["instance"]["live_until_ledger"] += 1
    assert compare_entries(baseline, candidate)["changes"][0]["field"] == [
        "instance",
        "live_until_ledger",
    ]


def test_library_dialog_uses_committed_same_resource_entries_and_keeps_draft_notes(app, tmp_path):
    """Open offline, change selections, survive reload, export only the report and keep originals."""
    store = InvestigationStore(tmp_path / "investigations.sqlite3")
    baseline, candidate, foreign = entry(), entry(), entry()
    candidate["evidence"][0]["metadata"]["sequence"] = "12345"
    foreign["network"] = "Mainnet"
    item = new_investigation("Private investigation")
    item.update(
        entries=[baseline, foreign, candidate], notes="Private note", labels="private label"
    )
    store.save(item)
    view = InvestigationsView(store, object())
    view.reload(item["id"])
    view.notes.setPlainText("Unsaved draft")
    view.annotations.setChecked(True)
    view.entries.setCurrentRow(0)
    assert view.entries.item(0).text().startswith("#1")
    view.compare_button.click()
    dialog = view.findChild(InvestigationComparisonDialog)
    assert dialog.isVisible() and dialog.candidate.count() == 2
    assert not dialog.export.isEnabled()
    dialog.candidate.setCurrentIndex(1)
    assert "12345" in dialog.changes.toPlainText()
    payload = dialog.export_document()
    assert payload["metadata"]["baseline_entry"] == 1
    assert payload["metadata"]["comparison_entry"] == 3
    for format_name in ("csv", "json"):
        output = serialize(payload, format_name)
        assert all(secret not in output for secret in (item["name"], item["notes"], item["labels"]))
        assert "Unsaved draft" not in output
    assert json.loads(serialize(payload, "json"))["records"][0]["after"] == "12345"
    view.reload(item["id"])
    assert dialog.export_document()["records"] == payload["records"]
    dialog.reject()
    assert view.notes.toPlainText() == "Unsaved draft" and store.list() == [item]


def test_library_handles_single_snapshot_and_unsupported_evidence(app, tmp_path):
    """Explain missing candidates and keep transaction-only evidence readable in the library."""
    store = InvestigationStore(tmp_path / "investigations.sqlite3")
    item = new_investigation("One observation")
    item["entries"] = [
        entry(),
        {"kind": "transaction", "identifier": "a" * 64, "network": "Testnet", "evidence": []},
    ]
    store.save(item)
    view = InvestigationsView(store, object())
    view.reload(item["id"])
    view.entries.setCurrentRow(0)
    view.compare_button.click()
    dialog = view.findChild(InvestigationComparisonDialog)
    assert "Save another observation" in dialog.status.text()
    assert not dialog.export.isEnabled()
    dialog.reject()
    view.entries.setCurrentRow(1)
    assert "transaction" in view.evidence.toPlainText() and not view.compare_button.isEnabled()
