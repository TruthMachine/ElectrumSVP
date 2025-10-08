#!/usr/bin/env python3
"""
sweep_helper.py

Sweep helper for ElectrumSV GUI supporting both compressed and uncompressed WIF keys,
including BIP38 keys and optional BEEF JSON, with detailed logging.

Updated to support float fees and auto-round outputs to nearest satoshi.
"""

from typing import Optional, List, Dict
import os
import json
import logging
import socket
import ssl
import math

import requests
import socks
import hashlib
import ecdsa
import ecdsa.util
from bitcoinx import PrivateKey, Address, SigHash, Script
from bip38 import BIP38
from bip38.bip38 import ICryptocurrency

from electrumsv.transaction import Transaction, XTxInput, XTxOutput
from electrumsv.keystore import is_address_valid
from electrumsv.networks import SVMainnet

logger = logging.getLogger("sweep")

# -------------------------
# Config
# -------------------------
ELECTRUMX_SERVERS = [
    ('electrumx.gorillapool.io', 50002),
    ('sv2.satoshi.io', 50002),
    ('neptune.api.sv', 50002),
    ('esv.bitails.io', 50002),
    ('bsv.aftrek.org', 50002),
    ('alpha-esv.api.sv', 50002),
    ('electrum.api.sv', 50002),
    ('sv.satoshi.io', 50001),
    ('bsv.aftrek.org', 50001),
    ('sv2.satoshi.io', 50001),
    ('neptune.api.sv', 50001),
    ('electrum.api.sv', 50001),
    ('alpha-esv.api.sv', 50001),
]

PEM_DIR = os.path.join(os.path.dirname(__file__), 'pemcerts')
SOCKS5_PROXY_HOST = '127.0.0.1'
SOCKS5_PROXY_PORT = 9050

# -------------------------
# BIP38 stub
# -------------------------
class Bitcoin(ICryptocurrency):
    NAME = "Bitcoin"
    SYMBOL = "BTC"
    ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    NETWORKS = {
        "mainnet": {
            "wif_prefix": 0x80,
            "address_prefix": 0x00,
            "p2sh_prefix": 0x05,
        }
    }

# -------------------------
# Utilities
# -------------------------
def base_encode(data: bytes, base=58) -> str:
    alphabet = b'123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    num = int.from_bytes(data, 'big')
    encode = b""
    while num > 0:
        num, rem = divmod(num, base)
        encode = bytes([alphabet[rem]]) + encode
    n_pad = 0
    for c in data:
        if c == 0:
            n_pad += 1
        else:
            break
    return (alphabet[0:1] * n_pad + encode).decode('ascii')


def public_key_to_address(pubkey: bytes) -> str:
    h160 = hashlib.new('ripemd160', hashlib.sha256(pubkey).digest()).digest()
    prefix = SVMainnet.ADDRTYPE_P2PKH
    payload = bytes([prefix]) + h160
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return base_encode(payload + checksum)


def address_to_scripthash(address_str: str) -> str:
    addr = Address.from_string(address_str, SVMainnet.COIN)
    script_bytes = addr.to_script_bytes()
    return hashlib.sha256(script_bytes).digest()[::-1].hex()


def is_tor_proxy_running(host='127.0.0.1', port=9050) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=1)
        s.close()
        return True
    except Exception:
        return False


def create_socket_with_optional_socks(proxy_host=None, proxy_port=None):
    if proxy_host and proxy_port:
        sock = socks.socksocket()
        sock.set_proxy(socks.SOCKS5, proxy_host, proxy_port, rdns=True)
        return sock
    return socket.socket(socket.AF_INET)


def create_ssl_context(host: str) -> ssl.SSLContext:
    import certifi
    ctx = ssl.create_default_context()
    try:
        cafile = certifi.where()
        if cafile and os.path.exists(cafile):
            ctx.load_verify_locations(cafile=cafile)
    except Exception:
        logger.debug("Could not load certifi cafile")
    pem_path = os.path.join(PEM_DIR, f"{host}.pem")
    if os.path.exists(pem_path):
        try:
            ctx.load_verify_locations(cafile=pem_path)
            ctx.check_hostname = False
        except Exception as e:
            logger.warning("Failed to load host PEM %s: %s", pem_path, e)
    return ctx

