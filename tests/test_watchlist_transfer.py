"""Validate bookmark portability, privacy, atomic import, filtered identities and UI race safety."""

import asyncio
import copy
import json

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from test_accounts import ADDRESS, ISSUER, account_data
from test_contracts import CONTRACT

from polarstellar.stellar.horizon import parse_account
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.storage.export import write_export
from polarstellar.storage.watchlist_transfer import matches, portable, read_file
from polarstellar.storage.watchlists import WatchlistStore, resource
from polarstellar.ui.watchlists_view import WatchlistsView


@pytest.fixture
def app():
    """Keep widgets alive with offline providers; no transfer or filtering test contacts APIs."""
    return QApplication.instance() or QApplication([])


def library(store):
    """Seed private annotations and snapshots alongside distinct networks/issuers/native identities."""
    identifier = store.create("Private research name")
    for identity in (
        resource("account", ADDRESS, Network.TESTNET),
        resource("account", ADDRESS, Network.MAINNET),
        resource("asset", "USD", Network.TESTNET, ISSUER),
        resource("asset", "USD", Network.TESTNET, ADDRESS),
        resource("asset", "XLM", Network.TESTNET),
        resource("asset", "XLM", Network.TESTNET, ISSUER),
        resource("contract", CONTRACT, Network.MAINNET),
    ):
        entry_id = store.add(identifier, identity)
        store.edit(identifier, entry_id, "Private label α", "Private multiline\nnotes β")
        store.snapshot(
            identifier, entry_id, {"private_snapshot": "do not transfer", "exact": "0.0000001"}
        )
    return store.list()[0]


def view_for(store, provider=None):
    """Construct the real library with unreachable providers unless a test injects a fixture."""
    return WatchlistsView(store, provider or object(), object(), lambda: Network.TESTNET)


@pytest.mark.parametrize("annotations", [False, True])
def test_lossless_identity_roundtrip_without_snapshots(tmp_path, annotations):
    """Portable files preserve networks and issuers while omitting snapshots/IDs and opt-out annotations."""
    original = WatchlistStore(tmp_path / "original.sqlite3")
    item = library(original)
    payload = portable(item, annotations=annotations)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "private_snapshot" not in serialized and item["id"] not in serialized
    assert ("Private research name" in serialized) == annotations
    assert ("Private label" in serialized) == annotations
    assert ("Private multiline" in serialized) == annotations
    target = WatchlistStore(tmp_path / "target.sqlite3")
    existing_id = target.create("Existing list")
    imported_id = target.import_portable(payload)
    restored = next(item for item in target.list() if item["id"] == imported_id)
    assert existing_id in [item["id"] for item in target.list()]
    assert restored["name"] == (item["name"] if annotations else "Imported watchlist")
    assert len(restored["entries"]) == 7
    for before, after in zip(item["entries"], restored["entries"], strict=True):
        for key in ("kind", "network", "identifier"):
            assert before[key] == after[key]
        if before["kind"] == "asset":
            assert (before["code"], before["issuer"]) == (after["code"], after["issuer"])
        assert before["id"] != after["id"] and after["snapshot"] is None
        assert after["notes"] == (before["notes"] if annotations else "")
    second = target.import_portable(payload)
    assert second != imported_id and len(target.list()) == 3


def valid_payload(tmp_path):
    """Export a small valid file envelope for independent hostile-shape mutations."""
    return portable(library(WatchlistStore(tmp_path / "source.sqlite3")), annotations=True)


