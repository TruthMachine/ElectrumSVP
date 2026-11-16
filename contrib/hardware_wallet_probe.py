"""Manual, offline Ledger protocol probe. No wallet files or network access.

Run from the checkout with `python -m contrib.hardware_wallet_probe --mode read`.
Display/sign/reject require a person at the device. Signing uses a deliberately
nonexistent outpoint and returns no raw signed transaction. This is NOT a spend
test and provides no evidence of broadcast, mining or coinbase spendability.
"""
import argparse
import ctypes
import hashlib
import json
import sys
import tempfile
import time


def main():
    probe_started = time.perf_counter()

    def progress(message):
        print('[+{:.3f}s] {}'.format(time.perf_counter() - probe_started, message), flush=True)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('read', 'display', 'sign', 'reject', 'message', 'status', 'partial', 'multisig', 'verify'), default='read')
    parser.add_argument('--inputs', type=int, choices=range(1, 513), default=2,
        help='Synthetic input count for sign/reject/verify; partial/multisig remain 2-input fixtures')
    parser.add_argument('--allow-large', action='store_true',
        help='Acknowledge the full-verification waiting-time prompt for a synthetic fixture')
    parser.add_argument('--input-mode', choices=('trusted', 'unverified'), default='trusted')
    parser.add_argument('--path', default="m/44'/0'/0'")
    parser.add_argument('--device-id', help='Exact ID from --mode status; required if ambiguous')
    parser.add_argument('--model', choices=('Nano S', 'Nano S Plus', 'Nano X'))
    args = parser.parse_args()
    if args.inputs != 2 and (args.mode in ('partial', 'multisig') or args.input_mode != 'trusted'):
        parser.error('Custom input counts require a trusted sign/reject/verify fixture.')
    # hidapi 0.10's macOS build requires AppKit symbols (normally loaded by Qt).
    if sys.platform == 'darwin':
        ctypes.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit', mode=ctypes.RTLD_GLOBAL)

    from bitcoinx import Script, PublicKey, PrivateKey
    from electrumsv import ripemd  # noqa: F401: portable RIPEMD160 registration
    from electrumsv.app_state import AppStateProxy, app_state
    from electrumsv.constants import ScriptType
    from electrumsv.devices.ledger.ledger import LedgerPlugin
    from electrumsv.devices.ledger.session import app_info, device_identifier, device_model
    from btchip.btchipException import BTChipException
    from electrumsv.exceptions import UserCancelled
    from electrumsv.simple_config import SimpleConfig
    from electrumsv.transaction import Transaction, TransactionContext, XTxInput, XTxOutput, XPublicKey

    class Handler:
        def update_status(self, paired):
            pass

        def show_message(self, message, **kwargs):
            progress(message)

        def show_error(self, message, **kwargs):
            progress(message)

        def finished(self):
            pass

        def yes_no_question(self, message):
            if args.allow_large and 'Continue with full verification?' in message:
                print('Explicit large offline fixture: ' + message, flush=True)
                return True
            if args.input_mode == 'unverified' and 'unverified inputs' in message:
                print('Explicit offline unverified-input fixture: ' + message, flush=True)
                return True
            raise RuntimeError('Device disconnected. Rerun the probe after reconnecting.')

    with tempfile.TemporaryDirectory(prefix='electrumsvp-probe-') as directory:
        AppStateProxy(SimpleConfig({'electrum_sv_path': directory}), 'cmdline')
        manager = app_state.device_manager
        plugin = LedgerPlugin('ledger')
        manager.plugins['ledger'] = plugin
        devices = [d for d in plugin.enumerate_devices() if d.interface_number == 0]
        # Apply selection before opening even a status connection: other devices
        # may be signing in independent probe processes.
        if args.device_id:
            devices = [d for d in devices if device_identifier(d) == args.device_id]
        if args.model:
            devices = [d for d in devices if device_model(d) == args.model]
        if args.mode == 'status':
            for device in devices:
                result = {'device_id': device_identifier(device), 'model': device_model(device)}
                connection = None
                try:
                    connection = plugin.get_btchip_device(device)
                    name, version = app_info(connection)
                    result.update(app=name, version=version,
                        state='bch_ready' if name == 'Bitcoin Cash' else 'app_not_open')
                except BTChipException as exc:
                    result.update(state='locked' if exc.sw == 0x5515 else 'device_error',
                        status=hex(exc.sw))
                except OSError as exc:
                    result.update(state='busy_or_unavailable', error=str(exc))
                finally:
                    if connection is not None:
                        connection.close()
                print(json.dumps(result), flush=True)
            if not devices:
                print(json.dumps({'state': 'not_detected',
                    'note': 'A locked device may not expose a USB interface.'}))
            return
        if len(devices) != 1:
            raise RuntimeError('Select exactly one Ledger using --device-id from --mode status.')
        device = devices[0]
        handler = Handler()
        client = manager._create_client(device, handler, plugin)
        client.handler = handler
        original_exchange = client.dongleObject.dongle.exchange
        def traced_exchange(apdu, *parameters, **options):
            try:
                return original_exchange(apdu, *parameters, **options)
            except BTChipException as exc:
                # Record only command/status, never paths, keys or APDU payloads.
                progress('Device response: INS=0x{:02x}, status=0x{:04x}'.format(apdu[1], exc.sw))
                raise
        client.dongleObject.dongle.exchange = traced_exchange
        try:
            public_key = client.get_master_public_key(args.path)
            version = client.dongleObject.getFirmwareVersion()['version']
            result = {'mode': args.mode, 'model': device_model(device), 'usb_product_id': hex(device.product_key[1]),
                'app': 'Bitcoin Cash', 'app_version': version, 'path': args.path}
            child = public_key.child_safe(0).child_safe(0)
            address = child.to_address().to_string()
            if args.mode == 'display':
                print('Compare the address on the device with: ' + address, flush=True)
                reply = client.dongleObject.getWalletPublicKey(args.path[2:] + '/0/0',
                    showOnScreen=True)
                returned = PublicKey.from_bytes(bytes(reply['publicKey']))
                if returned.to_bytes(compressed=True) != child.to_bytes(compressed=True):
                    raise RuntimeError('Displayed address response does not match derived key.')
                result['display_public_key_matches'] = True
            elif args.mode in ('sign', 'reject', 'message', 'status', 'partial', 'multisig', 'verify'):
                store = plugin.create_keystore({'hw_type': 'ledger', 'derivation': args.path,
                    'xpub': public_key.to_extended_key_string(), 'label': 'offline probe'}, None)
                store.handler = handler
                manager.pair_xpub(store.xpub, device.id_)
                if args.mode == 'message':
                    message = 'ElectrumSVP offline hardware test. No payment or authorization. 2026-11-16.'
                    print('MESSAGE TEST: ' + message, flush=True)
                    signature = manager.hardware.sign_message(store, (0, 0), message, '')
                    result['message_signature_verified'] = child.verify_message(signature, message)
                    result['passed'] = True
                    print(json.dumps(result), flush=True)
                    return
                xkey = XPublicKey(bip32_xpub=store.xpub, derivation_path=(0, 0))
                context = TransactionContext()
                fixture_key = PrivateKey.from_int(1)  # public test key; not a wallet seed
                if args.input_mode == 'trusted':
                    inputs = []
                    for number in range(args.inputs):
                        keys = [xkey]
                        kind, threshold = ScriptType.P2PKH, 1
                        if args.mode == 'partial' and number == 0:
                            keys = [XPublicKey(pubkey_bytes=fixture_key.public_key.to_bytes())]
                        elif args.mode == 'multisig':
                            keys = [xkey, XPublicKey(pubkey_bytes=fixture_key.public_key.to_bytes())]
                            kind, threshold = ScriptType.MULTISIG_BARE, 2
                        txin = XTxInput(bytes(32), 1, Script(), 0xfffffffe, value=1000,
                            x_pubkeys=keys, threshold=threshold, signatures=[b'\xff'] * len(keys),
                            script_type=kind)
                        parent = Transaction(2, [XTxInput(bytes(32), 0xffffffff,
                            Script(b'ElectrumSVP synthetic coinbase ' + number.to_bytes(2, 'little')), 0xffffffff)],
                            [XTxOutput(0, Script(b'\x6a')),
                                XTxOutput(1000, Script(Transaction.get_preimage_script_bytes(txin)))], 0)
                        txin.prev_hash = parent.hash()
                        context.prev_txs[parent.hash()] = parent
                        inputs.append(txin)
                    total = 1000 * len(inputs) - 100
                    first = min(900, total // 2)
                    tx = Transaction(2, inputs, [XTxOutput(first, child.P2PKH_script()),
                        XTxOutput(total - first, child.P2PKH_script())], 0)
                else:
                    txin = XTxInput(hashlib.sha256(b'ElectrumSVP nonexistent offline test UTXO').digest(),
                        0, Script(), 0xfffffffe, value=1000, x_pubkeys=[xkey], threshold=1,
                        signatures=[b'\xff'], script_type=ScriptType.P2PKH)
                    tx = Transaction(2, [txin], [XTxOutput(900, child.P2PKH_script())], 0)
                existing = {}
                if args.mode in ('partial', 'multisig'):
                    if args.input_mode != 'trusted':
                        raise RuntimeError('Partial/multisig fixtures require synthetic trusted parents.')
                    for index, txin in enumerate(tx.inputs):
                        key_index = 0 if args.mode == 'partial' else 1
                        if args.mode == 'partial' and index != 0:
                            continue
                        signature = fixture_key.sign(tx.preimage_hash(txin), None) + b'\x41'
                        txin.signatures[key_index] = signature
                        existing[index, key_index] = signature
                    data = tx.to_dict(force_signing_metadata=True)
                    data['prev_txs'] = [parent.to_hex() for parent in context.prev_txs.values()]
                    tx = Transaction.from_dict(data)
                    context = tx.context
                tx.output_info = [{} for _ in tx.outputs]
                result['input_verification'] = args.input_mode
                result['input_count'], result['output_count'] = len(tx.inputs), len(tx.outputs)
                # Measure parent verification independently of human review time.
                from electrumsv.devices.ledger.input_verification import plan_input_verification
                verification = plan_input_verification(tx, context, device_model(device), version)
                timings = []
                signing_timings = []
                review_timings = []
                preparation_timings = []
                original_prepare = client.dongleObject.startUntrustedTransaction
                def timed_prepare(*parameters, **options):
                    started = time.perf_counter()
                    try:
                        return original_prepare(*parameters, **options)
                    finally:
                        preparation_timings.append(round(time.perf_counter() - started, 4))
                client.dongleObject.startUntrustedTransaction = timed_prepare
                original_trusted = client.dongleObject.getTrustedInput
                def timed_trusted(*parameters, **options):
                    started = time.perf_counter()
                    reply = original_trusted(*parameters, **options)
                    elapsed = time.perf_counter() - started
                    timings.append(round(elapsed, 4))
                    print('Parent {} verified in {:.3f}s'.format(len(timings), elapsed), flush=True)
                    return reply
                client.dongleObject.getTrustedInput = timed_trusted
                original_sign = client.dongleObject.untrustedHashSign
                def timed_sign(*parameters, **options):
                    started = time.perf_counter()
                    reply = original_sign(*parameters, **options)
                    signing_timings.append(round(time.perf_counter() - started, 4))
                    return reply
                client.dongleObject.untrustedHashSign = timed_sign
                original_review = client.dongleObject.finalizeInputFull
                def timed_review(*parameters, **options):
                    started = time.perf_counter()
                    reply = original_review(*parameters, **options)
                    review_timings.append(round(time.perf_counter() - started, 4))
                    progress('Output review finished; subsequent signature timings exclude this '
                        'review/button wait.')
                    return reply
                client.dongleObject.finalizeInputFull = timed_review
                if args.mode == 'verify':
                    # Never fall back or request a transaction signature in this mode.
                    if verification.trusted:
                        from btchip.bitcoinTransaction import bitcoinTransaction
                        for index, raw in enumerate(verification.parents):
                            timed_trusted(bitcoinTransaction(bytearray(raw)), tx.inputs[index].prev_idx)
                    result.update(trusted=verification.trusted, reason=verification.reason,
                        parent_seconds=timings, total_parent_seconds=round(sum(timings), 4),
                        signing_requested=False, passed=True)
                    print(json.dumps(result), flush=True)
                    return
                print('OFFLINE FIXTURE: synthetic parents/outpoints only, outputs to own key, '
                    '100 nominal fee satoshis, version 2. ' +
                    ('REJECT on device.' if args.mode == 'reject' else 'APPROVE on device.'),
                    flush=True)
                try:
                    manager.hardware.sign_transaction(store, tx, '', context)
                except UserCancelled:
                    if args.mode != 'reject':
                        raise
                    result['rejected_without_signature'] = all(i.signatures == [b'\xff'] for i in tx.inputs)
                else:
                    if args.mode == 'reject':
                        raise RuntimeError('Expected rejection, but device signed the fixture.')
                    if not tx.is_complete():
                        raise RuntimeError('Offline fixture was not completed.')
                    result['signature_verified'] = True
                    if existing:
                        assert all(tx.inputs[i].signatures[k] == sig for (i, k), sig in existing.items())
                        result['existing_signatures_preserved_after_JSON_roundtrip'] = True
                    result['sighash'] = '0x41'
                    result['transaction_version'] = tx.version
                result['parent_seconds'] = timings
                result['total_parent_seconds'] = round(sum(timings), 4)
                result['signature_seconds'] = signing_timings
                result['total_signature_seconds'] = round(sum(signing_timings), 4)
                result['output_review_seconds_including_user'] = review_timings
                result['transaction_preparation_seconds'] = preparation_timings
                result['elapsed_seconds_including_user'] = round(time.perf_counter() - probe_started, 4)
            result['passed'] = True
            print(json.dumps(result), flush=True)
        finally:
            client.close()


if __name__ == '__main__':
    main()
