"""Offline validation for source and frozen apps using disposable investigation data."""

import asyncio
import json
import platform
import sys
from decimal import Decimal
from pathlib import Path

import certifi
import httpx
import networkx as nx
from PySide6.QtCore import QTimer, qVersion
from PySide6.QtWidgets import QApplication
from stellar_sdk import StrKey, scval

from polarstellar import __version__
from polarstellar.stellar.contracts import decoded_value
from polarstellar.stellar.horizon import HorizonProvider
from polarstellar.stellar.models import Network
from polarstellar.storage.export import serialize, write_export
from polarstellar.storage.investigations import InvestigationStore
from polarstellar.storage.watchlists import WatchlistStore


async def validate(window, root, screenshot=None):
    """Exercise bundled dependencies and Qt navigation without contacting any public endpoint.

    This is a startup/integration smoke check, not a substitute for live network or
    clean-machine usability testing. All persistence stays under the temporary root.
    """
    address = StrKey.encode_ed25519_public_key(bytes(32))
    issuer = StrKey.encode_ed25519_public_key(bytes([1]) * 32)
    checks = []
    ticks = []
    timer = QTimer(window)
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()

    def respond(request):
        """Serve exact fixture balances; unexpected network paths fail instead of going live."""
        if request.url.path != f"/accounts/{address}":
            raise AssertionError("Unexpected smoke-test request")
        return httpx.Response(
            200,
            json={
                "account_id": address,
                "sequence": "123",
                "home_domain": "",
                "balances": [
                    {"asset_type": "native", "balance": "100.0000001"},
                    {
                        "asset_type": "credit_alphanum4",
                        "asset_code": "TEST",
                        "asset_issuer": issuer,
                        "balance": "7.0000002",
                        "limit": "1000.0000000",
                        "is_authorized": True,
                    },
                ],
            },
        )

    # Replace only the transport in the injected provider, keeping service parsing,
    # network identity checks, async UI updates and the cache wrapper active.
    window.service.provider.provider = HorizonProvider(httpx.MockTransport(respond))
    window.search.setText(address)
    window.start_search()
    await window.task
    assert window.account is not None and window.account.balances[0].amount == Decimal(
        "100.0000001"
    )
    checks.append("account_lookup_exact_amounts")
    window.investigations.name.setText("Disposable native smoke check")
    window.investigations.create()
    window.save_resource()
    reopened = InvestigationStore(root / "investigations.sqlite3").list()
    assert reopened[0]["entries"][0]["network"] == Network.MAINNET.value
    checks.append("sqlite_saved_investigation_restart")
    # Exercise the production bookmark controls using the same disposable storage.
    # Adding/reopening the entry must stay offline and preserve its selected network.
    window.watchlists.name.setText("Disposable watchlist")
    window.watchlists.create()
    window.watch_resource()
    bookmarks = WatchlistStore(root / "watchlists.sqlite3").list()
    assert bookmarks[0]["entries"][0]["identifier"] == address
    assert bookmarks[0]["entries"][0]["network"] == Network.MAINNET.value
    checks.append("sqlite_watchlist_restart")
    snapshot = window.investigations.export_document()
    write_export(root / "evidence.json", snapshot, "json")
    write_export(root / "evidence.csv", snapshot, "csv")
    assert (
        json.loads((root / "evidence.json").read_text(encoding="utf-8"))["kind"] == "investigation"
    )
    assert "100.0000001" in serialize(snapshot, "csv")
    checks.append("csv_json_exports")
    value = decoded_value(scval.to_int128(2**100).to_xdr())
    assert value["value"] == str(2**100)
    checks.append("stellar_sdk_xdr_exact_integer")
    graph = nx.DiGraph([(address, issuer)])
    assert nx.shortest_path(graph, address, issuer) == [address, issuer]
    checks.append("networkx_relationship_model")
    # TLS is constructed locally to verify packaged CA certificates and SSL support.
    assert Path(certifi.where()).is_file()
    async with httpx.AsyncClient():
        pass
    checks.append("httpx_tls_certificate_bundle")
    # Opening the discovery tab itself must remain offline; only Search fetches pages.
    window.navigation.setCurrentRow(5)
    window.assets.tabs.setCurrentIndex(1)
    await asyncio.sleep(0.01)
    window.assets.tabs.setCurrentIndex(0)
    for index in (5, 6, 7, 8, 0):
        window.navigation.setCurrentRow(index)
        await asyncio.sleep(0.01)
    assert window.pages.currentIndex() == 0 and ticks
    checks.append("qt_navigation_and_async_event_loop")
    timer.stop()
    if screenshot:
        window.grab().save(str(screenshot))
        assert Path(screenshot).is_file()
    window.close()
    return {
        "status": "passed",
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "platform": platform.system(),
        "qt_version": qVersion(),
        "qt_platform_plugin": QApplication.platformName(),
        "architecture": platform.machine(),
        "checks": checks,
        "network_requests": "fixture transport only",
        "storage": "temporary directory, removed on exit",
    }