@pytest.mark.parametrize(
    "change",
    [
        "schema",
        "boolean_schema",
        "format",
        "extra_root",
        "no_timezone",
        "bad_network",
        "bad_account",
        "wrong_asset_identifier",
        "asset_case_mismatch",
        "snapshot",
        "foreign_id",
        "duplicate",
        "missing_issuer",
        "nonstring_notes",
        "long_label",
        "long_notes",
        "false_annotations",
        "noncanonical",
        "too_many",
        "bad_scope",
    ],
)
def test_invalid_import_never_opens_database(tmp_path, change):
    """Reject every entry/schema ambiguity before creating storage, not after partial inserts."""
    payload = valid_payload(tmp_path)
    entries = payload["watchlist"]["entries"]
    if change == "schema":
        payload["schema_version"] = 99
    elif change == "boolean_schema":
        payload["schema_version"] = True
    elif change == "format":
        payload["format"] = "other"
    elif change == "extra_root":
        payload["run_command"] = "ignored instructions"
    elif change == "no_timezone":
        payload["exported_at"] = "2026-10-08T00:00:00"
    elif change == "bad_network":
        entries[0]["network"] = "Other"
    elif change == "bad_account":
        entries[0]["identifier"] = "G" * 56
    elif change == "wrong_asset_identifier":
        entries[2]["identifier"] = "USD:" + ADDRESS
    elif change == "asset_case_mismatch":
        entries[2]["code"] = "usd"
    elif change == "snapshot":
        entries[0]["snapshot"] = {"claimed_verified": True}
    elif change == "foreign_id":
        entries[0]["id"] = "existing-local-id"
    elif change == "duplicate":
        entries.append(copy.deepcopy(entries[0]))
    elif change == "missing_issuer":
        del entries[2]["issuer"]
    elif change == "nonstring_notes":
        entries[0]["notes"] = ["not text"]
    elif change == "long_label":
        entries[0]["label"] = "x" * 201
    elif change == "long_notes":
        entries[0]["notes"] = "x" * 10_001
    elif change == "false_annotations":
        payload["annotations_included"] = False
    elif change == "noncanonical":
        entries[0]["identifier"] = " " + ADDRESS
    elif change == "too_many":
        payload["watchlist"]["entries"] = entries * 72
    elif change == "bad_scope":
        payload["scope"] = "unlimited"
    store = WatchlistStore(tmp_path / "not-created.sqlite3")
    with pytest.raises((ValueError, TypeError, AccountError)):
        store.import_portable(payload)
    assert not store.path.exists()


# Short IDs keep the real 10 MB boundary test compatible with Windows environment limits.
@pytest.mark.parametrize(
    "contents",
    [
        b"not JSON",
        b"\xff\xfe",
        b'{"a":1,"a":2}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b"[" * 2000 + b"0" + b"]" * 2000,
        b" " * 10_000_001,
    ],
    ids=[
        "not-json",
        "bad-utf8",
        "duplicate-keys",
        "nan",
        "infinity",
        "deep-nesting",
        "over-byte-limit",
    ],
)
def test_bounded_file_parsing_rejects_ambiguous_or_invalid_json(tmp_path, contents):
    """Bound reads and reject duplicate keys, invalid encodings/depth and nonstandard constants."""
    path = tmp_path / "input.json"
    path.write_bytes(contents)
    with pytest.raises((ValueError, TypeError)):
        read_file(path)


def test_utf8_bom_and_atomic_import_quota(tmp_path):
    """Accept UTF-8 BOM files but preserve all existing lists when the import quota is full."""
    payload = valid_payload(tmp_path)
    path = tmp_path / "bookmarks.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8-sig")
    assert read_file(path) == payload
    target = WatchlistStore(tmp_path / "target.sqlite3")
    for index in range(100):
        target.create("List " + str(index))
    previous = target.list()
    with pytest.raises(ValueError, match="100"):
        target.import_portable(read_file(path))
    assert target.list() == previous


def test_import_write_failure_rolls_back_whole_list(tmp_path, monkeypatch):
    """A failure after insertion must roll back the new list and retain existing library state."""
    payload = valid_payload(tmp_path)
    target = WatchlistStore(tmp_path / "target.sqlite3")
    target.create("Existing")
    previous = target.list()
    real_write = target.write

    def fail(connection, item):
        """Insert in the active transaction then fail, exercising real SQLite rollback."""
        real_write(connection, item)
        raise OSError("Controlled write failure")

    monkeypatch.setattr(target, "write", fail)
    with pytest.raises(OSError):
        target.import_portable(payload)
    assert target.list() == previous


