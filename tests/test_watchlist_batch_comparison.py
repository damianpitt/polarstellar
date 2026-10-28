"""Verify bounded manual batches, atomic snapshot pairs and conservative offline comparisons."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, localcontext

import pytest
from PySide6.QtWidgets import QApplication
from test_accounts import ADDRESS, ISSUER
from test_assets import asset_row
from test_contracts import CONTRACT
from test_contracts import provider as rpc_fixture
from test_watchlists import CachedWrapper, LiveProvider, account, saved, view_for

from polarstellar.stellar.assets import parse_asset
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.storage.export import account_document, document, plain, serialize
from polarstellar.storage.watchlist_comparison import (
    compare,
    compare_entry,
    comparison_document,
    pair_document,
)
from polarstellar.storage.watchlist_transfer import portable
from polarstellar.storage.watchlists import WatchlistStore, resource


@pytest.fixture
def app():
    """Keep offscreen Qt widgets alive throughout each batch and comparison scenario."""
    return QApplication.instance() or QApplication([])


def pair():
    """Provide independent same-account baseline/current JSON, preserving original exact values."""
    entry = resource("account", ADDRESS, Network.TESTNET)
    before = account_document(account())
    return entry, before, deepcopy(before)


def test_exact_account_changes_and_order_independence():
    """Use full asset identities and exact before/after amounts, without calculating transfer totals."""
    entry, before, after = pair()
    after["records"].reverse()
    assert compare(entry, before, after)["status"] == "No observed changes"
    after = deepcopy(before)
    after["records"][0]["amount"] = "123456789.1234568"
    with localcontext() as context:
        context.prec = 2
        result = compare(entry, before, after)
    assert result["status"] == "Changed" and len(result["changes"]) == 1
    row = result["changes"][0]
    assert row["before"] == "123456789.1234567" and row["after"] == "123456789.1234568"
    assert row["field"][:1] == ["balances"] and "delta" not in row


def test_decimal_formatting_and_retrieval_provenance_are_not_state_changes():
    """Equivalent amounts and new retrieval/export timestamps do not invent observed state changes."""
    entry, before, after = pair()
    after["records"][0]["amount"] += "0"
    after["metadata"]["fetched_at"] = "2026-10-28T05:00:00+00:00"
    after["exported_at"] = "2026-10-28T05:01:00+00:00"
    result = compare(entry, before, after)
    assert result["status"] == "No observed changes"
    assert result["previous_provenance"] != result["current_provenance"]


def test_balance_identity_changes_never_merge_issuers_or_missing_values_into_zero():
    """Newly/unreported balance fields remain distinct from known zero and from same-code issuers."""
    entry, before, after = pair()
    after["records"][1]["identity"] = ADDRESS
    after["records"][0]["authorized"] = False
    after["records"][0].pop("limit")
    result = compare(entry, before, after)
    assert result["status"] == "Changed"
    assert any(row["change"] == "Newly reported" for row in result["changes"])
    assert any(row["change"] == "No longer reported" for row in result["changes"])
    assert any(not row["after_present"] and row["after"] is None for row in result["changes"])


@pytest.mark.parametrize(
    "mutation", ["network", "address", "kind", "duplicate", "nan", "unsupported"]
)
def test_incomparable_or_malformed_history_never_becomes_no_change(mutation):
    """Foreign identity, duplicate balances and malformed old formats remain explicitly uncomparable."""
    entry, before, after = pair()
    if mutation == "network":
        before["metadata"]["network"] = "Mainnet"
    elif mutation == "address":
        before["metadata"]["address"] = ISSUER
    elif mutation == "kind":
        before["kind"] = "asset"
    elif mutation == "duplicate":
        before["records"].append(deepcopy(before["records"][0]))
    elif mutation == "nan":
        before["records"][0]["amount"] = "NaN"
    else:
        before = {"legacy": "unrecognized"}
    result = compare(entry, before, after)
    assert result["status"] == "Not comparable" and result["changes"] == []


def test_baseline_and_asset_unknown_statistics_flags():
    """First success is a baseline; missing asset statistics stay unknown instead of defaulting to zero."""
    entry = resource("asset", "USD", Network.TESTNET, ISSUER)
    before = document(
        "asset", plain(parse_asset(asset_row(), "USD", ISSUER, Network.TESTNET, "fixture")), []
    )
    assert compare(entry, None, before)["status"] == "Baseline only"
    after = deepcopy(before)
    after["metadata"]["statistics"]["num_liquidity_pools"] = 0
    after["metadata"]["flags"]["auth_revocable"] = False
    changes = compare(entry, before, after)["changes"]
    assert any(row["before"] is None and row["after"] == 0 for row in changes)
    assert any(row["before"] is True and row["after"] is False for row in changes)


def test_contract_storage_uses_xdr_and_ignores_moving_retention_counters():
    """A newer decoder description is not a storage change; changed executable/TTL remain observable."""
    rpc, _ = rpc_fixture()
    snapshot = asyncio.run(rpc.inspect(CONTRACT, Network.TESTNET))
    entry = resource("contract", CONTRACT, Network.TESTNET)
    before = document("contract", {"instance": snapshot, "events_queried": False}, [])
    after = deepcopy(before)
    instance = after["metadata"]["instance"]
    instance["instance_storage"][0]["value"]["note"] = "A newer decoder"
    instance["instance_latest_ledger"] += 10
    instance["latest_available_ledger"] += 10
    assert compare(entry, before, after)["status"] == "No observed changes"
    instance["wasm_hash"] = "a" * 64
    instance["live_until_ledger"] += 1
    assert len(compare(entry, before, after)["changes"]) == 2


def test_projection_limit_does_not_claim_partial_comparison():
    """Oversized comparisons stop at their field cap and never emit partially accepted changes."""
    entry, before, after = pair()
    after["records"] = [
        {"asset": str(index), "identity": ISSUER, "amount": "1", "limit": None, "authorized": True}
        for index in range(700)
    ]
    result = compare(entry, before, after)
    assert result["status"] == "Not comparable" and "2000" in result["note"]
    assert result["changes"] == []


def test_malformed_legacy_pair_metadata_fails_comparison_without_crashing():
    """Malformed legacy JSON cannot become a verified pairing or break offline selection."""
    entry, before, after = pair()
    after["metadata"]["unsupported_value"] = float("nan")
    entry.update(previous_snapshot=before, snapshot=after)
    result = compare_entry(entry)
    assert result["status"] == "Not comparable" and result["changes"] == []


def test_pair_rotation_cas_notes_and_older_writer_detection(tmp_path):
    """Keep last two successes atomically and detect stale baselines or legacy native-app writes."""
    store = WatchlistStore(tmp_path / "watch.db")
    list_id, entry_id = saved(store)
    entry, first, second = pair()
    store.snapshot(list_id, entry_id, first, entry, None)
    store.edit(list_id, entry_id, "Preserved", "Concurrent annotation")
    second["records"][0]["amount"] = "1.0000001"
    store.snapshot(list_id, entry_id, second, entry, first)
    current = store.get_entry(list_id, entry_id)
    assert current["snapshot"] == second and current["previous_snapshot"] == first
    assert (
        current["notes"] == "Concurrent annotation"
        and compare_entry(current)["status"] == "Changed"
    )
    with pytest.raises(ValueError, match="changed during refresh"):
        store.snapshot(list_id, entry_id, first, entry, first)
    assert store.get_entry(list_id, entry_id) == current
    # Simulate 0.0.23's snapshot-only writer, leaving additive pair metadata untouched.
    store.update(list_id, lambda item: item["entries"][0].update(snapshot=first))
    assert compare_entry(store.get_entry(list_id, entry_id))["status"] == "Not comparable"
    store.snapshot(list_id, entry_id, second, entry, first)
    assert compare_entry(store.get_entry(list_id, entry_id))["status"] == "Changed"
    store.snapshot(list_id, entry_id, second, entry, second)
    current = store.get_entry(list_id, entry_id)
    assert (
        current["previous_snapshot"] == second
        and compare_entry(current)["status"] == "No observed changes"
    )


def test_pair_size_failure_rolls_back_both_snapshots(tmp_path):
    """The unchanged 10 MB list limit applies to the pair; failure cannot discard the previous snapshot."""
    store = WatchlistStore(tmp_path / "watch.db")
    list_id, entry_id = saved(store)
    _, first, second = pair()
    first["padding"] = "x" * 5_000_000
    second["padding"] = "x" * 5_000_000
    store.snapshot(list_id, entry_id, first)
    previous = store.list()
    with pytest.raises(ValueError, match="10 MB"):
        store.snapshot(list_id, entry_id, second)
    assert store.list() == previous


def test_comparison_exports_and_portable_privacy(tmp_path):
    """Changes export without repeated full snapshots; raw pairs are separate from bookmark-only JSON."""
    store = WatchlistStore(tmp_path / "watch.db")
    list_id, entry_id = saved(store)
    _, first, second = pair()
    second["records"][0]["amount"] = "0.0000001"
    store.snapshot(list_id, entry_id, first)
    store.snapshot(list_id, entry_id, second)
    entry = store.get_entry(list_id, entry_id)
    changed = comparison_document(entry)
    assert "0.0000001" in serialize(changed, "csv")
    assert "previous_snapshot" not in changed["metadata"]
    assert json.loads(serialize(pair_document(entry), "json"))["records"][0]["previous"] == first
    text = json.dumps(portable(store.list()[0]))
    assert "snapshot" not in text and "digest" not in text


def test_mixed_batch_is_sequential_visible_scoped_and_failure_isolated(app, tmp_path):
    """Successful account/asset/contract entries save separately while an error retains that entry's pair."""

    async def scenario():
        """Mix networks and resource kinds through actual service/RPC boundaries, bypassing cache wrappers."""
        store = WatchlistStore(tmp_path / "watch.db")
        list_id, first_id = saved(store)
        store.add(list_id, resource("account", ADDRESS, Network.MAINNET))
        store.add(list_id, resource("asset", "USD", Network.TESTNET, ISSUER))
        store.add(list_id, resource("contract", CONTRACT, Network.TESTNET))

        class Provider(LiveProvider):
            """Fail Mainnet only, recording that the next resource still gets a turn."""

            async def get_account(self, address, network):
                """Keep explicit failure isolated to its saved network."""
                if network == Network.MAINNET:
                    self.calls.append(("failed", address, network))
                    raise AccountError("Fixture unavailable")
                return await super().get_account(address, network)

        raw = Provider()
        rpc, calls = rpc_fixture()
        view = view_for(store, CachedWrapper(raw), rpc)
        view.reload(list_id, first_id)
        view.notes.setPlainText("Unsaved draft")
        view.start_batch()
        await view.task
        entries = store.list()[0]["entries"]
        assert [item["snapshot"] is not None for item in entries] == [True, False, True, True]
        assert [row["status"] for row in view.batch_report["entries"]] == [
            "Refreshed",
            "Failed",
            "Refreshed",
            "Refreshed",
        ]
        assert "3 refreshed" in view.batch_summary.toPlainText()
        assert view.notes.toPlainText() == "Unsaved draft"
        assert len(raw.calls) == 3 and "getEvents" not in [body["method"] for _, body in calls]
        assert not view.batch_running and view.task is None
        # A filtered batch freezes only the visible type/network scope.
        view.save_notes()
        view.filter_kind.setCurrentText("Asset")
        view.start_batch()
        await view.task
        assert len(view.batch_report["entries"]) == 1
        assert store.list()[0]["entries"][2]["previous_snapshot"] is not None
        view.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel", "filter", "list", "delete", "concurrent", "select"])
