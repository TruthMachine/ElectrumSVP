import ssl
import socket
import json
import os
import hashlib
import sqlite3
import random
import platform
from bitcoinx import Address, Bitcoin
from PyQt5.QtWidgets import (
    QWidget, QPushButton, QVBoxLayout, QTextEdit, QDialog, QLabel,
    QHBoxLayout, QSizePolicy, QApplication, QFileDialog, QScrollArea, QCheckBox, QSplitter
)
from PyQt5.QtCore import Qt
from electrumsv.gui.qt.util import read_QIcon
from electrumsv.i18n import _

from concurrent.futures import ThreadPoolExecutor
import time

from bitcoinx import hex_str_to_hash

from electrumsv.bitcoin import scripthash_hex

from electrumsv.app_state import app_state

import time

# --- ElectrumX servers ---
SERVERS = {
    "sv.electrumr.sv": {"s": 50002},
    "electrumx.gorillapool.io": {"s": 50002},
    "sv2.electrumr.sv": {"s": 50002},
    "sv2.satoshi.io": {"s": 50002},
    "sv.satoshi.io": {"s": 50002},
    "sv3.satoshi.io": {"s": 50002},
    "electrum.server.sv": {"s": 50002},
    "bsv.aftrek.org": {"s": 50002},
    #"electrum.api.sv": {"s": 50002},
    #"neptune.api.sv": {"s": 50002},
    #"alpha-esv.api.sv": {"s": 50002},
}

TIMEOUT = 5  # seconds

# --- Paths & platform user dir ---
def user_data_dir() -> str:
    """Return platform-specific data directory for ElectrumSV (writable by user)."""
    system = platform.system()
    if system == "Darwin":
        path = os.path.expanduser("~/.electrum-sv")
    elif system == "Windows":
        path = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ElectrumSV")
    else:  # Linux / other Unix
        path = os.path.expanduser("~/.electrum-sv")

    try:
        os.makedirs(path, exist_ok=True)
    except PermissionError:
        # Very defensive: fallback to temp dir if user dir not writable
        import tempfile
        path = os.path.join(tempfile.gettempdir(), "electrumsv")
        os.makedirs(path, exist_ok=True)
    return path

# Default cache dir under user data dir
DATA_DIR = user_data_dir()
CACHE_DIR = os.path.join(DATA_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# Legacy possible header paths (kept from original)
POSSIBLE_HEADER_PATHS = [
    os.path.join(DATA_DIR, "headers"),                     # preferred, cross-platform
    os.path.expanduser("~/.electrum-sv/headers"),
    os.path.expanduser("~/.electrum-sv/headers-electrumsv"),
    os.path.expanduser("~/.electrumsv/headers"),
]

def find_headers_file():
    """Find the headers file in the preferred location or legacy locations."""
    for path in POSSIBLE_HEADER_PATHS:
        if os.path.exists(path) and os.path.isfile(path):
            return path
    return None

def get_headers_path():
    """Dynamically find headers file each time (fixes stale path issue)."""
    return find_headers_file()

# Cache paths under the platform-specific application data directory.
MERKLE_CACHE_PATH = os.path.join(CACHE_DIR, "merkle_cache.json")
ADDRESS_BEEF_CACHE_PATH = os.path.join(CACHE_DIR, "merkle_address_cache.json")

# --- Utilities ---
def extract_time_from_header(header_bytes: bytes) -> int:
    """Extract block timestamp from 80-byte header."""
    if len(header_bytes) != 80:
        raise ValueError("Header must be 80 bytes")
    return int.from_bytes(header_bytes[68:72], byteorder="little")

def get_local_header(height):
    """
    Return the raw 80-byte Bitcoin block header for a height
    from the local ElectrumSV headers file.
    """

    if height is None or height <= 0:
        return None

    try:
        header = read_header_from_file(height)

        if header is None:
            print("LOCAL HEADER NOT FOUND:", height)
            return None

        if isinstance(header, str):
            header = bytes.fromhex(header)

        if len(header) != 80:
            print(
                "INVALID LOCAL HEADER LENGTH:",
                height,
                len(header),
            )
            return None

        return header

    except Exception as e:
        print(
            "LOCAL HEADER ERROR:",
            height,
            repr(e),
        )
        return None


def get_local_address_utxos(account, address):
    """
    Return unspent wallet outputs belonging to the given address.

    Uses the local ElectrumSV wallet SQLite database instead of
    blockchain.scripthash.listunspent.

    Returns rows in the same general shape needed by
    build_beef_for_address().
    """

    # ---------------------------------------------------------
    # Resolve wallet database
    # ---------------------------------------------------------

    wallet_db_path = None

    try:
        if account is not None:
            wallet = account._wallet

            if wallet is not None:
                storage = wallet.get_storage()

                if storage is not None:
                    path = storage.get_path()

                    if path:
                        if path.lower().endswith(".sqlite"):
                            wallet_db_path = path
                        else:
                            wallet_db_path = path + ".sqlite"

    except Exception as e:
        print(
            "Could not resolve wallet database from account:",
            repr(e),
        )

    if wallet_db_path is None:
        try:
            from electrumsv.app_state import app_state

            config = app_state.config
            wallet_path = config.get_cmdline_wallet_filepath()

            if wallet_path:
                if wallet_path.lower().endswith(".sqlite"):
                    wallet_db_path = wallet_path
                else:
                    wallet_db_path = wallet_path + ".sqlite"

        except Exception as e:
            print(
                "Could not resolve configured wallet path:",
                repr(e),
            )

    if not wallet_db_path:
        print("No wallet database path available.")
        return []

    # ---------------------------------------------------------
    # Address -> Electrum scripthash
    # ---------------------------------------------------------

    scripthash = scripthash_from_address(address)

    try:
        scripthash_bytes = bytes.fromhex(scripthash)
    except Exception as e:
        print(
            "Invalid scripthash:",
            scripthash,
            repr(e),
        )
        return []

    # ElectrumSV TransactionOutputFlag.IS_SPENT = 1 << 0
    IS_SPENT = 1 << 0

    # ---------------------------------------------------------
    # Find wallet outputs for this script
    # ---------------------------------------------------------

    try:
        conn = sqlite3.connect(wallet_db_path)

        try:
            rows = conn.execute(
                """
                SELECT
                    txo.tx_hash,
                    txo.tx_index,
                    txo.value,
                    txo.keyinstance_id,
                    txo.flags,
                    t.block_height,
                    t.block_position
                FROM TransactionOutputs AS txo
                JOIN Transactions AS t
                    ON t.tx_hash = txo.tx_hash
                JOIN KeyInstanceScripts AS kis
                    ON kis.keyinstance_id = txo.keyinstance_id
                WHERE kis.script_hash = ?
                  AND (txo.flags & ?) = 0
                ORDER BY
                    t.block_height,
                    txo.tx_index
                """,
                (
                    scripthash_bytes,
                    IS_SPENT,
                ),
            ).fetchall()

        finally:
            conn.close()

        results = []

        for row in rows:

            (
                tx_hash_bytes,
                vout,
                value,
                keyinstance_id,
                flags,
                block_height,
                block_position,
            ) = row

            # SQLite stores the transaction hash in internal byte
            # order, while BREAD uses the normal displayed TXID.
            txid = tx_hash_bytes[::-1].hex()

            results.append({
                "txid": txid,
                "vout": vout,
                "satoshis": value,
                "keyinstance_id": keyinstance_id,
                "flags": flags,
                "blockheight": (
                    block_height
                    if block_height is not None
                    else -1
                ),
                "block_position": block_position,
            })


        return results

    except Exception as e:
        print(
            "LOCAL ADDRESS UTXO ERROR:",
            repr(e),
        )
        return []



def scripthash_from_address(address: str) -> str:
    addr = Address.from_string(address, Bitcoin)
    script_bytes = addr.to_script().to_bytes()
    return hashlib.sha256(script_bytes).digest()[::-1].hex()


def load_cache(path):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2)


CACHE = load_cache(MERKLE_CACHE_PATH)
ADDRESS_CACHE = load_cache(ADDRESS_BEEF_CACHE_PATH)


# Requests are sent to all ElectrumX servers in parallel.
# Return immediately when the first valid response arrives.
# Do not use ThreadPoolExecutor as a context manager here,
# because exiting the context would wait for all running
# requests to finish and defeat the purpose of the timeout/failover design.


def electrum_request(method, params, servers=None):
    import socks
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if servers is None:
        servers = SERVERS

    clean_params = [
        p.hex() if isinstance(p, bytes) else p.lower() if isinstance(p, str) else p
        for p in params
    ]

    server_items = list(servers.items())
    random.shuffle(server_items)

    use_tor = (
        os.path.exists("/etc/os-release")
        and "TAILS" in open("/etc/os-release").read().upper()
    )

    tor_proxy = ("127.0.0.1", 9050)

    if not use_tor:
        try:
            with socket.create_connection(tor_proxy, timeout=1):
                use_tor = True
        except Exception:
            pass

    def try_server(item):
        host, info = item
        port = info.get("s")

        if not port:
            return None

        sock = None
        ssock = None
        attempt_start = time.perf_counter()

        try:
            req = {
                "id": 0,
                "method": method,
                "params": clean_params,
            }

            msg = (json.dumps(req) + "\n").encode()

            if use_tor:
                sock = socks.socksocket()
                sock.set_proxy(
                    socks.SOCKS5,
                    tor_proxy[0],
                    tor_proxy[1],
                )
                sock.settimeout(TIMEOUT)
                sock.connect((host, port))
            else:
                sock = socket.create_connection(
                    (host, port),
                    timeout=TIMEOUT,
                )

            context = ssl._create_unverified_context()

            ssock = context.wrap_socket(
                sock,
                server_hostname=host,
            )

            ssock.settimeout(TIMEOUT)
            ssock.sendall(msg)

            data = b""

            while not data.endswith(b"\n"):
                chunk = ssock.recv(4096)

                if not chunk:
                    break

                data += chunk

            if not data:
                raise ConnectionError("Empty response")

            response = json.loads(data.decode())

            # Treat JSON-RPC errors as failures so another
            # ElectrumX server can be used instead.
            if response.get("error") is not None:
                raise ConnectionError(
                    f"ElectrumX error: {response['error']}"
                )

            elapsed = time.perf_counter() - attempt_start


            return response

        except Exception as e:
            elapsed = time.perf_counter() - attempt_start

            print(
                f"[ELECTRUMX] FAIL {method} -> "
                f"{host}:{port} (S) "
                f"after {elapsed:.3f}s: {repr(e)}"
            )

            return None

        finally:
            try:
                if ssock is not None:
                    ssock.close()
                elif sock is not None:
                    sock.close()
            except Exception:
                pass

    executor = ThreadPoolExecutor(
        max_workers=len(server_items)
    )

    futures = [
        executor.submit(try_server, item)
        for item in server_items
    ]

    try:
        for future in as_completed(futures):
            result = future.result()

            if result is not None:
                # Cancel anything that has not started yet.
                for other_future in futures:
                    if other_future is not future:
                        other_future.cancel()

                # IMPORTANT:
                # Do not wait for the other running requests.
                executor.shutdown(
                    wait=False,
                    cancel_futures=True,
                )

                return result

        # Every server failed.
        executor.shutdown(
            wait=False,
            cancel_futures=True,
        )

        raise ConnectionError(
            f"All servers failed for method "
            f"{method} with params {params}"
        )

    except Exception:
        executor.shutdown(
            wait=False,
            cancel_futures=True,
        )
        raise

