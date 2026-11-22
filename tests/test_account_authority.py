"""Verify public account authority, unknown old fields, exact comparisons and stale UI protection."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace

import httpx
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from stellar_sdk import SignerKey, StrKey
from stellar_sdk.signer_key import SignedPayloadSigner
from test_accounts import ADDRESS, ISSUER, account_data

from polarstellar.app.window import MainWindow
from polarstellar.stellar.account_authority import AccountSigner
from polarstellar.stellar.horizon import HorizonProvider, parse_account
from polarstellar.stellar.models import Network
from polarstellar.stellar.service import AccountService
from polarstellar.storage import codec
from polarstellar.storage.database import SnapshotCache
from polarstellar.storage.export import account_document, serialize
from polarstellar.storage.investigations import InvestigationStore
from polarstellar.storage.provider import CachedProvider
from polarstellar.storage.watchlist_comparison import compare
from polarstellar.storage.watchlists import resource


def authority_data():
    """Provide all known signer encodings and genuine zero/false values with reserve provenance."""
    data = account_data()
    payload = SignerKey.ed25519_signed_payload(
        SignedPayloadSigner(ISSUER, b"fixture")
    ).encoded_signer_key
    data.update(
        thresholds={"low_threshold": 0, "med_threshold": 2, "high_threshold": 3},
        flags={
            "auth_required": False,
            "auth_revocable": True,
            "auth_immutable": False,
            "auth_clawback_enabled": True,
        },
        signers=[
            {"key": ADDRESS, "type": "ed25519_public_key", "weight": 0},
            {"key": ISSUER, "type": "ed25519_public_key", "weight": 2, "sponsor": ADDRESS},
            {"key": StrKey.encode_pre_auth_tx(bytes([2]) * 32), "type": "preauth_tx", "weight": 1},
            {"key": StrKey.encode_sha256_hash(bytes([3]) * 32), "type": "sha256_hash", "weight": 1},
            {"key": payload, "type": "ed25519_signed_payload", "weight": 1},
        ],
        sponsor=ISSUER,
        num_sponsored=0,
        num_sponsoring=2,
        subentry_count=4,
        last_modified_ledger=1234,
    )
    data["balances"][1]["sponsor"] = ADDRESS
    return data


def observed(data=None):
    """Parse a network-scoped fixture through the production account entry point."""
    return parse_account(
        data if data is not None else authority_data(), ADDRESS, Network.TESTNET, "fixture"
    )


def test_complete_public_configuration_and_exports_are_lossless():
    """Keep exact public keys, reported zeros/false, sponsorship, source and raw fields together."""
    account = observed()
    authority = account.authority
    assert authority.low_threshold == 0 and authority.auth_required is False
    assert authority.signers[0].weight == 0 and len(authority.signers) == 5
    assert authority.signers[1].sponsor == ADDRESS
    assert authority.num_sponsored == 0 and authority.last_modified_ledger == 1234
    assert account.balances[1].sponsor == ADDRESS and account.balances[0].sponsor is None
    assert not authority.warnings
    raw = json.loads(authority.raw)
    assert raw["signers"] == authority_data()["signers"]
    assert raw["balance_sponsors"][0]["asset_issuer"] == ISSUER
    document = account_document(account)
    restored = json.loads(serialize(document, "json"))
    assert restored["metadata"]["authority"]["signers"][0]["weight"] == 0
    assert restored["records"][1]["sponsor"] == ADDRESS
    assert restored["metadata"]["source"] == "fixture"
    assert "123456789.1234567" in serialize(document, "csv")
    assert codec.loads(codec.dumps(account)) == account


@pytest.mark.parametrize(
    "name,value",
    [
        ("signers", [{}]),
        ("signers", [{"key": ISSUER, "type": "ed25519_public_key", "weight": True}]),
        ("signers", [{"key": ADDRESS, "type": "sha256_hash", "weight": 1}]),
        ("signers", [{"key": ISSUER, "type": "ed25519_public_key", "weight": 256}]),
        (
            "signers",
            [{"key": ISSUER, "type": "ed25519_public_key", "weight": 1, "sponsor": "Gbad"}],
        ),
        ("signers", "unavailable"),
        ("thresholds", {"low_threshold": -1, "med_threshold": True}),
        ("flags", {"auth_required": "false"}),
        ("flags", []),
        ("sponsor", "Ginvalid"),
        ("num_sponsoring", "0"),
        ("last_modified_ledger", 2**32),
    ],
)
def test_invalid_optional_fields_keep_balances_and_explicit_warnings(name, value):
    """Never convert malformed values into permissive configuration or discard valid base balances."""
    data = authority_data()
    data[name] = value
    account = observed(data)
    assert str(account.balances[0].amount) == "123456789.1234567"
    assert account.authority.warnings
    if name == "signers":
        assert account.authority.signers is None
    elif name == "thresholds":
        assert account.authority.low_threshold is None
    elif name == "flags":
        assert account.authority.auth_required is None


def test_invalid_balance_sponsor_preserves_source_and_warns():
    """Keep exact balances readable while retaining the invalid sponsor field in raw evidence."""
    data = authority_data()
    data["balances"][1]["sponsor"] = "invalid"
    account = observed(data)
    assert account.balances[1].sponsor is None
    assert "Balance row 2" in account.authority.warnings[0]
    assert json.loads(account.authority.raw)["balance_sponsors"][0]["sponsor"] == "invalid"


def test_duplicate_signers_refuse_entire_selection_and_unknown_types_stay_labeled():
    """Prevent partial signer sets and retain future types without treating their keys as validated."""
    data = authority_data()
    data["signers"].append(deepcopy(data["signers"][0]))
    assert observed(data).authority.signers is None
    data["signers"] = [{"key": "<unrecognized-public-key>", "type": "future_type", "weight": 0}]
    authority = observed(data).authority
    assert authority.signers[0].key == "<unrecognized-public-key>"
    assert "Unrecognized signer types" in authority.warnings[0]


def test_signed_payload_padding_is_checked_without_changing_full_key():
    """Reject a checksum-valid P-key whose opaque payload padding is noncanonical."""
    key = authority_data()["signers"][-1]["key"]
    raw = StrKey.decode_ed25519_signed_payload(key)
    malformed = StrKey.encode_ed25519_signed_payload(raw[:-1] + b"\x01")
    with pytest.raises(ValueError, match="encoding"):
        AccountSigner(malformed, "ed25519_signed_payload", 1)
    assert AccountSigner(key, "ed25519_signed_payload", 1).key == key


def test_absent_fields_empty_signers_and_zero_weights_remain_distinct():
    """An old observation is unknown; an empty selection or a zero weight is actual reported data."""
    assert observed(account_data()).authority is None
    data = account_data()
    data["signers"] = []
    assert observed(data).authority.signers == ()
    data["signers"] = [{"key": ADDRESS, "type": "ed25519_public_key", "weight": 0}]
    assert observed(data).authority.signers[0].weight == 0


def test_legacy_cache_shape_only_fills_known_new_optional_fields():
    """Decode old Account/Balance objects without relaxing the codec's general schema checks."""
    old = observed(account_data())
    encoded = json.loads(codec.dumps(old))
    encoded["fields"].pop("authority")
    for row in encoded["fields"]["balances"]:
        row["fields"].pop("sponsor")
    assert codec.loads(json.dumps(encoded)) == old
    encoded["fields"]["unexpected"] = 1
    with pytest.raises(ValueError):
        codec.loads(json.dumps(encoded))


