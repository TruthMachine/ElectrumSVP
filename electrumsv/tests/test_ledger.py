"""Ledger discovery and setup regressions, without connecting to a device."""

from types import SimpleNamespace
from unittest.mock import Mock

from bitcoinx import BIP32PrivateKey, bip32_decompose_chain_string
import pytest

hid = pytest.importorskip("hid")
pytest.importorskip("btchip.btchip")

from electrumsv.device import DeviceMgr
from electrumsv.devices.ledger import ledger
from electrumsv.networks import Net


@pytest.mark.parametrize("product_id", [0x0005, 0x5000, 0x5011, 0x5015, 0x1000, 0x1011, 0x0004, 0x4000, 0x4011, 0x4015])
def test_enumerate_nano_devices(monkeypatch, product_id):
    """S Plus USB identities and existing S/X devices reach the wallet wizard."""
    entries = [
        dict(vendor_id=0x2c97, product_id=product_id, path=b"ledger", serial_number="test",
            interface_number=0, usage_page=0xffa0),
        dict(vendor_id=0x2c97, product_id=product_id, path=b"fido", serial_number="test",
            interface_number=1, usage_page=0xf1d0),
        dict(vendor_id=0x1234, product_id=product_id, path=b"other", serial_number="other",
            interface_number=0, usage_page=0xffa0),
    ]
    monkeypatch.setattr(hid, "enumerate", lambda *args: entries)
    monkeypatch.setattr(ledger, "app_state", SimpleNamespace(device_manager=DeviceMgr()))

    plugin = ledger.LedgerPlugin("ledger")
    devices = plugin.enumerate_devices()

    assert len(devices) == 1
    assert devices[0].path == b"ledger"
    assert devices[0].product_key == (0x2c97, product_id)


class FakeBitcoinCashApp:
    """Model the public-key path restriction introduced by BCH app 2.4.10.

    This is a host-side test double, not a Ledger firmware emulator. The seed
    below is the public BIP32 test vector, never a user wallet.
    """

    def __init__(self, restricted):
        self.restricted = restricted
        self.root = BIP32PrivateKey.from_seed(bytes(range(16)), Net.COIN)
        self.paths = []
        self.dongle = Mock()
        self.dongle.exchange.return_value = b'\x01\x0cBitcoin Cash\x062.4.13\x01\x00'

    def getFirmwareVersion(self):
        return {"version": "2.4.13" if self.restricted else "2.0.3"}

    def getOperationMode(self):
        return 1

    def getVerifyPinRemainingAttempts(self):
        # Modern Nano devices unlock on the device, not through the HW.1 PIN API.
        raise ledger.BTChipException("Unsupported instruction", sw=0x6d00)

    def getWalletPublicKey(self, path):
        self.paths.append(path)
        indices = bip32_decompose_chain_string("m/" + path)
        if self.restricted:
            allowed = (len(indices) >= 2 and indices[1] in (0x80000000, 0x80000091))
            allowed = allowed or (indices and indices[0] in (0x8000002d, 0x80454c45))
            if not allowed:
                raise ledger.BTChipException("Invalid status 6f00", sw=0x6f00)
        node = self.root
        for index in indices:
            node = node.child_safe(index)
        return {
            "publicKey": node.public_key.to_bytes(compressed=False),
            "chainCode": node.derivation().chain_code,
        }


@pytest.mark.parametrize("restricted", [False, True], ids=["legacy-app", "hardened-app"])
@pytest.mark.parametrize("wallet_path", ["m/44'/0'/0'", "m/44'/145'/0'"])
def test_setup_preserves_wallet_keys(monkeypatch, coin, restricted, wallet_path):
    app = FakeBitcoinCashApp(restricted)
    monkeypatch.setattr(ledger, "btchip", lambda transport: app)
    client = ledger.Ledger_Client(None)
    manager = Mock()
    manager.client_by_id.return_value = client
    monkeypatch.setattr(ledger, "app_state", SimpleNamespace(device_manager=manager))
    plugin = ledger.LedgerPlugin("ledger")
    monkeypatch.setattr(plugin, "create_handler", lambda wizard: Mock())
    device_info = SimpleNamespace(device=SimpleNamespace(id_="test-ledger"))

    # The old setup probe tries m/44', which the hardened app refuses.
    plugin.setup_device(device_info, None)
    assert client.preflightDone

    # Setup is only a probe: the user's selected/stored account still determines
    # the complete xpub, including its parent fingerprint and child number.
    actual = plugin.get_master_public_key("test-ledger", wallet_path, None)
    expected = app.root
    for index in bip32_decompose_chain_string(wallet_path):
        expected = expected.child_safe(index)
    assert actual.to_extended_key_string() == expected.public_key.to_extended_key_string()


