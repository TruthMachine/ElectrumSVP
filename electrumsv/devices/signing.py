"""Public, transport-independent BSV signing contract.

Adapters receive a transaction and explicit signature targets, and return signatures only.
Constructing transactions, admitting signatures and broadcasting remain wallet responsibilities.
No Qt, vendor SDK, USB, network or wallet-database dependency belongs in this module.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import FrozenSet, Protocol, Tuple

from bitcoinx import PublicKey, SigHash
from bitcoinx.errors import InvalidSignature

from electrumsv.constants import ScriptType
from electrumsv.transaction import NO_SIGNATURE, Transaction


class SigningError(ValueError):
    pass


@dataclass(frozen=True)
class SigningCapabilities:
    scripts: FrozenSet[ScriptType]
    sighashes: FrozenSet[int]


@dataclass(frozen=True)
class SignatureTarget:
    input_index: int
    key_index: int
    public_key: bytes
    sighash: int


def _intent(tx):
    # Existing signatures may accumulate independently without changing the signing intent.
    return {'version': tx.version, 'locktime': tx.locktime,
        'inputs': [{'prev_hash': i.prev_hash.hex(), 'prev_idx': i.prev_idx,
            'sequence': i.sequence, 'value': i.value, 'threshold': i.threshold,
            'script': Transaction.get_preimage_script_bytes(i).hex(),
            'keys': [key.to_bytes().hex() for key in i.x_pubkeys]} for i in tx.inputs],
        'outputs': [{'value': o.value, 'script': bytes(o.script_pubkey).hex()} for o in tx.outputs]}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(',', ':')).encode('utf8')).hexdigest()


def _digest(tx, target):
    txin = tx.inputs[target.input_index]
    return tx.signature_hash(target.input_index, txin.value,
        Transaction.get_preimage_script_bytes(txin), SigHash(target.sighash))


def _target_dict(target):
    return {'input_index': target.input_index, 'key_index': target.key_index,
        'public_key': target.public_key.hex(), 'sighash': target.sighash}


def validate_present_signatures(tx):
    for input_index, txin in enumerate(tx.inputs):
        for key_index, signature in enumerate(txin.signatures):
            if signature == NO_SIGNATURE:
                continue
            try:
                kind = SigHash(signature[-1])
                target = SignatureTarget(input_index, key_index,
                    txin.x_pubkeys[key_index].to_public_key().to_bytes(), kind)
                valid = (kind.is_defined() and kind.has_forkid() and
                    PublicKey.from_bytes(target.public_key).verify_der_signature(
                        bytes(signature[:-1]), _digest(tx, target), None))
            except (ValueError, IndexError, InvalidSignature):
                valid = False
            if not valid:
                raise SigningError('An existing signature is invalid for its input and sighash')


@dataclass(frozen=True)
class SigningRequest:
    request_id: str
    transaction: Transaction
    targets: Tuple[SignatureTarget, ...]
    intent_hash: str

    def to_dict(self):
        data = self.transaction.to_dict(force_signing_metadata=True)
        data['prev_txs'] = [parent.to_hex() for _, parent in
            sorted(self.transaction.context.prev_txs.items())]
        return {'format': 'electrumsvp-signing-request', 'version': 1, 'chain': 'BSV',
            'request_id': self.request_id, 'transaction': data,
            'targets': [_target_dict(target) for target in self.targets]}

    @classmethod
    def from_dict(cls, data):
        """Decode the public envelope and check its content commitment, not device ownership."""
        try:
            if (data['format'], data['version'], data['chain']) != \
                    ('electrumsvp-signing-request', 1, 'BSV'):
                raise ValueError('Unsupported signing request format')
            tx = Transaction.from_dict(data['transaction'])
            validate_present_signatures(tx)
            targets, seen = [], set()
            for item in data['targets']:
                index, key_index, kind = item['input_index'], item['key_index'], item['sighash']
                if (any(type(value) is not int for value in (index, key_index, kind)) or
                        not 0 <= index < len(tx.inputs) or
                        not 0 <= key_index < len(tx.inputs[index].x_pubkeys)):
                    raise ValueError('Invalid signature target')
                key = bytes.fromhex(item['public_key'])
                if ((index, key_index) in seen or
                        tx.inputs[index].is_complete() or
                        tx.inputs[index].signatures[key_index] != NO_SIGNATURE or
                        key != tx.inputs[index].x_pubkeys[key_index].to_public_key().to_bytes() or
                        not SigHash(kind).is_defined() or not SigHash(kind).has_forkid() or
                        (SigHash(kind).base == SigHash.SINGLE and index >= len(tx.outputs))):
                    raise ValueError('Invalid signature target')
                seen.add((index, key_index))
                targets.append(SignatureTarget(index, key_index, key, kind))
            if not targets:
                raise ValueError('Empty signing request')
            intent_hash = _hash(_intent(tx))
            request_id = _hash({'intent': intent_hash, 'targets': [_target_dict(t) for t in targets]})
            if request_id != data['request_id']:
                raise ValueError('Signing request content does not match its identifier')
            return cls(request_id, tx, tuple(targets), intent_hash)
        except (KeyError, TypeError, ValueError, IndexError, AssertionError) as exc:
            raise SigningError('Invalid signing request: ' + str(exc)) from exc


@dataclass(frozen=True)
class InputSignature:
    input_index: int
    key_index: int
    signature: bytes  # DER followed by the sighash byte.


@dataclass(frozen=True)
class SigningResult:
    request_id: str
    signatures: Tuple[InputSignature, ...]

    def to_dict(self):
        return {'format': 'electrumsvp-signing-result', 'version': 1,
            'request_id': self.request_id, 'signatures': [
                {'input_index': s.input_index, 'key_index': s.key_index,
                 'signature': s.signature.hex()} for s in self.signatures]}

    @classmethod
    def from_dict(cls, data):
        try:
            if (data['format'], data['version']) != ('electrumsvp-signing-result', 1):
                raise ValueError('Unsupported signing result format')
            signatures = []
            for item in data['signatures']:
                if any(type(item[k]) is not int or item[k] < 0
                        for k in ('input_index', 'key_index')):
                    raise ValueError('Invalid signature target')
                signatures.append(InputSignature(item['input_index'], item['key_index'],
                    bytes.fromhex(item['signature'])))
            return cls(data['request_id'], tuple(signatures))
        except (KeyError, TypeError, ValueError) as exc:
            raise SigningError('Invalid signing result: ' + str(exc)) from exc


class ExternalSigner(Protocol):
    capabilities: SigningCapabilities

    def sign(self, request: SigningRequest) -> SigningResult:
        """Return any supported subset of the requested signatures, without broadcasting."""
        ...


def prepare_signing_request(tx, context, owns_key, capabilities, *, input_indexes=None,
        sighashes=None):
    """Sighashes map (input index, key index) to a requested type; defaults to 0x41."""
    snapshot = deepcopy(tx)
    snapshot.context = deepcopy(context)
    validate_present_signatures(snapshot)
    selected = set(range(len(tx.inputs))) if input_indexes is None else set(input_indexes)
    if any(type(index) is not int or not 0 <= index < len(tx.inputs) for index in selected):
        raise SigningError('Invalid signing input selection')
    targets = []
    for index, txin in enumerate(snapshot.inputs):
        if index not in selected or txin.is_complete():
            continue
        for key_index, key in enumerate(txin.x_pubkeys):
            if txin.signatures[key_index] != NO_SIGNATURE or not owns_key(key):
                continue
            kind = (sighashes or {}).get((index, key_index), tx.nHashType())
            if (kind not in capabilities.sighashes or not SigHash(kind).is_defined() or
                    not SigHash(kind).has_forkid()):
                raise SigningError('Signer does not support the requested BSV sighash')
            if txin.script_type not in capabilities.scripts:
                raise SigningError('Signer does not support this input script type')
            if SigHash(kind).base == SigHash.SINGLE and index >= len(tx.outputs):
                raise SigningError('SIGHASH_SINGLE requires a corresponding output')
            targets.append(SignatureTarget(index, key_index, key.to_public_key().to_bytes(), kind))
    if not targets:
        raise SigningError('No unsigned inputs belong to this signer and selection')
    intent_hash = _hash(_intent(snapshot))
    request_id = _hash({'intent': intent_hash, 'targets': [_target_dict(t) for t in targets]})
    return SigningRequest(request_id, snapshot, tuple(targets), intent_hash)


def apply_signing_result(tx, request, result):
    """Validate all returned signatures before changing any signature slot.

    Independent signers may add signatures since preparation, but neither transaction intent
    nor a populated signature slot may be replaced. Non-ALL signatures are verified with their
    own requested hash semantics, never with the wallet's default hash type.
    """
    if result.request_id != request.request_id or _hash(_intent(tx)) != request.intent_hash:
        raise SigningError('Signing response does not match the requested transaction')
    validate_present_signatures(tx)
    expected = {(t.input_index, t.key_index): t for t in request.targets}
    changes, seen = [], set()
    for item in result.signatures:
        slot = (item.input_index, item.key_index)
        if slot not in expected or slot in seen:
            raise SigningError('Unexpected or duplicate signature target')
        seen.add(slot)
        target = expected[slot]
        raw = bytes(item.signature)
        try:
            valid = (raw[-1:] == bytes([target.sighash]) and
                PublicKey.from_bytes(target.public_key).verify_der_signature(
                    raw[:-1], _digest(tx, target), None))
        except (ValueError, InvalidSignature):
            valid = False
        if not valid:
            raise SigningError('Invalid signature for the requested input, key or sighash')
        previous = tx.inputs[item.input_index].signatures[item.key_index]
        if previous not in (NO_SIGNATURE, raw):
            raise SigningError('Cannot replace an existing signature')
        changes.append(item)
    for item in changes:
        tx.inputs[item.input_index].signatures[item.key_index] = bytes(item.signature)
    return len(changes)
