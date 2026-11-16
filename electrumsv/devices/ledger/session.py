"""Application identity and selection on a specific Ledger USB device."""
import hashlib
import time

from btchip.btchipException import BTChipException

from electrumsv.exceptions import UserCancelled
from electrumsv.i18n import _


def device_model(device):
    product = device.product_key[1]
    family = product >> 12 if product >= 0x1000 else product
    return {1: 'Nano S', 4: 'Nano X', 5: 'Nano S Plus'}.get(family, 'Ledger')


def device_identifier(device):
    # Useful for choosing between devices; this is NOT cryptographic wallet identity.
    return hashlib.sha256(device.id_.encode()).hexdigest()[:8]


def app_info(connection):
    # Match Ledger's current GetAppAndVersion serialization, including zero Lc.
    # Nano S / BCH 2.4.7 accepts both four and five bytes in repeated physical tests.
    response = connection.exchange(bytearray.fromhex('b001000000'), timeout=5000)
    if len(response) < 2 or response[0] != 1:
        raise ValueError('Invalid Ledger app identity')
    fields, offset = [], 1
    for _field in range(2):
        if offset >= len(response):
            raise ValueError('Truncated Ledger app identity')
        length = response[offset]
        offset += 1
        if offset + length > len(response):
            raise ValueError('Truncated Ledger app identity')
        fields.append(bytes(response[offset:offset + length]).decode('ascii'))
        offset += length
    return tuple(fields)


def switch_to_bitcoin_cash(plugin, device, connection, current_app):
    """Close/open apps, reconnecting ONLY to the selected USB identity after each switch.

    No installation, firmware updates, PIN commands or key operations occur here.
    A device without a stable identity must be explicitly selected again if its
    path changes. Never fall back to the first available Ledger.
    """
    def send_switch(transport, command):
        try:
            transport.exchange(bytearray(command), timeout=10000)
        except BTChipException as exc:
            if exc.sw in (0x5501, 0x6985):
                raise UserCancelled() from exc
            raise
        except OSError:
            # Closing/opening an app can disconnect USB before the status response.
            # Success is established only by reconnecting and checking its identity.
            pass
        finally:
            transport.close()

    def reconnect(expected):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            for candidate in plugin.enumerate_devices():
                if candidate.id_ != device.id_ or candidate.interface_number != device.interface_number:
                    continue
                transport = None
                try:
                    transport = plugin.get_btchip_device(candidate)
                    if app_info(transport)[0] == expected:
                        return transport
                except BTChipException as exc:
                    if exc.sw in (0x5515, 0x6982):
                        if transport is not None:
                            transport.close()
                        raise RuntimeError(_('The selected Ledger is locked. Enter the PIN '
                            'on that device and retry.')) from exc
                except OSError:
                    pass
                except Exception:
                    if transport is not None:
                        transport.close()
                    raise
                if transport is not None:
                    transport.close()
            time.sleep(0.1)
        raise OSError(_('The selected Ledger did not reconnect with the expected app. '
            'Unlock it, close other wallet applications and select it again.'))

    if current_app != 'BOLOS':
        send_switch(connection, bytes.fromhex('b0a70000'))
        connection = reconnect('BOLOS')
    name = b'Bitcoin Cash'
    send_switch(connection, bytes([0xe0, 0xd8, 0, 0, len(name)]) + name)
    return reconnect('Bitcoin Cash')
