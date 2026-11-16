"""Encoding of previous coinbase transactions at the vendor boundary."""
from bitcoinx import PrivateKey, Script
import pytest
from electrumsv.transaction import Transaction, XTxInput, XTxOutput


def coinbase_transaction():
    return Transaction(2, [XTxInput(bytes(32), 0xffffffff,
        Script(bytes.fromhex('0350c300')), 0xffffffff)], [
        XTxOutput(900, PrivateKey.from_int(1).public_key.P2PKH_script())], 0)


def test_trezor_coinbase_previous_input_preserves_null_outpoint():
    pytest.importorskip('trezorlib')
    from electrumsv.devices.trezor.trezor import TrezorPlugin
    tx = coinbase_transaction()
    plugin = TrezorPlugin('trezor')
    encoded = plugin.tx_inputs(tx, is_prev_tx=True)[0]
    assert encoded.prev_hash == bytes(32)
    assert encoded.prev_index == 0xffffffff
    assert encoded.script_sig == bytes(tx.inputs[0].script_sig)
    assert encoded.sequence == 0xffffffff
