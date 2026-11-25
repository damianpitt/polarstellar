"""Exercise keyboard, trackpad and text-size behavior independently of live providers.

These are synthetic Qt events, not interactive OS acceptance or screen-reader
certification. Their platform matrix catches regressions in shared widget behavior.
"""

import os

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QAccessible, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsScene

from polarstellar.app.window import MainWindow
from polarstellar.stellar.service import AccountService
from polarstellar.ui.graph_view import Canvas, Edge, Node


@pytest.fixture(scope="module")
def app():
    """Create one offline Qt application; every test owns and closes its own windows."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_navigation_menu_search_and_disabled_destinations(app):
    """Native menu actions expose all usable pages and select search text without a request."""
    window = MainWindow(AccountService(object()))
    window.show()
    app.processEvents()
    try:
        window.page_actions[4].trigger()
        assert window.pages.currentIndex() == window.navigation.currentRow() == 4
        assert not window.page_actions[7].isEnabled()
        assert not window.page_actions[8].isEnabled()
        window.search.setText("retain this public identifier")
        window.focus_search()
        app.processEvents()
        assert window.search.hasFocus()
        assert window.search.selectedText() == "retain this public identifier"
        assert window.task is None and window.account is None
        interface = QAccessible.queryAccessibleInterface(window.navigation)
        assert interface.text(QAccessible.Text.Name) == "Explorer pages"
    finally:
        window.close()


def test_page_shortcut_works_outside_the_sidebar(app):
    """A page shortcut works from search focus and leaves its unsubmitted text intact."""
    window = MainWindow(AccountService(object()))
    window.show()
    window.activateWindow()
    window.search.setFocus()
    QTest.qWait(30)
    try:
        QTest.keyClick(window.search, Qt.Key.Key_5, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert window.pages.currentIndex() == 4
        assert window.task is None
    finally:
        window.close()


@pytest.mark.parametrize("size", [125, 150, 200])
def test_larger_text_and_return_to_default_retain_context(app, size):
    """Text size grows actual widget fonts and can be reset without changing search or network."""
    window = MainWindow(AccountService(object()))
    window.show()
    app.processEvents()
    try:
        window.search.setText("unsubmitted search")
        baseline = window.search.fontMetrics().height()
        next(a for a in window.text_size_actions.actions() if a.data() == size).trigger()
        app.processEvents()
        assert window.search.fontMetrics().height() > baseline
        assert window.text_size == size
        assert window.search.text() == "unsubmitted search"
        assert window.network.currentText() == "Mainnet" and window.task is None
        # Enlarging a compact view must preserve a way to reach lower controls.
        window.resize(800, 520)
        window.explorer_scroll.ensureWidgetVisible(window.export)
        app.processEvents()
        assert window.export.isVisible()
        window.set_text_size(100)
        app.processEvents()
        assert window.search.fontMetrics().height() == baseline
        with pytest.raises(ValueError):
            window.set_text_size(0)
    finally:
        window.close()


@pytest.mark.parametrize("angle,pixels", [(QPoint(120, 0), QPoint()), (QPoint(), QPoint())])
def test_horizontal_and_zero_wheel_do_not_zoom(app, angle, pixels):
    """Horizontal pans and trackpad gesture boundaries must not accidentally shrink the graph."""
    scene = QGraphicsScene()
    canvas = Canvas(scene)
    event = QWheelEvent(
        QPointF(20, 20),
        QPointF(20, 20),
        pixels,
        angle,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    before = canvas.transform().m11()
    canvas.wheelEvent(event)
    assert canvas.transform().m11() == before
    canvas.close()


def test_pixel_trackpad_zoom_keys_bounds_fit_and_focus_escape(app):
    """High-resolution scrolling and keyboard zoom share bounds; fit and Tab remain usable."""
    scene = QGraphicsScene()
    scene.addRect(-400, -300, 800, 600)
    canvas = Canvas(scene)
    canvas.resize(600, 400)
    canvas.show()
    app.processEvents()
    try:
        event = QWheelEvent(
            QPointF(20, 20),
            QPointF(20, 20),
            QPoint(0, 10),
            QPoint(),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        canvas.wheelEvent(event)
        assert canvas.transform().m11() > 1
        for _ in range(80):
            QTest.keyClick(canvas, Qt.Key.Key_Plus)
        assert canvas.transform().m11() == pytest.approx(5)
        for _ in range(100):
            QTest.keyClick(canvas, Qt.Key.Key_Minus)
        assert canvas.transform().m11() == pytest.approx(0.1)
        QTest.keyClick(canvas, Qt.Key.Key_0)
        assert canvas.transform().m11() > 0.1
        before = canvas.transform().m11()
        QTest.keyClick(canvas, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier)
        assert canvas.transform().m11() == before
        assert "arrow keys" in canvas.accessibleDescription()
    finally:
        canvas.close()


def test_node_drag_updates_evidence_arrow_without_navigation(app):
    """A synthetic pointer drag moves a node and its arrow without opening another account."""
    scene = QGraphicsScene()
    opened = []
    source, target = Node("source", opened.append), Node("target", opened.append)
    scene.addItem(source)
    scene.addItem(target)
    target.setPos(180, 0)
    edge = Edge(scene, source, target, "exact transaction evidence")
    canvas = Canvas(scene)
    canvas.resize(640, 400)
    canvas.show()
    app.processEvents()
    try:
        canvas.fit_graph()
        before = edge.line.line()
        start = canvas.mapFromScene(source.pos())
        end = start + QPoint(30, 35)
        QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(canvas.viewport(), end, delay=20)
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
        app.processEvents()
        assert source.pos() != QPointF()
        assert edge.line.line() != before
        assert not opened
        assert edge.line.toolTip() == "exact transaction evidence"
    finally:
        canvas.close()