def test_legacy_cache_keeps_original_time_and_fetches_new_fields_after_expiry(tmp_path):
    """Keep revision-4 old snapshots readable until normal expiry, then populate authority live."""
    now, calls = [0], []

    class Provider:
        """Return deterministic current account data when the cache actually expires."""

        async def get_account(self, address, network):
            """Record live retrieval without requesting a public endpoint."""
            calls.append((address, network))
            return observed()

    async def scenario():
        """Persist the exact former cache layout, reopen it and advance the configured clock."""
        provider = Provider()
        cache = SnapshotCache(tmp_path / "cache.sqlite3", clock=lambda: now[0])
        old = observed(account_data())
        encoded = json.loads(codec.dumps(old))
        encoded["fields"].pop("authority")
        for row in encoded["fields"]["balances"]:
            row["fields"].pop("sponsor")
        key = json.dumps(
            [
                4,
                type(provider).__module__,
                type(provider).__name__,
                "get_account",
                ADDRESS,
                Network.TESTNET,
            ]
        )
        cache.put(key, json.dumps(encoded))
        wrapped = CachedProvider(provider, cache)
        wrapped.set_enabled(True)
        cached = await wrapped.get_account(ADDRESS, Network.TESTNET)
        assert cached.authority is None and cached.fetched_at == old.fetched_at and not calls
        now[0] = 60
        fresh = await wrapped.get_account(ADDRESS, Network.TESTNET)
        assert fresh.authority.low_threshold == 0 and calls == [(ADDRESS, Network.TESTNET)]

    asyncio.run(scenario())


