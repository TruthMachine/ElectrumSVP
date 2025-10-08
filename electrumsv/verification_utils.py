import ssl
import socket
import json
import os
import hashlib
import random
from bitcoinx import Address, Bitcoin
from PyQt5.QtWidgets import (
        QWidget, QPushButton, QVBoxLayout, QTextEdit, QDialog, QLabel,
        QHBoxLayout, QSizePolicy, QApplication, QFileDialog, QScrollArea, QCheckBox
)
from PyQt5.QtCore import Qt

from electrumsv.gui.qt.util import read_QIcon
from electrumsv.i18n import _

from concurrent.futures import ThreadPoolExecutor
import time

# --- ElectrumX servers ---
SERVERS = {
    "electrumx.gorillapool.io": {"s": 50002, "t": 50001},
    "electrum.api.sv": {"s": 50002, "t": 50001},
    "neptune.api.sv": {"s": 50002, "t": 50001},
    "alpha-esv.api.sv": {"s": 50002, "t": 50001},
    "sv.satoshi.io": {"s": 50002, "t": 50001},
    "sv2.satoshi.io": {"s": 50002, "t": 50001},
    "electrum.server.sv": {"s": 50002, "t": 50001},
    "bsv.aftrek.org": {"s": 50002, "t": 50001},
}

TIMEOUT = 30  # seconds

# --- Paths ---
POSSIBLE_HEADER_PATHS = [
    os.path.expanduser("~/.electrum-sv/headers"),
    os.path.expanduser("~/.electrum-sv/headers-electrumsv"),
    os.path.expanduser("~/.electrumsv/headers"),
]
MERKLE_CACHE_PATH = os.path.expanduser("~/.electrum-sv/cache/merkle_cache.json")
ADDRESS_BEEF_CACHE_PATH = os.path.expanduser("~/.electrum-sv/cache/merkle_address_cache.json")


def extract_time_from_header(header_bytes: bytes) -> int:
    """Extract block timestamp from 80-byte header."""
    if len(header_bytes) != 80:
        raise ValueError("Header must be 80 bytes")
    return int.from_bytes(header_bytes[68:72], byteorder="little")


def find_headers_file():
    for path in POSSIBLE_HEADER_PATHS:
        if os.path.exists(path):
            return path
    return None

HEADERS_PATH = find_headers_file()


# --- Caching ---
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


# --- Networking ---
def electrum_request(method, params, servers=None):
    import socks  # PySocks is bundled with ElectrumSV builds
    if servers is None:
        servers = SERVERS

    clean_params = [
        p.hex() if isinstance(p, bytes) else p.lower() if isinstance(p, str) else p
        for p in params
    ]
    req = {"id": 0, "method": method, "params": clean_params}
    msg = (json.dumps(req) + "\n").encode()
    server_items = list(servers.items())
    random.shuffle(server_items)
    last_error = None

    # --- Detect TAILS or Tor ---
    use_tor = os.path.exists("/etc/os-release") and "TAILS" in open("/etc/os-release").read().upper()
    tor_proxy = ("127.0.0.1", 9050)
    if not use_tor:
        # fallback: detect running Tor daemon manually
        import socket
        try:
            with socket.create_connection(tor_proxy, timeout=1):
                use_tor = True
        except Exception:
            pass

    for host, info in server_items:
        for port_key in ("s", "t"):  # try SSL first, then TCP
            port = info.get(port_key)
            if not port:
                continue
            try:
                # --- Create connection (Tor-aware) ---
                if use_tor:
                    sock = socks.socksocket()
                    sock.set_proxy(socks.SOCKS5, tor_proxy[0], tor_proxy[1])
                    sock.settimeout(TIMEOUT)
                    sock.connect((host, port))
                else:
                    sock = socket.create_connection((host, port), timeout=TIMEOUT)

                if port_key == "s":
                    context = ssl._create_unverified_context()
                    ssock = context.wrap_socket(sock, server_hostname=host)
                else:
                    ssock = sock

                ssock.sendall(msg)
                data = b""
                while not data.endswith(b"\n"):
                    chunk = ssock.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                ssock.close()
                return json.loads(data.decode())

            except Exception as e:
                last_error = e
                continue

    raise ConnectionError(f"All servers failed for method {method} with params {params}. Last error: {last_error}")


