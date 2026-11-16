"""Bounded parent verification for Ledger BCH; budgets are host policy, not device specs."""
from dataclasses import dataclass
from typing import Tuple

from bitcoinx import hash160

from electrumsv.constants import ScriptType
from electrumsv.devices.hardware import HardwareError
from electrumsv.i18n import _
from electrumsv.transaction import Transaction
from electrumsv.util import versiontuple


@dataclass(frozen=True)
class VerificationBudget:
    inputs: int
    outputs: int
    parent_bytes: int
    total_parent_bytes: int
    script_bytes: int
    parent_commands: int


# Conservative initial work budgets. They are NOT advertised maximum firmware capabilities.
LEGACY_BUDGET = VerificationBudget(4, 8, 32768, 65536, 8192, 512)
NANO_S_BUDGET = VerificationBudget(512, 32, 65536, 524288, 16384, 8192)
MODERN_BUDGET = VerificationBudget(1024, 128, 524288, 2097152, 131072, 32768)


def verification_budget(model):
    if model in ('Nano S Plus', 'Nano X'):
        return MODERN_BUDGET
    if model == 'Nano S':
        return NANO_S_BUDGET
    return LEGACY_BUDGET


def large_verification(tx, model):
    # This threshold requests a patience/connection confirmation, not reduced verification.
    return len(tx.inputs) > (16 if model in ('Nano S Plus', 'Nano X') else 4)


@dataclass(frozen=True)
class InputVerification:
    parents: Tuple[bytes, ...] = ()
    reason: str = ''

    @property
    def trusted(self):
        return bool(self.parents)


def _varint_size(value):
    return 1 if value < 253 else 3 if value <= 65535 else 5 if value <= 0xffffffff else 9


def parent_work(parent):
    """Measure before serializing; count the legacy library's parent-stream APDUs."""
    size = 8 + _varint_size(len(parent.inputs)) + _varint_size(len(parent.outputs))
    commands, max_script = 3, 0  # header, output count, locktime
    for txin in parent.inputs:
        length = len(bytes(txin.script_sig))
        size += 40 + _varint_size(length) + length
        commands += 1 + max(1, (length + 250) // 251)
        max_script = max(max_script, length)
    for txout in parent.outputs:
        length = len(bytes(txout.script_pubkey))
        size += 8 + _varint_size(length) + length
        commands += 1 + (length + 254) // 255
        max_script = max(max_script, length)
    return size, commands, max_script


def plan_input_verification(tx, context, model, app_version):
    budget = verification_budget(model)
    if versiontuple(app_version) < (1, 4, 0):
        return InputVerification(reason=_('This app version is outside the trusted-input route.'))
    if len(tx.inputs) > budget.inputs or len(tx.outputs) > budget.outputs:
        return InputVerification(reason=_('Input/output count exceeds this model’s verification budget.'))
    parents, total_size, total_commands = [], 0, 0
    for txin in tx.inputs:
        parent = context.prev_txs.get(txin.prev_hash)
        if parent is None:
            return InputVerification(reason=_('A complete previous transaction is unavailable.'))
        size, commands, script_size = parent_work(parent)
        total_size += size
        total_commands += commands
        if (size > budget.parent_bytes or total_size > budget.total_parent_bytes or
                script_size > budget.script_bytes or total_commands > budget.parent_commands):
            return InputVerification(reason=_('Previous transactions exceed this model’s '
                'byte, script or communication budget.'))
        if parent.hash() != txin.prev_hash or not 0 <= txin.prev_idx < len(parent.outputs):
            raise HardwareError(_('Previous transaction does not match the requested input.'))
        output = parent.outputs[txin.prev_idx]
        if output.value != txin.value:
            raise HardwareError(_('Previous transaction amount does not match the requested input.'))
        script = Transaction.get_preimage_script_bytes(txin)
        if txin.script_type == ScriptType.MULTISIG_P2SH:
            script = b'\xa9\x14' + hash160(script) + b'\x87'
        if bytes(output.script_pubkey) != script:
            raise HardwareError(_('Previous transaction script does not match the requested input.'))
        parents.append(parent.to_bytes())
    return InputVerification(tuple(parents))
