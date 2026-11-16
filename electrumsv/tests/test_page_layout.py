"""Hidden optional pages must not leak controls into the main window."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
pytest.importorskip('PyQt5')
from PyQt5.QtWidgets import (QApplication, QGridLayout, QLabel, QLineEdit,
    QTabWidget, QToolBar, QTreeWidget, QVBoxLayout, QWidget)

from electrumsv.gui.qt.form_scroll_area import FormScrollArea
from electrumsv.gui.qt.main_window import ElectrumWindow
from electrumsv.gui.qt.notifications_view import View


@pytest.fixture
def gui():
    app = QApplication.instance() or QApplication([])
    yield app


def test_optional_page_stays_hidden_until_tab_is_enabled(gui):
    window = QWidget()
    tabs = QTabWidget(window)
    tabs.addTab(QWidget(), 'History')
    api = Mock()
    api.get_notification_rows.return_value = []
    page = View(api, window)
    owner = SimpleNamespace(config={'show_notifications_tab': False})
    ElectrumWindow._add_optional_tab(owner, tabs, page, page.windowIcon(),
        'Notifications', 'notifications')
    window.show(); gui.processEvents()
    assert not page.isVisible()
    assert not page.findChildren(QToolBar)
    tabs.addTab(page, 'Notifications'); tabs.setCurrentWidget(page)
    gui.processEvents()
    assert page.isVisible()
    window.close()




def test_large_font_form_scrolls_without_overlapping_controls(gui):
    window = QWidget(); layout = QVBoxLayout(window)
    font = window.font(); font.setPointSize(18); window.setFont(font)
    form = QGridLayout()
    fields = []
    for row, text in enumerate(['Pay to', 'Amount', 'Transaction fee', 'Description']):
        label, edit = QLabel(text), QLineEdit()
        form.addWidget(label, row, 0); form.addWidget(edit, row, 1)
        fields.append(edit)
    qr = QLabel('QR'); qr.setFixedSize(220, 220); form.addWidget(qr, 0, 2, 4, 1)
    scroll = FormScrollArea(form); layout.addWidget(scroll)
    window.resize(360, 150); window.show(); gui.processEvents()
    assert scroll.horizontalScrollBar().maximum() > 0
    assert scroll.verticalScrollBar().maximum() > 0
    for i, field in enumerate(fields):
        assert field.height() >= field.minimumSizeHint().height()
        assert field.geometry().right() < qr.geometry().left()
        if i:
            assert fields[i-1].geometry().bottom() < field.geometry().top()
    scroll.ensureWidgetVisible(fields[-1]); gui.processEvents()
    assert scroll.verticalScrollBar().value() > 0
    window.close()