# --- Utilities ---
def scripthash_from_address(address: str) -> str:
    addr = Address.from_string(address, Bitcoin)
    script_bytes = addr.to_script().to_bytes()
    return hashlib.sha256(script_bytes).digest()[::-1].hex()

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

def read_header_from_file(height: int) -> bytes:
    if HEADERS_PATH is None:
        raise FileNotFoundError("No local headers file found.")
    with open(HEADERS_PATH, "rb") as f:
        f.seek(height * 80)
        header = f.read(80)
        if len(header) != 80:
            raise ValueError(f"Header for block {height} not found.")
        return header

def merkle_root_from_header(header_bytes: bytes) -> bytes:
    return header_bytes[36:68]

def verify_merkle(txid: str, merkle_proof: dict, header_hex: str) -> bool:
    if not merkle_proof or not header_hex:
        return False
    try:
        h = le(txid)
        index = merkle_proof.get("pos", 0)
        for sibling_hex in merkle_proof.get("merkle", []):
            sibling = le(sibling_hex)
            h = double_sha256(h + sibling) if index % 2 == 0 else double_sha256(sibling + h)
            index >>= 1
        header_bytes = bytes.fromhex(header_hex)
        return h == header_bytes[36:68]
    except Exception:
        return False



def build_beef(tx, slim=False, retries=3, delay=0.5):
    txid = tx.txid()
    cached_entry = CACHE.get(txid, {})
    hex_cache = cached_entry.get("hex")

    utxos_by_id = {}
    blockheight_to_utxos = {}
    scripthash_to_vouts = {}

    # --- Collect outputs & merge cached ---
    for vout_index, txout in enumerate(tx.outputs):
        try:
            script_bytes = txout.script_pubkey.to_bytes()
            scripthash = hashlib.sha256(script_bytes).digest()[::-1].hex()
            scripthash_to_vouts.setdefault(scripthash, []).append(vout_index)

            cached_utxo = next(
                (u for u in cached_entry.get("utxos", []) if u.get("txid") == txid and u.get("vout") == vout_index),
                None
            )
            if cached_utxo:
                entry = cached_utxo.copy()
                if slim:
                    entry.pop("merkle", None)
                    entry.pop("header", None)
            else:
                entry = {"txid": txid, "vout": vout_index, "satoshis": txout.value, "blockheight": -1, "unverifiable": True, "time": None}

            utxos_by_id[(txid, vout_index)] = entry
        except Exception as e:
            utxos_by_id[(txid, vout_index)] = {"error": str(e)}

    # --- Fetch live UTXOs ---
    def fetch_scripthash(sh, vouts):
        need_query = any(
            ("unverifiable" in utxos_by_id[(txid, v)] or
             (not slim and (utxos_by_id[(txid, v)].get("blockheight", -1) > 0 and
                            ("header" not in utxos_by_id[(txid, v)] or "merkle" not in utxos_by_id[(txid, v)]))))
            for v in vouts
        )
        if not need_query:
            return []

        for _ in range(retries):
            try:
                return electrum_request("blockchain.scripthash.listunspent", [sh]).get("result", [])
            except Exception:
                time.sleep(delay)
        return []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_scripthash, sh, vouts): (sh, vouts) for sh, vouts in scripthash_to_vouts.items()}
        for future in futures:
            live_utxos = future.result()
            sh, vouts = futures[future]
            for utxo in live_utxos:
                utxo_txid = utxo.get("tx_hash", "").lower()
                utxo_vout = utxo.get("tx_pos")
                if utxo_vout not in vouts or utxo_txid != txid.lower():
                    continue
                entry = utxos_by_id.get((txid, utxo_vout))
                if not entry:
                    continue
                entry["satoshis"] = utxo.get("value", entry.get("satoshis", 0))
                entry["blockheight"] = utxo.get("height", -1)
                entry.pop("unverifiable", None)
                entry["time"] = None  # placeholder until header fetched
                if not slim and entry["blockheight"] > 0:
                    blockheight_to_utxos.setdefault(entry["blockheight"], []).append(entry)

    # --- Fetch headers & merkle ---
    if not slim and blockheight_to_utxos:
        headers_cache = {}

        def fetch_header(height):
            for _ in range(retries):
                try:
                    res = electrum_request("blockchain.block.header", [height]).get("result")
                    if res and len(res) == 160:
                        return res
                except Exception:
                    pass
                time.sleep(delay)
            return None

        def fetch_merkle(txid_, height):
            for _ in range(retries):
                try:
                    proof = electrum_request("blockchain.transaction.get_merkle", [txid_, height]).get("result")
                    if proof:
                        return proof
                except Exception:
                    pass
                time.sleep(delay)
            return None

        txids_to_fetch = [(e["txid"], e["blockheight"])
                          for entries in blockheight_to_utxos.values()
                          for e in entries if "merkle" not in e or "header" not in e]

        CHUNK_SIZE = 20
        for i in range(0, len(txids_to_fetch), CHUNK_SIZE):
            chunk = txids_to_fetch[i:i+CHUNK_SIZE]
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(lambda t: (t[0], t[1], fetch_header(t[1]), fetch_merkle(t[0], t[1])), chunk))
                for txid_, height_, header, proof in results:
                    headers_cache[height_] = header
                    for entries in blockheight_to_utxos.values():
                        for e in entries:
                            if e["txid"] == txid_:
                                e["merkle"] = proof
                                e["header"] = header
                                # --- Add timestamp ---
                                if header:
                                    try:
                                        e["time"] = extract_time_from_header(bytes.fromhex(header))
                                    except Exception:
                                        e["time"] = None

    # --- Deduplicate ---
    unique_utxos = []
    seen = set()
    for u in utxos_by_id.values():
        key = (u.get("txid"), u.get("vout"))
        if key not in seen:
            seen.add(key)
            unique_utxos.append(u)

    # --- Include hex ---
    if not slim and not hex_cache:
        try:
            hex_cache = tx.to_hex()
        except Exception:
            hex_cache = None

    beef_data = {"format": "BEEF", "version": 1, "txid": txid, "utxos": unique_utxos}
    if hex_cache:
        beef_data["hex"] = hex_cache

    CACHE[txid] = beef_data
    save_cache(CACHE, MERKLE_CACHE_PATH)
    return beef_data