def test_old_and_new_snapshot_comparison_preserves_unknown_and_uses_signer_identity():
    """New fields become newly reported, and signer order/raw formatting never changes state."""
    entry = resource("account", ADDRESS, "Testnet")
    old = account_document(observed(account_data()))
    current = account_document(observed())
    result = compare(entry, old, current)
    zero = next(row for row in result["changes"] if row["field"] == ["authority", "low_threshold"])
    assert zero["after"] == 0 and not zero["before_present"]
    assert any(row["field"][-1] == "sponsor" for row in result["changes"])
    reordered = deepcopy(current)
    reordered["metadata"]["authority"]["signers"].reverse()
    reordered["metadata"]["authority"]["raw"] = "Different presentation"
    assert compare(entry, current, reordered)["status"] == "No observed changes"
    reordered["metadata"]["authority"]["signers"][-1]["weight"] = 2
    assert compare(entry, current, reordered)["changes"][0]["field"] == [
        "signers",
        "ed25519_public_key",
        ADDRESS,
        "weight",
    ]
    invalid = deepcopy(current)
    invalid["metadata"]["authority"]["low_threshold"] = False
    assert compare(entry, current, invalid)["status"] == "Not comparable"


def test_window_lookup_save_and_network_invalidation_keep_authority_bound_to_account(tmp_path):
    """Expose details through Overview, save exact source evidence and clear it on network changes."""
    app = QApplication.instance() or QApplication([])

    async def scenario():
        """Use the real service/provider and storage paths without external requests."""
        requests = []

        def respond(request):
            """Supply public fixture configuration from the selected Testnet account endpoint."""
            requests.append(request)
            return httpx.Response(200, json=authority_data())

        store = InvestigationStore(tmp_path / "saved.sqlite3")
        window = MainWindow(AccountService(HorizonProvider(httpx.MockTransport(respond))), store)
        window.network.setCurrentText("Testnet")
        window.investigations.name.setText("Authority case")
        window.investigations.create()
        window.search.setText(ADDRESS)
        window.start_search()
        await window.task
        text = window.authority.details.toPlainText()
        assert "Master key weight  0" in text and "Low  0" in text
        assert "Authorization required  No" in text and "ed25519 signed payload" in text
        assert window.balances.item(1, 5).text() == ADDRESS
        assert len(requests) == 1
        window.show()
        window.overview_tabs.setCurrentIndex(1)
        window.resize(800, 520)
        app.processEvents()
        # Native window styles can queue another layout request while processing
        # the initial resize. Measure the settled view, as a user would see it.
        QTest.qWait(50)
        scroll = window.explorer_scroll
        assert window.authority.details.height() >= 140
        assert scroll.horizontalScrollBar().maximum() == 0, {
            "viewport": scroll.viewport().size().toTuple(),
            "content": scroll.widget().size().toTuple(),
            "minimum": scroll.widget().minimumSizeHint().toTuple(),
            "page": window.pages.minimumSizeHint().toTuple(),
            "buttons": [
                window.watch_resource_button.minimumSizeHint().toTuple(),
                window.save_resource_button.minimumSizeHint().toTuple(),
            ],
            "message_policy": str(window.message.sizePolicy().horizontalPolicy()),
        }
        window.save_resource()
        saved = store.list()[0]["entries"][0]["evidence"][0]
        assert saved["metadata"]["authority"]["num_sponsored"] == 0
        assert saved["records"][1]["sponsor"] == ADDRESS
        window.network.setCurrentText("Mainnet")
        assert "Inspect an account" in window.authority.details.toPlainText()
        assert not window.authority.raw.toPlainText() and window.balances.rowCount() == 0
        window.close()

    asyncio.run(scenario())
    assert app is not None


def test_late_authority_response_cannot_repaint_another_network():
    """Refuse cancellation-resistant old results before the authority pane can show foreign data."""
    app = QApplication.instance() or QApplication([])

    async def scenario():
        """Start a second lookup while the first provider is still preparing its account snapshot."""
        futures = {network: asyncio.get_running_loop().create_future() for network in Network}

        class Provider:
            """Keep each network's result under independent test control."""

            async def get_account(self, address, network):
                """Simulate a provider that completes even after its caller cancels."""
                try:
                    await asyncio.shield(futures[network])
                except asyncio.CancelledError:
                    await futures[network]
                return replace(observed(), network=network)

        window = MainWindow(AccountService(Provider()))
        window.search.setText(ADDRESS)
        window.start_search()
        first = window.task
        await asyncio.sleep(0)
        window.network.setCurrentText("Testnet")
        window.start_search()
        second = window.task
        await asyncio.sleep(0)
        futures[Network.MAINNET].set_result(None)
        await first
        assert (
            window.account is None
            and "Inspect an account" in window.authority.details.toPlainText()
        )
        futures[Network.TESTNET].set_result(None)
        await second
        assert window.account.network == Network.TESTNET
        assert "Testnet" in window.authority.details.toPlainText()
        window.close()

    asyncio.run(scenario())
    assert app is not None