# -------------------------
# UTXO Normalizer
# -------------------------
def _normalize_utxo(u: Dict) -> Dict:
    out = dict(u)
    out['txid'] = out.get('txid') or out.get('tx_hash') or out.get('tx_hash_hex')
    out['vout'] = int(out.get('vout') or out.get('tx_pos') or out.get('position') or 0)
    if 'satoshis' not in out:
        if 'value' in out:
            out['satoshis'] = int(float(out['value']))
        elif 'amount' in out:
            out['satoshis'] = int(round(float(out['amount']) * 1e8))
    out['scriptPubKey'] = out.get('scriptPubKey') or out.get('script_hex') or out.get('script')
    return out

# -------------------------
# Network Queries
# -------------------------
def get_utxos_whatsonchain(address: str) -> List[Dict]:
    logger.info("Fetching UTXOs from WhatsonChain for %s", address)
    url = f"https://api.whatsonchain.com/v1/bsv/main/address/{address}/unspent"
    proxies = None
    if is_tor_proxy_running(SOCKS5_PROXY_HOST, SOCKS5_PROXY_PORT):
        proxies = {
            'http': f'socks5h://{SOCKS5_PROXY_HOST}:{SOCKS5_PROXY_PORT}',
            'https': f'socks5h://{SOCKS5_PROXY_HOST}:{SOCKS5_PROXY_PORT}',
        }
        logger.info("Using Tor proxy for WhatsonChain request")
    r = requests.get(url, timeout=10, proxies=proxies)
    r.raise_for_status()
    utxos = [_normalize_utxo(u) for u in r.json()]
    logger.info("Fetched %d UTXOs from WhatsonChain", len(utxos))
    return utxos


def get_utxos_electrumx_socket_fallback(scripthash: str) -> List[Dict]:
    use_socks = is_tor_proxy_running(SOCKS5_PROXY_HOST, SOCKS5_PROXY_PORT)
    for host, port in ELECTRUMX_SERVERS:
        try:
            sock = create_socket_with_optional_socks(SOCKS5_PROXY_HOST if use_socks else None,
                                                     SOCKS5_PROXY_PORT if use_socks else None)
            sock.settimeout(8)
            sock.connect((host, port))
            if port == 50002:
                ctx = create_ssl_context(host)
                conn = ctx.wrap_socket(sock, server_hostname=host)
            else:
                conn = sock
            req = json.dumps({"id": 1, "method": "blockchain.scripthash.listunspent",
                              "params": [scripthash]}) + "\n"
            conn.sendall(req.encode())
            data = b''
            while True:
                part = conn.recv(4096)
                if not part:
                    break
                data += part
                if b'\n' in part:
                    break
            conn.close()
            resp = json.loads(data.decode())
            if 'result' in resp and isinstance(resp['result'], list):
                utxos = [_normalize_utxo(u) for u in resp['result']]
                logger.info("Fetched %d UTXOs from ElectrumX %s:%d", len(utxos), host, port)
                return utxos
        except Exception as e:
            logger.warning("ElectrumX %s:%d failed: %s", host, port, e)
            continue
    logger.info("ElectrumX servers failed, falling back to WhatsonChain API")
    return []

