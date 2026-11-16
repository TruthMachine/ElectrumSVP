"""Read-only previous-transaction lookup for transaction inspection."""
from concurrent.futures import ThreadPoolExecutor, as_completed

from bitcoinx import hash_to_hex_str

from electrumsv.transaction import Transaction


def input_values(tx, parents):
    """Only an exact previous transaction and a valid output index prove a value."""
    values = {}
    for index, txin in enumerate(tx.inputs):
        if txin.is_coinbase():
            continue
        parent = parents.get(txin.prev_hash)
        if parent is not None and parent.hash() == txin.prev_hash and \
                0 <= txin.prev_idx < len(parent.outputs):
            values[index] = parent.outputs[txin.prev_idx].value
    return values


def load_previous_transactions(tx, known, read_local, request_remote, stopped):
    """Deduplicate requests, bound concurrent I/O and never write to the wallet."""
    wanted = {txin.prev_hash for txin in tx.inputs if not txin.is_coinbase()}
    parents = {}
    for tx_hash in wanted:
        if stopped.is_set():
            return parents
        parent = known.get(tx_hash)
        if parent is None:
            try:
                parent = read_local(tx_hash)
            except Exception:
                parent = None
        if parent is not None and parent.hash() == tx_hash:
            parents[tx_hash] = parent
    if request_remote is None or stopped.is_set():
        return parents

    def fetch(tx_hash):
        if stopped.is_set():
            return None
        raw = request_remote('blockchain.transaction.get', [hash_to_hex_str(tx_hash)])
        parent = Transaction.from_hex(raw)
        return parent if parent.hash() == tx_hash else None

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='input-inspection') as pool:
        pending = {pool.submit(fetch, tx_hash): tx_hash for tx_hash in wanted - parents.keys()}
        for future in as_completed(pending):
            if stopped.is_set():
                for remaining in pending:
                    remaining.cancel()
                break
            try:
                parent = future.result()
            except Exception:
                continue  # The view offers a retry; unknown is never represented as zero.
            if parent is not None:
                parents[pending[future]] = parent
    return parents