def test_setup_propagates_device_failure(monkeypatch):
    """A failed device probe must not advance the wallet setup silently."""
    client = Mock()
    client.get_master_public_key.side_effect = ledger.BTChipException("Disconnected")
    manager = Mock()
    manager.client_by_id.return_value = client
    monkeypatch.setattr(ledger, "app_state", SimpleNamespace(device_manager=manager))
    plugin = ledger.LedgerPlugin("ledger")
    monkeypatch.setattr(plugin, "create_handler", lambda wizard: Mock())

    with pytest.raises(ledger.BTChipException, match="Disconnected"):
        plugin.setup_device(SimpleNamespace(device=SimpleNamespace(id_="test-ledger")), None)


@pytest.mark.parametrize('matching', [False, True])
def test_display_checks_device_public_key_against_wallet(matching):
    root = BIP32PrivateKey.from_seed(bytes(range(16)), Net.COIN)
    store = ledger.Ledger_KeyStore({'hw_type': 'ledger', 'derivation': "m/44'/0'/0'",
        'xpub': root.public_key.to_extended_key_string()}, None)
    store.handler = Mock()
    client = Mock()
    child = root.child_safe(0).child_safe(0 if matching else 1)
    client.getWalletPublicKey.return_value = {'publicKey': child.public_key.to_bytes(compressed=False)}
    store.get_client = lambda: client
    if matching:
        store.show_address('0/0')
    else:
        with pytest.raises(RuntimeError, match='does not match'):
            store.show_address('0/0')
    store.handler.finished.assert_called_once()


