from types import SimpleNamespace

from bitcoinx import PrivateKey, Script
import pytest

from electrumsv.constants import ScriptType
from electrumsv.devices.hardware import HardwareError
from electrumsv.devices.ledger.input_verification import (plan_input_verification, parent_work,
    verification_budget, large_verification)
from electrumsv.transaction import Transaction, TransactionContext, XPublicKey, XTxInput, XTxOutput


def fixture():
    key = PrivateKey.from_int(1).public_key
    context, inputs = TransactionContext(), []
    for number in range(2):
        parent = Transaction(2, [XTxInput(bytes(32), 0xffffffff,
            Script(b'public coinbase fixture' + bytes([number])), 0xffffffff)],
            [XTxOutput(0, Script(b'\x6a')), XTxOutput(1000, key.P2PKH_script())], 0)
        context.prev_txs[parent.hash()] = parent
        inputs.append(XTxInput(parent.hash(), 1, Script(), 0xfffffffe, value=1000,
            x_pubkeys=[XPublicKey(pubkey_bytes=key.to_bytes())], threshold=1,
            signatures=[b'\xff'], script_type=ScriptType.P2PKH))
    return Transaction(2, inputs, [XTxOutput(900, key.P2PKH_script()),
        XTxOutput(1000, key.P2PKH_script())], 0), context


@pytest.mark.parametrize('model', ['Nano S', 'Nano S Plus', 'Nano X'])
def test_small_two_input_two_output_coinbase_parents_are_verified(model):
    tx, context = fixture()
    plan = plan_input_verification(tx, context, model, '2.4.7')
    assert plan.trusted
    assert len(plan.parents) == 2
    for raw, parent in zip(plan.parents, context.prev_txs.values()):
        assert len(raw) == parent_work(parent)[0]
        assert Transaction.from_bytes(raw).is_coinbase()


@pytest.mark.parametrize('error', ['hash', 'index', 'amount', 'script'])
def test_inconsistent_parents_are_errors_not_unverified_fallback(error):
    tx, context = fixture()
    parent = context.prev_txs[tx.inputs[0].prev_hash]
    if error == 'hash':
        parent.locktime = 1
    elif error == 'index':
        tx.inputs[0].prev_idx = 7
    elif error == 'amount':
        tx.inputs[0].value = 999
    else:
        tx.inputs[0].x_pubkeys[0] = XPublicKey(pubkey_bytes=PrivateKey.from_int(2).public_key.to_bytes())
    with pytest.raises(HardwareError, match='does not match'):
        plan_input_verification(tx, context, 'Nano S', '2.4.7')


def test_two_inputs_can_still_exceed_legacy_parent_script_budget():
    tx, context = fixture()
    parent = context.prev_txs.pop(tx.inputs[0].prev_hash)
    parent.outputs[0].script_pubkey = Script(b'\x6a' + b'x' * 20000)
    tx.inputs[0].prev_hash = parent.hash()
    context.prev_txs[parent.hash()] = parent
    assert not plan_input_verification(tx, context, 'Nano S', '2.4.7').trusted
    assert plan_input_verification(tx, context, 'Nano S Plus', '2.4.14').trusted


def test_missing_parent_is_explicitly_unverified():
    tx, context = fixture()
    context.prev_txs.clear()
    plan = plan_input_verification(tx, context, 'Nano X', '2.4.14')
    assert not plan.trusted and 'unavailable' in plan.reason


def test_declining_reduced_verification_never_starts_transaction_signing():
    pytest.importorskip('btchip')
    from unittest.mock import Mock
    from electrumsv.devices.ledger.ledger import Ledger_KeyStore
    from electrumsv.exceptions import UserCancelled
    tx, context = fixture()
    context.prev_txs.clear()
    client = Mock()
    client.getFirmwareVersion.return_value = {'version': '2.4.7'}
    store = Mock()
    store.handler.yes_no_question.return_value = False
    store.get_client_electrum.return_value = SimpleNamespace(_device=None, dongleObject=client)
    with pytest.raises(UserCancelled):
        Ledger_KeyStore.sign_transaction(store, tx, '', context)
    client.getTrustedInput.assert_not_called()
    client.startUntrustedTransaction.assert_not_called()