# --- crypto/merkle helpers (from original) ---
def double_sha256(b: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


def le(hex_str: str) -> bytes:
    return bytes.fromhex(hex_str)[::-1]


def compute_merkle_root(txid: str, merkle_branch: list, pos: int) -> bytes:
    h = le(txid)
    for branch_hex in merkle_branch:
        branch = le(branch_hex)
        h = double_sha256(h + branch) if pos & 1 == 0 else double_sha256(branch + h)
        pos >>= 1
    return h


def merkle_root_from_header(header_bytes: bytes) -> bytes:
    return header_bytes[36:68]


def verify_merkle(txid: str, merkle_proof: dict, header_hex: str) -> bool:
    if not merkle_proof:
        return False

    if not header_hex:
        return False

    try:
        computed_root = compute_merkle_root(
            txid,
            merkle_proof["merkle"],
            merkle_proof["pos"],
        )

        header_root = bytes.fromhex(header_hex)[36:68]

        return computed_root == header_root

    except Exception as e:
        print("verify_merkle exception:", repr(e))
        return False



# --- Header reading: keep the Linux-working behavior (seek height*80),
# but use HEADERS_PATH detected above. Do NOT create or overwrite the headers file. ---
def read_header_from_file(height: int) -> bytes:
    headers_path = get_headers_path()
    if headers_path is None:
        raise FileNotFoundError(
            "No local headers file found. Expected one of: " + ", ".join(POSSIBLE_HEADER_PATHS)
        )

    with open(headers_path, "rb") as f:
        f.seek(height * 80)
        header = f.read(80)
        if len(header) != 80:
            raise ValueError(f"Header for block {height} not found (file too short or truncated).")
        return header


def fetch_merkle(account, txid_, height, retries=3, delay=0.5):
    """
    Fetch a Merkle proof from the wallet's SQLite transaction cache first.
    Fall back to ElectrumX if the proof is not locally available.
    """
    if account is not None:
        try:
            wallet = account._wallet
            tx_hash = hex_str_to_hash(txid_)
            cached = wallet.get_transaction_proof(tx_hash)

            if cached is not None:

                return {
                    "block_height": height,
                    "pos": cached.position,
                    "merkle": [h[::-1].hex() for h in cached.branch],
                }

        except Exception as e:
            print(f"SQLite proof exception for {txid_}: {e}")

    # Fall back to ElectrumX only when SQLite has no proof.
    for _ in range(retries):
        try:
            proof = electrum_request(
                "blockchain.transaction.get_merkle",
                [txid_, height],
            ).get("result")

            if proof:
                return proof

        except Exception as e:
            print(f"ElectrumX merkle exception for {txid_}: {e}")

        time.sleep(delay)

    return None

def fetch_electrumx_header(height):
    try:
        response = electrum_request(
            "blockchain.block.header",
            [height],
        )

        header_hex = response.get("result")

        if header_hex:
            return header_hex

    except Exception as e:
        print(f"ElectrumX header exception at {height}: {e}")

    return None



# ============================================================
# BEEF dependency-chain helpers
# ============================================================

def _read_varint(data: bytes, offset: int):
    """Read a Bitcoin compact-size integer."""
    if offset >= len(data):
        raise ValueError("Unexpected end of transaction while reading varint")

    value = data[offset]
    offset += 1

    if value < 0xfd:
        return value, offset

    if value == 0xfd:
        if offset + 2 > len(data):
            raise ValueError("Truncated uint16 varint")
        return int.from_bytes(data[offset:offset + 2], "little"), offset + 2

    if value == 0xfe:
        if offset + 4 > len(data):
            raise ValueError("Truncated uint32 varint")
        return int.from_bytes(data[offset:offset + 4], "little"), offset + 4

    if offset + 8 > len(data):
        raise ValueError("Truncated uint64 varint")

    return int.from_bytes(data[offset:offset + 8], "little"), offset + 8


def _parse_tx_dependencies(tx_hex: str):
    """
    Parse enough of a legacy Bitcoin transaction to identify:

        input -> previous txid + previous output index

    Also parses output count so we can sanity-check referenced vouts.

    Returns:
        {
            "inputs": [
                {
                    "prev_txid": "...display byte order...",
                    "prev_vout": 0
                },
                ...
            ],
            "output_count": N,
            "output_values": [...]
        }

    BSV transactions are legacy transactions, so we intentionally keep
    this parser simple and do not add SegWit parsing.
    """
    data = bytes.fromhex(tx_hex)
    offset = 0

    if len(data) < 4:
        raise ValueError("Transaction too short")

    # Version
    offset += 4

    input_count, offset = _read_varint(data, offset)

    inputs = []

    for _ in range(input_count):
        if offset + 36 > len(data):
            raise ValueError("Truncated transaction input")

        # Transaction serialization stores hashes in internal byte order.
        prev_hash_internal = data[offset:offset + 32]
        offset += 32

        prev_txid = prev_hash_internal[::-1].hex()

        prev_vout = int.from_bytes(
            data[offset:offset + 4],
            "little",
        )
        offset += 4

        script_len, offset = _read_varint(data, offset)

        if offset + script_len > len(data):
            raise ValueError("Truncated scriptSig")

        offset += script_len

        if offset + 4 > len(data):
            raise ValueError("Truncated sequence")

        offset += 4  # sequence

        # Coinbase input has a null previous txid/index.
        if prev_hash_internal == b"\x00" * 32:
            prev_txid = None

        inputs.append({
            "prev_txid": prev_txid,
            "prev_vout": prev_vout,
        })

    output_count, offset = _read_varint(data, offset)

    output_values = []

    for _ in range(output_count):
        if offset + 8 > len(data):
            raise ValueError("Truncated transaction output")

        value = int.from_bytes(
            data[offset:offset + 8],
            "little",
        )
        offset += 8

        script_len, offset = _read_varint(data, offset)

        if offset + script_len > len(data):
            raise ValueError("Truncated scriptPubKey")

        offset += script_len

        output_values.append(value)

    return {
        "inputs": inputs,
        "output_count": output_count,
        "output_values": output_values,
    }


def _txid_from_hex(tx_hex: str) -> str:
    """Calculate normal displayed TXID from raw transaction hex."""
    raw = bytes.fromhex(tx_hex)
    digest = hashlib.sha256(hashlib.sha256(raw).digest()).digest()
    return digest[::-1].hex()


def _get_wallet_db_path_for_beef(account):
    """Resolve the wallet SQLite path without depending on the caller."""
    try:
        if account is not None:
            wallet = account._wallet

            if wallet is not None:
                storage = wallet.get_storage()

                if storage is not None:
                    path = storage.get_path()

                    if path:
                        if path.lower().endswith(".sqlite"):
                            return path

                        return path + ".sqlite"

    except Exception as e:
        print(
            "Could not resolve wallet database from account:",
            repr(e),
        )

    try:
        from electrumsv.app_state import app_state

        config = app_state.config
        wallet_path = config.get_cmdline_wallet_filepath()

        if wallet_path:
            if wallet_path.lower().endswith(".sqlite"):
                return wallet_path

            return wallet_path + ".sqlite"

    except Exception as e:
        print(
            "Could not resolve configured wallet path:",
            repr(e),
        )

    return None


def _get_wallet_tx_metadata(wallet_db_path, txid):
    if not wallet_db_path:
        return None

    try:
        tx_hash_bytes = bytes.fromhex(txid)[::-1]

        conn = sqlite3.connect(wallet_db_path)

        try:
            row = conn.execute(
                """
                SELECT
                    block_height,
                    block_position
                FROM Transactions
                WHERE tx_hash = ?
                """,
                (tx_hash_bytes,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None

        block_height, block_position = row

        return {
            "blockheight": (
                block_height
                if block_height is not None
                else -1
            ),
            "block_position": block_position,
        }

    except Exception as e:
        print(
            "WALLET TX METADATA ERROR:",
            txid,
            repr(e),
        )
        return None


def _get_transaction_hex_for_beef(account, txid):
    """
    Get raw transaction hex.

    Order:
      1. Wallet transaction object
      2. Existing CACHE
      3. ElectrumX blockchain.transaction.get
    """
    # Wallet
    if account is not None:
        for key in (txid, bytes.fromhex(txid)):
            try:
                tx_obj = account.get_transaction(key)

                if tx_obj:
                    hex_str = tx_obj.serialize().hex()

                    if hex_str:
                        return hex_str

            except Exception:
                continue

    # Existing cache
    cached = CACHE.get(txid, {})

    if cached:
        hex_str = cached.get("hex")

        if hex_str:
            return hex_str

    # ElectrumX
    try:
        response = electrum_request(
            "blockchain.transaction.get",
            [txid],
        )

        hex_str = response.get("result")

        if hex_str:
            return hex_str

    except Exception as e:
        print(
            "TRANSACTION HEX FETCH ERROR:",
            txid,
            repr(e),
        )

    return None


def _get_proof_for_transaction(
    account,
    wallet_db_path,
    txid,
    retries=3,
    delay=0.5,
):
    """
    Try to obtain a proof for a transaction.

    We first use the wallet's native proof cache. That is important
    for historical transactions.

    If that misses, obtain the block height from the wallet DB and
    ask ElectrumX for a proof at that height.

    Returns:
        (proof, blockheight)
    """
    # ---------------------------------------------------------
    # 1. Native ElectrumSV transaction-proof cache
    # ---------------------------------------------------------

    if account is not None:
        try:
            wallet = account._wallet
            tx_hash = hex_str_to_hash(txid)

            cached = wallet.get_transaction_proof(tx_hash)

            if cached is not None:

                metadata = _get_wallet_tx_metadata(
                    wallet_db_path,
                    txid,
                )

                blockheight = (
                    metadata["blockheight"]
                    if metadata
                    else -1
                )

                proof = {
                    "block_height": blockheight,
                    "pos": cached.position,
                    "merkle": [
                        h[::-1].hex()
                        for h in cached.branch
                    ],
                }

                return proof, blockheight

        except Exception as e:
            print(
                "DEPENDENCY SQLITE PROOF ERROR:",
                txid,
                repr(e),
            )

    # ---------------------------------------------------------
    # 2. Determine block height from wallet DB
    # ---------------------------------------------------------

    metadata = _get_wallet_tx_metadata(
        wallet_db_path,
        txid,
    )

    blockheight = (
        metadata["blockheight"]
        if metadata
        else -1
    )

    if blockheight <= 0:
        print(
            "No known confirmed height for:",
            txid,
        )
        return None, blockheight

    # ---------------------------------------------------------
    # 3. ElectrumX Merkle proof
    # ---------------------------------------------------------

    for attempt in range(retries):

        try:
            proof = electrum_request(
                "blockchain.transaction.get_merkle",
                [txid, blockheight],
            ).get("result")

            if proof:
                return proof, blockheight

        except Exception as e:
            print(
                "DEPENDENCY ELECTRUMX MERKLE ERROR:",
                txid,
                "attempt=",
                attempt + 1,
                repr(e),
            )

        time.sleep(delay)

    print(
        "NO MERKLE PROOF AVAILABLE:",
        txid,
        "height=",
        blockheight,
    )

    return None, blockheight



def _is_internal_wallet_transaction(
    account,
    tx_hex,
    wallet_db_path,
):
    """
    Return True if at least one non-coinbase input of the transaction
    spends an output that is known to belong to this wallet.

    External incoming transactions normally spend outputs that are
    not present in this wallet's TransactionOutputs table.
    """
    if not wallet_db_path:
        return False

    try:
        parsed = _parse_tx_dependencies(tx_hex)

        conn = sqlite3.connect(wallet_db_path)

        try:
            for tx_input in parsed["inputs"]:
                parent_txid = tx_input["prev_txid"]
                parent_vout = tx_input["prev_vout"]

                # Coinbase input.
                if parent_txid is None:
                    continue

                parent_hash = bytes.fromhex(parent_txid)[::-1]

                row = conn.execute(
                    """
                    SELECT 1
                    FROM TransactionOutputs
                    WHERE tx_hash = ?
                      AND tx_index = ?
                    LIMIT 1
                    """,
                    (
                        parent_hash,
                        parent_vout,
                    ),
                ).fetchone()

                if row is not None:
                    return True

        finally:
            conn.close()

    except Exception as e:
        print(
            "INTERNAL TRANSACTION DETECTION ERROR:",
            repr(e),
        )

    return False



def _build_dependency_chain(
    account,
    subject_txid,
    subject_hex,
    wallet_db_path,
    retries=3,
    delay=0.5,
    max_depth=20,
    max_transactions=500,
    max_inputs_per_tx=100,
    max_seconds=15,
):
    """
    Walk backwards from the subject transaction.

    The resulting list is ordered oldest -> newest.

    Confirmed/proven transactions become proof anchors.
    Unconfirmed transactions are retained without a Merkle proof.

    Each item has roughly:

        {
            "txid": "...",
            "hex": "...",
            "blockheight": N,
            "merkle": {...} or None,
            "header": "..." or None,
            "confirmed": bool,
            "proof_available": bool,
            "depends_on": [...]
        }
    """

    transactions = {}
    visiting = set()
    metadata_cache = {}
    anchor_found = False

    walk_start = time.monotonic()

    def budget_exceeded():
        if len(transactions) >= max_transactions:
            print(
                "DEPENDENCY TRANSACTION LIMIT REACHED:",
                len(transactions),
                "limit=",
                max_transactions,
            )
            return True

        elapsed = time.monotonic() - walk_start

        if elapsed >= max_seconds:
            print(
                "DEPENDENCY TIME LIMIT REACHED:",
                round(elapsed, 2),
                "seconds",
            )
            return True

        return False


    def get_metadata(txid):
        if txid in metadata_cache:
            return metadata_cache[txid]

        metadata = _get_wallet_tx_metadata(
            wallet_db_path,
            txid,
        )

        metadata_cache[txid] = metadata

        return metadata




    def visit(txid, tx_hex, depth, path_from_subject):
        nonlocal anchor_found

        # ---------------------------------------------------------
        # Global dependency-walk safety limits
        # ---------------------------------------------------------

        if budget_exceeded():
            return False

        if depth > max_depth:
            print(
                "DEPENDENCY MAX DEPTH REACHED:",
                txid,
                "depth=",
                depth,
            )
            return False

        # Already completely processed.
        if txid in transactions:
            return

        # Currently being processed on this recursive path.
        if txid in visiting:
            print(
                "DEPENDENCY CYCLE DETECTED:",
                txid,
            )
            return False

        visiting.add(txid)


        try:
            # -------------------------------------------------
            # Verify the raw transaction actually hashes to the
            # TXID we think it is.
            # -------------------------------------------------

            try:
                calculated_txid = _txid_from_hex(tx_hex)

                if calculated_txid != txid:
                    print(
                        "WARNING: TXID/HEX MISMATCH:",
                        txid,
                        "calculated=",
                        calculated_txid,
                    )
            except Exception as e:
                print(
                    "TXID CALCULATION ERROR:",
                    txid,
                    repr(e),
                )

            # -------------------------------------------------
            # Wallet metadata
            # -------------------------------------------------

            metadata = get_metadata(txid)

            blockheight = (
                metadata["blockheight"]
                if metadata
                else -1
            )

            # -------------------------------------------------
            # Merkle proof
            # -------------------------------------------------

            proof, proof_height = _get_proof_for_transaction(
                account,
                wallet_db_path,
                txid,
                retries=1,
                delay=0.2,
            )

            # Native proof may tell us the height.
            if (
                proof_height is not None
                and proof_height > 0
            ):
                blockheight = proof_height

            confirmed = (
                blockheight is not None
                and blockheight > 0
            )

            header_hex = None
            header_time = None

            # -------------------------------------------------
            # Header for proven transaction
            # -------------------------------------------------

            if proof is not None and blockheight > 0:
                header = get_local_header(blockheight)

                if header is not None:
                    header_hex = header.hex()

                    try:
                        header_time = extract_time_from_header(
                            header
                        )
                    except Exception as e:
                        print(
                            "DEPENDENCY HEADER TIME ERROR:",
                            txid,
                            repr(e),
                        )

                else:
                    print(
                        "DEPENDENCY LOCAL HEADER UNAVAILABLE:",
                        txid,
                        "height=",
                        blockheight,
                    )

            item = {
                "txid": txid,
                "hex": tx_hex,
                "blockheight": blockheight,
                "time": header_time,
                "merkle": proof,
                "header": header_hex,
                "confirmed": bool(confirmed),
                "proof_available": proof is not None,
                "depends_on": [],
            }

            transactions[txid] = item

            # -------------------------------------------------
            # Stop this branch once we have a proof.
            # -------------------------------------------------


            if (
                proof is not None
                and blockheight is not None
                and blockheight > 0
            ):
                anchor_found = True
                return True


            if proof is not None:

                # ---------------------------------------------------------
                # If this is the original subject transaction, determine
                # whether any of its inputs spend a wallet-known output.
                #
                # If not, treat it as an external incoming transaction and
                # do NOT recursively walk the sender's ancestry.
                # ---------------------------------------------------------

                if depth == 0:
                    internal = _is_internal_wallet_transaction(
                        account,
                        tx_hex,
                        wallet_db_path,
                    )

                    if not internal:
                        return


            # -------------------------------------------------
            # Internal transaction: follow inputs.
            # -------------------------------------------------

            parsed = _parse_tx_dependencies(tx_hex)


            parent_specs = []
            seen_parents = set()


            inputs = parsed["inputs"]

            if len(inputs) > max_inputs_per_tx:
                print(
                    "DEPENDENCY INPUT LIMIT REACHED:",
                    txid,
                    "inputs=",
                    len(inputs),
                    "limit=",
                    max_inputs_per_tx,
                )

                inputs = inputs[:max_inputs_per_tx]

            for tx_input in inputs:

                parent_txid = tx_input["prev_txid"]
                parent_vout = tx_input["prev_vout"]


                if parent_txid is None:
                    # Coinbase input
                    continue

                item["depends_on"].append({
                    "txid": parent_txid,
                    "vout": parent_vout,
                })

                # Only walk each parent transaction once.
                if parent_txid not in seen_parents:
                    seen_parents.add(parent_txid)
                    parent_specs.append(
                        (parent_txid, parent_vout)
                    )


            # -------------------------------------------------
            # Walk all parents.
            # -------------------------------------------------

            for parent_txid, parent_vout in parent_specs:

                # Once a confirmed proof anchor has been found on another
                # branch, there is no need to recursively search this
                # remaining sibling branch for another anchor.
                if anchor_found:

                    break

                if budget_exceeded():

                    break

                # ---------------------------------------------------------
                # If we already encountered this transaction, the dependency
                # edge above is already preserved. Do not walk it again.
                # ---------------------------------------------------------

                if parent_txid in transactions:

                    continue

                # ---------------------------------------------------------
                # Obtain the parent's raw transaction.
                #
                # This must be available locally because the entire purpose
                # of the dependency walk is to avoid relying on ElectrumX
                # for historical transaction data.
                # ---------------------------------------------------------

                parent_hex = None

                try:
                    parent_hex = _get_transaction_hex_for_beef(
                        account,
                        parent_txid,
                    )
                except Exception as e:
                    print(
                        "DEPENDENCY HEX ERROR:",
                        parent_txid,
                        repr(e),
                    )

                if not parent_hex:
                    print(
                        "DEPENDENCY TRANSACTION UNAVAILABLE:",
                        parent_txid,
                        "needed by",
                        txid,
                        "vout",
                        parent_vout,
                    )

                    # The dependency edge remains in item["depends_on"].
                    continue

                # ---------------------------------------------------------
                # Verify that the transaction we obtained really hashes to
                # the parent TXID.
                # ---------------------------------------------------------

                try:
                    calculated_parent_txid = _txid_from_hex(
                        parent_hex
                    )

                    if calculated_parent_txid != parent_txid:
                        print(
                            "DEPENDENCY TXID/HEX MISMATCH:",
                            parent_txid,
                            "calculated=",
                            calculated_parent_txid,
                        )
                        continue

                except Exception as e:
                    print(
                        "DEPENDENCY TXID CALCULATION ERROR:",
                        parent_txid,
                        repr(e),
                    )
                    continue

                # ---------------------------------------------------------
                # Recursively walk the parent.
                # ---------------------------------------------------------

                visit(
                    parent_txid,
                    parent_hex,
                    depth + 1,
                    path_from_subject + [txid],
                )

        finally:
            visiting.discard(txid)

    visit(
        subject_txid,
        subject_hex,
        0,
        [],
    )

    # ---------------------------------------------------------
    # Sort roughly oldest/proof anchors first, subject last.
    #
    # A dependency graph can branch, so this is a deterministic
    # traversal ordering rather than pretending the graph is
    # always a single linear chain.
    # ---------------------------------------------------------

    ordered = list(transactions.values())

    ordered.sort(
        key=lambda x: (
            (
                x["blockheight"]
                if x["blockheight"] is not None
                and x["blockheight"] > 0
                else 10**18
            ),
            x["txid"],
        )
    )

    # Force subject to the end.
    ordered = [
        x for x in ordered
        if x["txid"] != subject_txid
    ] + [
        x for x in ordered
        if x["txid"] == subject_txid
    ]

    return ordered


# ============================================================
# Replacement build_beef
# ============================================================

def build_beef(tx, account=None, slim=False, retries=3, delay=0.5):
    import os
    import sqlite3
    import time

    mark_total = time.perf_counter()

    txid = tx.txid()

    # ---------------------------------------------------------
    # Resolve wallet database
    # ---------------------------------------------------------

    wallet_db_path = _get_wallet_db_path_for_beef(account)


    # ---------------------------------------------------------
    # Resolve spent flag
    # ---------------------------------------------------------

    # ElectrumSV 1.3.16 stores TransactionOutputFlag.IS_SPENT
    # as bit 2.
    IS_SPENT = 1 << 2

    # ---------------------------------------------------------
    # Get subject raw transaction hex
    #
    # Keep this reliable because it is now needed even when the
    # transaction has no wallet-owned UTXOs.
    # ---------------------------------------------------------

    subject_hex = None

    try:
        subject_hex = tx.to_hex()
    except Exception as e:
        print(
            "SUBJECT TX HEX ERROR:",
            repr(e),
        )

    if not subject_hex:
        subject_hex = _get_transaction_hex_for_beef(
            account,
            txid,
        )

    if not subject_hex:
        raise RuntimeError(
            f"Unable to obtain raw transaction hex for {txid}"
        )

    # ---------------------------------------------------------
    # Read wallet-owned outputs exactly as before.
    #
    # This preserves the existing confirmed-UTXO behavior.
    # ---------------------------------------------------------

    local_outputs = []

    if wallet_db_path:

        try:
            tx_hash_bytes = bytes.fromhex(txid)[::-1]

            conn = sqlite3.connect(wallet_db_path)

            try:
                local_outputs = conn.execute(
                    """
                    SELECT
                        txo.tx_index,
                        txo.value,
                        txo.keyinstance_id,
                        txo.flags,
                        t.block_height,
                        t.block_position
                    FROM TransactionOutputs AS txo
                    JOIN Transactions AS t
                        ON t.tx_hash = txo.tx_hash
                    WHERE txo.tx_hash = ?
                      AND (txo.flags & ?) = 0
                    ORDER BY txo.tx_index
                    """,
                    (
                        tx_hash_bytes,
                        IS_SPENT,
                    ),
                ).fetchall()

            finally:
                conn.close()

        except Exception as e:
            print(
                "LOCAL WALLET OUTPUT ERROR:",
                repr(e),
            )

    else:
        print(
            "No wallet DB available for local output lookup."
        )


    local_by_vout = {
        row[0]: row
        for row in local_outputs
    }

    utxos_by_id = {}

    # ---------------------------------------------------------
    # Build wallet-owned UTXO entries.
    #
    # Important:
    # Even if there are ZERO wallet-owned outputs, the function
    # continues. The subject TX and dependency graph still get
    # cached.
    # ---------------------------------------------------------

    for vout_index, txout in enumerate(tx.outputs):

        try:
            local_row = local_by_vout.get(
                vout_index
            )

            if local_row is None:
                continue

            (
                db_vout,
                db_value,
                keyinstance_id,
                output_flags,
                db_height,
                db_position,
            ) = local_row

            if db_value != txout.value:
                print(
                    "WARNING: DB value differs from tx output:",
                    txid,
                    vout_index,
                    db_value,
                    txout.value,
                )

            blockheight = (
                db_height
                if db_height is not None
                else -1
            )

            entry = {
                "txid": txid,
                "vout": vout_index,
                "satoshis": txout.value,
                "blockheight": blockheight,
                "time": None,
                "keyinstance_id": keyinstance_id,
            }

            if blockheight <= 0:
                if not slim:
                    entry["unverifiable"] = True

            utxos_by_id[
                (txid, vout_index)
            ] = entry

        except Exception as e:
            print(
                "ERROR processing output",
                vout_index,
                repr(e),
            )



    # ---------------------------------------------------------
    # Collect wallet-owned SPENT outputs separately.
    #
    # The existing utxos_by_id contains only unspent outputs.
    # Keep spent outputs in a separate list so we do not change
    # the existing BREAD verification behavior.
    # ---------------------------------------------------------

    spent_utxos = []

    if wallet_db_path:

        try:
            tx_hash_bytes = bytes.fromhex(txid)[::-1]

            conn = sqlite3.connect(
                wallet_db_path
            )

            try:
                spent_rows = conn.execute(
                    """
                    SELECT
                        txo.tx_index,
                        txo.value,
                        txo.keyinstance_id,
                        txo.flags
                    FROM TransactionOutputs AS txo
                    WHERE txo.tx_hash = ?
                      AND (txo.flags & ?) != 0
                    ORDER BY txo.tx_index
                    """,
                    (
                        tx_hash_bytes,
                        IS_SPENT,
                    ),
                ).fetchall()

            finally:
                conn.close()

            for (
                vout_index,
                value,
                keyinstance_id,
                output_flags,
            ) in spent_rows:

                spent_utxos.append({
                    "txid": txid,
                    "vout": vout_index,
                    "satoshis": value,
                    "keyinstance_id": keyinstance_id,
                    "flags": output_flags,
                    "spent": True,
                })

        except Exception as e:

            print(
                "SPENT OUTPUT DB ERROR:",
                repr(e),
            )



    # ---------------------------------------------------------
    # Existing direct proof population for this transaction.
    #
    # This preserves your existing automatic BEEF/cache
    # behavior for confirmed transactions.
    # ---------------------------------------------------------

    if not slim:

        current_metadata = _get_wallet_tx_metadata(
            wallet_db_path,
            txid,
        )

        current_height = (
            current_metadata["blockheight"]
            if current_metadata
            else -1
        )

        if current_height > 0:

            header = get_local_header(
                current_height
            )

            if header is not None:

                header_hex = header.hex()

                try:
                    header_time = extract_time_from_header(
                        header
                    )
                except Exception as e:
                    print(
                        "SUBJECT HEADER TIME ERROR:",
                        repr(e),
                    )
                    header_time = None

                proof = fetch_merkle(
                    account,
                    txid,
                    current_height,
                    retries,
                    delay,
                )

                for entry in utxos_by_id.values():
                    entry["header"] = header_hex
                    entry["merkle"] = proof
                    entry["time"] = header_time

                    if proof is None:
                        entry["unverifiable"] = True

            else:
                print(
                    "SUBJECT LOCAL HEADER UNAVAILABLE:",
                    current_height,
                )

    # ---------------------------------------------------------
    # Dependency chain
    # ---------------------------------------------------------

    dependency_transactions = []

    if not slim:
        dependency_transactions = (
            _build_dependency_chain(
                account=account,
                subject_txid=txid,
                subject_hex=subject_hex,
                wallet_db_path=wallet_db_path,
                retries=retries,
                delay=delay,
            )
        )

    # ---------------------------------------------------------
    # Deduplicate UTXOs
    # ---------------------------------------------------------

    unique_utxos = []

    seen = set()

    for u in utxos_by_id.values():

        key = (
            u.get("txid"),
            u.get("vout"),
        )

        if key in seen:
            continue

        seen.add(key)
        unique_utxos.append(u)

    # ---------------------------------------------------------
    # Build BREAD
    #
    # Existing fields are retained.
    #
    # New:
    #   transactions = dependency/proof package
    # ---------------------------------------------------------

    beef_data = {
        "format": "BREAD",
        "version": 2,
        "txid": txid,
        "utxos": unique_utxos,
        "spent_utxos": spent_utxos,
        "hex": subject_hex,
    }

    if dependency_transactions:
        beef_data["transactions"] = dependency_transactions

    # ---------------------------------------------------------
    # Put the subject's proof at the top level too.
    #
    # This improves future cache lookups without changing the
    # existing UTXO representation.
    # ---------------------------------------------------------

    subject_item = None

    for item in dependency_transactions:
        if item["txid"] == txid:
            subject_item = item
            break

    if subject_item is not None:

        if subject_item.get("merkle"):
            beef_data["merkle"] = subject_item["merkle"]

        if subject_item.get("header"):
            beef_data["header"] = subject_item["header"]

        if subject_item.get("blockheight", -1) > 0:
            beef_data["blockheight"] = (
                subject_item["blockheight"]
            )

        beef_data["confirmed"] = bool(
            subject_item.get("confirmed")
        )

    # ---------------------------------------------------------
    # HARD INVARIANT:
    # never cache/return a blank BEEF.
    # ---------------------------------------------------------

    if not beef_data.get("txid"):
        raise RuntimeError(
            "BEEF construction failed: missing subject TXID"
        )

    if not beef_data.get("hex"):
        raise RuntimeError(
            f"BEEF construction failed: no transaction hex for {txid}"
        )

    # ---------------------------------------------------------
    # Cache the complete subject BEEF.
    #
    # This preserves your restore/confirmed-TX cache behavior.
    # ---------------------------------------------------------

    CACHE[txid] = beef_data

    save_cache(
        CACHE,
        MERKLE_CACHE_PATH,
    )

    # ---------------------------------------------------------
    # ALSO cache each dependency transaction individually.
    #
    # This is useful later when a restore or another transaction
    # needs one of these historical proofs.
    # ---------------------------------------------------------

    for item in dependency_transactions:

        dep_txid = item.get("txid")

        if not dep_txid:
            continue

        cached_dep = CACHE.get(
            dep_txid,
            {},
        )

        cached_dep.update({
            "format": "BREAD",
            "version": 2,
            "txid": dep_txid,
            "hex": item.get("hex"),
            "blockheight": item.get(
                "blockheight",
                -1,
            ),
            "confirmed": item.get(
                "confirmed",
                False,
            ),
        })

        if item.get("merkle"):
            cached_dep["merkle"] = item["merkle"]

        if item.get("header"):
            cached_dep["header"] = item["header"]

        if item.get("time") is not None:
            cached_dep["time"] = item["time"]

        CACHE[dep_txid] = cached_dep

    save_cache(
        CACHE,
        MERKLE_CACHE_PATH,
    )


    return beef_data




def build_beef_for_address(
    account,
    address: str,
    slim=False,
    retries=3,
    delay=0.5,
):
    import sqlite3
    import time
    from concurrent.futures import ThreadPoolExecutor

    mark_total = time.perf_counter()

    # ---------------------------------------------------------
    # Resolve wallet database
    # ---------------------------------------------------------

    wallet_db_path = None

    try:
        wallet = account._wallet

        if wallet is not None:
            storage = wallet.get_storage()

            if storage is not None:
                wallet_db_path = storage.get_path()

                if wallet_db_path:
                    if not wallet_db_path.lower().endswith(".sqlite"):
                        wallet_db_path += ".sqlite"

    except Exception as e:
        print(
            "Could not resolve wallet database:",
            repr(e),
        )

    if not wallet_db_path:
        print(
            "No wallet database path available."
        )

        return {
            "format": "BREAD",
            "version": 2,
            "address": address,
            "utxos": [],
            "spent_utxos": [],
            "transactions": [],
        }


    # ---------------------------------------------------------
    # Convert requested address into wallet script hash
    # ---------------------------------------------------------

    try:
        requested_script = scripthash_from_address(
            address
        )

    except Exception as e:
        print(
            "ADDRESS SCRIPT ERROR:",
            repr(e),
        )

        return {
            "format": "BREAD",
            "version": 2,
            "address": address,
            "utxos": [],
            "spent_utxos": [],
            "transactions": [],
        }

    # ---------------------------------------------------------
    # Find matching wallet keyinstances
    # ---------------------------------------------------------

    matching_keyinstance_ids = []

    try:
        conn = sqlite3.connect(
            wallet_db_path
        )

        try:
            rows = conn.execute(
                """
                SELECT
                    keyinstance_id,
                    script_type,
                    flags
                FROM KeyInstances
                WHERE script_type = 2
                """
            ).fetchall()

        finally:
            conn.close()

        for (
            keyinstance_id,
            script_type,
            key_flags,
        ) in rows:

            try:
                script = account.get_script_for_id(
                    keyinstance_id,
                    script_type,
                )

                if script is None:
                    continue

                script_hash = scripthash_hex(
                    script
                )

                if script_hash == requested_script:
                    matching_keyinstance_ids.append(
                        keyinstance_id
                    )

            except Exception:
                pass

    except Exception as e:
        print(
            "KEYINSTANCE LOOKUP ERROR:",
            repr(e),
        )


    if not matching_keyinstance_ids:

        print(
            "No wallet keyinstance matches address:",
            address,
        )

        return {
            "format": "BREAD",
            "version": 2,
            "address": address,
            "utxos": [],
            "spent_utxos": [],
            "transactions": [],
        }

    # ---------------------------------------------------------
    # Read unspent outputs for this address
    # ---------------------------------------------------------

    IS_SPENT = 1 << 2

    local_outputs = []

    try:
        conn = sqlite3.connect(
            wallet_db_path
        )

        try:
            placeholders = ",".join(
                "?"
                for _ in matching_keyinstance_ids
            )

            rows = conn.execute(
                f"""
                SELECT
                    txo.tx_hash,
                    txo.tx_index,
                    txo.value,
                    txo.keyinstance_id,
                    txo.flags,
                    t.block_height,
                    t.block_position
                FROM TransactionOutputs AS txo
                JOIN Transactions AS t
                    ON t.tx_hash = txo.tx_hash
                WHERE txo.keyinstance_id IN ({placeholders})
                  AND (txo.flags & ?) = 0
                ORDER BY
                    t.block_height,
                    txo.tx_index
                """,
                tuple(
                    matching_keyinstance_ids
                ) + (
                    IS_SPENT,
                ),
            ).fetchall()

            local_outputs = rows

        finally:
            conn.close()

    except Exception as e:
        print(
            "LOCAL ADDRESS OUTPUT DB ERROR:",
            repr(e),
        )



    # ---------------------------------------------------------
    # Build address UTXO entries
    # ---------------------------------------------------------

    utxos_by_id = {}

    for (
        tx_hash_bytes,
        tx_index,
        value,
        keyinstance_id,
        output_flags,
        block_height,
        block_position,
    ) in local_outputs:

        txid = tx_hash_bytes[::-1].hex()

        if block_height is None:
            block_height = -1

        entry = {
            "txid": txid,
            "vout": tx_index,
            "satoshis": value,
            "blockheight": block_height,
            "time": None,
            "keyinstance_id": keyinstance_id,
        }

        if block_height <= 0:

            if not slim:
                entry["unverifiable"] = True

        utxos_by_id[
            (txid, tx_index)
        ] = entry


    # ---------------------------------------------------------
    # Group UTXOs by transaction
    #
    # This is important because one transaction can create
    # several outputs paying this address.
    # ---------------------------------------------------------

    tx_entries = {}

    for entry in utxos_by_id.values():

        tx_entries.setdefault(
            entry["txid"],
            [],
        ).append(entry)

    # ---------------------------------------------------------
    # Confirmed transaction proof population
    #
    # For confirmed transactions we attach header + merkle
    # directly to every matching address UTXO.
    # ---------------------------------------------------------

    confirmed_txids = []
    unconfirmed_txids = []

    for txid, entries in tx_entries.items():

        heights = [
            e.get(
                "blockheight",
                -1,
            )
            for e in entries
        ]

        confirmed = (
            len(heights) > 0
            and all(
                h > 0
                for h in heights
            )
        )

        if confirmed:
            confirmed_txids.append(txid)
        else:
            unconfirmed_txids.append(txid)


    # ---------------------------------------------------------
    # Build confirmed transaction proof information
    # ---------------------------------------------------------

    if not slim and confirmed_txids:

        # Group by block height so one header is reused.
        height_groups = {}

        for txid in confirmed_txids:

            entries = tx_entries[txid]

            height = entries[0].get(
                "blockheight",
                -1,
            )

            if height > 0:
                height_groups.setdefault(
                    height,
                    [],
                ).append(
                    txid
                )

        for height, txids_at_height in height_groups.items():

            header = get_local_header(
                height
            )

            if header is None:

                print(
                    "LOCAL HEADER UNAVAILABLE:",
                    height,
                )

                for txid in txids_at_height:

                    for entry in tx_entries[txid]:
                        entry[
                            "unverifiable"
                        ] = True

                continue

            header_hex = header.hex()

            try:
                header_time = extract_time_from_header(
                    header
                )

            except Exception as e:

                print(
                    "HEADER TIME ERROR:",
                    height,
                    repr(e),
                )

                header_time = None


            # -------------------------------------------------
            # Fetch all proofs for this height
            # -------------------------------------------------

            with ThreadPoolExecutor(
                max_workers=8
            ) as executor:

                futures = {}

                for txid in txids_at_height:

                    futures[
                        executor.submit(
                            fetch_merkle,
                            account,
                            txid,
                            height,
                            retries,
                            delay,
                        )
                    ] = txid

                proof_results = {}

                for future, txid in futures.items():

                    try:
                        proof_results[txid] = (
                            future.result()
                        )

                    except Exception as e:

                        print(
                            "MERKLE ERROR:",
                            txid,
                            repr(e),
                        )

                        proof_results[txid] = None

            # -------------------------------------------------
            # Attach proof data to UTXOs
            # -------------------------------------------------

            for txid in txids_at_height:

                proof = proof_results.get(
                    txid
                )

                for entry in tx_entries[txid]:

                    entry["header"] = (
                        header_hex
                    )

                    entry["merkle"] = (
                        proof
                    )

                    entry["time"] = (
                        header_time
                    )

                    if proof is None:

                        entry[
                            "unverifiable"
                        ] = True

    # ---------------------------------------------------------
    # Build transaction package
    #
    # This is the important addition.
    #
    # Every confirmed address transaction gets an entry.
    # Every unconfirmed address transaction gets its dependency
    # chain, allowing the GUI to find a proof anchor.
    # ---------------------------------------------------------

    transactions_by_id = {}

    for txid, entries in tx_entries.items():

        height = entries[0].get(
            "blockheight",
            -1,
        )

        # -----------------------------------------------------
        # Confirmed transaction
        # -----------------------------------------------------

        if height > 0:

            proof = None
            header_hex = None
            tx_time = None

            first_entry = entries[0]

            proof = first_entry.get(
                "merkle"
            )

            header_hex = first_entry.get(
                "header"
            )

            tx_time = first_entry.get(
                "time"
            )

            # Try to obtain raw transaction hex.
            tx_hex = None

            try:
                tx_hex = CACHE.get(
                    txid,
                    {}
                ).get(
                    "hex"
                )
            except Exception:
                tx_hex = None

            if not tx_hex:

                try:
                    tx_hex = _get_transaction_hex_for_beef(
                        account,
                        txid,
                    )

                except Exception as e:

                    print(
                        "ADDRESS TX HEX ERROR:",
                        txid,
                        repr(e),
                    )

            transaction_item = {
                "txid": txid,
                "blockheight": height,
                "time": tx_time,
                "merkle": proof,
                "header": header_hex,
                "confirmed": True,
                "proof_available": (
                    proof is not None
                ),
                "depends_on": [],
                "hex": tx_hex,
            }

            transactions_by_id[txid] = (
                transaction_item
            )

            continue

        # -----------------------------------------------------
        # Unconfirmed transaction
        # -----------------------------------------------------

        subject_hex = None

        try:
            cached_subject = CACHE.get(
                txid,
                {}
            )

            subject_hex = cached_subject.get(
                "hex"
            )

        except Exception:
            subject_hex = None

        if not subject_hex:

            try:
                subject_hex = (
                    _get_transaction_hex_for_beef(
                        account,
                        txid,
                    )
                )

            except Exception as e:

                print(
                    "UNCONFIRMED ADDRESS TX HEX ERROR:",
                    txid,
                    repr(e),
                )

        if not subject_hex:

            print(
                "Unable to obtain hex for unconfirmed address transaction:",
                txid,
            )

            # Still create a transaction record.
            transactions_by_id[txid] = {
                "txid": txid,
                "blockheight": -1,
                "time": None,
                "merkle": None,
                "header": None,
                "confirmed": False,
                "proof_available": False,
                "depends_on": [],
                "hex": None,
            }

            continue

        # -----------------------------------------------------
        # Build dependency chain.
        #
        # This allows a confirmed ancestor to become the proof
        # anchor for an unconfirmed address UTXO.
        # -----------------------------------------------------

        try:

            dependency_items = (
                _build_dependency_chain(
                    account=account,
                    subject_txid=txid,
                    subject_hex=subject_hex,
                    wallet_db_path=wallet_db_path,
                    retries=retries,
                    delay=delay,
                )
            )

        except Exception as e:

            print(
                "ADDRESS DEPENDENCY CHAIN ERROR:",
                txid,
                repr(e),
            )

            dependency_items = []

        # -----------------------------------------------------
        # Add every dependency transaction to the global map.
        # -----------------------------------------------------

        subject_item = None

        for item in dependency_items:

            dep_txid = item.get(
                "txid"
            )

            if not dep_txid:
                continue

            existing = transactions_by_id.get(
                dep_txid
            )

            if existing is None:

                transactions_by_id[
                    dep_txid
                ] = dict(item)

            else:

                # Prefer a richer proof-bearing record.
                if (
                    item.get("proof_available")
                    and not existing.get(
                        "proof_available"
                    )
                ):
                    transactions_by_id[
                        dep_txid
                    ] = dict(item)

            if dep_txid == txid:

                subject_item = (
                    transactions_by_id[
                        dep_txid
                    ]
                )

        # -----------------------------------------------------
        # Make absolutely sure the subject transaction is
        # represented, even if dependency-chain construction
        # returned an incomplete result.
        # -----------------------------------------------------

        if subject_item is None:

            subject_item = {
                "txid": txid,
                "blockheight": -1,
                "time": None,
                "merkle": None,
                "header": None,
                "confirmed": False,
                "proof_available": False,
                "depends_on": [],
                "hex": subject_hex,
            }

            transactions_by_id[
                txid
            ] = subject_item

    # ---------------------------------------------------------
    # Convert transaction map into stable list
    # ---------------------------------------------------------

    dependency_transactions = []

    for txid, item in transactions_by_id.items():

        dependency_transactions.append(
            item
        )

    # ---------------------------------------------------------
    # Deduplicate UTXOs
    # ---------------------------------------------------------

    unique_utxos = []

    seen = set()

    for entry in utxos_by_id.values():

        key = (
            entry.get("txid"),
            entry.get("vout"),
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        unique_utxos.append(
            entry
        )

    # ---------------------------------------------------------
    # Build BREAD
    #
    # Version 2 is appropriate here because we now carry a
    # transaction/dependency package just like transaction BEEF.
    # ---------------------------------------------------------

    beef_data = {
        "format": "BREAD",
        "version": 2,
        "address": address,
        "utxos": unique_utxos,
        "spent_utxos": [],
        "transactions": dependency_transactions,
    }

    # ---------------------------------------------------------
    # Address-level status
    #
    # Do NOT force a single confirmed/unconfirmed state for a
    # mixed address.
    # ---------------------------------------------------------

    confirmed_count = 0
    unconfirmed_count = 0
    proven_count = 0

    for entry in unique_utxos:

        height = entry.get(
            "blockheight",
            -1,
        )

        if height > 0:
            confirmed_count += 1

            if (
                entry.get("merkle") is not None
                and entry.get("header")
            ):
                proven_count += 1

        else:
            unconfirmed_count += 1

    beef_data[
        "confirmed_utxos"
    ] = confirmed_count

    beef_data[
        "unconfirmed_utxos"
    ] = unconfirmed_count

    beef_data[
        "proven_utxos"
    ] = proven_count

    if (
        confirmed_count > 0
        and unconfirmed_count == 0
    ):
        beef_data[
            "confirmed"
        ] = True

    elif (
        unconfirmed_count > 0
        and confirmed_count == 0
    ):
        beef_data[
            "confirmed"
        ] = False

    else:
        # Mixed state: deliberately omit a misleading single
        # confirmed boolean and describe the state explicitly.
        beef_data[
            "status"
        ] = "mixed"

    # ---------------------------------------------------------
    # Cache address BEEF
    # ---------------------------------------------------------

    try:

        ADDRESS_CACHE[
            requested_script
        ] = beef_data

        save_cache(
            ADDRESS_CACHE,
            ADDRESS_BEEF_CACHE_PATH,
        )

    except Exception as e:

        print(
            "ADDRESS CACHE ERROR:",
            repr(e),
        )

    # ---------------------------------------------------------
    # Also cache individual transaction packages.
    #
    # This makes later BEEF construction cheaper.
    # ---------------------------------------------------------

    for item in dependency_transactions:

        dep_txid = item.get(
            "txid"
        )

        if not dep_txid:
            continue

        cached_dep = CACHE.get(
            dep_txid,
            {},
        )

        cached_dep.update({
            "format": "BREAD",
            "version": 2,
            "txid": dep_txid,
            "hex": item.get(
                "hex"
            ),
            "blockheight": item.get(
                "blockheight",
                -1,
            ),
            "confirmed": item.get(
                "confirmed",
                False,
            ),
            "proof_available": item.get(
                "proof_available",
                False,
            ),
            "depends_on": item.get(
                "depends_on",
                [],
            ),
        })

        if item.get("merkle"):

            cached_dep[
                "merkle"
            ] = item[
                "merkle"
            ]

        if item.get("header"):

            cached_dep[
                "header"
            ] = item[
                "header"
            ]

        if item.get("time") is not None:

            cached_dep[
                "time"
            ] = item[
                "time"
            ]

        CACHE[
            dep_txid
        ] = cached_dep

    save_cache(
        CACHE,
        MERKLE_CACHE_PATH,
    )

    return beef_data





def fetch_scripthash_utxos_with_retry(
    scripthash,
    retries=3,
    delay=0.5,
):
    for _ in range(retries):

        try:
            result = electrum_request(
                "blockchain.scripthash.listunspent",
                [scripthash],
            ).get("result", [])

            if result:
                return result

        except Exception:
            pass

        time.sleep(delay)

    return []


# --- GUI: verification window (kept from your working code) ---

def open_simple_verification_window(main_window: QWidget, account, beef_or_tx=None, address=None):
    import copy, json, os
    from PyQt5.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton,
        QScrollArea, QLabel, QSizePolicy, QFileDialog, QCheckBox, QSplitter, QWidget, QApplication
    )
    from PyQt5.QtCore import Qt, QThread, pyqtSignal

    # --- Determine BEEF data ---
    if isinstance(beef_or_tx, dict) and beef_or_tx.get("format") == "BREAD":
        beef_data = copy.deepcopy(beef_or_tx)
        title_str = beef_data.get("address", beef_data.get("txid", ""))[:10] + "..."
        tx_obj = None
    elif beef_or_tx is not None:
        try:
            beef_data = build_beef(
                tx=beef_or_tx,
                account=account,
                slim=False,
            )
            #beef_data = build_beef(account, beef_or_tx, slim=False)
            title_str = getattr(beef_or_tx, "txid", lambda: "unknown")()[:10] + "..."
            tx_obj = beef_or_tx
        except Exception:
            beef_data = {"format": "BREAD", "version": 1, "utxos": []}
            title_str = "Unknown"
            tx_obj = None
    elif address is not None:
        beef_data = build_beef_for_address(account, address, slim=False)
        title_str = address[:10] + "..."
        tx_obj = None
    else:
        beef_data = {"format": "BREAD", "version": 1, "utxos": []}
        title_str = "Empty BREAD"
        tx_obj = None

    class SimpleVerificationWindow(QDialog):
        class HexFetcher(QThread):
            hex_fetched = pyqtSignal(str, object)

            def __init__(self, txids, account):
                super().__init__()
                self.txids = list(txids)
                self.account = account

            def _try_get_from_wallet(self, txid):
                for key in (txid, bytes.fromhex(txid)):
                    try:
                        tx_obj = self.account.get_transaction(key)
                        if tx_obj:
                            return tx_obj.serialize().hex()
                    except Exception:
                        continue
                return None

            def run(self):
                for txid in self.txids:
                    if self.isInterruptionRequested():
                        break
                    hex_str = self._try_get_from_wallet(txid)
                    if hex_str is None:
                        try:
                            resp = electrum_request("blockchain.transaction.get", [txid])
                            hex_str = resp.get("result")
                        except Exception:
                            hex_str = None
                    self.hex_fetched.emit(txid, hex_str)

        def __init__(self, account, beef_data, tx_obj=None, address=None):
            super().__init__(None)
            self.account = account
            self.tx_obj = tx_obj
            self.address = address

            self.full_beef = copy.deepcopy(beef_data)
            self.beef_data = copy.deepcopy(beef_data)
            self._utxo_tx_cache = {}

            # merge cached data
            for utxo in self.full_beef.get("utxos", []):
                txid = utxo.get("txid")
                cached_tx = CACHE.get(txid, {})
                if cached_tx.get("merkle") and not utxo.get("merkle"):
                    utxo["merkle"] = cached_tx.get("merkle")
                if cached_tx.get("header") and not utxo.get("header"):
                    utxo["header"] = cached_tx.get("header")
                self._utxo_tx_cache[txid] = cached_tx.get("hex")
            if self.tx_obj:
                txid_main = self.tx_obj.txid()
                self._utxo_tx_cache.setdefault(txid_main, CACHE.get(txid_main, {}).get("hex"))

            self.hex_thread = None
            self.setWindowTitle(f"BREAD Proof - {title_str}")
            self.setMinimumSize(750, 500)

            # --- Checkboxes ---
            checkbox_layout = QHBoxLayout()
            self.include_merkle_checkbox = QCheckBox("Include Merkle proofs")
            self.include_merkle_checkbox.setChecked(True)
            self.include_headers_checkbox = QCheckBox("Include block headers")
            self.include_headers_checkbox.setChecked(True)
            self.include_hex_checkbox = QCheckBox("Include transaction hex")
            self.include_hex_checkbox.setChecked(False)
            checkbox_layout.addWidget(self.include_merkle_checkbox)
            checkbox_layout.addWidget(self.include_headers_checkbox)
            checkbox_layout.addWidget(self.include_hex_checkbox)

            self.include_hex_checkbox.stateChanged.connect(self.on_hex_checkbox_toggle)
            self.include_merkle_checkbox.stateChanged.connect(self.update_beef_view)
            self.include_headers_checkbox.stateChanged.connect(self.update_beef_view)

            # --- Main splitter / display ---
            main_splitter = QSplitter(Qt.Vertical, self)
            main_splitter.setChildrenCollapsible(False)

            self.text = QTextEdit()
            self.text.setReadOnly(True)
            self.text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            main_splitter.addWidget(self.text)

            self.scroll_area = QScrollArea()
            self.scroll_area.setWidgetResizable(True)
            self.scroll_area.setVisible(False)
            scroll_content = QWidget()
            scroll_layout = QVBoxLayout()
            scroll_layout.setContentsMargins(0, 0, 0, 0)
            scroll_layout.setSpacing(2)
            scroll_content.setLayout(scroll_layout)
            self.scroll_area.setWidget(scroll_content)
            self.result_layout = scroll_layout
            main_splitter.addWidget(self.scroll_area)
            main_splitter.setStretchFactor(0, 4)
            main_splitter.setStretchFactor(1, 1)


            # --- Bottom buttons ---
            bottom_layout = QHBoxLayout()
            self.verify_btn = QPushButton("Verify Proof (SPV)")

            # Fixed height for verify button (cross-platform)
            self.verify_btn.setFixedHeight(26)
            bottom_layout.addWidget(self.verify_btn, stretch=4)
            self.verify_btn.clicked.connect(self.verify_proofs)

            # --- Determine OS-specific button heights / spacing ---
            system = platform.system()
            if system == "Darwin":
                btn_height = 28           # slightly taller on macOS
                btn_spacing = 6           # more spacing between buttons
                btn_margins = (0, 6, 0, 6)
            else:  # Windows / Linux
                btn_height = 22
                btn_spacing = 2
                btn_margins = (0, 2, 0, 2)

            # Container for copy/save buttons
            right_widget = QWidget()
            right_layout = QVBoxLayout(right_widget)
            right_layout.setContentsMargins(*btn_margins)
            right_layout.setSpacing(btn_spacing)

            self.copy_btn = QPushButton("Copy")
            self.copy_btn.setFixedHeight(btn_height)
            self.save_btn = QPushButton("Save")
            self.save_btn.setFixedHeight(btn_height)

            right_layout.addWidget(self.copy_btn)
            right_layout.addWidget(self.save_btn)
            self.copy_btn.clicked.connect(self.copy_to_clipboard)
            self.save_btn.clicked.connect(self.save_to_file)

            # Add container to bottom layout
            bottom_layout.addWidget(right_widget, stretch=0)


            main_layout = QVBoxLayout(self)
            main_layout.setContentsMargins(6, 6, 6, 6)
            main_layout.setSpacing(6)
            main_layout.addLayout(checkbox_layout)
            main_layout.addWidget(main_splitter)
            main_layout.addLayout(bottom_layout)

            # initial view
            self.update_beef_view()

        # ... rest of methods (same as before) ...

        def _stop_hex_fetcher(self):
            if self.hex_thread and self.hex_thread.isRunning():
                self.hex_thread.requestInterruption()
                self.hex_thread.wait(2000)
            self.hex_thread = None

        def _start_background_hex_fetcher(self, txids):
            # Only run parallel fetching for address tab
            if self.full_beef.get("txid"):
                return  # TX tab: handled synchronously for stability
            remaining = [t for t in txids if not self._utxo_tx_cache.get(t) and not (CACHE.get(t, {}).get("hex"))]
            if not remaining:
                return
            self._stop_hex_fetcher()
            self.hex_thread = self.HexFetcher(remaining, self.account)
            self.hex_thread.hex_fetched.connect(self._on_hex_fetched)
            self.hex_thread.start()

        def _fetch_tx_tab_hex_sync(self):
            # For TX tab: fetch hex synchronously to avoid glitches
            if not self.full_beef.get("txid"):
                return
            txid_main = self.full_beef.get("txid")
            if not self._utxo_tx_cache.get(txid_main):
                hex_str = None
                # try wallet first
                try:
                    tx_obj = self.tx_obj or self.account.get_transaction(txid_main)
                    if tx_obj:
                        hex_str = tx_obj.serialize().hex()
                except Exception:
                    pass
                if not hex_str:
                    # fallback to electrum request
                    try:
                        resp = electrum_request("blockchain.transaction.get", [txid_main])
                        hex_str = resp.get("result")
                    except Exception:
                        hex_str = None
                if hex_str:
                    self._utxo_tx_cache[txid_main] = hex_str
                    c = CACHE.setdefault(txid_main, {})
                    c["hex"] = hex_str
                    try:
                        save_cache(CACHE, MERKLE_CACHE_PATH)
                    except Exception:
                        pass

        def _on_hex_fetched(self, txid, hex_str):
            if hex_str:
                self._utxo_tx_cache[txid] = hex_str
                c = CACHE.setdefault(txid, {})
                if not c.get("utxos") and self.full_beef.get("utxos"):
                    c.update({"format": "BREAD", "version": 1, "txid": txid})
                c["hex"] = hex_str
                try:
                    save_cache(CACHE, MERKLE_CACHE_PATH)
                except Exception:
                    pass
                for utxo in self.full_beef.get("utxos", []):
                    if utxo.get("txid") == txid and not utxo.get("hex"):
                        utxo["hex"] = hex_str
            self.update_beef_view()

        def on_hex_checkbox_toggle(self):
            include_hex = self.include_hex_checkbox.isChecked()
            if include_hex:
                if self.full_beef.get("txid"):
                    self._fetch_tx_tab_hex_sync()
                else:
                    txids = {utxo["txid"] for utxo in self.full_beef.get("utxos", []) if utxo.get("txid")}
                    missing = [t for t in txids if not self._utxo_tx_cache.get(t)]
                    if missing:
                        self._start_background_hex_fetcher(missing)
            self.update_beef_view()

        def closeEvent(self, event):
            self._stop_hex_fetcher()
            super().closeEvent(event)

        def update_beef_view(self):
            include_merkle = self.include_merkle_checkbox.isChecked()
            include_headers = self.include_headers_checkbox.isChecked()
            include_hex = self.include_hex_checkbox.isChecked()

            beef_view = copy.deepcopy(self.full_beef)

            # --- Remove internal/cache-only fields ---
            beef_view.pop("_confirmed_cache", None)

            # Never use a top-level hex/hexes field.
            # Transaction hex belongs inside transactions[] only.
            beef_view.pop("hex", None)
            beef_view.pop("hexes", None)

            # --- Clean and update UTXOs ---
            for utxo in beef_view.get("utxos", []):
                utxo.pop("_confirmed_cache", None)

                # Include Merkle proofs
                if include_merkle and not utxo.get("merkle"):
                    cached_tx = CACHE.get(utxo.get("txid"), {})
                    if cached_tx.get("merkle"):
                        utxo["merkle"] = cached_tx.get("merkle")
                elif not include_merkle:
                    utxo.pop("merkle", None)

                # Include block headers
                if include_headers and not utxo.get("header"):
                    cached_tx = CACHE.get(utxo.get("txid"), {})
                    if cached_tx.get("header"):
                        utxo["header"] = cached_tx.get("header")
                elif not include_headers:
                    utxo.pop("header", None)

                # UTXOs should never contain transaction hex.
                utxo.pop("hex", None)

            # --- Clean and optionally add transaction hex ---
            for tx_entry in beef_view.get("transactions", []):
                txid = tx_entry.get("txid")

                # Always remove existing hex first.
                # This makes the checkbox authoritative.
                tx_entry.pop("hex", None)

                if not include_hex or not txid:
                    continue

                # Prefer the in-memory transaction cache.
                tx_hex = self._utxo_tx_cache.get(txid)

                # Fall back to the persistent cache.
                if not tx_hex:
                    cached_tx = CACHE.get(txid, {})
                    tx_hex = cached_tx.get("hex")

                # Finally, preserve hex that may already exist in
                # the original full_beef transaction entry.
                if not tx_hex:
                    original_transactions = self.full_beef.get("transactions", [])
                    for original_tx in original_transactions:
                        if original_tx.get("txid") == txid:
                            tx_hex = original_tx.get("hex")
                            break

                if tx_hex:
                    tx_entry["hex"] = tx_hex

            self.beef_data = beef_view

            # --- Update text display ---
            vbar = self.text.verticalScrollBar()
            old_value = vbar.value()

            self.text.setPlainText(
                json.dumps(self.beef_data, indent=2)
            )

            try:
                vbar.setValue(old_value)
            except Exception:
                pass

            self.verify_btn.setEnabled(include_merkle)

        def _get_confirmed_ancestor_info(self):
            """
            Walk the BEEF dependency graph and find the most recent
            confirmed transaction that has a valid Merkle proof.

            Returns:

                {
                    "hops": int,
                    "ancestors": [
                        {
                            "txid": str,
                            "blockheight": int,
                            "hops": int,
                        },
                        ...
                    ]
                }

            or None if no confirmed proof anchor exists.
            """

            transactions = self.beef_data.get(
                "transactions",
                [],
            )

            if not transactions:
                return None

            tx_map = {
                item.get("txid"): item
                for item in transactions
                if item.get("txid")
            }

            subject_txid = self.beef_data.get(
                "txid"
            )

            if not subject_txid:
                return None

            visited = set()
            queue = [
                (
                    subject_txid,
                    0,
                )
            ]

            anchors = []

            while queue:

                txid, hops = queue.pop(0)

                if txid in visited:
                    continue

                visited.add(txid)

                current = tx_map.get(
                    txid
                )

                if current is None:
                    continue

                # A proof anchor must have:
                #
                #   confirmed=True
                #   proof_available=True
                #   Merkle proof
                #   valid block height
                #
                # We do not consider the subject itself an
                # "ancestor", hence hops > 0.

                if (
                    hops > 0
                    and current.get("confirmed")
                    and current.get("proof_available")
                    and current.get("merkle")
                    and current.get(
                        "blockheight",
                        0,
                    ) > 0
                ):

                    anchors.append({
                        "txid": txid,
                        "blockheight": current.get(
                            "blockheight",
                            -1,
                        ),
                        "hops": hops,
                    })

                    # Once a confirmed proof anchor is reached,
                    # there is no reason to continue farther back
                    # along this branch.
                    continue

                for dependency in current.get(
                    "depends_on",
                    [],
                ):

                    next_txid = dependency.get(
                        "txid"
                    )

                    if (
                        next_txid
                        and next_txid not in visited
                    ):

                        queue.append(
                            (
                                next_txid,
                                hops + 1,
                            )
                        )

            if not anchors:
                return None

            # The "best" anchor is the most recently confirmed one.
            #
            # Highest block height wins.  If two anchors happen to
            # share the same height, prefer the one requiring fewer
            # hops.

            anchors.sort(
                key=lambda item: (
                    item.get(
                        "blockheight",
                        -1,
                    ),
                    -item.get(
                        "hops",
                        999999,
                    ),
                ),
                reverse=True,
            )

            best_blockheight = anchors[0].get(
                "blockheight",
                -1,
            )

            best_anchors = [
                anchor
                for anchor in anchors
                if anchor.get(
                    "blockheight",
                    -1,
                ) == best_blockheight
            ]

            nearest_hops = min(
                anchor.get(
                    "hops",
                    999999,
                )
                for anchor in best_anchors
            )

            return {
                "hops": nearest_hops,
                "ancestors": anchors,
            }




        def verify_proofs(self):
            while self.result_layout.count():

                item = self.result_layout.takeAt(0)

                w = item.widget()

                if w:
                    w.deleteLater()

            results = []

            # =========================================================
            # BASIC STATE
            # =========================================================

            is_address_beef = bool(
                self.beef_data.get(
                    "address"
                )
            )

            transactions = (
                self.beef_data.get(
                    "transactions",
                    [],
                )
                or []
            )

            utxos = (
                self.beef_data.get(
                    "utxos",
                    [],
                )
                or []
            )

            spent_utxos = (
                self.beef_data.get(
                    "spent_utxos",
                    [],
                )
                or []
            )

            # ---------------------------------------------------------
            # Split UTXOs by confirmation state.
            # ---------------------------------------------------------

            confirmed_utxos = []
            unconfirmed_utxos = []

            for utxo in utxos:

                height = utxo.get(
                    "blockheight",
                    -1,
                )

                try:
                    height = int(
                        height
                    )
                except Exception:
                    height = -1

                if height > 0:

                    confirmed_utxos.append(
                        utxo
                    )

                else:

                    unconfirmed_utxos.append(
                        utxo
                    )

            # =========================================================
            # SUBJECT STATE
            # =========================================================

            if is_address_beef:

                confirmed_utxo_count = len(
                    confirmed_utxos
                )

                unconfirmed_utxo_count = len(
                    unconfirmed_utxos
                )

                if (
                    confirmed_utxo_count > 0
                    and unconfirmed_utxo_count == 0
                ):

                    subject_state = "confirmed"

                elif (
                    unconfirmed_utxo_count > 0
                    and confirmed_utxo_count == 0
                ):

                    subject_state = "unconfirmed"

                elif (
                    confirmed_utxo_count > 0
                    and unconfirmed_utxo_count > 0
                ):

                    subject_state = "mixed"

                else:

                    subject_state = "empty"

            else:

                subject_confirmed = bool(
                    self.beef_data.get(
                        "confirmed",
                        False,
                    )
                )

                if subject_confirmed:

                    subject_state = "confirmed"

                else:

                    subject_state = "unconfirmed"

            # =========================================================
            # BUILD TRANSACTION LOOKUP
            # =========================================================

            tx_by_id = {}

            for item in transactions:

                item_txid = item.get(
                    "txid"
                )

                if item_txid:

                    tx_by_id[
                        item_txid
                    ] = item

            # =========================================================
            # FIND CONFIRMED PROOF ANCHORS
            # =========================================================

            confirmed_anchors = []

            for item in transactions:

                item_height = item.get(
                    "blockheight",
                    -1,
                )

                try:
                    item_height = int(
                        item_height
                    )
                except Exception:
                    item_height = -1

                item_confirmed = bool(
                    item.get(
                        "confirmed",
                        False,
                    )
                )

                item_proof = bool(
                    item.get(
                        "proof_available",
                        False,
                    )
                )

                if (
                    item_confirmed
                    and item_height > 0
                    and item_proof
                    and item.get(
                        "merkle"
                    )
                ):

                    confirmed_anchors.append(
                        item
                    )            

            # =========================================================
            # FIND NEAREST CONFIRMED ANCESTOR FOR AN UNCONFIRMED TX
            # =========================================================

            def find_anchor(
                start_txid,
                visited=None,
                hops=0,
            ):

                if visited is None:

                    visited = set()

                if start_txid in visited:

                    return None

                visited.add(
                    start_txid
                )

                item = tx_by_id.get(
                    start_txid
                )

                if item is None:

                    return None

                item_height = item.get(
                    "blockheight",
                    -1,
                )

                try:
                    item_height = int(
                        item_height
                    )
                except Exception:
                    item_height = -1

                item_confirmed = bool(
                    item.get(
                        "confirmed",
                        False,
                    )
                )

                item_proof = bool(
                    item.get(
                        "proof_available",
                        False,
                    )
                )

                # -----------------------------------------------------
                # A confirmed transaction reached through a dependency
                # is a proof anchor.
                # -----------------------------------------------------

                if (
                    hops > 0
                    and item_confirmed
                    and item_height > 0
                    and item_proof
                    and item.get(
                        "merkle"
                    )
                ):

                    return {
                        "hops": hops,
                        "ancestors": [
                            {
                                "txid": start_txid,
                                "blockheight": item_height,
                                "hops": hops,
                            }
                        ],
                    }

                dependencies = (
                    item.get(
                        "depends_on",
                        [],
                    )
                    or []
                )

                best = None

                for dependency in dependencies:

                    dependency_txid = (
                        dependency.get(
                            "txid"
                        )
                    )

                    if not dependency_txid:

                        continue

                    candidate = find_anchor(
                        dependency_txid,
                        visited.copy(),
                        hops + 1,
                    )

                    if candidate is None:

                        continue

                    if (
                        best is None
                        or candidate.get(
                            "hops",
                            999999,
                        )
                        < best.get(
                            "hops",
                            999999,
                        )
                    ):

                        best = candidate

                return best

            confirmed_ancestor = None

            # ---------------------------------------------------------
            # For addresses we keep ALL distinct ancestors found.
            # For normal transactions we continue using the nearest
            # confirmed ancestor.
            # ---------------------------------------------------------

            confirmed_ancestors = []

            # ---------------------------------------------------------
            # Normal transaction BEEF.
            # ---------------------------------------------------------

            if not is_address_beef:

                subject_txid = self.beef_data.get(
                    "txid"
                )

                if (
                    subject_txid
                    and not subject_confirmed
                    and transactions
                ):

                    confirmed_ancestor = (
                        find_anchor(
                            subject_txid
                        )
                    )

            # ---------------------------------------------------------
            # Address BEEF.
            #
            # Search every unconfirmed transaction and find its nearest
            # confirmed proof anchor.
            #
            # IMPORTANT:
            # Keep every distinct anchor rather than collapsing them
            # into one confirmed_ancestor.
            # ---------------------------------------------------------

            else:

                address_anchor_candidates = []

                for item in transactions:

                    item_txid = item.get(
                        "txid"
                    )

                    if not item_txid:

                        continue

                    item_height = item.get(
                        "blockheight",
                        -1,
                    )

                    try:
                        item_height = int(
                            item_height
                        )
                    except Exception:
                        item_height = -1

                    item_confirmed = bool(
                        item.get(
                            "confirmed",
                            False,
                        )
                    )

                    if (
                        not item_confirmed
                        or item_height <= 0
                    ):

                        candidate = find_anchor(
                            item_txid
                        )

                        if candidate:

                            address_anchor_candidates.append(
                                candidate
                            )

                # -----------------------------------------------------
                # Extract every distinct proof anchor.
                # -----------------------------------------------------

                seen_anchor_keys = set()

                for candidate in address_anchor_candidates:

                    for anchor in candidate.get(
                        "ancestors",
                        [],
                    ):

                        anchor_txid = anchor.get(
                            "txid",
                            "",
                        )

                        anchor_height = anchor.get(
                            "blockheight",
                            -1,
                        )

                        anchor_hops = anchor.get(
                            "hops",
                            candidate.get(
                                "hops",
                                999999,
                            ),
                        )

                        anchor_key = (
                            anchor_txid,
                            anchor_height,
                        )

                        if (
                            not anchor_txid
                            or anchor_key in seen_anchor_keys
                        ):

                            continue

                        seen_anchor_keys.add(
                            anchor_key
                        )

                        confirmed_ancestors.append({
                            "txid": anchor_txid,
                            "blockheight": anchor_height,
                            "hops": anchor_hops,
                        })

                # -----------------------------------------------------
                # Keep the old confirmed_ancestor variable pointing to
                # the nearest anchor, for compatibility with any logic
                # that still expects it.
                # -----------------------------------------------------

                if confirmed_ancestors:

                    nearest_anchor = min(
                        confirmed_ancestors,
                        key=lambda anchor: anchor.get(
                            "hops",
                            999999,
                        ),
                    )

                    confirmed_ancestor = {
                        "hops": nearest_anchor.get(
                            "hops",
                            999999,
                        ),
                        "ancestors": confirmed_ancestors,
                    }

            # =========================================================
            # ADDRESS BEEF
            # =========================================================

            if is_address_beef:

                self.scroll_area.setVisible(
                    True
                )

                # -----------------------------------------------------
                # Empty address.
                # -----------------------------------------------------

                if subject_state == "empty":

                    results.append(
                        "<span style='color:#777777'>"
                        "ℹ No wallet-owned UTXOs found"
                        "</span>"
                    )

                # -----------------------------------------------------
                # Confirmed address.
                # -----------------------------------------------------

                elif subject_state == "confirmed":

                    results.append(
                        "<span style='color:green'>"
                        "<b>✓ Confirmed</b>"
                        "</span>"
                    )

                    results.append(
                        "<span style='color:green'>"
                        f"✓ {len(confirmed_utxos)} "
                        "confirmed UTXO"
                        + (
                            ""
                            if len(
                                confirmed_utxos
                            ) == 1
                            else "s"
                        )
                        + "</span>"
                    )

                    results.append(
                        "<span style='color:#777777'>"
                        "All wallet-owned UTXOs for this "
                        "address are confirmed."
                        "</span>"
                    )

                # -----------------------------------------------------
                # Entirely unconfirmed address.
                # -----------------------------------------------------

                elif subject_state == "unconfirmed":

                    results.append(
                        "<span style='color:orange'>"
                        "<b>⚠ Unconfirmed</b>"
                        "</span>"
                    )

                    results.append(
                        "<span style='color:#777777'>"
                        f"{len(unconfirmed_utxos)} "
                        "unconfirmed UTXO"
                        + (
                            ""
                            if len(
                                unconfirmed_utxos
                            ) == 1
                            else "s"
                        )
                        + "</span>"
                    )

                    if confirmed_ancestors:

                        for anchor in confirmed_ancestors:

                            anchor_height = anchor.get(
                                "blockheight",
                                -1,
                            )

                            anchor_txid = anchor.get(
                                "txid",
                                "",
                            )

                            anchor_hops = anchor.get(
                                "hops",
                                0,
                            )

                            if anchor_hops == 1:

                                hop_text = "1 hop"

                            else:

                                hop_text = (
                                    f"{anchor_hops} hops"
                                )

                            results.append(
                                "<span style='color:green'>"
                                f"✓ Proof anchor: "
                                f"{hop_text} away"
                                "</span>"
                            )

                            results.append(
                                "<span style='color:#777777'>"
                                f"Block {anchor_height}"
                                "</span>"
                            )

                            if anchor_txid:

                                results.append(
                                    "<span style='color:#777777'>"
                                    "Proof anchor TXID: "
                                    "<span style='font-family:monospace'>"
                                    f"{anchor_txid}"
                                    "</span>"
                                    "</span>"
                                )

                    else:

                        results.append(
                            "<span style='color:#777777'>"
                            "ℹ No confirmed proof anchor "
                            "found in the dependency chain."
                            "</span>"
                        )

                # -----------------------------------------------------
                # Mixed address.
                # -----------------------------------------------------

                elif subject_state == "mixed":

                    results.append(
                        "<span style='color:orange'>"
                        "<b>⚠ Mixed confirmation state</b>"
                        "</span>"
                    )

                    results.append(
                        "<span style='color:green'>"
                        f"✓ {len(confirmed_utxos)} "
                        "confirmed UTXO"
                        + (
                            ""
                            if len(
                                confirmed_utxos
                            ) == 1
                            else "s"
                        )
                        + "</span>"
                    )

                    results.append(
                        "<span style='color:orange'>"
                        f"⚠ {len(unconfirmed_utxos)} "
                        "unconfirmed UTXO"
                        + (
                            ""
                            if len(
                                unconfirmed_utxos
                            ) == 1
                            else "s"
                        )
                        + "</span>"
                    )

                    if confirmed_ancestors:

                        for anchor in confirmed_ancestors:

                            anchor_height = anchor.get(
                                "blockheight",
                                -1,
                            )

                            anchor_txid = anchor.get(
                                "txid",
                                "",
                            )

                            anchor_hops = anchor.get(
                                "hops",
                                0,
                            )

                            if anchor_hops == 1:

                                hop_text = "1 hop"

                            else:

                                hop_text = (
                                    f"{anchor_hops} hops"
                                )

                            results.append(
                                "<span style='color:green'>"
                                "✓ Proof anchor for "
                                "unconfirmed chain: "
                                f"{hop_text} away"
                                "</span>"
                            )

                            results.append(
                                "<span style='color:#777777'>"
                                f"Block {anchor_height}"
                                "</span>"
                            )

                            if anchor_txid:

                                results.append(
                                    "<span style='color:#777777'>"
                                    "Proof anchor TXID: "
                                    "<span style='font-family:monospace'>"
                                    f"{anchor_txid}"
                                    "</span>"
                                    "</span>"
                                )

                    else:

                        results.append(
                            "<span style='color:#777777'>"
                            "ℹ No confirmed proof anchor "
                            "found for the unconfirmed "
                            "portion of this address."
                            "</span>"
                        )

                # -----------------------------------------------------
                # Verify every confirmed address UTXO individually.
                # -----------------------------------------------------

                for entry in confirmed_utxos:

                    entry_txid = entry.get(
                        "txid"
                    )

                    blockheight = entry.get(
                        "blockheight",
                        -1,
                    )

                    try:
                        blockheight = int(
                            blockheight
                        )
                    except Exception:
                        blockheight = -1

                    if (
                        not entry_txid
                        or blockheight <= 0
                    ):

                        continue

                    merkle = entry.get(
                        "merkle"
                    )

                    header_hex = entry.get(
                        "header"
                    )

                    electrumx_ok = False
                    local_ok = False

                    # -------------------------------------------------
                    # ElectrumX header
                    # -------------------------------------------------

                    try:

                        response = electrum_request(
                            "blockchain.block.header",
                            [blockheight],
                        )

                        electrumx_header = (
                            response.get(
                                "result"
                            )
                        )

                        if (
                            electrumx_header
                            and header_hex
                        ):

                            electrumx_ok = (
                                header_hex.lower()
                                == electrumx_header.lower()
                            )


                    except Exception as e:

                        print(
                            "ElectrumX header verification exception:",
                            repr(e),
                        )

                    # -------------------------------------------------
                    # Local Headers
                    # -------------------------------------------------

                    try:

                        if (
                            merkle
                            and header_hex
                        ):

                            header_bytes = (
                                read_header_from_file(
                                    blockheight
                                )
                            )

                            computed_root = (
                                compute_merkle_root(
                                    entry_txid,
                                    merkle.get(
                                        "merkle",
                                        [],
                                    ),
                                    merkle.get(
                                        "pos",
                                        0,
                                    ),
                                )
                            )

                            local_root = (
                                merkle_root_from_header(
                                    header_bytes
                                )
                            )

                            local_ok = (
                                computed_root
                                == local_root
                            )

                    except Exception as e:

                        print(
                            "Local Headers verification exception:",
                            repr(e),
                        )

                    # -------------------------------------------------
                    # Display verification result.
                    # -------------------------------------------------

                    tx_label = QLabel(
                        (
                            "<b>Confirmed UTXO</b> "
                            "<span style='font-family:monospace'>"
                            f"{entry_txid}"
                            "</span>"
                        )
                    )

                    tx_label.setTextFormat(
                        Qt.RichText
                    )

                    tx_label.setTextInteractionFlags(
                        Qt.TextSelectableByMouse
                    )

                    tx_label.setWordWrap(
                        True
                    )

                    self.result_layout.addWidget(
                        tx_label
                    )

                    ex_text = (
                        "<span style='color:green'>"
                        "✓ Verified"
                        "</span>"
                        if electrumx_ok
                        else
                        "<span style='color:red'>"
                        "✗ Failed"
                        "</span>"
                    )

                    local_text = (
                        "<span style='color:green'>"
                        "✓ Verified"
                        "</span>"
                        if local_ok
                        else
                        "<span style='color:red'>"
                        "✗ Failed"
                        "</span>"
                    )

                    verification_label = QLabel(
                        (
                            f"{ex_text} "
                            "(ElectrumX header) | "
                            f"{local_text} "
                            f"(Local Headers, Block "
                            f"{blockheight})"
                        )
                    )

                    verification_label.setTextFormat(
                        Qt.RichText
                    )

                    verification_label.setTextInteractionFlags(
                        Qt.TextSelectableByMouse
                    )

                    verification_label.setWordWrap(
                        True
                    )

                    self.result_layout.addWidget(
                        verification_label
                    )

                # -----------------------------------------------------
                # Display every address UTXO.
                # -----------------------------------------------------

                for utxo in utxos:

                    utxo_txid = utxo.get(
                        "txid",
                        "",
                    )

                    vout = utxo.get(
                        "vout",
                        -1,
                    )

                    value = utxo.get(
                        "satoshis",
                        0,
                    )

                    height = utxo.get(
                        "blockheight",
                        -1,
                    )

                    try:
                        height = int(
                            height
                        )
                    except Exception:
                        height = -1

                    amount_text = (
                        app_state.format_amount(
                            value,
                            whitespaces=True,
                        )
                    )

                    status = (
                        "<span style='color:green'>"
                        "Confirmed"
                        "</span>"
                        if height > 0
                        else
                        "<span style='color:orange'>"
                        "Unconfirmed"
                        "</span>"
                    )

                    utxo_label = QLabel(
                        (
                            f"{utxo_txid[:12]}... "
                            f"vout {vout}: "
                            f"{amount_text} — "
                            f"{status}"
                        )
                    )

                    utxo_label.setTextFormat(
                        Qt.RichText
                    )

                    utxo_label.setTextInteractionFlags(
                        Qt.TextSelectableByMouse
                    )

                    utxo_label.setWordWrap(
                        True
                    )

                    self.result_layout.addWidget(
                        utxo_label
                    )

                # -----------------------------------------------------
                # Add summary lines at top.
                # -----------------------------------------------------

                for text in reversed(
                    results
                ):

                    lbl = QLabel(
                        text
                    )

                    lbl.setTextFormat(
                        Qt.RichText
                    )

                    lbl.setTextInteractionFlags(
                        Qt.TextSelectableByMouse
                    )

                    lbl.setWordWrap(
                        True
                    )

                    self.result_layout.insertWidget(
                        0,
                        lbl
                    )

                return

            # =========================================================
            # NORMAL TRANSACTION BEEF
            #
            # This handles:
            #
            #   * confirmed + unspent
            #   * confirmed + spent
            #   * confirmed + mixed
            #   * unconfirmed + UTXO
            #   * unconfirmed + no UTXO
            #
            # The presence of utxos[] is NOT used to decide whether
            # the transaction itself should be verified.
            # =========================================================

            txid = self.beef_data.get(
                "txid"
            )

            subject_merkle = self.beef_data.get(
                "merkle"
            )

            subject_header = self.beef_data.get(
                "header"
            )

            subject_blockheight = self.beef_data.get(
                "blockheight",
                -1,
            )

            try:
                subject_blockheight = int(
                    subject_blockheight
                )
            except Exception:
                subject_blockheight = -1

            subject_confirmed = bool(
                self.beef_data.get(
                    "confirmed",
                    False,
                )
            )

            # ---------------------------------------------------------
            # If subject proof is not at top level, find it in
            # transactions[].
            # ---------------------------------------------------------

            subject_entry = None

            for tx_entry in transactions:

                if (
                    tx_entry.get(
                        "txid"
                    )
                    == txid
                ):

                    subject_entry = tx_entry
                    break

            if subject_entry is not None:

                if not subject_merkle:

                    subject_merkle = (
                        subject_entry.get(
                            "merkle"
                        )
                    )

                if not subject_header:

                    subject_header = (
                        subject_entry.get(
                            "header"
                        )
                    )

                if subject_blockheight <= 0:

                    try:

                        subject_blockheight = int(
                            subject_entry.get(
                                "blockheight",
                                -1,
                            )
                        )

                    except Exception:

                        subject_blockheight = -1

                if subject_entry.get(
                    "confirmed",
                    False,
                ):

                    subject_confirmed = True

            # =========================================================
            # CONFIRMED TRANSACTION
            # =========================================================

            if (
                subject_confirmed
                and subject_merkle
                and subject_header
                and subject_blockheight > 0
            ):

                blockheight = (
                    subject_blockheight
                )

                electrumx_ok = False
                local_ok = False

                # -----------------------------------------------------
                # ElectrumX header verification
                # -----------------------------------------------------

                try:

                    response = electrum_request(
                        "blockchain.block.header",
                        [blockheight],
                    )

                    electrumx_header = (
                        response.get(
                            "result"
                        )
                    )

                    if electrumx_header:

                        electrumx_ok = (
                            subject_header.lower()
                            == electrumx_header.lower()
                        )

                except Exception as e:

                    print(
                        "ElectrumX header verification exception:",
                        repr(e),
                    )

                # -----------------------------------------------------
                # Local Headers verification
                # -----------------------------------------------------

                try:

                    header_bytes = (
                        read_header_from_file(
                            blockheight
                        )
                    )

                    computed_root = (
                        compute_merkle_root(
                            txid,
                            subject_merkle.get(
                                "merkle",
                                [],
                            ),
                            subject_merkle.get(
                                "pos",
                                0,
                            ),
                        )
                    )

                    local_root = (
                        merkle_root_from_header(
                            header_bytes
                        )
                    )

                    local_ok = (
                        computed_root
                        == local_root
                    )

                except Exception as e:

                    print(
                        "Local Headers verification exception:",
                        repr(e),
                    )

                self.scroll_area.setVisible(
                    True
                )

                tx_status = (
                    "<span style='color:green'>"
                    "<b>✓ Confirmed</b>"
                    "</span>"
                )

                tx_label = QLabel(
                    (
                        f"{tx_status} "
                        "<span style='font-family:monospace'>"
                        f"{txid}"
                        "</span>"
                    )
                )

                tx_label.setTextFormat(
                    Qt.RichText
                )

                tx_label.setTextInteractionFlags(
                    Qt.TextSelectableByMouse
                )

                tx_label.setWordWrap(
                    True
                )

                self.result_layout.addWidget(
                    tx_label
                )

                ex_text = (
                    "<span style='color:green'>"
                    "✓ Verified"
                    "</span>"
                    if electrumx_ok
                    else
                    "<span style='color:red'>"
                    "✗ Failed"
                    "</span>"
                )

                local_text = (
                    "<span style='color:green'>"
                    "✓ Verified"
                    "</span>"
                    if local_ok
                    else
                    "<span style='color:red'>"
                    "✗ Failed"
                    "</span>"
                )

                verification_label = QLabel(
                    (
                        f"{ex_text} "
                        "(ElectrumX header) | "
                        f"{local_text} "
                        f"(Local Headers, Block "
                        f"{blockheight})"
                    )
                )

                verification_label.setTextFormat(
                    Qt.RichText
                )

                verification_label.setTextInteractionFlags(
                    Qt.TextSelectableByMouse
                )

                verification_label.setWordWrap(
                    True
                )

                self.result_layout.addWidget(
                    verification_label
                )

            # =========================================================
            # UNCONFIRMED TRANSACTION
            #
            # Do not attempt direct Merkle verification.
            #
            # Instead report the confirmed proof anchor.
            # =========================================================

            elif not subject_confirmed:

                self.scroll_area.setVisible(
                    True
                )

                results.append(
                    "<span style='color:orange'>"
                    "<b>⚠ Unconfirmed</b>"
                    "</span>"
                )

                if confirmed_ancestor:

                    hops = confirmed_ancestor.get(
                        "hops",
                        0,
                    )

                    if hops == 1:

                        hop_text = "1 hop"

                    else:

                        hop_text = (
                            f"{hops} hops"
                        )

                    results.append(
                        "<span style='color:green'>"
                        f"✓ Proof anchor: "
                        f"{hop_text} away"
                        "</span>"
                    )

                    for anchor in confirmed_ancestor.get(
                        "ancestors",
                        [],
                    ):

                        anchor_height = anchor.get(
                            "blockheight",
                            -1,
                        )

                        anchor_txid = anchor.get(
                            "txid",
                            "",
                        )

                        results.append(
                            "<span style='color:#777777'>"
                            f"Block {anchor_height}"
                            "</span>"
                        )

                        if anchor_txid:

                            results.append(
                                "<span style='color:#777777'>"
                                "Proof anchor TXID: "
                                "<span style='font-family:monospace'>"
                                f"{anchor_txid}"
                                "</span>"
                                "</span>"
                            )

                else:

                    results.append(
                        "<span style='color:#777777'>"
                        "ℹ No confirmed proof anchor "
                        "found in the dependency chain."
                        "</span>"
                    )

                results.append(
                    "<span style='color:#777777'>"
                    "The subject transaction itself is "
                    "not yet included in a block."
                    "</span>"
                )

            # =========================================================
            # DISPLAY TRANSACTION WALLET OUTPUTS
            # =========================================================

            wallet_outputs = []

            try:

                import sqlite3

                wallet_db_path = None

                try:

                    wallet = (
                        self.account._wallet
                    )

                    if wallet is not None:

                        storage = (
                            wallet.get_storage()
                        )

                        if storage is not None:

                            wallet_db_path = (
                                storage.get_path()
                            )

                            if (
                                wallet_db_path
                                and not wallet_db_path.lower().endswith(
                                    ".sqlite"
                                )
                            ):

                                wallet_db_path += (
                                    ".sqlite"
                                )

                except Exception as e:

                    print(
                        "Could not resolve wallet database:",
                        repr(e),
                    )

                if wallet_db_path and txid:

                    tx_hash_bytes = (
                        bytes.fromhex(
                            txid
                        )[::-1]
                    )

                    conn = sqlite3.connect(
                        wallet_db_path
                    )

                    try:

                        wallet_outputs = conn.execute(
                            """
                            SELECT
                                tx_index,
                                value,
                                keyinstance_id,
                                flags
                            FROM TransactionOutputs
                            WHERE tx_hash = ?
                            ORDER BY tx_index
                            """,
                            (
                                tx_hash_bytes,
                            ),
                        ).fetchall()

                    finally:

                        conn.close()

            except Exception as e:

                print(
                    "WALLET OUTPUT LOOKUP ERROR:",
                    repr(e),
                )

            for (
                vout,
                value,
                keyinstance_id,
                flags,
            ) in wallet_outputs:

                is_spent = bool(
                    flags & (1 << 2)
                )

                amount_text = (
                    app_state.format_amount(
                        value,
                        whitespaces=True,
                    )
                )

                if is_spent:

                    status_text = (
                        "<span style='color:#777777'>"
                        "Spent"
                        "</span>"
                    )

                else:

                    status_text = (
                        "<span style='color:green'>"
                        "Unspent"
                        "</span>"
                    )

                vout_label = QLabel(
                    (
                        f"vout {vout}: "
                        f"{amount_text} — "
                        f"{status_text}"
                    )
                )

                vout_label.setTextFormat(
                    Qt.RichText
                )

                vout_label.setTextInteractionFlags(
                    Qt.TextSelectableByMouse
                )

                vout_label.setWordWrap(
                    True
                )

                self.result_layout.addWidget(
                    vout_label
                )

            if not wallet_outputs:

                no_outputs_label = QLabel(
                    "<span style='color:#777777'>"
                    "ℹ No wallet-owned outputs found"
                    "</span>"
                )

                no_outputs_label.setTextFormat(
                    Qt.RichText
                )

                no_outputs_label.setTextInteractionFlags(
                    Qt.TextSelectableByMouse
                )

                self.result_layout.addWidget(
                    no_outputs_label
                )

            # =========================================================
            # ADD TRANSACTION STATUS / ANCHOR RESULTS
            # =========================================================

            for text in results:

                lbl = QLabel(
                    text
                )

                lbl.setTextFormat(
                    Qt.RichText
                )

                lbl.setTextInteractionFlags(
                    Qt.TextSelectableByMouse
                )

                lbl.setWordWrap(
                    True
                )

                self.result_layout.addWidget(
                    lbl
                )

            self.scroll_area.setVisible(
                True
            )

            return



        def copy_to_clipboard(self):
            QApplication.clipboard().setText(
                self.text.toPlainText()
            )


        def save_to_file(self):
            desktop_dir = os.path.expanduser(
                "~/Desktop"
            )

            os.makedirs(
                desktop_dir,
                exist_ok=True
            )

            default_filename = self.beef_data.get(
                "address",
                self.beef_data.get(
                    "txid",
                    "proof"
                ),
            )

            default_path = os.path.join(
                desktop_dir,
                f"{default_filename}.json",
            )

            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save BREAD Proof",
                default_path,
                "JSON Files (*.json)",
            )

            if path:

                with open(
                    path,
                    "w"
                ) as f:

                    f.write(
                        self.text.toPlainText()
                    )


    dialog = SimpleVerificationWindow(
        account,
        beef_data,
        tx_obj,
        address
    )

    dialog.resize(
        900,
        550
    )

    dialog.exec_()