@pytest.mark.parametrize('script_type', ['P2PKH', 'MULTISIG_BARE', 'MULTISIG_P2SH'])
@pytest.mark.parametrize('explicit_selection', [False, True])
@pytest.mark.parametrize('verify_inputs', [False, True])
def test_ledger_partial_signing_preserves_other_signatures(script_type, explicit_selection,
        verify_inputs, locked_after_first=False):
    from bitcoinx import PrivateKey, Script, hash160
    from electrumsv.constants import ScriptType
    from electrumsv.devices.hardware import HardwareWallets
    from electrumsv.transaction import Transaction, TransactionContext, XPublicKey, XTxInput, XTxOutput
    root = BIP32PrivateKey.from_seed(bytes(range(16)), Net.COIN)
    store = ledger.Ledger_KeyStore({'hw_type': 'ledger', 'derivation': "m/44'/0'/0'",
        'xpub': root.public_key.to_extended_key_string()}, None)
    store.handler = Mock()
    key = root.child_safe(0).child_safe(0)
    peer = PrivateKey.from_int(2)
    own = XPublicKey(bip32_xpub=store.xpub, derivation_path=(0, 0))
    other = XPublicKey(pubkey_bytes=peer.public_key.to_bytes())
    kind = getattr(ScriptType, script_type)
    multisig = kind != ScriptType.P2PKH
    context = TransactionContext()
    inputs = []
    for index in range(2):
        keys = [own, other] if multisig else [other if index == 0 and not explicit_selection else own]
        txin = XTxInput(bytes(32), 0, Script(), 0xfffffffe, value=1000,
            x_pubkeys=keys, signatures=[b'\xff'] * len(keys), threshold=len(keys), script_type=kind)
        script = Transaction.get_preimage_script_bytes(txin)
        if kind == ScriptType.MULTISIG_P2SH:
            script = b'\xa9\x14' + hash160(script) + b'\x87'
        parent = Transaction(2, [XTxInput(bytes(32), 0xffffffff, Script(bytes([index])), 0xffffffff)],
            [XTxOutput(1000, Script(script))], 0)
        txin.prev_hash = parent.hash()
        context.prev_txs[parent.hash()] = parent
        inputs.append(txin)
    tx = Transaction(2, inputs, [XTxOutput(1900, key.public_key.P2PKH_script())], 0)
    tx.output_info = [{}]
    existing = {}
    for index, txin in enumerate(tx.inputs):
        if multisig or (index == 0 and not explicit_selection):
            slot = 1 if multisig else 0
            existing[index, slot] = peer.sign(tx.preimage_hash(txin), None) + b'\x41'
            txin.signatures[slot] = existing[index, slot]
    client = Mock()
    client.getFirmwareVersion.return_value = {'version': '2.4.7'}
    client.getTrustedInput.side_effect = lambda parent, index: {'value': b'trusted'}
    client.finalizeInputFull.return_value = {'confirmationNeeded': False}
    targets = [1] if explicit_selection or not multisig else [0, 1]
    client.untrustedHashSign.side_effect = [bytearray(key.sign(tx.preimage_hash(tx.inputs[i]), None)
        + b'\x41') for i in targets]
    store.get_client_electrum = lambda: SimpleNamespace(dongleObject=client, _device=None)
    manager = Mock()
    manager.get_plugin.return_value.libraries_available = True
    if locked_after_first:
        from copy import deepcopy
        before = deepcopy(tx.to_dict(force_signing_metadata=True))
        client.untrustedHashSign.side_effect = [
            bytearray(key.sign(tx.preimage_hash(tx.inputs[0]), None) + b'\x41'),
            ledger.BTChipException('Device locked', sw=0x5515)]
        with pytest.raises(Exception, match='Ledger is locked'):
            HardwareWallets(manager).sign_transaction(store, tx, '', context)
        assert client.untrustedHashSign.call_count == 2
        assert tx.to_dict(force_signing_metadata=True) == before
        store.handler.finished.assert_called_once()
        return
    HardwareWallets(manager).sign_transaction(store, tx, '', context,
        input_indexes={1} if explicit_selection else None, verify_inputs=verify_inputs)
    assert client.untrustedHashSign.call_count == len(targets)
    assert client.getTrustedInput.call_count == (2 if verify_inputs else 0)
    initial = client.startUntrustedTransaction.call_args_list[0]
    assert len(initial.args[2]) == 2  # Current inputs remain committed even when parents are omitted.
    if not verify_inputs:
        store.handler.yes_no_question.assert_called_once()
    for (index, slot), signature in existing.items():
        assert tx.inputs[index].signatures[slot] == signature
    if explicit_selection:
        assert tx.inputs[0].signatures[0] == b'\xff'
        assert not tx.is_complete()
    else:
        assert tx.is_complete()
        # Check final unlocking-script ordering as well as individual ECDSA signatures.
        from bitcoinx import Tx, TxOutput
        raw_tx = Tx.from_bytes(tx.to_bytes())
        for raw_input, txin in zip(raw_tx.inputs, tx.inputs):
            output = context.prev_txs[txin.prev_hash].outputs[txin.prev_idx]
            raw_input.txo = TxOutput(output.value, output.script_pubkey)
        # P2SH is a legacy pre-Genesis UTXO case on BSV.
        raw_tx.verify_inputs(utxos_after_genesis=[kind != ScriptType.MULTISIG_P2SH] * 2)
    assert all(call.kwargs['version'] == 2 for call in client.startUntrustedTransaction.call_args_list)


def test_mid_signing_lock_keeps_existing_cosigner_and_discards_incomplete_device_result():
    test_ledger_partial_signing_preserves_other_signatures(
        'MULTISIG_BARE', False, True, locked_after_first=True)


def test_status_rejects_non_wallet_interface_without_none_exchange(monkeypatch):
    device = SimpleNamespace(product_key=(0x2c97, 0x5011), id_='fido', path=b'fido',
        interface_number=1, usage_page=0xf1d0)
    manager = Mock(); manager.client_lookup.return_value = None
    monkeypatch.setattr(ledger, 'app_state', SimpleNamespace(device_manager=manager))
    plugin = ledger.LedgerPlugin('ledger')
    status = plugin.get_device_status(device)
    assert status.state == 'unavailable'
    assert 'USB interface' in status.detail
    assert 'NoneType' not in status.detail
