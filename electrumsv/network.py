# ElectrumSV - lightweight Bitcoin SV client
# Copyright (C) 2019-2020 The ElectrumSV Developers
# Copyright (c) 2011-2016 Thomas Voegtlin
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.



import asyncio
from collections import defaultdict
from contextlib import suppress
from enum import IntEnum
from functools import partial
import os
import random
import re
import ssl
import stat
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Tuple



import certifi
from aiorpcx import (
    connect_rs, RPCSession, Notification, BatchError, RPCError, CancelledError, SOCKSError,
    TaskTimeout, TaskGroup, handler_invocation, sleep, ignore_after, timeout_after,
    SOCKS4a, SOCKS5, SOCKSProxy, SOCKSUserAuth, NewlineFramer
)
from bitcoinx import (
    MissingHeader, IncorrectBits, InsufficientPoW, hex_str_to_hash, hash_to_hex_str,
    sha256, double_sha256
)





from .app_state import app_state
from .bitcoin import scripthash_hex
from .constants import ScriptType, TxFlags
from .i18n import _
from .logs import logs
from .transaction import Transaction
from .util import chunks, JSON, protocol_tuple, TriggeredCallbacks, version_string
from .networks import Net
import json

from typing import Union
from electrumsv.headers_storage import PersistentHeaders
from bitcoinx.headers import deserialized_header, Headers, Header, Chain, Network
from electrumsv.app_state import app_state

import logging


import shutil
import sys
import logging
from bitcoinx import Bitcoin
from bitcoinx.errors import MissingHeader




from .version import PACKAGE_VERSION, PROTOCOL_MIN, PROTOCOL_MAX


if TYPE_CHECKING:
    from .wallet import AbstractAccount, Wallet



Network.deserialized_header = staticmethod(deserialized_header)



logger = logs.get_logger("network")

HEADER_SIZE = 80
ONE_MINUTE = 60
ONE_DAY = 24 * 3600
HEADERS_SUBSCRIBE = 'blockchain.headers.subscribe'
REQUEST_MERKLE_PROOF = 'blockchain.transaction.get_merkle'
SCRIPTHASH_HISTORY = 'blockchain.scripthash.get_history'
SCRIPTHASH_SUBSCRIBE = 'blockchain.scripthash.subscribe'
SCRIPTHASH_UNSUBSCRIBE = 'blockchain.scripthash.unsubscribe'
BROADCAST_TX_MSG_LIST = (
    ('dust', _('very small "dust" payments')),
    (('Missing inputs', 'Inputs unavailable', 'bad-txns-inputs-spent'),
     _('missing, already-spent, or otherwise invalid coins')),
    ('insufficient priority', _('insufficient fees or priority')),
    ('bad-txns-premature-spend-of-coinbase', _('attempt to spend an unmatured coinbase')),
    (('txn-already-in-mempool', 'txn-already-known'),
     _("it already exists in the server's mempool")),
    ('txn-mempool-conflict', _("it conflicts with one already in the server's mempool")),
    ('bad-txns-nonstandard-inputs', _('use of non-standard input scripts')),
    ('absurdly-high-fee', _('fee is absurdly high')),
    ('non-mandatory-script-verify-flag', _('the script fails verification')),
    ('tx-size', _('transaction is too large')),
    ('scriptsig-size', _('it contains an oversized script')),
    ('scriptpubkey', _('it contains a non-standard signature')),
    ('bare-multisig', _('it contains a bare multisig input')),
    ('multi-op-return', _('it contains more than 1 OP_RETURN input')),
    ('scriptsig-not-pushonly', _('a scriptsig is not simply data')),
    ('bad-txns-nonfinal', _("transaction is not final"))
)

# Strip out bad servers.
# 20230522 satoshi.vision.cash is pruned, will not provide correct state for older
#     transactions.
BAD_SERVER_HOSTS = { "satoshi.vision.cash" }



# Determine preloaded headers path


if getattr(sys, 'frozen', False):
    # Running from AppImage
    BASE_PATH = os.path.join(sys._MEIPASS, 'usr', 'share')
else:
    # Running from source
    BASE_PATH = os.path.join(os.path.dirname(__file__), 'resources')

PRELOADED_HEADERS = os.path.join(BASE_PATH, 'headersloaded', 'headers')



def load_custom_headers(path: str):
    """Load binary 80-byte headers into app_state.headers"""
    from bitcoinx import Headers

    headers = Headers()
    with open(path, "rb") as f:
        raw = f.read()
    count = len(raw) // 80
    for i in range(count):
        header = raw[i*80 : (i+1)*80]
        headers.connect(header)
    app_state.headers = headers
    return headers


def broadcast_failure_reason(exception):
    if isinstance(exception, RPCError):
        msg = exception.message
        for in_msgs, out_msg in BROADCAST_TX_MSG_LIST:
            if isinstance(in_msgs, str):
                in_msgs = (in_msgs, )
            if any(in_msg in msg for in_msg in in_msgs):
                return out_msg
    return _('reason unknown')


class SwitchReason(IntEnum):
    '''The reason the main server was changed.'''
    disconnected = 0
    lagging = 1
    user_set = 2


def _require_list(obj):
    assert isinstance(obj, (tuple, list))
    return obj


def _require_number(obj):
    assert isinstance(obj, (int, float))
    return obj


def _require_string(obj):
    assert isinstance(obj, str)
    return obj


def _history_status(history) -> Optional[str]:
    if not history:
        return None
    status = ''.join(f'{tx_id}:{tx_height}:' for tx_id, tx_height in history)
    return sha256(status.encode()).hex()


def _root_from_proof(hash, branch, index):
    '''From ElectrumX.'''
    for elt in branch:
        if index & 1:
            hash = double_sha256(elt + hash)
        else:
            hash = double_sha256(hash + elt)
        index >>= 1
    if index:
        raise ValueError(f'index {index} out of range for proof of length {len(branch)}')
    return hash


class DisconnectSessionError(Exception):

    def __init__(self, reason, *, blacklist=False):
        super().__init__(reason)
        self.blacklist = False


class SVServerState:
    '''The run-time state of an SVServer.'''

    def __init__(self):
        self.banner = ''
        self.donation_address = ''
        self.last_try = 0
        self.last_good = 0
        self.last_blacklisted = 0
        self.retry_delay = 0

    def can_retry(self, now):
        return not self.is_blacklisted(now) and self.last_try + self.retry_delay < now

    def is_blacklisted(self, now):
        return self.last_blacklisted > now - ONE_DAY

    def to_json(self):
        return {
            'last_try': int(self.last_try),
            'last_good': int(self.last_good),
            'last_blacklisted': int(self.last_blacklisted),
        }

    @classmethod
    def from_json(cls, dct):
        result = cls()
        for attr, value in dct.items():
            setattr(result, attr, value)
        return result

    def __str__(self):
        return str(self.to_json())



