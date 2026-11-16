"""A selected chain-tip snapshot and transaction membership checks, independent of networking."""
from dataclasses import dataclass
from enum import Enum

from bitcoinx import MissingHeader, double_sha256


class CoinbaseStatus(Enum):
    UNVERIFIED = 'unverified'
    IMMATURE = 'immature'
    MATURE = 'mature'


@dataclass(frozen=True)
class ChainTip:
    chain: object
    height: int
    block_hash: bytes

    def contains(self, tx_hash, metadata, proof):
        """A stored proof must belong to an ancestor of this exact tip, not merely its height."""
        if (proof is None or metadata.height is None or
                not 0 < metadata.height <= self.height or
                metadata.position is None or proof.position != metadata.position or
                proof.position < 0):
            return False
        try:
            if self.chain.header_at_height(self.height).hash != self.block_hash:
                return False
            header = self.chain.header_at_height(metadata.height)
            root, index = tx_hash, proof.position
            for sibling in proof.branch:
                if len(sibling) != 32:
                    return False
                root = double_sha256(sibling + root if index & 1 else root + sibling)
                index >>= 1
            return index == 0 and root == header.merkle_root
        except (MissingHeader, ValueError):
            return False
