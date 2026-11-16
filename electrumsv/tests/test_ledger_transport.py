from unittest.mock import Mock

import pytest
pytest.importorskip('hid')
pytest.importorskip('btchip')
from btchip.ledgerWrapper import wrapCommandAPDU

from electrumsv.devices.ledger.transport import LedgerHIDTransport
from electrumsv.devices.ledger import transport


def test_complete_multipacket_response_without_blocking_reads():
    payload = bytes(range(100))
    frames = wrapCommandAPDU(0x0101, bytearray(payload + b'\x90\x00'), 64)
    device = Mock()
    device.write.side_effect = len
    device.read.side_effect = [frames[i:i+64] for i in range(0, len(frames), 64)]
    connection = LedgerHIDTransport(device)
    assert connection.exchange(b'\xe0\x40\x00\x00\x00') == payload
    device.set_nonblocking.assert_called_once_with(True)


def test_timeout_closes_connection_including_partial_response(monkeypatch):
    device = Mock()
    device.write.side_effect = len
    frames = wrapCommandAPDU(0x0101, bytearray(bytes(100) + b'\x90\x00'), 64)
    device.read.return_value = frames[:64]
    connection = LedgerHIDTransport(device)
    times = iter([0, 0, 1])
    monkeypatch.setattr(transport.time, 'monotonic', lambda: next(times))
    with pytest.raises(TimeoutError, match='reconnect'):
        connection.exchange(b'\xe0\x40\x00\x00\x00', timeout=10)
    assert not connection.opened
    device.close.assert_called_once()


def test_parallel_exchange_does_not_write_or_close_existing_request():
    device = Mock()
    connection = LedgerHIDTransport(device)
    connection._exchange_lock.acquire()
    with pytest.raises(OSError, match='busy'):
        connection.exchange(b'\xe0\x40\x00\x00\x00')
    device.write.assert_not_called()
    device.close.assert_not_called()