def test_filters_map_visible_rows_to_saved_ids(app, tmp_path):
    """Filtered row zero must target its visible asset, not the underlying list's first account."""
    store = WatchlistStore(tmp_path / "watchlists.sqlite3")
    item = library(store)
    view = view_for(store)
    view.filter_network.setCurrentText("Testnet")
    view.filter_kind.setCurrentText("Asset")
    view.query.setText("usd")
    assert len(view.visible_entries) == 2
    view.entries.setCurrentRow(0)
    selected = view.current["id"]
    assert selected == item["entries"][2]["id"]
    view.label.setText("Changed visible asset")
    view.save_notes()
    entries = store.list()[0]["entries"]
    assert entries[2]["label"] == "Changed visible asset"
    assert entries[0]["label"] == "Private label α"
    assert matches(entries[2], "CHANGED", "Testnet", "Asset")
    assert not matches(entries[2], "changed", "Mainnet", "Asset")
    assert not matches(entries[2], "changed", "Testnet", "Account")


def test_filter_hiding_unsaved_selection_can_be_refused(app, tmp_path, monkeypatch):
    """Filter changes cannot erase a draft; refusals restore both filters and selected identity."""
    store = WatchlistStore(tmp_path / "watchlists.sqlite3")
    item = library(store)
    view = view_for(store)
    view.entries.setCurrentRow(0)
    entry_id = view.current["id"]
    view.notes.setPlainText("Uncommitted draft")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    view.filter_kind.setCurrentText("Contract")
    assert view.filter_kind.currentText() == "All types"
    assert view.current["id"] == entry_id and view.notes.toPlainText() == "Uncommitted draft"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    view.filter_kind.setCurrentText("Contract")
    assert view.current is None and len(view.visible_entries) == 1
    assert store.list()[0]["entries"][0]["notes"] == item["entries"][0]["notes"]


def test_export_scope_annotations_and_committed_state(app, tmp_path):
    """Default exports use the whole list; filtered scope is explicit and drafts are never included."""
    store = WatchlistStore(tmp_path / "watchlists.sqlite3")
    item = library(store)
    view = view_for(store)
    view.filter_kind.setCurrentText("Asset")
    view.query.setText("USD")
    payload = view.export_document()
    assert len(payload["watchlist"]["entries"]) == 7 and payload["scope"] == "whole_list"
    assert "Private" not in json.dumps(payload)
    view.filtered_export.setChecked(True)
    view.include_annotations.setChecked(True)
    view.entries.setCurrentRow(0)
    view.notes.setPlainText("Uncommitted draft must not be shared")
    payload = view.export_document()
    assert payload["scope"] == "filtered_entries" and len(payload["watchlist"]["entries"]) == 2
    assert "Uncommitted" not in json.dumps(payload)
    assert payload["watchlist"]["name"] == item["name"]
    assert "snapshot" not in json.dumps(payload)
    view.query.setText("usd")
    assert view.notes.toPlainText() == "Uncommitted draft must not be shared"


def test_transfer_dialogs_no_network_and_privacy_reset(app, tmp_path, monkeypatch):
    """File controls round-trip bookmarks offline and reset sharing preferences for a new list."""
    store = WatchlistStore(tmp_path / "watchlists.sqlite3")
    library(store)
    view = view_for(store)
    path = tmp_path / "polarstellar-watchlist.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), "JSON"))
    view.include_annotations.setChecked(True)
    view.export_file()
    payload = read_file(path)
    assert payload["annotations_included"]
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), "JSON"))
    view.import_file()
    assert len(store.list()) == 2 and len(view.current_list["entries"]) == 7
    assert not view.include_annotations.isChecked() and not view.filtered_export.isChecked()
    assert all(entry["snapshot"] is None for entry in view.current_list["entries"])
    assert "No snapshots or network requests" in view.status.text()
    assert len({entry["id"] for item in store.list() for entry in item["entries"]}) == 14


