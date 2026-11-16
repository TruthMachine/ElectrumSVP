"""Mining rewards: output indices, maturity and previous-transaction encoding."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from bitcoinx import PrivateKey, Script
import pytest

from electrumsv.bitcoin import is_coinbase_mature
from electrumsv.chain_support import ChainTip, CoinbaseStatus
from electrumsv.constants import ScriptType, TransactionOutputFlag, TxFlags
from electrumsv.transaction import Transaction, XTxInput, XTxOutput
from electrumsv.wallet import AbstractAccount, Wallet
from electrumsv.wallet_database import (DatabaseContext, SynchronousWriter, TransactionCache,
    TransactionTable, TxData, TxProof)
from electrumsv.wallet_database.migration import create_database, update_database


@pytest.mark.parametrize('height,tip,mature', [
    (500, 598, False), (500, 599, True), (500, 600, True),
    (0, 1000, False), (-1, 1000, False), (None, 1000, False), (1001, 1000, False)])
def test_reward_balance_and_coin_selection_agree(height, tip, mature):
    reward = SimpleNamespace(tx_hash=b'c' * 32, value=1000, is_coinbase=True)
    status = CoinbaseStatus.MATURE if mature else CoinbaseStatus.IMMATURE
    if height is None or not 0 < height <= tip:
        status = CoinbaseStatus.UNVERIFIED
    account = SimpleNamespace(_utxos_lock=threading.RLock(), _utxos={0: reward},
        _wallet=SimpleNamespace(get_local_height=lambda: tip, get_coinbase_status=lambda _: status),
        get_transaction_metadata=lambda _: TxData(height=height))
    assert is_coinbase_mature(height, tip) == mature
    assert bool(AbstractAccount.get_utxos(account, mature=True)) == mature
    expected = (1000, 0, 0) if mature else (0, 0, 1000)
    if status == CoinbaseStatus.UNVERIFIED:
        expected = (0, 0, 0)
    assert AbstractAccount.get_balance(account) == expected


def coinbase_transaction():
    key = PrivateKey.from_int(1).public_key
    # Output 0 is data; the wallet's reward is output 2, not the block position (0).
    return Transaction(2, [XTxInput(bytes(32), 0xffffffff,
        Script(bytes.fromhex('0350c300')), 0xffffffff)], [
        XTxOutput(0, Script(bytes.fromhex('6a'))),
        XTxOutput(100, PrivateKey.from_int(2).public_key.P2PKH_script()),
        XTxOutput(900, key.P2PKH_script())], 0)


def test_coinbase_reward_index_and_flag_survive_wallet_indexing():
    tx = coinbase_transaction()
    assert tx.is_coinbase()
    account = Mock()
    account._sync_state.get_transaction_key_ids.return_value = [7]
    account._sync_state.get_key_history.return_value = []
    account.get_keyinstance.return_value = SimpleNamespace(keyinstance_id=7)
    account._get_cached_script.return_value = (tx.outputs[2].script_pubkey,
        bytes(tx.outputs[2].script_pubkey), None)
    account.get_utxo.return_value = None
    account.get_stxo.return_value = None
    account._keyinstances = {7: account.get_keyinstance.return_value}
    assert AbstractAccount._process_key_usage(account, tx.hash(), tx, [(2, tx.outputs[2])])
    args = account.create_transaction_output.call_args.args
    assert args[1] == 2
    assert args[2] == 900
    assert args[3] & TransactionOutputFlag.IS_COINBASE


@pytest.fixture
def reward_cache(tmp_path):
    context = DatabaseContext(str(tmp_path / 'rewards.sqlite'))
    connection = context.acquire_connection()
    create_database(connection)
    update_database(connection)
    store = TransactionTable(context)
    cache = TransactionCache(store)
    tx = coinbase_transaction()
    with SynchronousWriter() as writer:
        cache.add([(tx.hash(), TxData(height=500, position=0), tx,
            TxFlags.StateSettled, None)], completion_callback=writer.get_callback())
        assert writer.succeeded()
    with SynchronousWriter() as writer:
        cache.update_proof(tx.hash(), TxProof(0, [b'p' * 32]), writer.get_callback())
        assert writer.succeeded()
    yield cache, store, tx
    store.close()
    context.release_connection(connection)
    context.close()


def wallet_on_chain(cache, tip=599, *, roots=None, tip_hash=b't' * 32):
    from electrumsv.network import _root_from_proof
    if roots is None:
        roots = {}
        for tx_hash, metadata in cache.get_metadatas():
            proof = cache.get_proof(tx_hash)
            if proof is not None:
                roots[metadata.height] = _root_from_proof(tx_hash, proof.branch, proof.position)
    chain = SimpleNamespace(height=tip, header_at_height=lambda height: SimpleNamespace(
        height=height, hash=tip_hash if height == tip else bytes([height % 256]) * 32,
        merkle_root=roots.get(height, bytes(32))))
    view = ChainTip(chain, tip, tip_hash)
    wallet = SimpleNamespace(_transaction_cache=cache, get_local_height=lambda: tip,
        get_chain_tip=lambda: view)
    wallet.get_coinbase_status = lambda tx_hash, tip=None: Wallet.get_coinbase_status(wallet, tx_hash, tip)
    wallet.is_transaction_chain_valid = lambda tx_hash, **kw: Wallet.is_transaction_chain_valid(
        wallet, tx_hash, **kw)
    return wallet


def eligible(cache, tx_hash, tip=599, **kwargs):
    return wallet_on_chain(cache, tip).is_transaction_chain_valid(tx_hash, **kwargs)


def flush(store):
    with SynchronousWriter() as writer:
        store._db_context.queue_write(lambda db: None, writer.get_callback())
        assert writer.succeeded()


@pytest.mark.parametrize('cause', ['reorg', 'history_removed', 'height_changed'])
def test_reward_proof_revoked_persistently_and_reconfirmation_needs_new_proof(reward_cache, cause):
    cache, store, tx = reward_cache
    tx_hash = tx.hash()
    assert eligible(cache, tx_hash)
    assert not eligible(cache, tx_hash, tip=598)
    assert cache.get_proof(tx_hash) is not None
    with SynchronousWriter() as writer:
        callback = writer.get_callback()
        if cause == 'reorg':
            count, hashes = cache.apply_reorg(499, callback)
            assert (count, hashes) == (1, [tx_hash])
        elif cause == 'history_removed':
            assert cache.invalidate_coinbase(tx_hash, callback)
        else:
            cache.update([(tx_hash, TxData(height=501), None, TxFlags.HasHeight)], callback)
        assert writer.succeeded()
    assert not eligible(cache, tx_hash, tip=1000)
    assert cache.get_proof(tx_hash) is None
    reloaded = TransactionCache(store)
    assert reloaded.get_proof(tx_hash) is None
    assert reloaded.get_transaction(tx_hash).is_coinbase()
    assert not reloaded.settle_verified(tx_hash, 500, 0, TxProof(0, []))
    # A new reported height alone must not restore the old verified state.
    with SynchronousWriter() as writer:
        reloaded.update([(tx_hash, TxData(height=501), None, TxFlags.HasHeight)],
            writer.get_callback())
        # A no-op update has no writer callback.
        if cause != 'height_changed':
            assert writer.succeeded()
    assert not eligible(reloaded, tx_hash, tip=1000)
    assert reloaded.settle_verified(tx_hash, 501, 0, TxProof(0, [b'n' * 32]))
    flush(store)
    final = TransactionCache(store)
    assert eligible(final, tx_hash, tip=600)
    assert not eligible(final, tx_hash, tip=599)
    assert final.get_proof(tx_hash).branch == [b'n' * 32]


def test_orphan_descendant_outputs_are_not_spendable(reward_cache):
    cache, store, reward = reward_cache
    child = Transaction(2, [XTxInput(reward.hash(), 2, Script(), 0xffffffff)],
        [XTxOutput(800, reward.outputs[2].script_pubkey)], 0)
    with SynchronousWriter() as writer:
        cache.add([(child.hash(), TxData(height=0), child, TxFlags.StateCleared, None)],
            writer.get_callback())
        assert writer.succeeded()
    assert eligible(cache, child.hash())
    with SynchronousWriter() as writer:
        assert cache.invalidate_coinbase(reward.hash(), writer.get_callback())
        assert writer.succeeded()
    assert not eligible(cache, child.hash())
    assert not eligible(cache, child.hash(), for_spending=False)
    assert cache.get_transaction(child.hash()) is not None
    assert not cache.invalidate_coinbase(child.hash())


def test_invalid_coin_selection_is_rejected_before_fee_estimation():
    account = SimpleNamespace(_wallet=SimpleNamespace(is_transaction_chain_valid=lambda _: False))
    with pytest.raises(ValueError, match='mining reward'):
        AbstractAccount.make_unsigned_transaction(account, [SimpleNamespace(tx_hash=b'x' * 32)],
            [], Mock())


@pytest.mark.parametrize('same_block', [False, True])
def test_merkle_reply_from_superseded_block_is_not_admitted(same_block):
    import asyncio
    from electrumsv.network import Network
    tx_hash = b't' * 32
    old_header = SimpleNamespace(merkle_root=tx_hash, hash=b'o' * 32, timestamp=1)
    current_header = old_header if same_block else SimpleNamespace(hash=b'n' * 32)
    async def request_proof(tx_id, height):
        return {'merkle': [], 'pos': 0}
    async def headers_at_heights(heights):
        return {500: old_header}
    session = SimpleNamespace(logger=Mock(), request_proof=request_proof,
        headers_at_heights=headers_at_heights)
    async def main_session():
        return session
    network = SimpleNamespace(_main_session=main_session,
        chain=lambda: SimpleNamespace(header_at_height=lambda height: current_header))
    wallet = Mock()
    assert not asyncio.run(Network._request_proofs(network, wallet, {tx_hash: 500}))
    assert wallet.add_transaction_proof.called == same_block


def test_history_shows_invalidated_reward_as_unverified_not_normal_mempool(reward_cache):
    from electrumsv.gui.qt.history_list import get_tx_status, TxStatus
    cache, store, reward = reward_cache
    with SynchronousWriter() as writer:
        cache.invalidate_coinbase(reward.hash(), writer.get_callback())
        assert writer.succeeded()
    account = SimpleNamespace(have_transaction_data=lambda tx_hash: True,
        get_transaction_metadata=cache.get_metadata, get_transaction=cache.get_transaction)
    assert get_tx_status(account, reward.hash(), 0, 0, False) == TxStatus.REWARD_UNVERIFIED


@pytest.mark.parametrize('another_key_reports_reward', [False, True])
def test_reward_removed_from_all_subscriptions_is_invalidated(reward_cache, monkeypatch,
        another_key_reports_reward):
    import asyncio
    from bitcoinx import hash_to_hex_str
    from electrumsv.wallet import SyncState
    from electrumsv.app_state import app_state
    cache, store, reward = reward_cache
    sync = SyncState()
    tx_id = hash_to_hex_str(reward.hash())
    sync.set_key_history(7, [(tx_id, 500)])
    if another_key_reports_reward:
        sync.set_key_history(8, [(tx_id, 500)])
    account = SimpleNamespace(_stopped=False, lock=threading.RLock(), _logger=Mock(),
        _sync_state=sync, _keyinstances={7: SimpleNamespace(script_type=ScriptType.P2PKH)},
        synchronize=Mock())
    account._wallet = SimpleNamespace(_transaction_cache=cache, get_accounts=lambda: [account],
        txs_changed_event=Mock(), trigger_callback=Mock())
    # Completion is deliberately deferred; this test only exercises history admission/invalidation.
    monkeypatch.setattr('electrumsv.wallet.app_state', SimpleNamespace(
        app=SimpleNamespace(run_in_thread=lambda function: None)))
    asyncio.run(AbstractAccount.set_key_history(account, 7, ScriptType.P2PKH, [], {}))
    flush(store)
    assert bool(cache.get_flags(reward.hash()) & TxFlags.StateSettled) == another_key_reports_reward


@pytest.mark.parametrize('reward_parent,tip,selectable', [
    (False, 598, True), (True, 599, True), (True, 598, False)])
def test_zero_confirmation_selection_preserved_except_invalid_reward_ancestry(
        reward_cache, reward_parent, tip, selectable):
    cache, store, reward = reward_cache
    parent_hash = reward.hash() if reward_parent else b'e' * 32
    tx = Transaction(2, [XTxInput(parent_hash, 2, Script(), 0xffffffff)],
        [XTxOutput(800, reward.outputs[2].script_pubkey)], 0)
    with SynchronousWriter() as writer:
        cache.add([(tx.hash(), TxData(height=0), tx, TxFlags.StateCleared, None)],
            writer.get_callback())
        assert writer.succeeded()
    wallet = SimpleNamespace(_transaction_cache=cache, get_local_height=lambda: tip,
        is_transaction_chain_valid=lambda tx_hash: eligible(cache, tx_hash, tip))
    utxo = SimpleNamespace(tx_hash=tx.hash(), value=800, is_coinbase=False)
    account = SimpleNamespace(_utxos_lock=threading.RLock(), _utxos={0: utxo}, _wallet=wallet,
        get_transaction_metadata=cache.get_metadata)
    assert bool(AbstractAccount.get_utxos(account, mature=True)) == selectable
    assert AbstractAccount.get_utxos(account, mature=True, confirmed_only=True) == []
    assert AbstractAccount.get_balance(account) == ((0, 800, 0) if selectable else (0, 0, 0))


@pytest.mark.parametrize('fork_tip', [599, 650])
def test_selected_fork_blocks_old_reward_before_async_reorg_cleanup(reward_cache, fork_tip):
    cache, store, reward = reward_cache
    tx_hash = reward.hash()
    original = wallet_on_chain(cache, 599)
    assert original.get_coinbase_status(tx_hash) == CoinbaseStatus.MATURE
    # Same or greater height, but this branch does not contain the reward's proof.
    fork = wallet_on_chain(cache, fork_tip, roots={500: b'f' * 32}, tip_hash=b'n' * 32)
    assert cache.get_flags(tx_hash) & TxFlags.StateSettled  # Cleanup has not run yet.
    assert fork.get_coinbase_status(tx_hash) == CoinbaseStatus.UNVERIFIED
    assert not fork.is_transaction_chain_valid(tx_hash)
    utxo = SimpleNamespace(tx_hash=tx_hash, value=900, is_coinbase=True)
    account = SimpleNamespace(_utxos_lock=threading.RLock(), _utxos={0: utxo}, _wallet=fork,
        get_transaction_metadata=cache.get_metadata)
    assert AbstractAccount.get_utxos(account, mature=True) == []
    assert AbstractAccount.get_balance(account) == (0, 0, 0)
    assert Wallet.get_tx_height(fork, tx_hash) == (500, 0, False)
    # Switching back to the proven branch restores eligibility without editing the record.
    assert original.get_coinbase_status(tx_hash) == CoinbaseStatus.MATURE


def test_shorter_selected_tip_makes_reward_immature_again(reward_cache):
    cache, store, reward = reward_cache
    assert wallet_on_chain(cache, 599).get_coinbase_status(reward.hash()) == CoinbaseStatus.MATURE
    assert wallet_on_chain(cache, 598).get_coinbase_status(reward.hash()) == CoinbaseStatus.IMMATURE


def test_formerly_confirmed_descendant_cannot_bypass_fork_membership(reward_cache):
    cache, store, reward = reward_cache
    child = Transaction(2, [XTxInput(reward.hash(), 2, Script(), 0xffffffff)],
        [XTxOutput(800, reward.outputs[2].script_pubkey)], 0)
    with SynchronousWriter() as writer:
        cache.add([(child.hash(), TxData(height=600, position=1), child, TxFlags.StateSettled, None)],
            writer.get_callback())
        assert writer.succeeded()
    with SynchronousWriter() as writer:
        cache.update_proof(child.hash(), TxProof(1, [b'q' * 32]), writer.get_callback())
        assert writer.succeeded()
    assert wallet_on_chain(cache, 699).is_transaction_chain_valid(child.hash())
    fork = wallet_on_chain(cache, 699, roots={}, tip_hash=b'n' * 32)
    assert not fork.is_transaction_chain_valid(child.hash())


@pytest.mark.parametrize('legacy_byte_order', [False, True])
def test_offline_tip_resolves_by_hash_not_only_stored_height(monkeypatch, legacy_byte_order):
    block_hash = bytes(range(32))
    chain = SimpleNamespace()
    headers = Mock()
    headers.lookup.side_effect = lambda value: (chain, 599) if value == block_hash else (None, -1)
    monkeypatch.setattr('electrumsv.wallet.app_state', SimpleNamespace(headers=headers))
    wallet = SimpleNamespace(_network=None, _last_load_height=599,
        _last_load_hash=block_hash[::-1] if legacy_byte_order else block_hash)
    tip = Wallet.get_chain_tip(wallet)
    assert (tip.height, tip.block_hash) == (599, block_hash)
    wallet._last_load_height = 600
    assert Wallet.get_chain_tip(wallet) is None
    headers.longest_chain.assert_not_called()


def test_stopping_wallet_persists_actual_tip_method_and_hash_order():
    from bitcoinx import hash_to_hex_str
    header = SimpleNamespace(height=599, hash=bytes(range(32)))
    network = Mock()
    network.chain.return_value = SimpleNamespace(tip=lambda: header)
    wallet = SimpleNamespace(_stopped=False, _network=network, _storage=Mock(),
        _last_load_height=0, _last_load_hash=None, get_accounts=lambda: [], _transaction_table=None)
    Wallet.stop(wallet)
    wallet._storage.put.assert_any_call('stored_height', 599)
    wallet._storage.put.assert_any_call('last_tip_hash', hash_to_hex_str(header.hash))


def test_fork_after_reward_keeps_common_ancestor_reward_mature(reward_cache):
    from electrumsv.network import _root_from_proof
    cache, store, reward = reward_cache
    proof = cache.get_proof(reward.hash())
    root = _root_from_proof(reward.hash(), proof.branch, proof.position)
    fork = wallet_on_chain(cache, 599, roots={500: root}, tip_hash=b'n' * 32)
    assert fork.get_coinbase_status(reward.hash()) == CoinbaseStatus.MATURE


def test_online_selection_uses_network_chain_not_global_longest(monkeypatch):
    header = SimpleNamespace(height=599, hash=b's' * 32)
    selected = SimpleNamespace(height=599, header_at_height=lambda height: header)
    network = SimpleNamespace(chain=lambda: selected)
    headers = Mock()
    monkeypatch.setattr('electrumsv.wallet.app_state', SimpleNamespace(headers=headers))
    tip = Wallet.get_chain_tip(SimpleNamespace(_network=network))
    assert (tip.chain, tip.height, tip.block_hash) == (selected, 599, b's' * 32)
    headers.longest_chain.assert_not_called()
