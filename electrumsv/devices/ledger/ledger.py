import hashlib
from struct import pack, unpack
from typing import Any, cast, Dict, List, NamedTuple, Optional, Tuple, TYPE_CHECKING

from bitcoinx import (
    BIP32Derivation, BIP32PublicKey, PublicKey, pack_be_uint32, pack_list, pack_le_int64,
    der_signature_to_compact,
)

from electrumsv.app_state import app_state
from electrumsv.bitcoin import compose_chain_string, int_to_hex, ScriptTemplate
from electrumsv.constants import ScriptType
from electrumsv.i18n import _
from electrumsv.keystore import Hardware_KeyStore
from electrumsv.logs import logs
from electrumsv.networks import Net
from electrumsv.transaction import classify_tx_output, Transaction, TransactionContext, XTxOutput, NO_SIGNATURE
from electrumsv.util import versiontuple
from electrumsv.wallet import AbstractAccount

from ..hw_wallet import HW_PluginBase

if TYPE_CHECKING:
    from .qt import Ledger_Handler
    from electrumsv.wallet_database.tables import MasterKeyRow

try:
    import hid
    from .transport import LedgerHIDTransport
    from .session import app_info, device_model, device_identifier, switch_to_bitcoin_cash
    from btchip.btchip import btchip
    from btchip.btchipUtils import compress_public_key
    from btchip.bitcoinTransaction import bitcoinTransaction
    from btchip.btchipFirmwareWizard import checkFirmware
    from btchip.btchipException import BTChipException
    BTCHIP = True
    BTCHIP_DEBUG = logs.is_debug_level()
except ImportError:
    BTCHIP = False


logger = logs.get_logger("plugin.ledger")

BITCOIN_CASH_SUPPORT = (1, 1, 8)


class YInput(NamedTuple):
    value: Optional[int]
    script_sig: bytes
    txin_xpub_idx: int
    sequence: int



