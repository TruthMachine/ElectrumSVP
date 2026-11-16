"""Shared adapter contract tests. No USB devices, network or real wallet seeds."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from bitcoinx import BIP32PrivateKey, PrivateKey, Script
import pytest

from electrumsv.constants import ScriptType
from electrumsv.device import Device, DeviceMgr, DeviceUnpairableError
from electrumsv.devices.hardware import BACKENDS, HardwareError, HardwareWallets
from electrumsv.exceptions import UserCancelled
from electrumsv.networks import Net
from electrumsv.transaction import Transaction, TransactionContext, XPublicKey, XTxInput, XTxOutput


def signing_fixture(count=1):
    key = PrivateKey.from_int(1)  # public test key, never a real wallet
    xkey = XPublicKey(pubkey_bytes=key.public_key.to_bytes())
    inputs = [XTxInput(bytes([i + 1]) * 32, i, Script(), 0xfffffffe, value=1000,
        x_pubkeys=[xkey], threshold=1, signatures=[b'\xff'], script_type=ScriptType.P2PKH)
        for i in range(count)]
    tx = Transaction(2, inputs, [XTxOutput(count * 1000 - 100, key.public_key.P2PKH_script())], 0)
    tx.output_info = [{}]
    def sign(candidate, password, context):
        for txin in candidate.inputs:
            txin.signatures[0] = key.sign(candidate.preimage_hash(txin), None) + b'\x41'
    store = SimpleNamespace(hw_type='ledger', sign_transaction=sign,
        is_signature_candidate=lambda k: k == xkey)
    manager = Mock()
    manager.get_plugin.return_value.libraries_available = True
    return HardwareWallets(manager), store, tx, key


def test_signatures_are_verified_before_application():
    support, store, tx, key = signing_fixture(2)
    support.sign_transaction(store, tx, '', TransactionContext())
    assert tx.is_complete()
    assert all(i.signatures[0][-1] == 0x41 for i in tx.inputs)
    assert tx.version == 2


@pytest.mark.parametrize('failure', ['wrong_amount', 'wrong_key', 'wrong_sighash', 'cancel',
    'disconnect', 'partial_bad_signature'])
def test_bad_device_results_never_modify_transaction(failure):
    support, store, tx, key = signing_fixture(2)
    valid_sign = store.sign_transaction
    def sign(candidate, password, context):
        if failure == 'wrong_amount':
            candidate.outputs[0].value += 1
        valid_sign(candidate, password, context)
        if failure == 'wrong_key':
            candidate.inputs[0].signatures[0] = PrivateKey.from_int(2).sign(
                candidate.preimage_hash(candidate.inputs[0]), None) + b'\x41'
        if failure == 'wrong_sighash':
            candidate.inputs[0].signatures[0] = candidate.inputs[0].signatures[0][:-1] + b'\x01'
        if failure == 'partial_bad_signature':
            candidate.inputs[1].signatures[0] = b'bad\x41'
        if failure == 'cancel':
            raise UserCancelled()
        if failure == 'disconnect':
            raise OSError('disconnected')
    store.sign_transaction = sign
    with pytest.raises((HardwareError, UserCancelled, OSError)):
        support.sign_transaction(store, tx, '', TransactionContext())
    assert all(i.signatures == [b'\xff'] for i in tx.inputs)
    assert tx.outputs[0].value == 1900


def test_discovery_isolates_missing_vendor_and_reports_device_busy():
    manager = DeviceMgr()
    good = Mock(libraries_available=True, name='ledger')
    good.name = 'ledger'
    good.enumerate_devices.return_value = ['device']
    manager.get_plugin = Mock(side_effect=lambda kind: good if kind == 'ledger' else
        (_ for _ in ()).throw(ImportError('optional library missing')))
    manager.device_infos = Mock(return_value=['ready'])
    result = manager.hardware.discover()
    assert result.devices == (('ledger', 'ready'),)
    assert set(result.errors) == {'trezor', 'keepkey', 'digitalbitbox'}
    assert 'hardware-trezor' in result.errors['trezor']
    manager.device_infos.side_effect = OSError('Close Ledger Wallet and retry')
    result = manager.hardware.discover()
    assert 'Close Ledger Wallet' in result.errors['ledger']


def test_existing_device_metadata_can_be_loaded_without_libraries():
    manager = DeviceMgr()
    # Registry is metadata only: constructing the manager must not eagerly load SDKs.
    assert manager.plugins == {}
    assert set(manager.all_devices) == set(BACKENDS)


@pytest.mark.parametrize('kind', list(BACKENDS))
def test_wallet_public_data_survives_missing_vendor_sdk(kind):
    manager = DeviceMgr()
    manager.get_plugin = Mock(side_effect=ImportError('optional SDK absent'))
    xpub = BIP32PrivateKey.from_seed(bytes(range(16)), Net.COIN).public_key.to_extended_key_string()
    data = {'hw_type': kind, 'xpub': xpub, 'derivation': "m/44'/0'/0'", 'label': 'test',
        'cfg': {'mode': 0}}
    store = manager.create_keystore(data, None)
    assert store.get_master_public_key() == xpub
    assert store.derive_pubkey((0, 0)) is not None
    assert store.to_derivation_data()['cfg'] == data['cfg']
    assert not store.can_export()
    with pytest.raises(RuntimeError, match='reopen'):
        store.sign_message((0, 0), 'test', '')


def test_uninitialized_device_never_enters_legacy_seed_import():
    support, store, tx, key = signing_fixture()
    with pytest.raises(HardwareError, match='manufacturer'):
        support.connect('ledger', SimpleNamespace(initialized=False), Mock())
    support.manager.get_plugin.return_value.setup_device.assert_not_called()


def test_parallel_scan_does_not_interrupt_signing():
    support, store, tx, key = signing_fixture()
    entered, release = threading.Event(), threading.Event()
    errors = []
    def hold():
        with support._operation_lock:
            entered.set()
            release.wait(2)
    thread = threading.Thread(target=hold)
    thread.start()
    assert entered.wait(2)
    try:
        with pytest.raises(HardwareError, match='already in progress'):
            support.discover()
    finally:
        release.set()
        thread.join()


@pytest.mark.parametrize('bad', [False, True])
def test_message_signature_must_match_requested_message_and_key(bad):
    support, store, tx, key = signing_fixture()
    store.derive_pubkey = lambda path: key.public_key
    store.sign_message = lambda path, message, password: key.sign_message(
        'different message' if bad else message)
    if bad:
        with pytest.raises(HardwareError, match='invalid message signature'):
            support.sign_message(store, (0, 0), 'ElectrumSVP test', '')
    else:
        signature = support.sign_message(store, (0, 0), 'ElectrumSVP test', '')
        assert key.public_key.verify_message(signature, 'ElectrumSVP test')


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('matching', [False, True])
def test_usb_identity_alone_never_proves_wallet_pairing(cached, matching):
    manager = DeviceMgr()
    device = Device(b'usb', 0, 'same-usb-identity', (0x2c97, 0x5000), 0xffa0, 'hid')
    key = BIP32PrivateKey.from_seed(bytes(range(16)), Net.COIN).public_key
    expected = key.to_extended_key_string()
    client, plugin, handler = Mock(), Mock(), Mock()
    client.get_master_public_key.return_value = key if matching else key.child_safe(1)
    plugin.create_client.return_value = client
    manager.pair_xpub(expected, device.id_)
    if cached:
        manager.clients[client] = (device.path, device.id_)
    if matching:
        assert manager._client_by_xpub(plugin, expected, "m/44'/0'/0'", handler, [device]) is client
    else:
        with pytest.raises(DeviceUnpairableError, match='does not match'):
            manager._client_by_xpub(plugin, expected, "m/44'/0'/0'", handler, [device])
        assert manager.xpub_id(expected) is None
        client.close.assert_called_once()
    client.get_master_public_key.assert_called_once_with("m/44'/0'/0'")
    assert client.handler is handler


def test_one_device_can_be_selected_for_multiple_accounts_and_unpaired_together():
    manager = DeviceMgr()
    device = Device(b'usb', 0, 'same-device', (0x2c97, 0x5000), 0xffa0, 'hid')
    client = Mock()
    plugin = Mock(name='ledger', libraries_available=True, DEVICE_IDS=[device.product_key])
    plugin.create_client.return_value = client
    manager.pair_xpub('first-account', device.id_)
    manager.pair_xpub('second-account', device.id_)
    infos = manager.device_infos(None, plugin, [device])
    assert len(infos) == 1
    assert infos[0].device == device
    manager.unpair_id(device.id_)
    assert manager.xpub_ids == {}
    client.close.assert_called_once()


def test_selected_device_checks_each_account_path_not_just_transport():
    from electrumsv.devices.hardware import DeviceStatus
    manager = DeviceMgr()
    plugin, client, handler = Mock(), Mock(), Mock()
    manager.get_plugin = Mock(return_value=plugin)
    manager._create_client = Mock(return_value=client)
    root = BIP32PrivateKey.from_seed(bytes(range(16)), Net.COIN).public_key
    stores = [SimpleNamespace(hw_type='ledger', xpub=key.to_extended_key_string(),
        get_derivation=lambda path=path: path) for path, key in [
            ("m/44'/0'/0'", root), ("m/44'/0'/1'", root.child_safe(1))]]
    client.get_master_public_key.side_effect = [root.child_safe(9), root.child_safe(1)]
    device = SimpleNamespace(id_='selected-device')
    status = DeviceStatus('ledger', 'Nano S', 'app_not_open', '', device=device)
    manager.hardware.verify_device(status, stores, handler)
    assert [call.args[0] for call in client.get_master_public_key.call_args_list] == [
        "m/44'/0'/0'", "m/44'/0'/1'"]
    assert manager.xpub_id(stores[0].xpub) is None
    assert manager.xpub_id(stores[1].xpub) == device.id_


def test_optional_parents_are_local_bounded_and_missing_does_not_block_review():
    support, store, tx, _ = signing_fixture(2)
    context = TransactionContext()
    parent = object()
    lookup = Mock(side_effect=[parent, None])
    support.add_available_parents([store], tx, context, lookup)
    assert context.prev_txs == {tx.inputs[0].prev_hash: parent}
    assert lookup.call_count == 2
    tx.inputs *= 513  # Beyond every currently supported Ledger verification input budget.
    lookup.reset_mock()
    support.add_available_parents([store], tx, context, lookup)
    lookup.assert_not_called()


@pytest.mark.parametrize('outcome', ['match', 'wrong_key', 'removed', 'wrong_brand'])
def test_explicit_transport_choice_never_falls_back_and_rechecks_xpub(outcome):
    from electrumsv.devices.hardware import DeviceStatus
    manager = DeviceMgr()
    device = Device(b'usb', 0, 'chosen', (0x2c97, 0x5000), 0xffa0, 'hid')
    key = BIP32PrivateKey.from_seed(bytes(range(16)), Net.COIN).public_key
    client = Mock()
    client.get_master_public_key.return_value = key.child_safe(1) if outcome == 'wrong_key' else key
    plugin = Mock(libraries_available=True); plugin.name = 'ledger'
    store = SimpleNamespace(plugin=plugin, handler=Mock(), xpub=key.to_extended_key_string(),
        get_derivation=lambda: "m/44'/0'/1'")
    manager.scan_devices = Mock(return_value=[] if outcome == 'removed' else [device])
    manager._create_client = Mock(return_value=client)
    manager.select_device = Mock(side_effect=AssertionError('must not fall back'))
    manager.hardware.selected_device = DeviceStatus(
        'trezor' if outcome == 'wrong_brand' else 'ledger', 'Chosen device', 'bch_ready', '',
        device=device, verified_xpubs=(store.xpub,))
    if outcome == 'match':
        assert manager.client_for_keystore(plugin, store, True) is client
        client.get_master_public_key.assert_called_once_with("m/44'/0'/1'")
        assert manager.xpub_id(store.xpub) == 'chosen'
    else:
        with pytest.raises(DeviceUnpairableError):
            manager.client_for_keystore(plugin, store, True)
        assert manager.xpub_id(store.xpub) is None
    manager.select_device.assert_not_called()


def test_connection_check_does_not_commit_transport_or_key_pairing():
    from electrumsv.devices.hardware import DeviceStatus
    manager = DeviceMgr()
    device = Device(b'usb', 0, 'candidate', (0x2c97, 0x5000), 0xffa0, 'hid')
    key = BIP32PrivateKey.from_seed(bytes(range(16)), Net.COIN).public_key
    plugin, client = Mock(libraries_available=True), Mock()
    manager.get_plugin = Mock(return_value=plugin)
    manager._create_client = Mock(return_value=client)
    plugin.enumerate_devices.return_value = [device]
    status = DeviceStatus('ledger', 'Nano S Plus', 'bch_ready', '', device=device)
    plugin.get_device_status.return_value = status
    client.get_master_public_key.return_value = key
    store = SimpleNamespace(hw_type='ledger', xpub=key.to_extended_key_string(),
        get_derivation=lambda: "m/44'/0'/0'")
    checked = manager.hardware.check_device(status, [store], Mock())
    assert checked.verified_xpubs == (store.xpub,)
    assert not manager.xpub_ids
    assert manager.hardware.selected_device is None
    client.sign_transaction.assert_not_called()


def test_probe_never_connects_or_requests_account_keys():
    from electrumsv.devices.hardware import DeviceStatus
    manager = DeviceMgr()
    row = DeviceStatus('ledger', 'Nano X', 'bch_ready', '', device=Mock())
    manager.hardware.status = Mock(return_value=(row,))
    manager.hardware.check_device = Mock(side_effect=AssertionError('probe must be passive'))
    assert manager.hardware.inspect([SimpleNamespace(hw_type='ledger')]) == (row,)
    manager.hardware.check_device.assert_not_called()


def test_locked_connection_preserves_typed_state_and_never_reads_keys():
    from electrumsv.devices.hardware import DeviceCheckError, DeviceStatus
    manager = DeviceMgr()
    device = Device(b'usb', 0, 'locked', (0x2c97, 0x5000), 0xffa0, 'hid')
    plugin = Mock(libraries_available=True)
    manager.get_plugin = Mock(return_value=plugin)
    manager._create_client = Mock()
    plugin.enumerate_devices.return_value = [device]
    row = DeviceStatus('ledger', 'Nano S', 'locked', 'Unlock the device', device=device)
    plugin.get_device_status.return_value = row
    with pytest.raises(DeviceCheckError) as error:
        manager.hardware.check_device(row, [], Mock())
    assert error.value.status.state == 'locked'
    manager._create_client.assert_not_called()
