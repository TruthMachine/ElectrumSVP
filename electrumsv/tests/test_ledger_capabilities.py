import pytest
from electrumsv.devices.signing import SigningError, prepare_signing_request
from electrumsv.transaction import TransactionContext
from electrumsv.tests.test_external_signing import transaction


@pytest.mark.parametrize('kind', [0x43, 0xc1, 0xc3])
def test_ledger_capabilities_reject_non_all_before_device_io(kind):
    from electrumsv.devices.hardware import BACKENDS
    tx, keys = transaction()
    with pytest.raises(SigningError, match='sighash'):
        prepare_signing_request(tx, TransactionContext(), lambda key: True,
            BACKENDS['ledger'].signing, sighashes={(0, 0): kind})