def build_beef_for_address(address: str, slim=False, retries=3, delay=0.5):
    scripthash = scripthash_from_address(address)

    # --- Fetch live UTXOs from ElectrumX ---
    live_utxos = fetch_scripthash_utxos_with_retry(scripthash, retries=retries, delay=delay)
    utxos_by_id = {}
    blockheight_to_utxos = {}

    for utxo in live_utxos:
        utxo_id = (utxo.get("txid", utxo.get("tx_hash")), utxo.get("vout", utxo.get("tx_pos")))
        entry = {
            "txid": utxo.get("txid", utxo.get("tx_hash")),
            "vout": utxo.get("vout", utxo.get("tx_pos")),
            "satoshis": utxo.get("value", utxo.get("satoshis", 0)),
            "blockheight": utxo.get("height", utxo.get("blockheight", -1)),
            "time": None,
        }

        # If blockheight <=0 mark unverifiable
        if not slim and entry["blockheight"] <= 0:
            entry["unverifiable"] = True
        elif slim:
            entry.pop("unverifiable", None)
            entry.pop("merkle", None)
            entry.pop("header", None)
        elif not slim and entry["blockheight"] > 0:
            blockheight_to_utxos.setdefault(entry["blockheight"], []).append(entry)

        utxos_by_id[utxo_id] = entry

    # --- Fetch headers & merkle for confirmed utxos ---
    if not slim and blockheight_to_utxos:
        headers_cache = {}

        def fetch_header(height):
            for _ in range(retries):
                try:
                    res = electrum_request("blockchain.block.header", [height]).get("result")
                    if res and len(res) == 160:
                        return res
                except Exception:
                    pass
                time.sleep(delay)
            return None

        def fetch_merkle(txid_, height):
            for _ in range(retries):
                try:
                    proof = electrum_request("blockchain.transaction.get_merkle", [txid_, height]).get("result")
                    if proof:
                        return proof
                except Exception:
                    pass
                time.sleep(delay)
            return None

        for height, entries in blockheight_to_utxos.items():
            header = headers_cache.get(height)
            if not header:
                header = fetch_header(height)
                headers_cache[height] = header

            txids = [e["txid"] for e in entries]
            if txids and header:
                with ThreadPoolExecutor(max_workers=8) as executor:
                    merkle_results = list(executor.map(lambda txid_: fetch_merkle(txid_, height), txids))
                    for e, proof in zip(entries, merkle_results):
                        e["header"] = header
                        e["merkle"] = proof
                        try:
                            e["time"] = extract_time_from_header(bytes.fromhex(header))
                        except Exception:
                            e["time"] = None

    # --- Deduplicate & build final list ---
    unique_utxos = []
    seen = set()
    for u in utxos_by_id.values():
        key = (u.get("txid"), u.get("vout"))
        if key not in seen:
            seen.add(key)
            unique_utxos.append(u)

    # --- Build BEEF data ---
    beef_data = {
        "format": "BEEF",
        "version": 1,
        "address": address,
        "utxos": unique_utxos,
    }

    # --- Replace old cache ---
    ADDRESS_CACHE[scripthash] = beef_data
    save_cache(ADDRESS_CACHE, ADDRESS_BEEF_CACHE_PATH)

    return beef_data