def test_pending_batch_respects_context_deletions_concurrent_refresh_and_selection(
    app, tmp_path, action
):
    """Completed work remains saved; a late pending result cannot overwrite changed or cancelled context."""

    async def scenario():
        """Pause the second resource after accepting the first, then alter the requested context."""
        entered, release = asyncio.Event(), asyncio.Event()
        store = WatchlistStore(tmp_path / "watch.db")
        list_id, first_id = saved(store)
        second_id = store.add(list_id, resource("account", ADDRESS, Network.MAINNET))

        class Provider:
            """Wait only on Mainnet and deliberately resist task cancellation."""

            async def get_account(self, address, network):
                """Return an independently network-scoped fixture after a controlled delay."""
                if network == Network.MAINNET:
                    entered.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        await release.wait()
                return account(network)

        view = view_for(store, Provider())
        view.reload(list_id, first_id)
        view.start_batch()
        task = view.task
        await entered.wait()
        assert store.get_entry(list_id, first_id)["snapshot"] is not None
        if action == "cancel":
            view.cancel_refresh()
        elif action == "filter":
            view.filter_network.setCurrentText("Testnet")
        elif action == "list":
            other = store.create("Other")
            view.reload(list_id, first_id)
            view.library.setCurrentIndex(view.library.findData(other))
        elif action == "delete":
            store.remove(list_id, second_id)
        elif action == "concurrent":
            store.snapshot(list_id, second_id, {"newer": "retain"})
        else:
            view.entries.setCurrentRow(1)
            view.notes.setPlainText("New selected draft")
        release.set()
        await task
        assert store.get_entry(list_id, first_id)["snapshot"] is not None
        if action == "delete":
            with pytest.raises(ValueError):
                store.get_entry(list_id, second_id)
        else:
            pending = store.get_entry(list_id, second_id)["snapshot"]
            if action == "select":
                assert pending is not None
            else:
                assert pending == ({"newer": "retain"} if action == "concurrent" else None)
        if action == "select":
            assert (
                view.current["id"] == second_id and view.notes.toPlainText() == "New selected draft"
            )
        view.close()

    asyncio.run(scenario())


