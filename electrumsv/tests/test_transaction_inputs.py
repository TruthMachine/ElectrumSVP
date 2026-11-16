from threading import Event
from unittest.mock import Mock

from bitcoinx import Script

from electrumsv.services.transaction_inputs import input_values, load_previous_transactions
from electrumsv.transaction import Transaction, XTxInput, XTxOutput


def fixture():
    parent = Transaction(1, [XTxInput(b'\0' * 32, 0xffffffff, Script(b'\x01\x01'),
        0xffffffff)], [XTxOutput(1000, Script(b'\x51')), XTxOutput(2000, Script(b'\x51'))], 0)
    tx = Transaction(1, [XTxInput(parent.hash(), i, Script(), 0xffffffff)
        for i in range(2)], [XTxOutput(2900, Script(b'\x51'))], 0)
    return tx, parent


def test_external_inputs_fetch_one_parent_and_resolve_amounts_without_mutating_tx():
    tx, parent = fixture()
    request = Mock(return_value=parent.to_hex())
    before = tx.to_bytes()
    parents = load_previous_transactions(tx, {}, lambda _: None, request, Event())
    assert input_values(tx, parents) == {0: 1000, 1: 2000}
    assert sum(input_values(tx, parents).values()) - sum(o.value for o in tx.outputs) == 100
    assert request.call_count == 1
    assert tx.to_bytes() == before and all(i.value is None for i in tx.inputs)


def test_unrelated_parent_and_invalid_output_index_never_supply_amounts():
    tx, parent = fixture()
    unrelated = Transaction(2, parent.inputs, parent.outputs, 0)
    parents = load_previous_transactions(tx, {}, lambda _: None,
        Mock(return_value=unrelated.to_hex()), Event())
    assert input_values(tx, parents) == {}
    tx.inputs[1].prev_idx = 2
    assert input_values(tx, {parent.hash(): parent}) == {0: 1000}


def test_local_offline_failure_and_cancellation():
    tx, parent = fixture()
    request = Mock()
    assert load_previous_transactions(tx, {}, lambda _: parent, request, Event())
    request.assert_not_called()
    assert not load_previous_transactions(tx, {}, lambda _: None, None, Event())
    request.side_effect = TimeoutError()
    assert not load_previous_transactions(tx, {}, lambda _: None, request, Event())
    request.reset_mock(); stopped = Event(); stopped.set()
    assert not load_previous_transactions(tx, {}, lambda _: None, request, stopped)
    request.assert_not_called()


def test_coinbase_input_is_not_requested():
    _, parent = fixture()
    request = Mock()
    assert load_previous_transactions(parent, {}, lambda _: None, request, Event()) == {}
    assert input_values(parent, {}) == {}
    request.assert_not_called()


def test_nonstandard_output_display_handles_opcodes_and_truncation():
    from electrumsv.bitcoin import script_bytes_to_asm
    assert script_bytes_to_asm(Script(b'\x51')) == '1'
    assert '[script truncated]' in script_bytes_to_asm(Script(b'\x02\x01'))