class SVServer:
    '''A smart wrapper around a (host, port, protocol) tuple with automatic retry on connect.'''

    all_servers: Dict[Tuple[str, int, str], 'SVServer'] = {}

    def __init__(self, host, port, protocol):
        if not isinstance(host, str) or not host:
            raise ValueError(f'bad host: {host}')
        if not isinstance(port, int):
            raise ValueError(f'bad port: {port}')
        if protocol not in 'st':
            raise ValueError(f'unknown protocol: {protocol}')
        key = (host, port, protocol)
        assert key not in SVServer.all_servers
        SVServer.all_servers[key] = self
        self.host = host
        self.port = port
        self.protocol = protocol
        self.state = SVServerState()

    @classmethod
    def unique(cls, host, port, protocol):
        if isinstance(port, str):
            with suppress(ValueError):
                port = int(port)
        key = (host, port, protocol)
        obj = cls.all_servers.get(key)
        if not obj:
            obj = cls(host, port, protocol)
        return obj

    def _sslc(self):
        if self.protocol != 's':
            return None
        return ssl.SSLContext(ssl.PROTOCOL_TLS)

    def _connector(self, session_factory, proxy):
        return connect_rs(
            self.host,
            self.port,
            proxy=proxy,
            session_factory=session_factory,
            ssl=self._sslc()
        )

    def _logger(self, n):
        logger_name = f'[{self.host}:{self.port} {self.protocol_text()} #{n}]'
        return logs.get_logger(logger_name)

    def to_json(self):
        return (self.host, self.port, self.protocol, self.state)

    @classmethod
    def from_string(cls, s):
        parts = s.split(':', 3)
        return cls.unique(*parts)

    @classmethod
    def from_json(cls, hpps):
        host, port, protocol, state = hpps
        result = cls.unique(host, port, protocol)
        result.state = state
        return result

    async def connect(self, network, n):
        """Connect to server with automatic retry."""
        logger = self._logger(n)
        logger.info('connecting...')

        self.state.last_try = time.time()
        session_factory = partial(SVSession, network, self, logger)

        # Exponential backoff retry
        for attempt in range(6):  # ~1 minute total retries
            try:
                async with self._connector(session_factory, proxy=network.proxy) as session:
                    try:
                        await session.run()
                        break  # success
                    except DisconnectSessionError as error:
                        await session.disconnect(str(error), blacklist=error.blacklist)
                    except (RPCError, BatchError, TaskTimeout) as error:
                        await session.disconnect(str(error))
            except (OSError, ssl.SSLError, SOCKSError) as e:
                logger.warning(f'Connection attempt {attempt+1} failed: {e}')
                await sleep(10)
        else:
            logger.error('All connection attempts failed')

        logger.info('disconnected')

    def protocol_text(self):
        return 'SSL' if self.protocol == 's' else 'TCP'

    def __repr__(self):
        return f'SVServer("{self.host}", {self.port}, "{self.protocol}")'

    def __str__(self):
        return str(self.to_json()[:3])


class SVUserAuth(SOCKSUserAuth):
    def __repr__(self):
        # So its safe in logs, etc.  Also used in proxy comparisons.
        hash_str = sha256(str((self.username, self.password)).encode())[:8].hex()
        return f'{self.__class__.__name__}({hash_str})'


class SVProxy(SOCKSProxy):
    '''Encapsulates a SOCKS proxy.'''

    kinds = {'SOCKS4' : SOCKS4a, 'SOCKS5': SOCKS5}

    def __init__(self, address, kind, auth):
        protocol = self.kinds.get(kind.upper())
        if not protocol:
            raise ValueError(f'invalid proxy kind: {kind}')
        # This class is serialised using `util.JSON` hooks, this is not possible for `SVUserAuth`
        # as that is naturally serialisable via `json.dumps` and the hook is not called.
        if isinstance(auth, list):
            auth = SVUserAuth(*auth)
        super().__init__(address, protocol, auth)

    def to_json(self):
        return (str(self.address), self.kind(), self.auth)

    @classmethod
    def from_json(cls, obj):
        return cls(*obj)

    @classmethod
    def from_string(cls, obj):
        # Backwards compatibility
        try:
            kind, host, port, username, password = obj.split(':', 5)
            return cls((host, port), kind, SVUserAuth(username, password))
        except Exception:
            return None

    def kind(self):
        return 'SOCKS4' if self.protocol is SOCKS4a else 'SOCKS5'

    def host(self):
        return self.address.host

    def port(self):
        return self.address.port

    def username(self):
        return self.auth.username if self.auth else ''

    def password(self):
        return self.auth.password if self.auth else ''

    def __str__(self):
        return ', '.join((repr(self.address), self.kind(), repr(self.auth)))


