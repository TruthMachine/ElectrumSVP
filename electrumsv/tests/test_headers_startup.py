"""A remote response is not evidence that a local headers file is corrupt."""
import pytest
from bitcoinx import Bitcoin
from electrumsv.headers_storage import PersistentHeaders
from electrumsv import verification_utils


@pytest.mark.parametrize('response', [None, [], {}, {'result': {}}, {'result': 3},
    {'result': 'not hex'}, {'result': '00'}, {'error': 'unavailable'}])
def test_bad_network_response_preserves_valid_local_headers(tmp_path, monkeypatch, response):
    path = tmp_path / 'headers'
    path.write_bytes(Bitcoin.genesis_header)
    monkeypatch.setattr(verification_utils, 'electrum_request', lambda *args: response)
    headers = PersistentHeaders(file_path=str(path))
    assert path.read_bytes() == Bitcoin.genesis_header
    assert not (tmp_path / 'headers.old').exists()
    assert headers.headers.longest_chain().height == 0


def test_offline_headers_never_request_remote_validation(tmp_path, monkeypatch):
    path = tmp_path / 'headers'
    path.write_bytes(Bitcoin.genesis_header)
    def unexpected_request(*args):
        pytest.fail('Offline header loading attempted a network request')
    monkeypatch.setattr(verification_utils, 'electrum_request', unexpected_request)
    headers = PersistentHeaders(file_path=str(path), validate_network=False)
    assert headers.headers.longest_chain().height == 0
