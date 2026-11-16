"""Bounded HID transport for the legacy Bitcoin Cash APDU adapter."""
import threading
import time

from btchip.btchipComm import HIDDongleHIDAPI
from btchip.btchipException import BTChipException
from btchip.ledgerWrapper import wrapCommandAPDU, unwrapResponseAPDU

from electrumsv.i18n import _


class LedgerHIDTransport(HIDDongleHIDAPI):
    def __init__(self, device, ledger=True, debug=False):
        super().__init__(device, ledger, debug)
        self._exchange_lock = threading.Lock()

    def exchange(self, apdu, timeout=120000):
        """Timeout is in milliseconds, including all response packets.

        The old library interprets its 20000 default as seconds and then switches
        to an unbounded blocking read for continuation packets. Keep every read
        nonblocking so disconnects and abandoned confirmations can be retried.
        """
        if not self._exchange_lock.acquire(False):
            raise OSError(_('Ledger is busy with another request. Finish it and try again.'))
        try:
            if not self.opened:
                raise OSError(_('Ledger connection is closed. Reconnect and try again.'))
            if not self.ledger:
                raise OSError(_('This adapter requires a Ledger Nano USB device.'))
            self.device.set_nonblocking(True)
            framed = wrapCommandAPDU(0x0101, bytearray(apdu), 64)
            for offset in range(0, len(framed), 64):
                packet = bytes(framed[offset:offset + 64]).ljust(64, b'\0')
                if self.device.write(b'\0' + packet) <= 0:
                    raise OSError(_('Cannot write to Ledger. Close Ledger Wallet and retry.'))
            deadline = time.monotonic() + timeout / 1000
            packets = bytearray()
            while time.monotonic() < deadline:
                packet = self.device.read(65)
                if packet:
                    packets.extend(packet)
                    response = unwrapResponseAPDU(0x0101, packets, 64)
                    if response is not None:
                        if len(response) < 2:
                            raise OSError(_('Ledger returned an incomplete response.'))
                        status = int.from_bytes(response[-2:], 'big')
                        if status != 0x9000:
                            raise BTChipException('Ledger status %04x' % status, status)
                        return bytearray(response[:-2])
                else:
                    time.sleep(0.02)
            raise TimeoutError(_('Ledger did not respond. Finish or reject the request on the '
                'device, reconnect it, close other wallet applications and retry.'))
        except OSError:
            self.close()
            raise
        finally:
            self._exchange_lock.release()