# --- Helper ---
def fetch_scripthash_utxos_with_retry(scripthash, retries=3, delay=0.5):
    for _ in range(retries):
        try:
            result = electrum_request("blockchain.scripthash.listunspent", [scripthash]).get("result", [])
            if result:
                return result
        except Exception:
            pass
        time.sleep(delay)
    return []




def open_simple_verification_window(main_window: QWidget, account, beef_or_tx=None, address=None):
    import copy, json, os
    from PyQt5.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton,
        QScrollArea, QLabel, QSizePolicy, QFileDialog, QCheckBox, QSplitter, QWidget, QApplication
    )
    from PyQt5.QtCore import Qt, QThread, pyqtSignal

    # --- Determine BEEF data ---
    if isinstance(beef_or_tx, dict) and beef_or_tx.get("format") == "BEEF":
        beef_data = copy.deepcopy(beef_or_tx)
        title_str = beef_data.get("address", beef_data.get("txid", ""))[:10] + "..."
        tx_obj = None
    elif beef_or_tx is not None:
        try:
            beef_data = build_beef(beef_or_tx, slim=False)
            title_str = getattr(beef_or_tx, "txid", lambda: "unknown")()[:10] + "..."
            tx_obj = beef_or_tx
        except Exception:
            beef_data = {"format": "BEEF", "version": 1, "utxos": []}
            title_str = "Unknown"
            tx_obj = None
    elif address is not None:
        beef_data = build_beef_for_address(address, slim=False)
        title_str = address[:10] + "..."
        tx_obj = None
    else:
        beef_data = {"format": "BEEF", "version": 1, "utxos": []}
        title_str = "Empty BEEF"
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
            self.setWindowTitle(f"BEEF Proof - {title_str}")
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
            self.verify_btn.setFixedHeight(26)
            bottom_layout.addWidget(self.verify_btn, stretch=4)
            self.verify_btn.clicked.connect(self.verify_proofs)

            right_layout = QVBoxLayout()
            right_layout.setSpacing(2)
            bottom_layout.addLayout(right_layout, stretch=0.1)
            self.copy_btn = QPushButton("Copy")
            self.copy_btn.setFixedHeight(20)
            self.save_btn = QPushButton("Save")
            self.save_btn.setFixedHeight(20)
            right_layout.addWidget(self.copy_btn)
            right_layout.addWidget(self.save_btn)
            self.copy_btn.clicked.connect(self.copy_to_clipboard)
            self.save_btn.clicked.connect(self.save_to_file)

            main_layout = QVBoxLayout(self)
            main_layout.setContentsMargins(6, 6, 6, 6)
            main_layout.setSpacing(6)
            main_layout.addLayout(checkbox_layout)
            main_layout.addWidget(main_splitter)
            main_layout.addLayout(bottom_layout)

            # initial view
            self.update_beef_view()

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
                    c.update({"format": "BEEF", "version": 1, "txid": txid})
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

            # --- Remove _confirmed_cache everywhere ---
            beef_view.pop("_confirmed_cache", None)
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

                # Remove per-UTXO hex to avoid duplication
                utxo.pop("hex", None)

            # --- Include hexes ---
            if include_hex:
                if self.full_beef.get("txid"):
                    txid_main = self.full_beef.get("txid")
                    main_hex = self._utxo_tx_cache.get(txid_main) or CACHE.get(txid_main, {}).get("hex")
                    if main_hex:
                        beef_view["hex"] = main_hex
                    else:
                        beef_view.pop("hex", None)
                elif self.full_beef.get("address"):
                    hexes = {}
                    for utxo in beef_view.get("utxos", []):
                        txid = utxo.get("txid")
                        if not txid:
                            continue
                        tx_hex = self._utxo_tx_cache.get(txid) or CACHE.get(txid, {}).get("hex")
                        if tx_hex:
                            hexes[txid] = tx_hex
                    if hexes:
                        beef_view["hexes"] = hexes
                    else:
                        beef_view.pop("hexes", None)
            else:
                beef_view.pop("hex", None) if "hex" in beef_view else None
                beef_view.pop("hexes", None) if "hexes" in beef_view else None

            self.beef_data = beef_view

            # --- Update text display ---
            vbar = self.text.verticalScrollBar()
            old_value = vbar.value()
            self.text.setPlainText(json.dumps(self.beef_data, indent=2))
            try:
                vbar.setValue(old_value)
            except Exception:
                pass

            self.verify_btn.setEnabled(include_merkle)




        def verify_proofs(self):
            while self.result_layout.count():
                item = self.result_layout.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()

            results = []
            blockheight_to_utxos = {}
            for utxo in self.beef_data.get("utxos", []):
                h = utxo.get("blockheight", -1)
                if h >= 0 and utxo.get("merkle"):
                    blockheight_to_utxos.setdefault(h, []).append(utxo)

            headers_cache = {}
            if HEADERS_PATH and blockheight_to_utxos:
                for height in blockheight_to_utxos.keys():
                    try:
                        headers_cache[height] = read_header_from_file(height)
                    except Exception:
                        headers_cache[height] = None

            for utxo in self.beef_data.get("utxos", []):
                blockheight = utxo.get("blockheight", -1)
                electrumx_ok = False
                local_ok = False
                if utxo.get("merkle") and utxo.get("header"):
                    electrumx_ok = verify_merkle(utxo.get("txid", ""), utxo["merkle"], utxo["header"])
                if HEADERS_PATH and utxo.get("merkle") and blockheight >= 0:
                    header_bytes = headers_cache.get(blockheight)
                    if header_bytes:
                        try:
                            computed_root = compute_merkle_root(
                                utxo.get("txid", ""), utxo["merkle"]["merkle"], utxo["merkle"]["pos"]
                            )
                            local_root = merkle_root_from_header(header_bytes)
                            local_ok = computed_root == local_root
                        except Exception:
                            local_ok = False

                # --- Build colored HTML text ---
                if not utxo.get("merkle"):
                    html_line = f"vout {utxo.get('vout')}: <span style='color:orange'>⚠ No Merkle proof available</span>"
                else:
                    ex_text = f"<span style='color:green'>✓ Verified</span>" if electrumx_ok else f"<span style='color:red'>✗ Failed</span>"
                    local_text = f"<span style='color:green'>✓ Verified</span>" if local_ok else f"<span style='color:red'>✗ Failed</span>"
                    html_line = (
                        f"vout {utxo.get('vout')}: "
                        f"{ex_text} (ElectrumX) | {local_text} (Local headers, Block {blockheight})"
                    )

                results.append(html_line)

            # --- Add labels to the layout ---
            if results:
                self.scroll_area.setVisible(True)
                for line in results:
                    lbl = QLabel(line)
                    lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                    lbl.setWordWrap(True)
                    lbl.setTextFormat(Qt.RichText)  # allow HTML coloring
                    self.result_layout.addWidget(lbl)
            else:
                self.scroll_area.setVisible(False)


        def copy_to_clipboard(self):
            QApplication.clipboard().setText(self.text.toPlainText())

        def save_to_file(self):
            desktop_dir = os.path.expanduser("~/Desktop")
            os.makedirs(desktop_dir, exist_ok=True)
            default_filename = self.beef_data.get("address", self.beef_data.get("txid", "proof"))
            default_path = os.path.join(desktop_dir, f"{default_filename}.json")
            path, _ = QFileDialog.getSaveFileName(self, "Save BEEF Proof", default_path, "JSON Files (*.json)")
            if path:
                with open(path, "w") as f:
                    f.write(self.text.toPlainText())

    dialog = SimpleVerificationWindow(account, beef_data, tx_obj, address)
    dialog.resize(900, 550)
    dialog.exec_()

