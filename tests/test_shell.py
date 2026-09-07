"""The skeleton must start without network access on a headless display."""

import os

from PySide6.QtWidgets import QApplication

from polarstellar.app.window import MainWindow


def test_shell_can_start_and_switch_network():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    assert not window.search.isEnabled()
    window.network.setCurrentText("Testnet")
    assert "Testnet selected" in window.statusBar().currentMessage()
    assert "Not connected" in window.statusBar().currentMessage()
    window.close()