# -------------------------
# Sweep Function
# -------------------------
def sweep_single_privkey(
    wif_privkey: str,
    destination: str,
    network_service,
    wallet,
    network=SVMainnet,
    fee_rate_sat_per_byte: float = 1.0,  # now accepts float
    manual_fee: Optional[int] = None,  # allows user to override fee directly
    beef_utxos: Optional[List[Dict]] = None,
    bip38_passphrase: Optional[str] = None,
    use_uncompressed: bool = False,
) -> Transaction:

    logger.info("Starting sweep for destination %s", destination)

    if not is_address_valid(destination):
        raise ValueError("Invalid destination address")

    # handle BIP38
    if wif_privkey.startswith('6P'):
        if not bip38_passphrase:
            raise ValueError("BIP38 key requires passphrase")
        bip38_obj = BIP38(Bitcoin, network="mainnet")
        try:
            wif_privkey = bip38_obj.decrypt(wif_privkey, bip38_passphrase)
            logger.info("BIP38 decryption successful")
        except Exception as e:
            raise ValueError(f"Failed to decrypt BIP38 key: {e}")

    privkey = PrivateKey.from_WIF(wif_privkey)
    is_compressed = privkey.is_compressed() and not use_uncompressed
    pubkey_bytes = privkey.public_key.to_bytes(compressed=is_compressed)
    source_address = public_key_to_address(pubkey_bytes)
    logger.info("Source address: %s (compressed=%s)", source_address, is_compressed)

    # fetch UTXOs
    utxos: List[Dict] = []

    if beef_utxos:
        for u in beef_utxos:
            u_norm = _normalize_utxo(u)
            u_norm['address'] = source_address
            utxos.append(u_norm)
        logger.info("Using %d UTXOs from BEEF JSON", len(utxos))
    else:
        scripthash = address_to_scripthash(source_address)
        utxos = get_utxos_electrumx_socket_fallback(scripthash)
        if not utxos:
            utxos = get_utxos_whatsonchain(source_address)

    if not utxos:
        raise ValueError("No funds found for this key")

    total_value = sum(u['satoshis'] for u in utxos)

    # estimate transaction size: 148 bytes per input, 34 bytes per output, +10
    estimated_size = 10 + len(utxos) * 148 + 34  # single output
    fee_satoshis = manual_fee if manual_fee is not None else max(1, int(math.ceil(estimated_size * fee_rate_sat_per_byte)))

    if total_value <= fee_satoshis:
        raise ValueError(f"Not enough balance ({total_value} sats) to cover fee {fee_satoshis}")

    # build transaction
    inputs: List[XTxInput] = []
    prevouts: List[tuple] = []
    for u in utxos:
        tx_hash = bytes.fromhex(u['txid'])[::-1]
        vout = int(u['vout'])
        satoshis = int(u['satoshis'])
        script_bytes = bytes.fromhex(u.get('scriptPubKey') or "")
        if not script_bytes:
            script_bytes = Address.from_string(source_address, SVMainnet.COIN).to_script_bytes()
        inputs.append(XTxInput(tx_hash, vout, b'', 0xFFFFFFFF))
        prevouts.append((script_bytes, satoshis))

    # Log UTXOs used
    for i, txin in enumerate(inputs):
        script_pubkey_bytes, value_sats = prevouts[i]
        line = f"txid={txin.prev_hash.hex()} vout={txin.prev_idx} sats={value_sats}"
        logger.info("UTXO used in sweep: %s", line)

    # compute output amount rounded to nearest satoshi
    output_value = total_value - fee_satoshis
    output_value = max(0, int(round(output_value)))

    outputs = [XTxOutput(output_value,
                         Address.from_string(destination, SVMainnet.COIN).to_script_bytes())]

    tx = Transaction.from_io(inputs, outputs)

    # sign
    sk = ecdsa.SigningKey.from_string(privkey.to_bytes(), curve=ecdsa.SECP256k1)
    for i, txin in enumerate(tx.inputs):
        script_pubkey_bytes, value_sats = prevouts[i]
        sighash = tx.signature_hash(i, value_sats, script_pubkey_bytes,
                                    sighash=SigHash(SigHash.ALL | SigHash.FORKID))
        der_sig = sk.sign_digest(sighash, sigencode=ecdsa.util.sigencode_der_canonize)
        signature = der_sig + b"\x41"  # SigHash.ALL|FORKID
        txin.script_sig = Script(bytes([len(signature)]) + signature +
                                 bytes([len(pubkey_bytes)]) + pubkey_bytes)

    logger.info("Sweep transaction created successfully: %s (fee %d sats)", tx.txid(), fee_satoshis)
    return tx