def test_cancelled_dialogs_and_failed_export_leave_state_intact(app, tmp_path, monkeypatch):
    """Cancellation is inert and failed atomic replacement retains an existing destination file."""
    import polarstellar.storage.export as exporting

    store = WatchlistStore(tmp_path / "watchlists.sqlite3")
    library(store)
    view = view_for(store)
    before = store.list()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    view.import_file()
    view.export_file()
    assert store.list() == before
    path = tmp_path / "existing.json"
    path.write_text("Existing bytes")

    def fail(*args):
        """Fail during replacement, after the temporary file has been written."""
        raise OSError("Controlled replacement failure")

    monkeypatch.setattr(exporting.os, "replace", fail)
    with pytest.raises(OSError):
        write_export(path, view.export_document(), "json")
    assert path.read_text() == "Existing bytes" and not list(tmp_path.glob("*.tmp"))


def test_refresh_cannot_hide_unsaved_draft_after_external_annotation_edit(app, tmp_path):
    """A fresh committed label can stop matching a filter without erasing the selected draft."""

    async def scenario():
        """Save an external label during a delayed refresh while the UI is typing notes."""
        future = asyncio.get_running_loop().create_future()

        class Provider:
            """Wait for controlled account evidence without contacting Horizon."""

            async def get_account(self, *args):
                """Return after both persisted and draft annotations change."""
                return await future

        store = WatchlistStore(tmp_path / "watchlists.sqlite3")
        item = library(store)
        entry_id = item["entries"][0]["id"]
        view = view_for(store, Provider())
        view.query.setText("Private label")
        view.entries.setCurrentRow(0)
        view.start_refresh()
        pending = view.task
        await asyncio.sleep(0)
        view.notes.setPlainText("Still typing")
        store.edit(item["id"], entry_id, "Different label", "Committed elsewhere")
        future.set_result(parse_account(account_data(), ADDRESS, Network.TESTNET, "fixture"))
        await pending
        assert view.current["id"] == entry_id and view.notes.toPlainText() == "Still typing"
        assert view.query.text() == ""
        stored = store.list()[0]["entries"][0]
        assert stored["label"] == "Different label" and stored["snapshot"]

    asyncio.run(scenario())


def test_filtered_removal_targets_visible_entry_and_list_switch_resets_sharing(
    app, tmp_path, monkeypatch
):
    """Deleting filtered row zero must not remove the hidden account or carry sharing consent to another list."""
    store = WatchlistStore(tmp_path / "watchlists.sqlite3")
    first = library(store)
    second_id = store.create("Separate list")
    view = view_for(store)
    view.reload(first["id"])
    view.filter_kind.setCurrentText("Contract")
    view.entries.setCurrentRow(0)
    assert view.current["id"] == first["entries"][6]["id"]
    view.include_annotations.setChecked(True)
    view.filtered_export.setChecked(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    view.remove()
    saved = next(item for item in store.list() if item["id"] == first["id"])
    assert len(saved["entries"]) == 6 and saved["entries"][0]["kind"] == "account"
    view.library.setCurrentIndex(view.library.findData(second_id))
    assert not view.include_annotations.isChecked() and not view.filtered_export.isChecked()


def test_export_formatted_byte_limit_prevents_unimportable_file(tmp_path, monkeypatch):
    """A compact envelope fitting the limit must still be refused if the actual pretty JSON would exceed it."""
    import polarstellar.storage.watchlist_transfer as transfer

    store = WatchlistStore(tmp_path / "watchlists.sqlite3")
    item = library(store)
    payload = portable(item)
    compact_size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    monkeypatch.setattr(transfer, "MAX_BYTES", compact_size + 10)
    with pytest.raises(ValueError, match="Portable export exceeds"):
        portable(item)
