"""Exercise async searches, cancellation, and stale responses without public APIs."""

import asyncio
import os

import pytest
from PySide6.QtWidgets import QApplication
from test_accounts import ADDRESS, account_data

from polarstellar.app.window import MainWindow
from polarstellar.stellar.horizon import parse_account
from polarstellar.stellar.models import Network
from polarstellar.stellar.providers import AccountError
from polarstellar.stellar.service import AccountService


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


class DelayedProvider:
    def __init__(self):
        self.calls = []

    async def get_account(self, address, network):
        future = asyncio.get_running_loop().create_future()
        self.calls.append((network, future))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # Simulate a provider that still delivers a response after cancellation.
            return await future


def snapshot(network):
    return parse_account(account_data(), ADDRESS, network, "fixture")


def test_network_switch_discards_late_response(app):
    async def scenario():
        provider = DelayedProvider()
        window = MainWindow(AccountService(provider))
        window.search.setText(ADDRESS)
        window.start_search()
        await asyncio.sleep(0)
        assert window.cancel.isEnabled()
        window.network.setCurrentText("Testnet")
        assert window.balances.rowCount() == 0
        window.start_search()
        await asyncio.sleep(0)
        provider.calls[1][1].set_result(snapshot(Network.TESTNET))
        await asyncio.sleep(0.01)
        provider.calls[0][1].set_result(snapshot(Network.MAINNET))
        await asyncio.sleep(0.01)
        assert "Testnet" in window.message.text()
        assert "Mainnet" not in window.message.text()
        assert window.balances.rowCount() == 2
        window.close()

    asyncio.run(scenario())


def test_new_search_and_cancel_discard_late_results(app):
    async def scenario():
        provider = DelayedProvider()
        window = MainWindow(AccountService(provider))
        window.search.setText(ADDRESS)
        window.start_search()
        await asyncio.sleep(0)
        window.start_search()
        await asyncio.sleep(0)
        provider.calls[0][1].set_result(snapshot(Network.MAINNET))
        await asyncio.sleep(0.01)
        assert "Loading" in window.message.text()
        window.cancel_search()
        provider.calls[1][1].set_result(snapshot(Network.MAINNET))
        await asyncio.sleep(0.01)
        assert "cancelled" in window.message.text()
        assert window.balances.rowCount() == 0
        window.close()

    asyncio.run(scenario())


def test_invalid_input_and_error_are_visible(app):
    class FailingProvider:
        async def get_account(self, address, network):
            raise AccountError("Account not found on Mainnet.")

    async def scenario():
        window = MainWindow(AccountService(FailingProvider()))
        window.start_search()
        assert "valid Stellar G-address" in window.message.text()
        window.search.setText(ADDRESS)
        window.start_search()
        await asyncio.sleep(0)
        assert "not found" in window.message.text()
        assert not window.cancel.isEnabled()
        assert window.balances.rowCount() == 0
        window.close()

    asyncio.run(scenario())


def test_qt_event_loop_stays_responsive(app):
    from PySide6.QtCore import QTimer
    from qasync import QEventLoop

    class SlowProvider:
        async def get_account(self, address, network):
            await asyncio.sleep(0.05)
            return snapshot(network)

    window = MainWindow(AccountService(SlowProvider()))
    loop = QEventLoop(app)
    ticks = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(True))

    async def scenario():
        window.search.setText(ADDRESS)
        window.start_search()
        await window.task
        assert window.balances.rowCount() == 2
        assert len(ticks) >= 2

    with loop:
        timer.start()
        loop.run_until_complete(scenario())
        timer.stop()
        window.close()
