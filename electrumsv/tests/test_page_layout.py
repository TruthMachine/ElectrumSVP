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


def test_transaction_input_labels_require_outpoint_ownership(gui, monkeypatch):
    from PyQt5.QtGui import QBrush, QFont
    from bitcoinx import Script
    from electrumsv.transaction import XTxInput
    from electrumsv.types import TxoKeyType
    from electrumsv.gui.qt import transaction_dialog as module
    monkeypatch.setattr(module, 'app_state', SimpleNamespace(
        format_amount=lambda value, **kwargs: 'unknown' if value is None else str(value)))
    txins = [XTxInput(bytes([i]) * 32, 0, Script(), 0xffffffff) for i in (1, 2)]
    account = Mock()
    account._utxos = {TxoKeyType(txins[0].prev_hash, 0): SimpleNamespace(keyinstance_id=9)}
    account._stxos = {}
    account.get_id.return_value = 1
    account.display_name.return_value = 'Hardware wallet'
    account.get_derivation_path.return_value = None  # Imported key, no BIP32 branch.
    coins = Mock(); coins.get_outputs.return_value = []
    view = SimpleNamespace(_account=account, _tx_hash=b'\x03' * 32,
        tx=SimpleNamespace(inputs=txins, outputs=[], is_complete=lambda: True),
        _coin_service=coins, _input_values={0: 1000},
        _spent_value_label=QLabel(), _received_value_label=QLabel(),
        _monospace_font=QFont(), _receiving_brush=QBrush(), _change_brush=QBrush(),
        _broken_brush=QBrush())
    inputs, outputs = QTreeWidget(), QTreeWidget()
    inputs.setColumnCount(4); outputs.setColumnCount(4)
    module.TxDialog._update_io(view, inputs, outputs)
    assert inputs.topLevelItem(0).text(module.InputColumns.ACCOUNT) == '1: Hardware wallet'
    assert inputs.topLevelItem(0).data(0, module.Roles.IS_MINE)
    assert inputs.topLevelItem(1).text(module.InputColumns.ACCOUNT) == ''
    assert not inputs.topLevelItem(1).data(0, module.Roles.IS_MINE)
    assert view._spent_value_label.text().endswith('unknown')
    assert all(call.args[0] == 9 for call in account.get_derivation_path.call_args_list)
    view._input_values[1] = 2000
    inputs.clear(); module.TxDialog._update_io(view, inputs, outputs)
    assert view._spent_value_label.text().endswith('3000')


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
