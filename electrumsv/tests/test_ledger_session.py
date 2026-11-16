from types import SimpleNamespace
from unittest.mock import Mock

import pytest
hid = pytest.importorskip('hid')
pytest.importorskip('btchip')

from electrumsv.device import DeviceMgr
from electrumsv.devices.ledger import session, ledger
from electrumsv.exceptions import UserCancelled


def entry(product, path):
    return dict(vendor_id=0x2c97, product_id=product, path=path, serial_number='shared-serial',
        interface_number=0, usage_page=0xffa0)


def test_distinct_models_with_same_serial_have_distinct_ids(monkeypatch):
    monkeypatch.setattr(hid, 'enumerate', lambda *args: [entry(0x5000, b'plus'),
        entry(0x1000, b's')])
    devices = DeviceMgr().find_hid_devices(ledger.LedgerPlugin.DEVICE_IDS)
    assert len({d.id_ for d in devices}) == 2
    assert {session.device_model(d) for d in devices} == {'Nano S Plus', 'Nano S'}
    initial = {session.device_model(d): d.id_ for d in devices}
    monkeypatch.setattr(hid, 'enumerate', lambda *args: [entry(0x1011, b's-new-path'),
        entry(0x5011, b'plus-new-path')])
    switched = DeviceMgr().find_hid_devices(ledger.LedgerPlugin.DEVICE_IDS)
    assert {session.device_model(d): d.id_ for d in switched} == initial


def test_duplicate_serials_on_same_model_are_never_merged(monkeypatch):
    entries = [entry(0x5000, b'one'), entry(0x5000, b'two')]
    monkeypatch.setattr(hid, 'enumerate', lambda *args: entries)
    manager = DeviceMgr()
    devices = manager.find_hid_devices(ledger.LedgerPlugin.DEVICE_IDS)
    assert len({d.id_ for d in devices}) == 2
    before = {d.path: d.id_ for d in devices}
    entries.reverse()
    assert {d.path: d.id_ for d in manager.find_hid_devices(ledger.LedgerPlugin.DEVICE_IDS)} == before


def test_app_identity_uses_ledger_serialization_with_zero_length_data():
    connection = Mock()
    connection.exchange.return_value = b'\x01\x0cBitcoin Cash\x052.4.7\x01\x02'
    assert session.app_info(connection) == ('Bitcoin Cash', '2.4.7')
    connection.exchange.assert_called_once_with(bytearray.fromhex('b001000000'), timeout=5000)


def test_open_app_reconnects_only_selected_device(monkeypatch):
    selected = SimpleNamespace(id_='selected', interface_number=0)
    other = SimpleNamespace(id_='other', interface_number=0)
    initial, reopened = Mock(), Mock()
    plugin = Mock()
    plugin.enumerate_devices.return_value = [other, selected]
    plugin.get_btchip_device.return_value = reopened
    monkeypatch.setattr(session, 'app_info', lambda connection: ('Bitcoin Cash', '2.4.14'))
    assert session.switch_to_bitcoin_cash(plugin, selected, initial, 'BOLOS') is reopened
    plugin.get_btchip_device.assert_called_once_with(selected)
    initial.exchange.assert_called_once_with(bytearray(b'\xe0\xd8\x00\x00\x0cBitcoin Cash'),
        timeout=10000)
    initial.close.assert_called_once()


def test_refusing_open_app_does_not_reconnect_or_switch_to_other_device():
    plugin, connection = Mock(), Mock()
    connection.exchange.side_effect = ledger.BTChipException('denied', sw=0x5501)
    with pytest.raises(UserCancelled):
        session.switch_to_bitcoin_cash(plugin, Mock(), connection, 'BOLOS')
    plugin.enumerate_devices.assert_not_called()


def test_locked_response_is_not_reported_as_missing_bch(monkeypatch):
    monkeypatch.setattr(ledger, 'btchip', lambda connection: SimpleNamespace(dongle=connection))
    connection = Mock()
    connection.exchange.side_effect = ledger.BTChipException('locked', sw=0x5515)
    client = ledger.Ledger_Client(connection)
    with pytest.raises(RuntimeError, match='locked'):
        client.checkDevice()