def test_batch_limit_refuses_without_silent_truncation_or_network_calls(app, tmp_path):
    """An over-limit visible scope requires filtering, rather than quietly refreshing only its first rows."""
    from stellar_sdk import StrKey

    store = WatchlistStore(tmp_path / "watch.db")
    list_id = store.create("Large")
    for index in range(51):
        store.add(
            list_id,
            resource(
                "account",
                StrKey.encode_ed25519_public_key(index.to_bytes(32, "big")),
                Network.TESTNET,
            ),
        )
    view = view_for(store)
    view.reload(list_id)
    view.start_batch()
    assert view.task is None and view.batch_report is None and "50" in view.status.text()


def test_single_refresh_uses_same_pair_rotation_and_offline_comparison(app, tmp_path):
    """The existing single-entry action gets identical retention/comparison behavior without background polling."""

    async def scenario():
        """Change the next fixture amount and compare after a second successful manual read."""

        class Provider:
            """Return sequential exact observations for one account."""

            def __init__(self):
                """Start at the original balance."""
                self.calls = 0

            async def get_account(self, *args):
                """Change only the amount after the first read."""
                self.calls += 1
                result = account()
                if self.calls > 1:
                    result = replace(
                        result,
                        balances=(
                            replace(result.balances[0], amount=Decimal("0.0000001")),
                            *result.balances[1:],
                        ),
                    )
                return result

        store = WatchlistStore(tmp_path / "watch.db")
        list_id, entry_id = saved(store)
        raw = Provider()
        view = view_for(store, raw)
        view.reload(list_id, entry_id)
        for _ in range(2):
            view.start_refresh()
            await view.task
        assert raw.calls == 2 and "Changed" in view.comparison.toPlainText()
        assert view.previous_evidence.toPlainText() != view.evidence.toPlainText()
        assert view.comparison_document()["records"][0]["after"] == "0.0000001"
        assert raw.calls == 2
        view.close()

    asyncio.run(scenario())