class Ledger_Client():
    handler: 'Ledger_Handler'

    def __init__(self, hidDevice, plugin=None, device=None):
        self._plugin = plugin
        self._device = device
        self.dongleObject = btchip(hidDevice)
        self.preflightDone = False

    def is_pairable(self):
        return True

    def close(self):
        self.dongleObject.dongle.close()

    def timeout(self, cutoff):
        pass

    def is_initialized(self):
        return True

    def label(self):
        if self._device is None:
            return "Ledger"
        return "{} [{}]".format(device_model(self._device), device_identifier(self._device))

    def i4b(self, x):
        return pack('>I', x)

    def has_usable_connection_with_device(self):
        try:
            self.dongleObject.getFirmwareVersion()
        except Exception:
            return False
        return True

    def test_pin_unlocked(func: Any): # pylint: disable=no-self-argument
        """Function decorator to test the Ledger for being unlocked, and if not,
        raise a human-readable exception.
        """
        def catch_exception(self, *args, **kwargs):
            try:
                # pylint: disable=not-callable
                return func(self, *args, **kwargs)
            except BTChipException as e:
                if e.sw == 0x6982:
                    raise Exception(_('Your Ledger is locked. Please unlock it.'))
                else:
                    raise
        return catch_exception

    @test_pin_unlocked
    def get_master_public_key(self, bip32_path):
        self.checkDevice()
        # bip32_path is of the form 44'/0'/1'
        # S-L-O-W - we don't handle the fingerprint directly, so compute
        # it manually from the previous node
        # This only happens once so it's bearable
        #self.get_client() # prompt for the PIN before displaying the dialog if necessary
        #self.handler.show_message("Computing master public key")
        splitPath = bip32_path.split('/')
        if splitPath[0] == 'm':
            splitPath = splitPath[1:]
            bip32_path = bip32_path[2:]
        fingerprint = 0
        if len(splitPath) > 1:
            prevPath = "/".join(splitPath[0:len(splitPath) - 1])
            nodeData = self.dongleObject.getWalletPublicKey(prevPath)
            publicKey = compress_public_key(nodeData['publicKey'])
            h = hashlib.new('ripemd160')
            h.update(hashlib.sha256(publicKey).digest())
            fingerprint = unpack(">I", h.digest()[0:4])[0]
        nodeData = self.dongleObject.getWalletPublicKey(bip32_path)
        publicKey = bytes(compress_public_key(nodeData['publicKey']))
        depth = len(splitPath)
        lastChild = splitPath[len(splitPath) - 1].split('\'')
        childnum = int(lastChild[0]) if len(lastChild) == 1 else 0x80000000 | int(lastChild[0])

        derivation = BIP32Derivation(chain_code=nodeData['chainCode'], depth=depth,
                                     parent_fingerprint=pack_be_uint32(fingerprint),
                                     n=childnum)
        return BIP32PublicKey(PublicKey.from_bytes(publicKey), derivation, Net.COIN)

    def has_detached_pin_support(self, client):
        try:
            client.getVerifyPinRemainingAttempts()
            return True
        except BTChipException as e:
            if e.sw == 0x6d00:
                return False
            raise e

    def is_pin_validated(self, client):
        try:
            # Invalid SET OPERATION MODE to verify the PIN status
            client.dongle.exchange(bytearray([0xe0, 0x26, 0x00, 0x00, 0x01, 0xAB]))
        except BTChipException as e:
            if e.sw == 0x6982:
                return False
            if e.sw == 0x6A80:
                return True
            raise e

    def supports_bitcoin_cash(self):
        return self.bitcoinCashSupported

    def perform_hw1_preflight(self):
        try:
            firmwareInfo = self.dongleObject.getFirmwareVersion()
            firmware = firmwareInfo['version']
            self.bitcoinCashSupported = versiontuple(firmware) >= BITCOIN_CASH_SUPPORT

            if not checkFirmware(firmwareInfo) or not self.supports_bitcoin_cash():
                self.dongleObject.dongle.close()
                raise Exception("HW1 firmware version too old. Please update at "
                                "https://www.ledgerwallet.com")
            try:
                self.dongleObject.getOperationMode()
            except BTChipException as e:
                if e.sw == 0x6985:
                    self.dongleObject.dongle.close()
                    self.handler.get_setup( )
                    # Acquire the new client on the next run
                else:
                    raise e
            if (self.has_detached_pin_support(self.dongleObject) and
                    not self.is_pin_validated(self.dongleObject) and (self.handler is not None)):
                remaining_attempts = self.dongleObject.getVerifyPinRemainingAttempts()
                if remaining_attempts != 1:
                    msg = "Enter your Ledger PIN - remaining attempts : " + str(remaining_attempts)
                else:
                    msg = ("Enter your Ledger PIN - WARNING : LAST ATTEMPT. "
                           "If the PIN is not correct, the dongle will be wiped.")
                confirmed, p, pin = self.password_dialog(msg)
                if not confirmed:
                    raise Exception('Aborted by user - please unplug the dongle '
                                    'and plug it again before retrying')
                pin = pin.encode()
                self.dongleObject.verifyPin(pin)

        except BTChipException as e:
            if e.sw == 0x6faa:
                raise Exception("Dongle is temporarily locked - please unplug it and "
                                "replug it again")
            if (e.sw & 0xFFF0) == 0x63c0:
                raise Exception("Invalid PIN - please unplug the dongle and plug "
                                "it again before retrying")
            if e.sw == 0x6f00 and e.message == 'Invalid channel':
                # based on docs 0x6f00 might be a more general error, hence we also
                # compare message to be sure
                raise Exception("Invalid channel.\nPlease make sure that "
                                "'Browser support' is disabled on your device.")
            raise e

    def checkDevice(self):
        try:
            name, version = app_info(self.dongleObject.dongle)
            if name != 'Bitcoin Cash' and self._plugin is not None:
                if self.handler:
                    self.handler.show_message(_('Opening Bitcoin Cash on the selected Ledger. '
                        'Confirm on the device if requested.'))
                try:
                    transport = switch_to_bitcoin_cash(self._plugin, self._device,
                        self.dongleObject.dongle, name)
                    self.dongleObject = btchip(transport)
                    self.preflightDone = False
                    name, version = app_info(transport)
                finally:
                    if self.handler:
                        self.handler.finished()
        except BTChipException as exc:
            if exc.sw in (0x5515, 0x6982):
                raise RuntimeError(_('Ledger is locked. Enter your PIN on the device and retry.')) from exc
            raise
        if name != 'Bitcoin Cash':
            raise RuntimeError(_('Open the Bitcoin Cash app on your Ledger (currently: {}).')
                .format(name))
        if not self.preflightDone:
            try:
                self.perform_hw1_preflight()
            except BTChipException as e:
                if e.sw == 0x6d00 or e.sw == 0x6700:
                    raise Exception(_("Device not in Bitcoin Cash mode")) from e
                raise e
            self.preflightDone = True

    def password_dialog(self, msg: str) -> Tuple[bool, Optional[str], Optional[str]]:
        response = self.handler.get_word(msg)
        if response is None:
            return False, None, None
        return True, response, response


