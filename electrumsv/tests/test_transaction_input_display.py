from types import SimpleNamespace
from unittest.mock import Mock
import pytest
pytest.importorskip('PyQt5')
from PyQt5.QtWidgets import QApplication, QLabel, QTreeWidget

@pytest.fixture
def gui():
    app = QApplication.instance() or QApplication([])
    yield app


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
