"""Optional hardware adapters, without importing Qt, USB or vendor libraries.

DeviceMgr owns transports and pairing. This module owns adapter selection, capability
checks and the rules for admitting device results into a wallet. I/O methods run on a
worker; presentation handlers must be constructed on the GUI thread by the caller.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
from importlib import import_module
from functools import wraps
import threading
from typing import Any, Dict, Tuple

from bitcoinx import BIP32PublicKey, bip32_decompose_chain_string
from bitcoinx.errors import InvalidSignature

from electrumsv.constants import ScriptType
from electrumsv.exceptions import UserCancelled
from electrumsv.i18n import _
from electrumsv.networks import Net, SVMainnet
from electrumsv.transaction import NO_SIGNATURE
from .signing import (InputSignature, SigningCapabilities, SigningError, SigningResult,
    prepare_signing_request, apply_signing_result)


class HardwareError(RuntimeError):
    pass


def device_operation(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        if not self._operation_lock.acquire(False):
            raise HardwareError(_('A hardware wallet operation is already in progress. '
                'Finish or reject it on the device, then try again.'))
        try:
            return method(self, *args, **kwargs)
        finally:
            self._operation_lock.release()
    return guarded


@dataclass(frozen=True)
class Backend:
    kind: str
    label: str
    module: str
    extra: str
    screen: bool = True
    testnet: bool = True
    optional_previous_inputs: int = 0
    signing: SigningCapabilities = SigningCapabilities(
        frozenset({ScriptType.P2PKH, ScriptType.MULTISIG_P2SH}), frozenset({0x41}))

    def plugin_class(self, gui_kind):
        return import_module(self.module).plugin_class(gui_kind)


BACKENDS = {
    b.kind: b for b in (
        Backend('ledger', 'Ledger', 'electrumsv.devices.ledger', 'hardware-ledger', testnet=False,
            optional_previous_inputs=1024,
            signing=SigningCapabilities(frozenset({ScriptType.P2PKH, ScriptType.P2PK,
                ScriptType.MULTISIG_P2SH, ScriptType.MULTISIG_BARE}), frozenset({0x41}))),
        Backend('trezor', 'Trezor', 'electrumsv.devices.trezor', 'hardware-trezor'),
        Backend('keepkey', 'KeepKey', 'electrumsv.devices.keepkey', 'hardware-keepkey',
            testnet=False),
        Backend('digitalbitbox', 'BitBox01', 'electrumsv.devices.digitalbitbox',
            'hardware-bitbox01', screen=False),
    )
}


@dataclass(frozen=True)
class Discovery:
    devices: Tuple[Tuple[str, Any], ...]
    errors: Dict[str, str]


@dataclass(frozen=True)
class DeviceStatus:
    kind: str
    label: str
    state: str
    detail: str
    app: str = ''
    version: str = ''
    device: Any = None
    verified_xpubs: Tuple[str, ...] = ()
    check_error: str = ''


class DeviceCheckError(HardwareError):
    def __init__(self, status):
        self.status = status
        super().__init__(status.check_error or status.detail)


class HardwareWallets:
    def __init__(self, manager):
        self.manager = manager
        self._operation_lock = threading.RLock()
        # A session-only transport choice, never evidence that it controls an xpub.
        self.selected_device = None

    def add_available_parents(self, stores, tx, context, lookup):
        """Enrich optional verification from local data; never fetch unbounded network parents.

        Mandatory parent retrieval (e.g. Trezor) remains the wallet's existing responsibility.
        Missing optional parents are handled by the adapter's explicit review policy.
        """
        limit = max((BACKENDS[store.hw_type].optional_previous_inputs for store in stores
            if getattr(store, 'hw_type', None) in BACKENDS), default=0)
        if not 0 < len(tx.inputs) <= limit:
            return
        for txin in tx.inputs:
            if txin.prev_hash not in context.prev_txs:
                parent = lookup(txin.prev_hash)
                if parent is not None:
                    context.prev_txs[txin.prev_hash] = parent

    def backend(self, kind):
        try:
            return BACKENDS[kind]
        except KeyError:
            raise HardwareError(_('Unknown hardware wallet: {}').format(kind)) from None

    def plugin(self, kind):
        backend = self.backend(kind)
        try:
            plugin = self.manager.get_plugin(kind)
            if not plugin.libraries_available:
                raise HardwareError(plugin.get_library_not_available_message())
            return plugin
        except Exception as exc:
            raise HardwareError('{}: {}\n{}'.format(backend.label, exc,
                _('Install the optional {} dependencies.').format(backend.extra))) from exc

    @device_operation
    def status(self):
        """Read connection/app information without opening apps or asking for keys."""
        rows = []
        for kind, backend in BACKENDS.items():
            try:
                plugin = self.plugin(kind)
                devices = plugin.enumerate_devices()
                if not devices:
                    rows.append(DeviceStatus(kind, backend.label, 'not_detected',
                        _('Connect the device, unlock it and refresh. No USB interface was found; '
                          'this alone does not establish whether it is locked.')))
                for device in devices:
                    try:
                        rows.append(plugin.get_device_status(device))
                    except Exception as exc:
                        rows.append(DeviceStatus(kind, backend.label, 'unavailable', str(exc),
                            device=device))
            except Exception as exc:
                rows.append(DeviceStatus(kind, backend.label, 'unavailable', str(exc)))
        return tuple(rows)

    @device_operation
    def check_device(self, status, stores, handler):
        """Check transport/app and requested public keys, without committing a selection."""
        if status.device is None:
            raise HardwareError(_('Select a detected device.'))
        plugin = self.plugin(status.kind)
        device = next((device for device in plugin.enumerate_devices()
            if device.id_ == status.device.id_), None)
        if device is None:
            raise HardwareError(_('The selected device is unavailable. Refresh and retry.'))
        checked = plugin.get_device_status(device)
        if checked.state in ('locked', 'unavailable'):
            raise DeviceCheckError(checked)
        try:
            client = self.manager._create_client(device, handler, plugin)
            if client is None:
                raise HardwareError(_('The selected device is unavailable. Refresh and retry.'))
            client.handler = handler
            plugin.check_connection(client)
            matched = []
            for store in stores:
                if getattr(store, 'hw_type', None) == status.kind:
                    key = client.get_master_public_key(store.get_derivation())
                    if key is not None and key.to_extended_key_string() == store.xpub:
                        matched.append(store.xpub)
            checked = plugin.get_device_status(device)
            if checked.state not in ('connected', 'bch_ready'):
                raise DeviceCheckError(checked)
            if not matched:
                raise HardwareError(_('This device does not match the current account. '
                    'Check the selected device, account path and passphrase.'))
            return replace(checked, verified_xpubs=tuple(matched), check_error='')
        except (DeviceCheckError, UserCancelled):
            raise
        except Exception as exc:
            # Preserve a precise transport state even when connection/key checking failed.
            try:
                checked = plugin.get_device_status(device)
            except Exception:
                checked = replace(status, state='unavailable', detail=str(exc))
            raise DeviceCheckError(replace(checked, verified_xpubs=(),
                check_error='' if checked.state == 'locked' else str(exc))) from exc

    @device_operation
    def inspect(self, stores):
        """Read presence, lock and app state only; Connect owns application/key checks."""
        return self.status()

    @device_operation
    def verify_device(self, status, stores, handler):
        candidates = [store for store in stores if getattr(store, 'hw_type', None) == status.kind]
        if status.device is None or not candidates:
            raise HardwareError(_('Select a device of the same brand as this hardware account.'))
        plugin = self.plugin(status.kind)
        client = self.manager._create_client(status.device, handler, plugin)
        if client is None:
            raise HardwareError(_('The selected device is unavailable. Refresh and retry.'))
        client.handler = handler
        for store in candidates:
            key = client.get_master_public_key(store.get_derivation())
            if key is not None and key.to_extended_key_string() == store.xpub:
                self.manager.pair_xpub(store.xpub, status.device.id_)
                return
        raise HardwareError(_('This device does not match the current account. '
            'Check the selected device, account path and passphrase.'))

    @device_operation
    def discover(self):
        devices, errors = [], {}
        for kind in BACKENDS:
            try:
                plugin = self.plugin(kind)
                # One failed vendor must not hide working devices of another brand.
                found = plugin.enumerate_devices()
                infos = self.manager.device_infos(None, plugin, devices=found)
                devices.extend((kind, info) for info in infos)
                if kind in self.manager.discovery_errors:
                    errors[kind] = self.manager.discovery_errors[kind]
            except Exception as exc:
                errors[kind] = str(exc)
        return Discovery(tuple(devices), errors)

    @device_operation
    def connect(self, kind, info, handler):
        if not info.initialized:
            raise HardwareError(_('Initialize this device with its manufacturer application '
                'before importing it. Never enter its recovery seed into ElectrumSVP.'))
        self.plugin(kind).setup_device(info, handler)

    @device_operation
    def public_key(self, kind, info, path, handler):
        indices = bip32_decompose_chain_string(path)
        if not indices or len(indices) > 255:
            raise HardwareError(_('Choose an account derivation path.'))
        key = self.plugin(kind).get_master_public_key(info.device.id_, path, handler)
        if not isinstance(key, BIP32PublicKey):
            raise HardwareError(_('Device did not return an extended public key.'))
        return key

    def can_display_address(self, account):
        stores = account.get_keystores()
        if len(stores) != 1:
            # Legacy show_key implementations select account.get_keystore(), which is
            # not sufficient to choose a cosigner. Do not display the wrong address.
            return False
        kind = getattr(stores[0], 'hw_type', None)
        if getattr(stores[0], 'plugin', None) is None:
            return False
        if kind not in BACKENDS or not BACKENDS[kind].screen:
            return False
        return (account.get_default_script_type() == ScriptType.P2PKH and
            (BACKENDS[kind].testnet or Net.COIN is SVMainnet.COIN))

    @device_operation
    def display_address(self, account, key_id):
        if not self.can_display_address(account):
            raise HardwareError(_('Address display is unavailable for this account.'))
        store = account.get_keystores()[0]
        self.plugin(store.hw_type).show_key(account, key_id)

    @device_operation
    def sign_message(self, store, path, message, password):
        self.plugin(store.hw_type)
        if not message or len(message.encode('utf8')) > 65535:
            raise HardwareError(_('Enter a message between 1 and 65535 UTF-8 bytes.'))
        signature = store.sign_message(path, message, password)
        if not signature:
            raise UserCancelled()
        try:
            valid = store.derive_pubkey(path).verify_message(signature, message)
        except (ValueError, InvalidSignature):
            valid = False
        if not valid:
            raise HardwareError(_('Device returned an invalid message signature.'))
        return bytes(signature)

    @device_operation
    def sign_transaction(self, store, tx, password, context, *, input_indexes=None,
            verify_inputs=True):
        backend = self.backend(store.hw_type)
        self.plugin(store.hw_type)
        if not backend.testnet and Net.COIN is not SVMainnet.COIN:
            raise HardwareError(_('This adapter supports BSV mainnet only.'))
        if tx.nHashType() != 0x41:
            raise HardwareError(_('Hardware signing requires SIGHASH_ALL | FORKID (0x41).'))
        if input_indexes is not None and store.hw_type != 'ledger':
            raise HardwareError(_('Selecting individual inputs is implemented for Ledger only.'))
        if not verify_inputs and store.hw_type != 'ledger':
            raise HardwareError(_('Optional previous-transaction verification is implemented '
                'for Ledger only.'))
        # Never leave partial or malformed device results in the caller's transaction.
        try:
            request = prepare_signing_request(tx, context, store.is_signature_candidate,
                backend.signing, input_indexes=input_indexes)
        except SigningError as exc:
            raise HardwareError(str(exc)) from exc
        candidate = deepcopy(tx)
        candidate_context = deepcopy(context)
        options = {}
        if input_indexes is not None:
            options['input_indexes'] = input_indexes
        if not verify_inputs:
            options['verify_inputs'] = False
        store.sign_transaction(candidate, password, candidate_context, **options)
        handler = getattr(store, 'handler', None)
        if handler is not None and hasattr(handler, 'check_cancelled'):
            handler.check_cancelled()
        if handler is not None and hasattr(handler, 'show_stage'):
            handler.show_stage('verify')
        if len(candidate.inputs) != len(tx.inputs):
            raise HardwareError(_('Device returned an unexpected input count.'))
        changes = []
        for input_index, (original, signed) in enumerate(zip(tx.inputs, candidate.inputs)):
            if len(signed.signatures) != len(original.signatures):
                raise HardwareError(_('Device returned an unexpected signature count.'))
            for index, (old, new) in enumerate(zip(original.signatures, signed.signatures)):
                if old == new:
                    continue
                if old != NO_SIGNATURE:
                    raise HardwareError(_('Device attempted to replace an existing signature.'))
                changes.append(InputSignature(input_index, index, bytes(new)))
        if not changes:
            raise UserCancelled()
        try:
            apply_signing_result(tx, request, SigningResult(request.request_id, tuple(changes)))
        except SigningError as exc:
            raise HardwareError(str(exc)) from exc
