from electrumsv.bitcoin import Bitcoin
from electrumsv.constants import CheckPoint
from electrumsv.util.json import read_json_dict

class CheckPoint:
    def __init__(self, header_hash: bytes, height: int):
        self.header_hash = header_hash
        self.height = height


class SVMainnet(object):
    ADDRTYPE_P2PKH = 0
    ADDRTYPE_P2SH = 5
    CASHADDR_PREFIX = "bitcoincash"
    DEFAULT_PORTS = {'t': '50001', 's': '50002'}
    DEFAULT_SERVERS = read_json_dict('servers.json')
    GENESIS = "000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"
    NAME = 'mainnet'
    BITCOIN_URI_PREFIX = "bitcoin"
    PAY_URI_PREFIX = "pay"
    WIF_PREFIX = 0x80
    BIP276_VERSION = 1

    # Bitcoin Cash fork block specification
    BITCOIN_CASH_FORK_BLOCK_HEIGHT = 478559
    BITCOIN_CASH_FORK_BLOCK_HASH = (
        "000000000000000000651ef99cb9fcbe0dadde1d424bd9f15ff20136191a5eec"
    )

    COIN = Bitcoin



    CHECKPOINT = CheckPoint(
        bytes.fromhex(
            '00e0ff37689867096945489d8f39ccb2859e31f6f0fb3894705e3b0b0000000000000000f282be97'
            'e1a80610fd44c3cd9bfa386f49e4b3bce4126600c0a98d22f18ae3314db8b3637cc30b1802cd6874'
        ),
        773040
    )



    VERIFICATION_BLOCK_MERKLE_ROOT = (
        '8e9c79a13f25f19bd2e126475cd6fcc359d8a66485cd6a7c1deebacf289dfd15'
    )

    BIP44_COIN_TYPE = 0

    BLOCK_EXPLORERS = {
        'bitails.io': (
            'https://bitails.io',
            {'tx': 'tx', 'addr': 'address', 'script': 'script'},
        ),
        'whatsonchain.com': (
            'https://whatsonchain.com',
            {'tx': 'tx', 'addr': 'address', 'script': 'script'},
        ),        
        'satoshi.io': (
            'https://satoshi.io',
            {'tx': 'tx', 'addr': 'address', 'script': 'script'},
        ),
    }

    FAUCET_URL = "https://faucet.satoshisvision.network"
    KEEPKEY_DISPLAY_COIN_NAME = 'Bitcoin'
    TREZOR_COIN_NAME = 'Bcash'
    TWENTY_MINUTE_RULE = False