def parent_with_size(size, script_limit, tag):
    key = PrivateKey.from_int(1).public_key
    parent = Transaction(2, [XTxInput(bytes(32), 0xffffffff,
        Script(b'offline fixture' + tag.to_bytes(4, 'little')), 0xffffffff)],
        [XTxOutput(1000, key.P2PKH_script())], 0)
    if size is not None:
        count = size // (script_limit - 16) + 1
        parent.outputs.extend(XTxOutput(0, Script()) for _ in range(count))
        length = (size - len(parent.to_bytes())) // count
        for output in parent.outputs[1:]:
            output.script_pubkey = Script(b'x' * length)
        for _ in range(4):
            delta = size - len(parent.to_bytes())
            if not delta:
                break
            last = parent.outputs[-1]
            last.script_pubkey = Script(b'x' * (len(bytes(last.script_pubkey)) + delta))
        assert len(parent.to_bytes()) == size
    return parent


def transaction_spending(parents, output_count=2):
    key = PrivateKey.from_int(1).public_key
    inputs = [XTxInput(parent.hash(), 0, Script(), 0xfffffffe, value=1000,
        x_pubkeys=[XPublicKey(pubkey_bytes=key.to_bytes())], threshold=1,
        signatures=[b'\xff'], script_type=ScriptType.P2PKH) for parent in parents]
    return (Transaction(2, inputs, [XTxOutput(1, key.P2PKH_script())
        for _ in range(output_count)], 0),
        TransactionContext(prev_txs={p.hash(): p for p in parents}))


@pytest.mark.parametrize('model', ['Nano S', 'Nano S Plus', 'Nano X', 'Unknown legacy Ledger'])
@pytest.mark.parametrize('field', ['inputs', 'outputs', 'parent_bytes', 'total_parent_bytes',
    'script_bytes', 'parent_commands'])
@pytest.mark.parametrize('extra', [0, 1])
def test_real_serialized_parent_at_and_above_each_budget(model, field, extra):
    budget = verification_budget(model)
    limit = getattr(budget, field) + extra
    parents = [parent_with_size(None, budget.script_bytes, 0)]
    output_count = 2
    if field == 'inputs':
        parents = [parent_with_size(None, budget.script_bytes, i) for i in range(limit)]
    elif field == 'outputs':
        output_count = limit
    elif field == 'parent_bytes':
        parents = [parent_with_size(limit, budget.script_bytes, 0)]
    elif field == 'total_parent_bytes':
        count = budget.total_parent_bytes // budget.parent_bytes + 1
        sizes = [limit // count] * count
        sizes[-1] += limit - sum(sizes)
        parents = [parent_with_size(size, budget.script_bytes, i) for i, size in enumerate(sizes)]
    elif field == 'script_bytes':
        parents[0].outputs.append(XTxOutput(0, Script(b'x' * limit)))
    else:
        # Empty parent outputs consume commands cheaply. Split across parents so
        # the byte cap does not accidentally mask the communication boundary.
        count = max(1, (limit * 9) // budget.parent_bytes + 1)
        commands = [limit // count] * count
        commands[-1] += limit - sum(commands)
        parents = [parent_with_size(None, budget.script_bytes, i) for i in range(count)]
        for parent, wanted in zip(parents, commands):
            parent.outputs.extend(XTxOutput(0, Script())
                for _ in range(wanted - parent_work(parent)[1]))
        assert sum(parent_work(p)[1] for p in parents) == limit
    tx, context = transaction_spending(parents, output_count)
    plan = plan_input_verification(tx, context, model, '2.4.14')
    assert plan.trusted == (extra == 0)


@pytest.mark.parametrize('model', ['Nano S', 'Nano S Plus', 'Nano X'])
@pytest.mark.parametrize('count', [160, 161])
def test_miner_sized_small_parents_keep_full_verification(model, count):
    tx, context = transaction_spending([parent_with_size(None, 8192, i) for i in range(count)])
    assert plan_input_verification(tx, context, model, '2.4.14').trusted
    assert large_verification(tx, model)


def test_explicit_opt_out_requires_confirmation_and_does_not_send_parents():
    pytest.importorskip('btchip')
    from unittest.mock import Mock
    from electrumsv.devices.ledger.ledger import Ledger_KeyStore
    from electrumsv.exceptions import UserCancelled
    tx, context = fixture()
    client, store = Mock(), Mock()
    store.handler.yes_no_question.return_value = False
    store.get_client_electrum.return_value = SimpleNamespace(_device=None, dongleObject=client)
    with pytest.raises(UserCancelled):
        Ledger_KeyStore.sign_transaction(store, tx, '', context, verify_inputs=False)
    assert 'switched off' in store.handler.yes_no_question.call_args[0][0]
    client.getTrustedInput.assert_not_called()
    client.startUntrustedTransaction.assert_not_called()