class SVSession(RPCSession):

    ca_path = certifi.where()
    _connecting_tips: Dict[bytes, asyncio.Event] = {}
    _need_checkpoint_headers = False
    # account -> list of script hashes.  Also acts as a list of registered accounts
    _subs_by_account: Dict['AbstractAccount', List[str]] = {}
    # script_hash -> (keyinstance_id, script_type)
    _keyinstance_map: Dict[str, Tuple[int, ScriptType]] = {}

    def __init__(self, network, server, logger, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._handlers = {}
        self._network = network
        self._closed_event = app_state.async_.event()
        # These attributes are intended to part of the external API
        self.chain = None
        self.logger = logger
        self.server = server
        self.tip = None
        self.ptuple = (0, )
        self.network = network


    @property
    def tip(self):
        # fallback to network tip if session has no headers yet
        if getattr(self, '_tip', None) is None:
            if getattr(self, 'headers', None) and self.headers.longest_chain():
                self._tip = self.headers.longest_chain().tip()
            elif getattr(self, 'network', None) and getattr(self.network, '_tip', None):
                self._tip = self.network._tip
        return self._tip

    @tip.setter
    def tip(self, value):
        self._tip = value




    def set_throttled(self, flag: bool) -> None:
        if flag:
            RPCSession.recalibrate_count = 30
        else:
            RPCSession.recalibrate_count = 10000000000

    def get_current_outgoing_concurrency_target(self) -> int:
        return self._outgoing_concurrency.max_concurrent

    def default_framer(self) -> NewlineFramer:
        max_size = app_state.electrumx_message_size_limit()*1024*1024
        return NewlineFramer(max_size=max_size)


    #Check is left over, but after bitcoinx 0.9.0, instead now using preloaded headers file
    @classmethod
    def _required_checkpoint_headers(cls):
        """
        Returns (start_height, count) needed to supply the DAA window ending at the
        checkpoint. Uses Net.CHECKPOINT dict.
        """
        if cls._need_checkpoint_headers:
            headers_obj = app_state.headers
            chain = headers_obj.longest_chain()
            checkpoint = Net.CHECKPOINT
            cp_height = checkpoint['height']
            if cp_height == 0:
                cls._need_checkpoint_headers = False
            else:
                # We need roughly the 146 headers before the checkpoint to validate DAA.
                start = max(0, cp_height - 146)
                for h in range(start, cp_height):
                    try:
                        # chain.header_at_height raises MissingHeader if absent
                        chain.header_at_height(h)
                    except MissingHeader:
                        # first missing header; ask for the remainder up to checkpoint
                        return h, cp_height - h
                # all present
                cls._need_checkpoint_headers = False
        return 0, 0



    async def _connect_header(self, raw_header, height):
        try:
            if isinstance(raw_header, str):
                raw_header = bytes.fromhex(raw_header)

            header = deserialized_header(raw_header, height)

            try:
                chain = self.headers.connect(raw_header)
                # --- persist to disk immediately ---
                if hasattr(self.headers, "flush"):
                    self.headers.flush()
            except MissingHeader as mh:
                self.logger.warning(f"Cannot connect header at height {height}: missing parent {mh}")
                return None

            return header, chain

        except Exception as e:
            self.logger.warning(f"Failed to connect header at height {height}: {repr(e)}")
            return None




    def _connect_chunk(self, headers_obj, raw_chunk, start_height):
        HEADER_SIZE = 80
        count = len(raw_chunk) // HEADER_SIZE
        for i in range(count):
            raw_header = raw_chunk[i*HEADER_SIZE : (i+1)*HEADER_SIZE]
            height = start_height + i
            try:
                headers_obj.connect(raw_header, check_work=True)
            except Exception as e:
                hex_str = hash_to_hex_str(Net.COIN.header_hash(raw_header))
                self.logger.warning(f"Cannot connect header at height {height}: {e}")

        # --- persist after full chunk ---
        if hasattr(headers_obj, "flush"):
            headers_obj.flush()

        return headers_obj.longest_chain()


    @classmethod
    def _log_missing_header(cls, height, expected):
        logger = getattr(cls, 'logger', print)
        logger.debug(f'MissingHeader: expected {expected}, got {height}')

    @classmethod
    def _log_prev_hash_mismatch(cls, height, raw_header, prev_raw):
        logger = getattr(cls, 'logger', print)
        logger.debug(f'PrevHash mismatch at {height}: '
                     f'{hash_to_hex_str(Net.COIN.header_hash(raw_header))} '
                     f'prev {hash_to_hex_str(Net.COIN.header_hash(prev_raw))}')


    async def _negotiate_protocol(self):
        '''Raises: RPCError, TaskTimeout'''
        method = 'server.version'
        args = (PACKAGE_VERSION, [ version_string(PROTOCOL_MIN), version_string(PROTOCOL_MAX) ])
        try:
            server_string, protocol_string = await self.send_request(method, args)
            self.logger.debug(f'server string: {server_string}')
            self.logger.debug(f'negotiated protocol: {protocol_string}')
            self.ptuple = protocol_tuple(protocol_string)
            assert PROTOCOL_MIN <= self.ptuple <= PROTOCOL_MAX
        except (AssertionError, ValueError) as e:
            raise DisconnectSessionError(f'{method} failed: {e}', blacklist=True)

    async def _get_checkpoint_headers(self):
        '''Raises: RPCError, TaskTimeout'''
        while True:
            start_height, count = self._required_checkpoint_headers()
            if not count:
                break
            logger.info(f'{count:,d} checkpoint headers needed')
            await self._request_chunk(start_height, count)

    async def _request_chunk(self, height, count):
        """
        Request headers from server via blockchain.block.headers.
        Preserve behavior: if request crosses checkpoint, server may omit proof (cp_arg=0).
        Returns last height connected.
        """
        self.logger.info(f'requesting {count:,d} headers from height {height:,d}')
        method = 'blockchain.block.headers'
        checkpoint_height = Net.CHECKPOINT['height']
        # if the request crosses or reaches checkpoint, ask server to omit proof (0)
        cp_arg = 0 if (height + count) >= checkpoint_height else checkpoint_height

        try:
            result = await self.send_request(method, (height, count, cp_arg))
            rec_count = result['count']
            last_height = height + rec_count - 1
            if rec_count == 0:
                return height - 1

            if count != rec_count:
                self.logger.info(f'received just {rec_count:,d} headers')

            raw_chunk = bytes.fromhex(result['hex'])
            HEADER_SIZE = 80
            assert len(raw_chunk) == HEADER_SIZE * rec_count

            if cp_arg:
                # server returned proof for the tail header
                hex_root = result['root']
                branch = [hex_str_to_hash(item) for item in result['branch']]
                tail_raw = raw_chunk[-HEADER_SIZE:]
                self._check_header_proof(hex_root, branch, tail_raw, last_height)

            # Attempt to append the chunk; will raise MissingHeader if it doesn't continue the tip.
            self.chain = self._connect_chunk(app_state.headers, raw_chunk, height)

        except (AssertionError, KeyError, TypeError, ValueError,
                IncorrectBits, InsufficientPoW, MissingHeader) as e:
            raise DisconnectSessionError(f'{method} failed: {e}', blacklist=True)

        self.logger.info(f'connected {rec_count:,d} headers up to height {last_height:,d}')
        return last_height




    async def _subscribe_headers(self):
        if not hasattr(self, 'headers'):
            self.headers = app_state.headers

        local_tip_height = self.headers.tip.height
        self.logger.info(f"FORCE SUBSCRIBE HEADERS: local tip={local_tip_height}")

        try:
            cp_height = Net.CHECKPOINT['height']
            if local_tip_height < cp_height:
                self.logger.info(f"Local tip {local_tip_height} below checkpoint, requesting headers...")
                await self._network.request_headers_from_checkpoint(self)

            # register the notify handler once
            self._handlers['blockchain.headers.subscribe'] = self._on_new_tip

            # send initial subscribe request
            result = await self.send_request('blockchain.headers.subscribe')
            tip_json = result if isinstance(result, dict) else {
                'hex': result['hex'],
                'height': result['height']
            }
            tip_header = deserialized_header(bytes.fromhex(tip_json['hex']), tip_json['height'])

            if self.headers.tip.height < tip_header.height:
                await self._catch_up_to_tip(tip_header)

            # --- NEW: push local tip to GUI immediately ---
            if self.headers.tip.height > 0:
                chain_tip = self.headers.tip
                latest_header = chain_tip.header_at_height(chain_tip.height)
                await self._on_new_tip({
                    'hex': latest_header.raw.hex(),
                    'height': latest_header.height
                })

        except Exception as e:
            self.logger.warning(f"Error during header subscription: {e}")


    def _secs_to_next_ping(self):
        return self.last_send + 300 - time.time()

    async def _ping_loop(self):
        '''Raises: RPCError, TaskTimeout'''
        method = 'server.ping'
        while True:
            await sleep(self._secs_to_next_ping())
            if self._secs_to_next_ping() < 1:
                self.logger.debug(f'sending {method}')
                await self.send_request(method)

    def _check_header_proof(self, hex_root, branch, raw_header, height):
        '''Raises: DisconnectSessionError'''
        expected_root = Net.VERIFICATION_BLOCK_MERKLE_ROOT
        if hex_root != expected_root:
            raise DisconnectSessionError(f'bad header merkle root {hex_root} expected '
                                         f'{expected_root}', blacklist=True)
        header = Net.COIN.deserialized_header(raw_header, height)
        proven_root = hash_to_hex_str(_root_from_proof(header.hash, branch, height))
        if proven_root != expected_root:
            raise DisconnectSessionError(f'invalid header proof {proven_root} expected '
                                         f'{expected_root}', blacklist=True)
        self.logger.debug(f'good header proof for height {height}')




    async def _on_new_tip(self, json_tip):
        """
        Called when the server sends a new tip (or returns it from subscription).
        Connects the new tip to the local chain, respecting checkpoint rules.
        Updates self.tip and self.chain even if _connect_header returns None.
        """
        try:
            raw_header = json_tip['hex']  # may be hex string
            height = json_tip['height']
            assert isinstance(height, int), "height must be an integer"
        except Exception as e:
            raise DisconnectSessionError(f"Error parsing tip: {e} {json_tip}")

        cp_height = Net.CHECKPOINT['height']
        if height < cp_height:
            raise DisconnectSessionError(f"Server tip height {height:,d} below checkpoint")

        # Reset tip and chain
        self.tip = None
        self.chain = None

        # Attempt to connect tip
        try:
            result = await self._connect_header(raw_header, height)
            if result is not None:
                success, chain = result
            else:
                success, chain = False, None
        except Exception as e:
            self.logger.warning(f"_connect_header failed at height {height:,d}: {e}")
            success, chain = False, None

        if success:
            self.logger.debug(f"Connected tip at height {height:,d}")
            longest = app_state.headers.longest_chain()
            self.tip = longest.header_at_height(height)
            self.chain = longest
            self._network.check_main_chain_event.set()
            return

        # Fallback: catch up from checkpoint
        self.logger.debug(f"Tip at height {height:,d} not connectable, starting catch-up")
        try:
            tip_obj = self.network.COIN.deserialized_header(
                bytes.fromhex(raw_header), height
            )
            await self._catch_up_to_tip_throttled(tip_obj)
        except Exception as e:
            self.logger.warning(f"Catch-up to tip at height {height:,d} failed: {e}")

        # Always update tip and chain after catch-up
        longest = app_state.headers.longest_chain()
        if longest:
            self.tip = longest.tip()
            self.chain = longest



    async def _catch_up_to_tip(self, tip):
        """Catch up local headers to the server tip."""
        headers_obj = app_state.headers
        local_tip_height = headers_obj.tip.height
        remote_tip_height = tip.height

        while local_tip_height < remote_tip_height:
            chunk_size = min(2016, remote_tip_height - local_tip_height)
            last_height = await self._request_chunk(local_tip_height + 1, chunk_size)
            local_tip_height = last_height


    async def _subscribe_to_script_hash(self, script_hash: str) -> None:
        '''Raises: RPCError, TaskTimeout'''
        status = await self.send_request(SCRIPTHASH_SUBSCRIBE, [script_hash])
        await self._on_queue_status_changed(script_hash, status)

    async def _unsubscribe_from_script_hash(self, script_hash: str) -> bool:
        return await self.send_request(SCRIPTHASH_UNSUBSCRIBE, [script_hash])

    async def _on_status_changed(self, script_hash: str, status: str) -> None:
        keydata = self._keyinstance_map.get(script_hash)
        if keydata is None:
            self.logger.error(f'received status notification for unsubscribed {script_hash}')
            return
        keyinstance_id, script_type = keydata

        # Accounts needing a notification.
        accounts = [account for account, subs in self._subs_by_account.items()
            if script_hash in subs and
            _history_status(account.get_key_history(keyinstance_id, script_type)) != status]
        if not accounts:
            return

        # Status has changed; get history
        result = await self.request_history(script_hash)
        self.logger.debug(f'received history of {keyinstance_id} length {len(result)}')
        try:
            history = [(item['tx_hash'], item['height']) for item in result]
            tx_fees = {item['tx_hash']: item['fee'] for item in result if 'fee' in item}
            # Check that txids are unique
            assert len(set(tx_hash for tx_hash, tx_height in history)) == len(history), \
                f'server history for {keyinstance_id} has duplicate transactions'
        except (AssertionError, KeyError) as e:
            self._network._on_status_queue.put_nowait((script_hash, status))  # re-queue
            raise DisconnectSessionError(f'bad history returned: {e}')

        # Check the status; it can change legitimately between initial notification and
        # history request
        hstatus = _history_status(history)
        if hstatus != status:
            self.logger.warning(
                f'history status mismatch {hstatus} vs {status} for {keyinstance_id}')

        for account in accounts:
            if history != account.get_key_history(keyinstance_id, script_type):
                self.logger.debug("_on_status_changed new=%s old=%s", history,
                    account.get_key_history(keyinstance_id, script_type))

            await account.set_key_history(keyinstance_id, script_type, history, tx_fees)

    async def _main_server_batch(self):
        '''Raises: DisconnectSessionError, BatchError, TaskTimeout'''
        async with timeout_after(10):
            async with self.send_batch(raise_errors=True) as batch:
                batch.add_request('server.banner')
                batch.add_request('server.donation_address')
                batch.add_request('server.peers.subscribe')
        server = self.server
        try:
            server.state.banner = _require_string(batch.results[0])
            server.state.donation_address = _require_string(batch.results[1])
            server.state.peers = self._parse_peers_subscribe(batch.results[2])
            self._network.trigger_callback('banner')
        except AssertionError as e:
            raise DisconnectSessionError(f'main server requests bad batch response: {e}')

    def _parse_peers_subscribe(self, result):
        peers = []
        for host_details in _require_list(result):
            host_details = _require_list(host_details)
            host = _require_string(host_details[1])
            for v in host_details[2]:
                if re.match(r"[st]\d*", _require_string(v)):
                    protocol, port = v[0], v[1:]
                    try:
                        peers.append(SVServer.unique(host, port, protocol))
                    except ValueError:
                        pass
        self.logger.info(f'{len(peers)} servers returned from server.peers.subscribe')
        return peers

    async def _request_headers_at_heights(self, heights):
        """
        Request arbitrary heights as a batch and try to connect them (lowest-first).
        Returns greatest connected height (-1 if none).
        If headers needed are <= checkpoint height, we expect proof fields in response and
        validate via _check_header_proof().
        """
        headers_obj = app_state.headers
        heights = sorted(set(heights))
        cp_height = Net.CHECKPOINT['height']
        method = 'blockchain.block.header'
        good_height = -1

        async def _request_header_batch(self, server, start_height, end_height):
            # Request [start_height, end_height] inclusive and connect lowest-first.
            # If a parent is missing, backfill from local_tip+1 to height-1, then retry once.
            if end_height < start_height:
                return -1

            try:
                batch = await server.request_headers(start_height, end_height)
            except Exception as e:
                self.logger.warning(f"Failed to request headers {start_height}-{end_height} from {server}: {repr(e)}")
                return -1

            good_height = -1
            for idx, raw_header in enumerate(batch):
                height = start_height + idx

                result = await self._connect_header(raw_header, height)
                if result is None:
                    # Parent missing (or other transient issue). Backfill and retry once.
                    try:
                        local_tip = app_state.headers.longest_chain().height
                    except Exception:
                        local_tip = -1

                    missing_from = max(local_tip + 1, start_height)
                    if height - 1 >= missing_from:
                        # fetch parents first
                        await _request_header_batch(self, server, missing_from, height - 1)
                        # retry current header
                        result = await self._connect_header(raw_header, height)
                        if result is None:
                            continue  # still bad – skip and keep going
                    else:
                        continue  # nothing to backfill – skip

                header, chain = result
                good_height = height
                try:
                    self.logger.info(f"Connected header at height {height}: {header.hash.hex()}")
                except Exception:
                    self.logger.info(f"Connected header at height {height}")

            return good_height








    async def handle_request(self, request):
        if isinstance(request, Notification):
            handler = self._handlers.get(request.method)
        else:
            handler = None
        coro = handler_invocation(handler, request)()
        return await coro

    async def connection_lost(self):
        await super().connection_lost()
        self._closed_event.set()

    #
    # API exposed to the rest of this file
    #

    async def disconnect(self, reason, *, blacklist=False):
        if blacklist:
            self.server.state.last_blacklisted = time.time()
            self.logger.error(f'disconnecting and blacklisting: {reason}')
        else:
            self.logger.error(f'disconnecting: {reason}')
        await self.close()

    async def run(self):
        '''Called when a connection is established to manage the connection.

        Raises: RPCError, BatchError, TaskTimeout, DisconnectSessionError
        '''
        # Negotiate the protocol before doing anything else
        await self._negotiate_protocol()
        # Checkpoint headers are essential to attempting tip connection
        await self._get_checkpoint_headers()
        # Then subscribe headers and connect the server's tip
        await self._subscribe_headers()
        # Only once the tip is connected to our set of chains do we consider the
        # session good and add it to the network's session list.  The network and
        # other client code can assume a session 'tip' and 'chain' set.
        is_main_server = await self._network.session_established(self)
        try:
            self.server.state.retry_delay = 0
            async with TaskGroup() as group:
                if is_main_server:
                    self.logger.info('using as main server')
                    await group.spawn(self._main_server_batch)
                # This raises a TaskTimeout but it gets discarded as it also seems to trigger
                # the closed event which cancels the ping exception before that gets raised up.
                await group.spawn(self._ping_loop)
                await self._closed_event.wait()
                await group.cancel_remaining()
        finally:
            await self._network.session_closed(self)

    async def headers_at_heights(self, heights):
        """
        Return dict {height: header_obj}. Request missing headers from server when needed.
        """
        result = {}
        missing = []
        chain = self.chain or app_state.headers.longest_chain()

        for h in set(heights):
            try:
                result[h] = chain.header_at_height(h)
            except MissingHeader:
                missing.append(h)

        if missing:
            await self._request_headers_at_heights(missing)
            # refresh chain in case best chain switched
            chain = app_state.headers.longest_chain()
            for h in missing:
                result[h] = chain.header_at_height(h)

        self._network.trigger_callback('updated')
        return result

    async def request_tx(self, tx_id: str):
        '''Raises: RPCError, TaskTimeout'''
        return await self.send_request('blockchain.transaction.get', [tx_id])

    async def request_proof(self, *args):
        '''Raises: RPCError, TaskTimeout'''
        return await self.send_request(REQUEST_MERKLE_PROOF, args)

    async def request_history(self, script_hash):
        '''Raises: RPCError, TaskTimeout'''
        return await self.send_request(SCRIPTHASH_HISTORY, [script_hash])

    async def _on_queue_status_changed(self, script_hash: str, status: str) -> None:
        item = (script_hash, status)
        self._network._on_status_queue.put_nowait(item)

    async def subscribe_to_triples(self, account: 'AbstractAccount', triples) -> None:
        '''triples is an iterable of (keyinstance_id, script_type, script_hash) triples.

        Raises: RPCError, TaskTimeout'''
        # Set notification handler
        self._handlers[SCRIPTHASH_SUBSCRIBE] = self._on_queue_status_changed
        if account not in self._subs_by_account:
            self._subs_by_account[account] = []
        # Take reference so account can be unsubscribed asynchronously without conflict
        subs = self._subs_by_account[account]
        async with TaskGroup() as group:
            for keyinstance_id, script_type, script_hash in triples:
                subs.append(script_hash)
                # Send request even if already subscribed, as our user expects a response
                # to trigger other actions and won't get one if we swallow it.
                self._keyinstance_map[script_hash] = keyinstance_id, script_type
                await group.spawn(self._subscribe_to_script_hash(script_hash))

            # ensure GUI doesn't keep showing synchronizing...(100/101) when there are only 100 subs
            account.request_count += len(set(triples) - set(subs))
            account._wallet.progress_event.set()

            while await group.next_done():
                account.response_count += 1
                account._wallet.progress_event.set()

        assert len(set(subs)) == len(subs), "account subscribed to the same keys twice"

    async def unsubscribe_from_pairs(self, account: 'AbstractAccount', pairs) -> None:
        '''pairs is an iterable of (keyinstance_id, script_hash) pairs.

        Raises: RPCError, TaskTimeout'''
        subs = self._subs_by_account[account]
        exclusive_subs = self._get_exclusive_set(account, subs)
        async with TaskGroup() as group:
            for keyinstance_id, script_type, script_hash in pairs:
                if script_hash not in exclusive_subs:
                    continue
                # Blocking on each removal allows for race conditions.
                if script_hash not in subs:
                    continue
                subs.remove(script_hash)
                del self._keyinstance_map[script_hash]
                await group.spawn(self._unsubscribe_from_script_hash(script_hash))

    @classmethod
    def _get_exclusive_set(cls, account: 'AbstractAccount', subs: List[str]) -> set:
        # This returns the script hashes the given account is subscribed to, that no other
        # account is also subscribed to. This ensures that when we unsubscribe script hashes for
        # the given account, as the server subscription is shared between wallets, we only
        # unsubscribe if the script hash will no longer be needed for any account.
        subs_set = set(subs)
        for other_account, other_subs in cls._subs_by_account.items():
            if other_account == account:
                continue
            subs_set -= set(other_subs)
        return subs_set

    @classmethod
    async def unsubscribe_account(cls, account: 'AbstractAccount', session):
        subs = cls._subs_by_account.pop(account, None)
        if subs is None:
            return
        if not session:
            return
        exclusive_subs = cls._get_exclusive_set(account, subs)
        if not exclusive_subs:
            return

        if session.ptuple < (1, 4, 2):
            logger.debug("negotiated protocol does not support unsubscribing")
            return
        logger.debug(f"unsubscribing {len(exclusive_subs)} subscriptions for {account}")
        async with TaskGroup() as group:
            for script_hash in exclusive_subs:
                await group.spawn(session._unsubscribe_from_script_hash(script_hash))
        logger.debug(f"unsubscribed {len(exclusive_subs)} subscriptions for {account}")


class Network(TriggeredCallbacks):
    COIN = 100_000_000

    '''Manages a set of connections to remote ElectrumX servers.  All operations are
    asynchronous.
    '''


    async def send_request(self, method: str, params: list):
        session = await self._main_session()
        return await session.send_request(method, params)



    async def get_unspent(self, scripthash: str):
        try:
            return await self.send_request('blockchain.scripthash.get_utxos', [scripthash])
        except Exception as e:
            logger.warning(f'get_utxos failed: {e}, trying listunspent...')
            return await self.send_request('blockchain.scripthash.listunspent', [scripthash])

    def __init__(self, headers_path: str = None):
        self.logger = logging.getLogger("network")
        self.deserialized_header = None

        TriggeredCallbacks.__init__(self)

        # --- Load headers ---
        #PRELOADED_HEADERS = os.path.join(
        #    os.path.dirname(__file__), 'resources', 'headersloaded', 'headers'
        #)

        if headers_path:
            headers = self.load_custom_headers(headers_path)
        else:
            headers_file = app_state.config.file_path('headers')  # .electrum-sv/headers
            try:
                # Try reading existing headers
                app_state.read_headers()
                headers = app_state.headers
            except Exception as e:
                self.logger.warning(f"Failed to read headers file, replacing with preloaded one.")
                            
                # Remove old headers if it exists
                if os.path.exists(headers_file):
                    try:
                        os.remove(headers_file)
                    except Exception as e2:
                        self.logger.warning(f"Could not delete old headers file: {e2}")
                # Copy preloaded headers
                shutil.copyfile(PRELOADED_HEADERS, headers_file)
                # Retry reading
                app_state.read_headers()
                headers = app_state.headers

        # Initialize the network tip from local headers
        try:
            local_tip = headers.longest_chain().tip()
            if local_tip:
                self._tip = local_tip
            else:
                # fallback to genesis
                self._tip = headers.chain(0).tip()
        except Exception:
            self._tip = None

        # Mark sessions to skip checkpoint download
        SVSession._need_checkpoint_headers = False

        # --- Sessions ---
        self.sessions = []
        self.chosen_servers = set()
        self.main_server = None
        self.proxy = None

        # --- Events ---
        self.sessions_changed_event = app_state.async_.event()
        self.check_main_chain_event = app_state.async_.event()
        self.stop_network_event = app_state.async_.event()
        self.shutdown_complete_event = app_state.async_.event()

        # --- Jobs ---
        self.account_jobs = app_state.async_.queue()
        self._wallet_jobs = app_state.async_.queue()

        # --- Status queue ---
        self._on_status_queue = app_state.async_.queue()

        # --- Certs directory ---
        dir_path = app_state.config.file_path('certs')
        if not os.path.exists(dir_path):
            os.mkdir(dir_path)
            os.chmod(dir_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

        # --- Spawn main network task ---
        self.future = app_state.async_.spawn(self._main_task)
    async def request_headers_from_checkpoint(self, session: 'SVSession'):
        """Stub: headers are preloaded, so nothing to request."""
        # Optionally log for debugging
        local_tip = session.headers.tip.height if session.headers.tip else 0
        cp_height = Net.CHECKPOINT['height']
        if local_tip < cp_height:
            self.logger.info(f"Local tip {local_tip} below checkpoint {cp_height}, "
                             f"but headers are preloaded, skipping fetch")
        return



    async def _main_task(self):
        try:
            async with TaskGroup() as group:
                await group.spawn(self._start_network, group)
                await group.spawn(self._monitor_lagging_sessions)
                await group.spawn(self._monitor_main_chain)
                await group.spawn(self._monitor_accounts, group)
                await group.spawn(self._monitor_wallets, group)
        finally:
            self.shutdown_complete_event.set()
            app_state.config.set_key('servers', list(server for server in
                SVServer.all_servers.values() if server.host not in BAD_SERVER_HOSTS), True)

    async def _restart_network(self):
        self.stop_network_event.set()

    async def _start_network(self, group):
        while True:
            # Treat all servers as not used so connections are not delayed
            for server in SVServer.all_servers.values():
                server.state.retry_delay = 0

            if self.main_server is None:
                self.main_server, self.proxy = self._read_config()

            logger.debug('starting...')
            connections_task = await group.spawn(self._maintain_connections)
            await self.stop_network_event.wait()
            self.stop_network_event.clear()
            with suppress(CancelledError):
                await connections_task

    async def _maintain_connections(self):
        count = 1 if app_state.config.get('oneserver') else 10
        async with TaskGroup() as group:
            for n in range(0, count):
                await group.spawn(self._maintain_connection, n)

    async def _maintain_connection(self, n):
        # Connection 0 initially connects to the main_server.  main_server can change if
        # auto_connect is true, or the user specifies a new one in the network dialog.
        server = self.main_server if n == 0 else None
        while True:
            if server is self.main_server:
                self.trigger_callback('status')
            else:
                server = await self._random_server(self.main_server.protocol)

            self.chosen_servers.add(server)
            try:
                await server.connect(self, n)
            except (OSError, SOCKSError) as e:
                logger.warning(f'{server} connection error: {e}')
            finally:
                self.chosen_servers.remove(server)

            if server is self.main_server:
                await self._maybe_switch_main_server(SwitchReason.disconnected)

    async def _maybe_switch_main_server(self, reason):
        now = time.time()
        max_height = max((session.tip.height for session in self.sessions
            if session.tip is not None), default=0)
        for session in self.sessions:
            if session.tip is not None and session.tip.height > max_height - 2:
                session.server.state.last_good = now
        # Give a 60-second breather for a lagging server to catch up
        good_servers = [session.server for session in self.sessions
                        if session.server.state.last_good > now - 60]
        if not good_servers:
            logger.warning(f'no good servers available')
        elif self.main_server not in good_servers:
            if self.auto_connect():
                await self._set_main_server(random.choice(good_servers), reason)
            else:
                logger.warning(f'main server {self.main_server} is not good, but '
                               f'retaining it because auto-connect is off')

    async def _monitor_lagging_sessions(self):
        '''Monitor which sessions are lagging.

        If the main server is lagging switch the main server if auto_connect.
        '''
        while True:
            async with ignore_after(20):
                await self.sessions_changed_event.wait()
            await self._maybe_switch_main_server(SwitchReason.lagging)

    async def _monitor_wallets(self, group):
        tasks = {}
        while True:
            job, wallet = await self._wallet_jobs.get()
            if job == 'add':
                if wallet not in tasks:
                    tasks[wallet] = await group.spawn(self._maintain_wallet(wallet))
            elif job == 'remove':
                if wallet in tasks:
                    tasks.pop(wallet).cancel()
            elif job == 'undo_verifications':
                above_height = wallet
                for wallet in tasks:
                    wallet.undo_verifications(above_height)
            elif job == 'check_verifications':
                for wallet in tasks:
                    wallet.txs_changed_event.set()
            else:
                logger.error(f'unknown wallet job {job}')

    async def _monitor_accounts(self, group):
        account_tasks = {}
        while True:
            job, account = await self.account_jobs.get()
            if job == 'add':
                if account not in account_tasks:
                    account_tasks[account] = await group.spawn(self._maintain_account(account))
            elif job == 'remove':
                if account in account_tasks:
                    account_tasks.pop(account).cancel()
            else:
                logger.error(f'unknown account job {job}')

    async def _monitor_main_chain(self):
        main_chain = None
        while True:
            await self.check_main_chain_event.wait()
            self.check_main_chain_event.clear()
            main_session = await self._main_session()
            new_main_chain = main_session.chain
            if main_chain != new_main_chain and main_chain:
                _chain, above_height = main_chain.common_chain_and_height(new_main_chain)
                logger.info(f'main chain updated; undoing wallet verifications '
                            f'above height {above_height:,d}')
                await self._wallet_jobs.put(('undo_verifications', above_height))
            # It has been observed that we may receive headers after all the history events that
            # relate to the height of those headers. Queueing a check here will cover those new
            # headers and also due to sequential nature of jobs undo any existing ones first.
            await self._wallet_jobs.put(('check_verifications', None))
            main_chain = new_main_chain
            self.trigger_callback('updated')
            self.trigger_callback('main_chain', main_chain, new_main_chain)

    async def _set_main_server(self, server, reason):
        '''Set the main server to something new.'''
        assert isinstance(server, SVServer), f"got invalid server value: {server}"
        logger.info(f'switching main server to {server}: {reason.name}')
        old_main_session = self.main_session()
        self.main_server = server
        self.check_main_chain_event.set()
        main_session = self.main_session()
        # Disconnect the old main session, if any, in order to lose scripthash
        # subscriptions.
        if old_main_session:
            if reason == SwitchReason.user_set:
                old_main_session.server.retry_delay = 0
            await old_main_session.close()
        self.trigger_callback('status')


    def _read_config(self):
        # Remove obsolete key
        app_state.config.set_key('server_blacklist', None)

        # Priority server list (user-defined)
        priority_servers = [
            ('electrumx.gorillapool.io', {'s': '50002'}),            
            ('electrum.api.sv', {'s': '50002'}),
            ('neptune.api.sv', {'s': '50002'}),
            ('alpha-esv.api.sv', {'s': '50002'}),
            ('sv.satoshi.io', {'s': '50002'}),
            ('sv2.satoshi.io', {'s': '50002'}),
            ('esv.bitails.io', {'s': '50002'}),
            ('electrum.server.sv', {'s': '50002'}),
            ('alpha-esv.api.sv', {'s': '50002'}),
            ('bsv.aftrek.org', {'s': '50002'}),
        ]

        # Load priority servers into SVServer first
        for host, data in priority_servers:
            for protocol in 'st':
                if protocol in data:
                    SVServer.unique(host, data[protocol], protocol)

        # Load fallback defaults if needed
        count = len(SVServer.all_servers)
        logger.info(f'read {count:,d} servers from config file')
        if count < 5:
            for host, data in Net.DEFAULT_SERVERS.items():
                for protocol in 'st':
                    if protocol in data:
                        SVServer.unique(host, data[protocol], protocol)

        # Load main server from config or select one
        main_server = app_state.config.get('server', None)
        if isinstance(main_server, str):
            try:
                main_server = SVServer.from_string(main_server)
                app_state.config.set_key('server', main_server, True)
            except Exception:
                pass
        if not isinstance(main_server, SVServer):
            logger.info('choosing from prioritized server list')
            main_server = self._random_server_nowait('s')
            if not main_server:
                raise RuntimeError('no servers available')

        # Load proxy settings
        proxy = app_state.config.get('proxy', None)
        if isinstance(proxy, str):
            proxy = SVProxy.from_string(proxy)

        logger.info(f'main server: {main_server}; proxy: {proxy}')
        return main_server, proxy


    async def _request_transactions(self, wallet, missing_hashes: List[bytes]) -> bool:
        wallet.request_count += len(missing_hashes)
        wallet.progress_event.set()
        had_timeout = False
        session = await self._main_session()
        session.logger.debug(f'requesting {len(missing_hashes)} missing transactions')
        async with TaskGroup() as group:
            tasks = {}
            for tx_hash in missing_hashes:
                tx_id = hash_to_hex_str(tx_hash)
                tasks[await group.spawn(session.request_tx(tx_id))] = tx_hash

            while tasks:
                task = await group.next_done()
                wallet.response_count += 1
                wallet.progress_event.set()
                tx_hash = tasks.pop(task)
                tx_id = hash_to_hex_str(tx_hash)
                try:
                    tx_hex = task.result()
                    tx = Transaction.from_hex(tx_hex)
                    session.logger.debug(f'received tx {tx_id} bytes: {len(tx_hex)//2}')
                except CancelledError:
                    had_timeout = True
                except Exception as default_error:
                    logger.exception("fetching transaction %s", tx_id, exc_info=default_error)
                else:
                    wallet.add_transaction(tx_hash, tx, TxFlags.StateCleared | TxFlags.HasByteData,
                        True)
        return had_timeout

    # Removing duplicate code
    # async def get_unspent(self, scripthash: str):
        # '''Fetch list of unspent outputs for the given scripthash.'''
        # return await self.send_request('blockchain.scripthash.listunspent', [scripthash])


    def _available_servers(self, protocol):
        now = time.time()
        unchosen = set(server for server in SVServer.all_servers.values()
            if server.host not in BAD_SERVER_HOSTS).difference(self.chosen_servers)
        return [server for server in unchosen
                if server.protocol == protocol and server.state.can_retry(now)]

    def _random_server_nowait(self, protocol):
        servers = self._available_servers(protocol)
        return random.choice(servers) if servers else None

    async def _random_server(self, protocol):
        while True:
            server = self._random_server_nowait(protocol)
            if server:
                return server
            await sleep(10)

    async def _request_proofs(self, wallet: 'Wallet', wanted_map) -> bool:
        had_timeout = False
        session = await self._main_session()
        session.logger.debug(f'requesting {len(wanted_map)} proofs')
        async with TaskGroup() as group:
            tasks = {}
            for tx_hash, tx_height in wanted_map.items():
                tx_id = hash_to_hex_str(tx_hash)
                tasks[await group.spawn(session.request_proof(tx_id, tx_height))] = (tx_hash,
                    tx_id)
            headers = await session.headers_at_heights(wanted_map.values())

            while tasks:
                task = await group.next_done()
                tx_hash, tx_id = tasks.pop(task)
                tx_height = wanted_map[tx_hash]
                try:
                    result = task.result()
                    branch = [hex_str_to_hash(item) for item in result['merkle']]
                    tx_pos = result['pos']
                    proven_root = _root_from_proof(tx_hash, branch, tx_pos)
                    header = headers[wanted_map[tx_hash]]
                except CancelledError:
                    had_timeout = True
                except Exception as e:
                    logger.error(f'getting proof for {tx_id}: {e}')
                else:
                    if header.merkle_root == proven_root:
                        logger.debug(f'received valid proof for {tx_id}')
                        wallet.add_transaction_proof(tx_hash, tx_height, header.timestamp, tx_pos,
                            tx_pos, branch)
                    else:
                        hhts = hash_to_hex_str
                        logger.error(f'invalid proof for tx {tx_id} in block '
                                     f'{hhts(header.hash)}; got {hhts(proven_root)} expected '
                                     f'{hhts(header.merkle_root)}')
        return had_timeout

    async def _monitor_on_status(self, group):
        """worker task to process new aiorpcx 'Notifications' from queue"""
        while True:
            session = await self._main_session()
            script_hash, status = await self._on_status_queue.get()
            await group.spawn(session._on_status_changed, script_hash, status)

    async def _monitor_txs(self, wallet: 'Wallet') -> None:
        '''Raises: RPCError, BatchError, TaskTimeout, DisconnectSessionError'''
        # When the wallet receives notification of new transactions, it signals that this
        # monitoring loop should awaken. The loop retrieves all outstanding transaction data and
        # proofs in parallel. However, the prerequisite for needing a proof for a transaction is
        # first having it's data. So after fetching transactions, it becomes necessary to fetch
        # proofs again, and loop at least twice. So this loop only blocks if it knows for sure
        # there are no outstanding needs for either transaction data or proof.
        while True:
            # The set of transactions we know about, but lack the actual transaction data for.
            wanted_tx_map = wallet.missing_transactions()
            # The set of transactions we have data for, but not proof for.
            wanted_proof_map = wallet.unverified_transactions()

            coros = []
            if wanted_tx_map:
                coros.append(self._request_transactions(wallet, wanted_tx_map))
            if wanted_proof_map:
                coros.append(self._request_proofs(wallet, wanted_proof_map))
            if not coros:
                for account in wallet.get_accounts():
                    account.poll_used_key_detection(every_n_seconds=20)

                await wallet.txs_changed_event.wait()
                wallet.txs_changed_event.clear()

            async with TaskGroup() as group:
                for coro in coros:
                    await group.spawn(coro)

    async def _monitor_active_keys(self, account) -> None:
        '''Raises: RPCError, TaskTimeout'''
        session = await self._main_session()
        additional_keys = set(account.existing_active_keys())
        while True:
            session.logger.info(f'subscribing to {len(additional_keys):,d} new keys for {account}')
            # Do in reverse to require fewer account re-sync loops
            pairs = [ (k, script_type, scripthash_hex(script)) for k in additional_keys
                for script_type, script in account.get_possible_scripts_for_id(k) ]
            pairs.reverse()
            await session.subscribe_to_triples(account, pairs)
            additional_keys = await account.new_activated_keys()
            session = await self._main_session()

    async def _monitor_inactive_keys(self, account) -> None:
        '''Raises: RPCError, TaskTimeout'''
        while True:
            keys = await account.new_deactivated_keys()
            session = await self._main_session()
            session.logger.info(f'unsubscribing from {len(keys):,d} '+
                f'deactivated keys for {account}')
            pairs = [ (k, script_type, scripthash_hex(script)) for k in keys
                for script_type, script in account.get_possible_scripts_for_id(k) ]
            await session.unsubscribe_from_pairs(account, pairs)

    async def _maintain_wallet(self, wallet: 'Wallet') -> None:
        '''Put all tasks for a single wallet in a group so they can be cancelled together.'''
        logger.info('maintaining wallet %s', wallet)
        try:
            while True:
                try:
                    async with TaskGroup() as group:
                        await group.spawn(self._monitor_txs, wallet)
                except (RPCError, BatchError, DisconnectSessionError, TaskTimeout) as error:
                    blacklist = isinstance(error, DisconnectSessionError) and error.blacklist
                    session = self.main_session()
                    if session:
                        await session.disconnect(str(error), blacklist=blacklist)
        finally:
            logger.info('stopped maintaining %s', wallet)

    async def _maintain_account(self, account):
        '''Put all tasks for a single account in a group so they can be cancelled together.'''
        logger.info(f'maintaining account {account}')
        try:
            while True:
                logger.info(f'renewing maintaining account {account}')
                try:
                    async with TaskGroup() as group:
                        await group.spawn(self._monitor_active_keys, account)
                        await group.spawn(self._monitor_inactive_keys, account)
                        await group.spawn(account.synchronize_loop)
                        await group.spawn(self._monitor_on_status, group)

                        # None of the above bvlock on things that necessarily are network events.
                        # So we explicitly detect that and handle it.
                        session = await self._main_session()
                        await session._closed_event.wait()
                        await group.cancel_remaining()
                except (RPCError, BatchError, DisconnectSessionError, TaskTimeout) as error:
                    blacklist = isinstance(error, DisconnectSessionError) and error.blacklist
                    session = self.main_session()
                    if session:
                        await session.disconnect(str(error), blacklist=blacklist)
                if account in SVSession._subs_by_account:
                    del SVSession._subs_by_account[account]
                    account.request_count = 0
                    account.response_count = 0
                    wallet = account.get_wallet()
                    wallet.request_count = 0
                    wallet.response_count = 0
        finally:
            await SVSession.unsubscribe_account(account, self.main_session())
            logger.info(f'stopped maintaining account {account}')

    async def _main_session(self):
        while True:
            session = self.main_session()
            if session:
                return session
            await self.sessions_changed_event.wait()

    async def _random_session(self):
        while not self.sessions:
            logger.info('waiting for new session')
            await self.sessions_changed_event.wait()
        return random.choice(self.sessions)

    #
    # API exposed to SVSession
    #

    async def session_established(self, session):
        self.sessions.append(session)
        self.sessions_changed_event.set()
        self.sessions_changed_event.clear()
        self.trigger_callback('sessions')
        if session.server is self.main_server:
            self.trigger_callback('status')
            return True
        return False

    async def session_closed(self, session):
        self.sessions.remove(session)
        self.sessions_changed_event.set()
        self.sessions_changed_event.clear()
        if session.server is self.main_server:
            self.trigger_callback('status')
        self.trigger_callback('sessions')

    #
    # External API
    #

    async def shutdown_wait(self):
        self.future.cancel()
        await self.shutdown_complete_event.wait()
        if len(self.sessions) != 0:
            logger.debug("Networks shutdown leaked %d sessions", len(self.sessions))
        logger.debug('stopped')

    def auto_connect(self):
        return app_state.config.get('auto_connect', True)

    def is_connected(self):
        return self.main_session() is not None

    def main_session(self):
        '''Returns the session, if any, connected to main_server.'''
        for session in self.sessions:
            if session.server is self.main_server:
                return session
        return None

    def get_servers(self):
        return SVServer.all_servers.values()

    def add_wallet(self, wallet: 'Wallet') -> None:
        app_state.async_.spawn(self._wallet_jobs.put, ('add', wallet))

    def remove_wallet(self, wallet: 'Wallet') -> None:
        app_state.async_.spawn(self._wallet_jobs.put, ('remove', wallet))

    def add_account(self, account):
        app_state.async_.spawn(self.account_jobs.put, ('add', account))

    def remove_account(self, account):
        app_state.async_.spawn(self.account_jobs.put, ('remove', account))

    def chain(self):
        main_session = self.main_session()
        if main_session:
            return main_session.chain
        return app_state.headers.longest_chain()

    def get_local_height(self) -> int:
        chain = self.chain()
        # This can be called from network_dialog.py when there is no chain
        return chain.height if chain else 0

    def get_server_height(self) -> int:
        main_session = self.main_session()
        if main_session and main_session.tip:
            return main_session.tip.height
        return 0

    def backfill_headers_at_heights(self, heights: List[int]) -> None:
        app_state.async_.spawn(self._backfill_headers_at_heights, heights)

    async def _backfill_headers_at_heights(self, heights: List[int]) -> None:
        main_session = self.main_session()
        if main_session:
            await main_session._request_headers_at_heights(heights)
            self.trigger_callback('on_header_backfill')

    def set_server(self, server, auto_connect) -> None:
        config = app_state.config
        config.set_key('server', server, True)
        if config.get('server') is server:
            app_state.config.set_key('auto_connect', auto_connect, False)
            app_state.async_.spawn(self._set_main_server, server, SwitchReason.user_set)

    def set_proxy(self, proxy) -> None:
        if str(proxy) == str(self.proxy):
            return
        app_state.config.set_key("proxy", proxy, False)
        # See if config accepted the update
        if str(app_state.config.get('proxy')) == str(proxy):
            self.proxy = proxy
            logger.info(f"Set proxy to {proxy}")
            app_state.async_.spawn(self._restart_network)

    def sessions_by_chain(self):
        '''Return a map {chain: sessions} for each chain being followed by any session.'''
        result = defaultdict(list)
        for session in self.sessions:
            if session.chain:
                result[session.chain].append(session)
        return result

    def status(self) -> Dict[str, Any]:
        return {
            'server': str(self.main_server),
            'blockchain_height': self.get_local_height(),
            'server_height': self.get_server_height(),
            'spv_nodes': len(self.sessions),
            'connected': self.is_connected(),
            'auto_connect': self.auto_connect(),
        }

    # FIXME: this should be removed; its callers need to be fixed
    def request_and_wait(self, method, args):
        async def send_request():
            # We'll give 10 seconds for the wallet to reconnect..
            async with timeout_after(10):
                session = await self._main_session()
            return await session.send_request(method, args)

        return app_state.async_.spawn_and_wait(send_request)

    def broadcast_transaction_and_wait(self, transaction: Union[str, Transaction]) -> str:
        print(f"broadcast_transaction_and_wait called with type: {type(transaction)}")
        if isinstance(transaction, str):
            # transaction is already a hex string
            tx_hex = transaction
            print("Using tx hex string directly")
        else:
            serialized = transaction.serialize()
            print(f"Serialized transaction type: {type(serialized)}")
            if isinstance(serialized, bytes):
                tx_hex = serialized.hex()
            elif isinstance(serialized, str):
                # serialize() returned a hex string already
                tx_hex = serialized
            else:
                raise TypeError(f"Unexpected type from serialize(): {type(serialized)}")
            print("Prepared tx_hex:", tx_hex[:20] + "...")

        return self.request_and_wait('blockchain.transaction.broadcast', [tx_hex])



    def create_checkpoint(self, height=None):
        '''Handy utility to dump a checkpoint for networks.py when preparing a new release.'''
        headers_obj = app_state.headers
        chain = headers_obj.longest_chain()
        if height is None:
            height = max(0, chain.height - 6)
        prev_work = headers_obj.chainwork_to_height(chain, height - 1)
        header_info = self.request_and_wait('blockchain.block.header', [height, height])
        header_hex = header_info['header']
        merkle_root = header_info['root']

        print(
            f"    CHECKPOINT = CheckPoint(bytes.fromhex(\n"
            f"        '{header_hex[:80]}'\n"
            f"        '{header_hex[80:]}'\n"
            f"    ), height={height}, prev_work={hex(prev_work)})\n"
            f"\n"
            f"    VERIFICATION_BLOCK_MERKLE_ROOT = (\n"
            f"        '{merkle_root}'\n"
            f"    )\n"
        )


JSON.register(SVServerState, SVServer, SVProxy)