class Ledger_KeyStore(Hardware_KeyStore):
    hw_type = 'ledger'
    device = 'Ledger'
    handler: Optional['Ledger_Handler']

    def __init__(self, data: Dict[str, Any], row: 'MasterKeyRow') -> None:
        Hardware_KeyStore.__init__(self, data, row)
        # Errors and other user interaction is done through the wallet's
        # handler.  The handler is per-window and preserved across
        # device reconnects
        self.force_watching_only = False
        self.signing = False
        self.cfg = data.get('cfg', {'mode':0})

    def to_derivation_data(self) -> Dict[str, Any]:
        obj = super().to_derivation_data()
        obj['cfg'] = self.cfg
        return obj

    def get_derivation(self):
        return self.derivation

    def requires_input_transactions(self):
        # Preferred, not mandatory: missing parents require explicit reduced-verification consent.
        # The shared hardware layer supplies locally available parents without network downloads.
        return False

    def get_client(self):
        return self.plugin.get_client(self).dongleObject

    def get_client_electrum(self):
        return self.plugin.get_client(self)

    def give_error(self, message, clear_client = False) -> None:
        logger.error(message)
        if not self.signing:
            assert self.handler is not None
            self.handler.show_error(message)
        else:
            self.signing = False
        if clear_client:
            self.client = None
        raise Exception(message)

    def set_and_unset_signing(func: Any): # pylint: disable=no-self-argument
        """Function decorator to set and unset self.signing."""
        def wrapper(self, *args, **kwargs):
            try:
                self.signing = True
                # pylint: disable=not-callable
                return func(self, *args, **kwargs)
            finally:
                self.signing = False
        return wrapper

    def decrypt_message(self, pubkey, message, password):
        raise RuntimeError(_('Encryption and decryption are not supported for {}').format(
            self.device))

    @set_and_unset_signing
    def sign_message(self, sequence, message, password):
        message = message.encode('utf8')
        message_hash = hashlib.sha256(message).hexdigest().upper()
        # prompt for the PIN before displaying the dialog if necessary
        client = self.get_client()
        address_path = self.get_derivation()[2:] + "/{:d}/{:d}".format(*sequence)
        self.handler.show_message(_('Signing message...\nMessage hash: {}').format(message_hash))
        try:
            info = client.signMessagePrepare(address_path, message)
            pin = ""
            if info['confirmationNeeded']:
                # does the authenticate dialog and returns pin
                pin = self.handler.get_auth(self, info)
                if not pin:
                    raise UserWarning(_('Cancelled by user'))
                pin = str(pin).encode()
            signature = client.signMessageSign(pin)
        except BTChipException as e:
            if e.sw == 0x6a80:
                self.give_error(
                    _("The Ledger app rejected this message. Check the derivation path and "
                      "try a shorter message supported by your app version."))
            elif e.sw == 0x6985:  # cancelled by user
                return b''
            else:
                self.give_error(e, True)
        except UserWarning:
            self.handler.show_error(_('Cancelled by user'))
            return b''
        except Exception as e:
            self.give_error(e, True)
        finally:
            self.handler.finished()
        # Ledger embeds recovery parity in the DER sequence tag. The shared DER
        # parser pads short r/s integers as well as stripping sign-padding bytes.
        parity = signature[0] & 1
        der = b'\x30' + bytes(signature[1:])
        return bytes([31 + parity]) + der_signature_to_compact(der)

    @set_and_unset_signing
    def sign_transaction(self, tx: Transaction, password: str,
            tx_context: TransactionContext, *, input_indexes=None, verify_inputs=True) -> None:
        if tx.is_complete():
            return

        assert self.handler is not None
        from .input_verification import InputVerification, plan_input_verification, large_verification
        from electrumsv.exceptions import UserCancelled
        def stage(name, completed=0, total=0):
            if hasattr(self.handler, 'show_stage'):
                self.handler.show_stage(name, completed, total)

        def check_cancelled():
            if hasattr(self.handler, 'check_cancelled'):
                self.handler.check_cancelled()

        check_cancelled()
        session = self.get_client_electrum()
        client = session.dongleObject
        model = device_model(session._device) if session._device is not None else 'Ledger'
        verification = (plan_input_verification(tx, tx_context, model,
            client.getFirmwareVersion()['version']) if verify_inputs else
            InputVerification(reason=_('Previous-transaction verification was switched off '
                'for this signing operation.')))
        if verification.trusted and large_verification(tx, model):
            if not self.handler.yes_no_question(
                    _('Full verification of {} inputs will send {:.1f} KiB of previous '
                      'transactions to your Ledger. Verification and signing may take several '
                      'minutes. Keep the device connected and review the transaction on its '
                      'screen when prompted. Continue with full verification?').format(
                          len(tx.inputs), sum(map(len, verification.parents)) / 1024)):
                raise UserCancelled()
        if not verification.trusted:
            if not self.handler.yes_no_question(verification.reason + '\n\n' +
                    _('The device cannot verify input amounts against previous transactions. '
                      'It may show "unverified inputs". Continue with this reduced verification?')):
                raise UserCancelled()
        inputs: List[YInput] = []
        inputsPaths = []
        chipInputs = []
        redeemScripts = []
        signatures = []
        changePath = ""
        changeAmount = None
        output = None
        outputAmount = None
        pin = ""


        # Fetch inputs of the transaction to sign
        foundP2SHSpend = False
        allSpendsAreP2SH = True
        for input_index, txin in enumerate(tx.inputs):
            foundP2SHSpend = foundP2SHSpend or txin.type() == ScriptType.MULTISIG_P2SH
            allSpendsAreP2SH = allSpendsAreP2SH and txin.type() == ScriptType.MULTISIG_P2SH

            txin_xpub_idx, inputPath = -1, None
            for i, x_pubkey in enumerate(txin.x_pubkeys):
                if ((input_indexes is None or input_index in input_indexes) and
                        not txin.is_complete() and txin.signatures[i] == NO_SIGNATURE and
                        self.is_signature_candidate(x_pubkey)):
                    txin_xpub_idx = i
                    inputPath = "%s/%d/%d" % (self.get_derivation()[2:], *x_pubkey.bip32_path())
                    break

            inputs.append(YInput(txin.value, Transaction.get_preimage_script_bytes(txin),
                txin_xpub_idx, txin.sequence))
            inputsPaths.append(inputPath)

        # Sanity check
        if foundP2SHSpend and not allSpendsAreP2SH:
            self.give_error("P2SH / regular input mixed in same transaction not supported")

        # Concatenate all the tx outputs as binary
        txOutput = pack_list(tx.outputs, XTxOutput.to_bytes)

        # Recognize outputs - only one output and one change is authorized
        if not foundP2SHSpend:
            keystore_fingerprint = self.get_fingerprint()
            assert tx.output_info is not None
            for tx_output, output_metadatas in zip(tx.outputs, tx.output_info):
                info = output_metadatas.get(keystore_fingerprint)
                if (info is not None) and len(tx.outputs) != 1:
                    key_derivation, xpubs, m = info
                    key_subpath = compose_chain_string(key_derivation)[1:]
                    changePath = self.get_derivation()[2:] + key_subpath
                    changeAmount = tx_output.value
                else:
                    output = classify_tx_output(tx_output)
                    outputAmount = tx_output.value

        self.handler.show_message(_('Verifying previous transactions on your Ledger...')
            if verification.trusted else _("Confirm Transaction on your Ledger device..."))
        stage('parents', 0, len(inputs) if verification.trusted else -1)
        try:
            for i, utxo in enumerate(inputs):
                check_cancelled()
                txin = tx.inputs[i]
                sequence = int_to_hex(utxo.sequence, 4)
                if verification.trusted:
                    message = _('Verifying input {} of {} on your Ledger...').format(i + 1, len(inputs))
                    if hasattr(self.handler, 'show_progress'):
                        self.handler.show_progress(message, i + 1, len(inputs))
                    else:
                        self.handler.show_message(message)
                    trusted = client.getTrustedInput(bitcoinTransaction(bytearray(
                        verification.parents[i])), txin.prev_idx)
                    trusted.update(witness=True, sequence=sequence)
                    chipInputs.append(trusted)
                    stage('parents', i + 1, len(inputs))
                else:
                    value_bytes = txin.prevout_bytes() + pack_le_int64(utxo.value)
                    chipInputs.append({'value': value_bytes, 'witness': True, 'sequence': sequence})
                redeemScripts.append(utxo.script_sig)

            # Sign all inputs
            inputIndex = 0
            rawTx = tx.serialize()
            client.enableAlternate2fa(False)
            self.handler.show_message(_('Preparing the current transaction ({} inputs) '
                'on your Ledger...').format(len(inputs)))
            check_cancelled()
            client.startUntrustedTransaction(True, inputIndex, chipInputs,
                redeemScripts[inputIndex], version=tx.version)
            self.handler.show_message(_('Review the transaction on your Ledger. '
                'Input amounts were checked against previous transactions; chain status '
                'and coinbase maturity are checked by the wallet.') if verification.trusted
                else _('Review the transaction on your Ledger. Input amounts are unverified.'))
            stage('review')
            check_cancelled()
            outputData = client.finalizeInputFull(txOutput)
            outputData['outputData'] = txOutput
            transactionOutput = outputData['outputData']
            if outputData['confirmationNeeded']:
                outputData['address'] = cast(ScriptTemplate, output).to_string()
                self.handler.finished()
                # the authenticate dialog and returns pin
                auth_pin = self.handler.get_auth(self, outputData)
                if not auth_pin:
                    raise UserWarning()
                pin = auth_pin
                self.handler.show_message(_("Confirmed. Signing Transaction..."))
            signed_count = 0
            sign_total = sum(path is not None for path in inputsPaths)
            while inputIndex < len(inputs):
                check_cancelled()
                if inputsPaths[inputIndex] is None:
                    inputIndex += 1
                    continue
                message = _('Signing input {} of {} on your Ledger...').format(inputIndex + 1, len(inputs))
                # Some BCH versions defer approval until the first sign APDU.
                # Keep Review active until that first signature actually returns.
                if signed_count:
                    if hasattr(self.handler, 'show_progress'):
                        self.handler.show_progress(message, inputIndex + 1, len(inputs))
                    else:
                        self.handler.show_message(message)
                singleInput = [ chipInputs[inputIndex] ]
                client.startUntrustedTransaction(
                    False, 0, singleInput, redeemScripts[inputIndex], version=tx.version)
                inputSignature = client.untrustedHashSign(inputsPaths[inputIndex],
                                                                     pin, lockTime=tx.locktime,
                                                                     sighashType=tx.nHashType())
                inputSignature[0] = 0x30 # force for 1.4.9+
                signatures.append((inputIndex, inputSignature))
                signed_count += 1
                stage('sign', signed_count, sign_total)
                inputIndex = inputIndex + 1
        except UserCancelled:
            raise
        except UserWarning:
            self.handler.show_error(_('Cancelled by user'))
            return
        except BTChipException as e:
            if e.sw == 0x6985:  # cancelled by user
                return
            elif e.sw == 0x5515:
                self.give_error(_('Ledger is locked. Enter your PIN on the device and retry.'), True)
            else:
                logger.exception("")
                self.give_error(e, True)
        except Exception as e:
            logger.exception("")
            self.give_error(e, True)
        finally:
            self.handler.finished()

        check_cancelled()
        for input_index, signature in signatures:
            tx.inputs[input_index].signatures[inputs[input_index].txin_xpub_idx] = signature

    @set_and_unset_signing
    def show_address(self, derivation_subpath: str) -> None:
        client = self.get_client()
        # prompt for the PIN before displaying the dialog if necessary
        address_path = self.get_derivation()[2:] +"/"+ derivation_subpath
        assert self.handler is not None
        self.handler.show_message(_("Showing address ..."))
        try:
            response = client.getWalletPublicKey(address_path, showOnScreen=True)
            subpath = tuple(int(index) for index in derivation_subpath.split('/'))
            expected = self.derive_pubkey(subpath).to_bytes(compressed=True)
            actual = PublicKey.from_bytes(bytes(response['publicKey'])).to_bytes(compressed=True)
            if actual != expected:
                raise RuntimeError(_('The address displayed by the Ledger does not match '
                    'this wallet. Check the selected device and account.'))
        finally:
            self.handler.finished()


