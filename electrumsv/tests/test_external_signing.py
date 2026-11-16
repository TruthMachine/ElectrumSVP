from copy import deepcopy

from bitcoinx import PrivateKey, Script, SigHash
import pytest

from electrumsv.constants import ScriptType
from electrumsv.devices.signing import (InputSignature, SigningCapabilities, SigningError,
    SigningResult, prepare_signing_request, apply_signing_result, validate_present_signatures)
from electrumsv.transaction import Transaction, TransactionContext, XPublicKey, XTxInput, XTxOutput


KINDS = frozenset({0x41, 0x42, 0x43, 0xc1, 0xc2, 0xc3})
CAPABILITIES = SigningCapabilities(frozenset({ScriptType.P2PKH, ScriptType.MULTISIG_BARE}), KINDS)


def transaction():
    keys = [PrivateKey.from_int(1), PrivateKey.from_int(2)]
    inputs = [XTxInput(bytes([n + 1]) * 32, 0, Script(), 0xfffffffe, value=1000,
        x_pubkeys=[XPublicKey(pubkey_bytes=key.public_key.to_bytes())], threshold=1,
        signatures=[b'\xff'], script_type=ScriptType.P2PKH) for n, key in enumerate(keys)]
    return Transaction(2, inputs, [XTxOutput(900, keys[0].public_key.P2PKH_script()),
        XTxOutput(1000, keys[1].public_key.P2PKH_script())], 0), keys


def sign(request, key):
    target = request.targets[0]
    tx = request.transaction
    txin = tx.inputs[target.input_index]
    digest = tx.signature_hash(target.input_index, txin.value,
        Transaction.get_preimage_script_bytes(txin), SigHash(target.sighash))
    raw = key.sign(digest, None) + bytes([target.sighash])
    return SigningResult(request.request_id, (InputSignature(target.input_index, target.key_index, raw),))


@pytest.mark.parametrize('kind', sorted(KINDS))
def test_each_requested_sighash_is_verified_and_survives_json_roundtrip(kind):
    tx, keys = transaction()
    request = prepare_signing_request(tx, TransactionContext(), lambda key: True, CAPABILITIES,
        input_indexes={0}, sighashes={(0, 0): kind})
    apply_signing_result(tx, request, sign(request, keys[0]))
    assert tx.inputs[1].signatures == [b'\xff']
    restored = Transaction.from_dict(tx.to_dict(force_signing_metadata=True))
    assert restored.inputs[0].signatures == tx.inputs[0].signatures
    validate_present_signatures(restored)
    second = prepare_signing_request(restored, TransactionContext(), lambda key: True,
        CAPABILITIES, input_indexes={1})
    apply_signing_result(restored, second, sign(second, keys[1]))
    assert restored.is_complete()


def test_two_bare_multisig_cosigners_can_return_independently():
    tx, keys = transaction()
    tx.inputs = tx.inputs[:1]
    tx.inputs[0].x_pubkeys = [XPublicKey(pubkey_bytes=k.public_key.to_bytes()) for k in keys]
    tx.inputs[0].threshold = 2
    tx.inputs[0].script_type = ScriptType.MULTISIG_BARE
    tx.inputs[0].signatures = [b'\xff', b'\xff']
    requests = [prepare_signing_request(tx, TransactionContext(),
        lambda xkey, key=key: xkey.to_public_key() == key.public_key, CAPABILITIES) for key in keys]
    apply_signing_result(tx, requests[0], sign(requests[0], keys[0]))
    first_signature = tx.inputs[0].signatures[0]
    assert not tx.is_complete()
    apply_signing_result(tx, requests[1], sign(requests[1], keys[1]))
    assert tx.is_complete()
    assert tx.inputs[0].signatures[0] == first_signature


@pytest.mark.parametrize('kind', [0x43, 0xc1, 0xc3])
def test_ledger_capabilities_reject_non_all_before_device_io(kind):
    from electrumsv.devices.hardware import BACKENDS
    tx, keys = transaction()
    with pytest.raises(SigningError, match='sighash'):
        prepare_signing_request(tx, TransactionContext(), lambda key: True,
            BACKENDS['ledger'].signing, sighashes={(0, 0): kind})


@pytest.mark.parametrize('mutation', ['output', 'input', 'signature_target', 'signature_type'])
def test_invalid_or_stale_results_never_partially_apply(mutation):
    tx, keys = transaction()
    request = prepare_signing_request(tx, TransactionContext(), lambda key: True, CAPABILITIES)
    result = sign(request, keys[0])
    if mutation == 'output':
        tx.outputs[0].value += 1
    elif mutation == 'input':
        tx.inputs[0].value += 1
    elif mutation == 'signature_target':
        result = SigningResult(result.request_id, (InputSignature(1, 0, result.signatures[0].signature),))
    else:
        result = SigningResult(result.request_id, (InputSignature(0, 0,
            result.signatures[0].signature[:-1] + b'\xc1'),))
    with pytest.raises(SigningError):
        apply_signing_result(tx, request, result)
    assert all(txin.signatures == [b'\xff'] for txin in tx.inputs)


def test_public_wire_request_and_result_roundtrip():
    from electrumsv.devices.signing import SigningRequest
    import json
    tx, keys = transaction()
    request = prepare_signing_request(tx, TransactionContext(), lambda key: True, CAPABILITIES,
        input_indexes={1})
    decoded = SigningRequest.from_dict(json.loads(json.dumps(request.to_dict())))
    assert decoded.request_id == request.request_id
    assert decoded.targets == request.targets
    result = SigningResult.from_dict(json.loads(json.dumps(sign(decoded, keys[1]).to_dict())))
    assert apply_signing_result(tx, request, result) == 1
    assert tx.inputs[0].signatures == [b'\xff']
    assert tx.inputs[1].signatures != [b'\xff']


@pytest.mark.parametrize('field,value', [('input_index', -1), ('key_index', 9),
    ('input_index', True), ('public_key', '00'), ('sighash', 1)])
def test_malformed_wire_target_rejected(field, value):
    from electrumsv.devices.signing import SigningRequest
    tx, _ = transaction()
    data = prepare_signing_request(tx, TransactionContext(), lambda key: True, CAPABILITIES).to_dict()
    data['targets'][0][field] = value
    with pytest.raises(SigningError):
        SigningRequest.from_dict(data)