class LedgerPlugin(HW_PluginBase):
    libraries_available = BTCHIP
    keystore_class = Ledger_KeyStore
    client = None
    DEVICE_IDS = [
                   (0x2581, 0x1807), # HW.1 legacy btchip
                   (0x2581, 0x2b7c), # HW.1 transitional production
                   (0x2581, 0x3b7c), # HW.1 ledger production
                   (0x2581, 0x4b7c), # HW.1 ledger test
                   (0x2c97, 0x0000), # Blue
                   (0x2c97, 0x0011), # Blue app-bitcoin >= 1.5.1
                   (0x2c97, 0x0015), # Blue app-bitcoin >= 1.5.1
                   (0x2c97, 0x1000), # Nano-S
                   (0x2c97, 0x0001), # Nano-S
                   (0x2c97, 0x1011), # Nano-S app-bitcoin >= 1.5.1
                   (0x2c97, 0x1015), # Nano-S app-bitcoin >= 1.5.1
                   (0x2c97, 0x4000), # Nano-X
                   (0x2c97, 0x0004), # Nano-X
                   (0x2c97, 0x4011), # Nano-X app-bitcoin >= 1.5.1
                   (0x2c97, 0x4015), # Nano-X app-bitcoin >= 1.5.1
                   (0x2c97, 0x0005), # Nano-S-Plus
                   (0x2c97, 0x5000), # Nano-S-Plus
                   (0x2c97, 0x5011), # Nano-S-Plus Bitcoin Cash app
                   (0x2c97, 0x5015), # Nano-S-Plus Bitcoin Cash app
                 ]

    def __init__(self, name):
        HW_PluginBase.__init__(self, name)
        self.logger = logger

    def enumerate_devices(self):
        if self.libraries_available:
            # Nano exposes other HID interfaces (for example FIDO/U2F). They are
            # not extra wallets and must never enter discovery or status probes.
            return [device for device in app_state.device_manager.find_hid_devices(self.DEVICE_IDS)
                if device.product_key[0] != 0x2c97 or device.interface_number == 0 or
                device.usage_page == 0xffa0]
        return []

    def get_device_status(self, device):
        from electrumsv.devices.hardware import DeviceStatus
        label = '{} [{}]'.format(device_model(device), device_identifier(device))
        cached = app_state.device_manager.client_lookup(device.id_)
        connection = cached.dongleObject.dongle if cached is not None else None
        owned = connection is None or not connection.opened
        try:
            if owned:
                connection = self.get_btchip_device(device)
            if connection is None:
                raise OSError(_('This USB interface does not support Ledger wallet communication.'))
            name, version = app_info(connection)
            ready = name == 'Bitcoin Cash'
            return DeviceStatus(self.name, label, 'bch_ready' if ready else 'app_not_open',
                _('Bitcoin Cash is open. Wallet pairing is checked separately before use.')
                if ready else _('Unlock this device before signing. The wallet can request '
                    'opening Bitcoin Cash on the selected device; confirm there if asked.'),
                name, version, device)
        except BTChipException as exc:
            if exc.sw in (0x5515, 0x6982):
                return DeviceStatus(self.name, label, 'locked',
                    _('Enter the PIN on this device, then refresh.'), device=device)
            return DeviceStatus(self.name, label, 'unavailable', str(exc), device=device)
        except OSError as exc:
            return DeviceStatus(self.name, label, 'unavailable', str(exc), device=device)
        finally:
            if owned and connection is not None:
                connection.close()

    def check_connection(self, client):
        client.checkDevice()

    def get_btchip_device(self, device):
        ledger = False
        if device.product_key[0] == 0x2581 and device.product_key[1] == 0x3b7c:
            ledger = True
        if device.product_key[0] == 0x2581 and device.product_key[1] == 0x4b7c:
            ledger = True
        if device.product_key[0] == 0x2c97:
            if device.interface_number == 0 or device.usage_page == 0xffa0:
                ledger = True
            else:
                return None  # non-compatible interface of a nano s or blue
        dev = hid.device()
        try:
            dev.open_path(device.path)
        except OSError as exc:
            dev.close()
            raise OSError(_('Cannot open Ledger. Close Ledger Wallet, browser device sessions '
                'and other wallet applications, then reconnect, unlock and retry.')) from exc
        dev.set_nonblocking(True)
        return LedgerHIDTransport(dev, ledger, BTCHIP_DEBUG)

    def create_client(self, device, handler):
        self.handler = handler

        client = self.get_btchip_device(device)
        if client is not None:
            client = Ledger_Client(client, self, device)
        return client

    def setup_device(self, device_info, handler):
        device_id = device_info.device.id_
        client = app_state.device_manager.client_by_id(device_id)
        if client is None:
            raise Exception(_('Failed to create a client for this device.') + '\n' +
                            _('Make sure it is in the correct state.'))
        client.handler = handler
        # get_master_public_key also derives the parent for its fingerprint.
        # BCH app >= 2.4.10 rejects m/44', the parent of the old m/44'/0' probe.
        # Probe an allowed account and parent; this does not set the wallet's path.
        client.get_master_public_key("m/44'/145'/0'")

    def get_master_public_key(self, device_id, derivation, handler):
        client = app_state.device_manager.client_by_id(device_id)
        if client is None:
            raise RuntimeError(_('Device disconnected. Please reconnect and try again.'))
        client.handler = handler
        client.checkDevice()
        return client.get_master_public_key(derivation)

    def get_client(self, keystore, force_pair=True):
        client = app_state.device_manager.client_for_keystore(self, keystore, force_pair)
        if client:
            client.checkDevice()
        return client

    def show_key(self, account: AbstractAccount, keyinstance_id: int) -> None:
        derivation_path = account.get_derivation_path(keyinstance_id)
        assert derivation_path is not None
        subpath = '/'.join(str(x) for x in derivation_path)
        keystore = cast(Ledger_KeyStore, account.get_keystore())
        keystore.show_address(subpath)
